import { Fragment, useEffect, useState } from 'react'

import { ApiError, apiFetch } from '../api/client'
import { useSession } from '../auth/useSession'
import { AdminBar } from '../components/AdminBar'

const COST = '/api/admin/cost'
/** 全局约定 #4：`size` 上限 100 */
const PAGE_SIZE = 100
const CST_OFFSET_MS = 8 * 60 * 60 * 1000

/**
 * 金额一律**存分、渲染时才除 100**（全局约定 3）。
 * 主数字与「按商户」的合计**不由前端二次聚合**——两个来源迟早会漂，
 * 而且分页之后自己加出来的数只会悄悄少算（见 `CT-15`）。
 */
function money(cents: number): string {
  return `¥${(cents / 100).toFixed(2)}`
}

/** 成功率是 0..1 的比例；空集后端给 0，故这里是 `0%` 而不是 `NaN%` */
function percent(rate: number): string {
  return `${Math.round(rate * 100)}%`
}

/** 北京时间的今天。统计区间跟运营翻的日历走，不跟 UTC。 */
function todayCst(): string {
  return new Date(Date.now() + CST_OFFSET_MS).toISOString().slice(0, 10)
}

function shiftDays(day: string, days: number): string {
  const at = new Date(`${day}T00:00:00Z`)
  at.setUTCDate(at.getUTCDate() + days)
  return at.toISOString().slice(0, 10)
}

interface Range {
  from: string
  to: string
}

const PRESETS = [
  { key: 'today', label: '今天', of: (): Range => ({ from: todayCst(), to: todayCst() }) },
  { key: 'week', label: '近 7 天', of: (): Range => ({ from: shiftDays(todayCst(), -6), to: todayCst() }) },
  { key: 'month30', label: '近 30 天', of: (): Range => ({ from: shiftDays(todayCst(), -29), to: todayCst() }) },
  { key: 'thisMonth', label: '本月', of: (): Range => ({ from: `${todayCst().slice(0, 7)}-01`, to: todayCst() }) },
] as const

interface Summary {
  total_cents: number
  gen_count: number
  job_count: number
  success_count: number
  fail_count: number
  avg_cents: number
}

interface ProviderRow {
  provider: string
  total_cents: number
  gen_count: number
  avg_cents: number
  success_rate: number
}

interface MerchantRow {
  merchant_id: number
  shop_name: string | null
  total_cents: number
  gen_count: number
  video_count: number
  copy_count: number
}

interface DayRow {
  date: string
  total_cents: number
  gen_count: number
}

interface AlertRow {
  merchant_id: number
  shop_name: string | null
  alert_date: string
  spend_cents: number
  limit_cents: number
}

interface MerchantDetail {
  merchant: { id: number; account: string | null; nickname: string | null; status: string }
  by_provider: ProviderRow[]
  recent_jobs: {
    job_id: number
    kind: string
    status: string
    provider: string | null
    cost_cents: number
  }[]
}

/** 后端给 null 时不把格子留空——运营拿 id 还能查，空白只能猜 */
function shopLabel(shopName: string | null, merchantId: number): string {
  return shopName ?? `商户 #${merchantId}`
}

const ALERT_DATE_LABEL = '告警日'
const KIND_LABEL: Record<string, string> = { copy: '文案', video: '视频' }

export function CostDashboard() {
  const session = useSession()
  const [range, setRange] = useState<Range>(() => PRESETS[0].of())
  const [summary, setSummary] = useState<Summary | null>(null)
  const [providers, setProviders] = useState<ProviderRow[]>([])
  const [merchants, setMerchants] = useState<MerchantRow[]>([])
  const [days, setDays] = useState<DayRow[]>([])
  const [alerts, setAlerts] = useState<AlertRow[]>([])
  const [busy, setBusy] = useState(true)
  const [rangeError, setRangeError] = useState<string | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [expanded, setExpanded] = useState<number | null>(null)
  const [details, setDetails] = useState<Record<number, MerchantDetail | null>>({})

  // 五段**一起重取**，看的是同一个时间窗。各段各自算一个窗口的看板，
  // 数字永远对不上，而且那种错不会报错。
  useEffect(() => {
    let alive = true
    const qs = new URLSearchParams({ from: range.from, to: range.to })
    setRangeError(null)
    setLoadError(null)
    setBusy(true)

    const load = async <T,>(path: string, commit: (data: T) => void) => {
      try {
        const data = await apiFetch<T>(`${path}?${qs}`)
        if (alive) commit(data)
      } catch (error) {
        // 401 的令牌已被清空，守卫会把人送到 /login——这里不要再写状态
        if (!alive || (error instanceof ApiError && error.status === 401)) return
        const detail = error instanceof ApiError && error.detail ? error.detail : '加载失败，请稍后再试'
        // 区间非法是**输入**的问题，就地报在区间控件上
        if (error instanceof ApiError && error.status === 422) setRangeError(detail)
        else setLoadError(detail)
      }
    }

    void (async () => {
      await Promise.all([
        load<Summary>(`${COST}/summary`, setSummary),
        load<{ items: ProviderRow[] }>(`${COST}/by-provider`, (data) => setProviders(data.items)),
        // 这一段的查询串**多两个分页参数**：拼成 `?page=..&size=..&${qs}` 而不是
        // 再挂一个 `?`——后者会让整条 from/to 变成 URL 里的普通字符，
        // 后端照旧返回，而前端拿到的是一个**不随时间窗走的**报表
        load<{ items: MerchantRow[] }>(
          `${COST}/by-merchant?page=1&size=${PAGE_SIZE}&${qs}`,
          (data) => setMerchants(data.items),
        ),
        load<{ items: DayRow[] }>(`${COST}/by-day`, (data) => setDays(data.items)),
        load<{ items: AlertRow[] }>(`${COST}/budget-alerts`, (data) => setAlerts(data.items)),
      ])
      // 失败的那几段**保留上一次成功的数字**（区间非法时五段全失败，
      // 屏幕上仍是上一个窗口的报表——空屏比旧数字更难判断）
      if (alive) setBusy(false)
    })()

    return () => {
      alive = false
    }
  }, [range])

  // 明细按需取：一进页面就把每家店的明细都拉一遍，为的是没人看的那几份
  useEffect(() => {
    if (expanded === null) return
    const merchantId = expanded
    const qs = new URLSearchParams({ from: range.from, to: range.to })
    let alive = true
    void (async () => {
      try {
        const data = await apiFetch<MerchantDetail>(`${COST}/merchants/${merchantId}/detail?${qs}`)
        if (alive) setDetails((prev) => ({ ...prev, [merchantId]: data }))
      } catch {
        if (alive) setDetails((prev) => ({ ...prev, [merchantId]: null }))
      }
    })()
    return () => {
      alive = false
    }
  }, [expanded, range])

  if (!session) return null

  const peak = days.reduce((max, day) => Math.max(max, day.total_cents), 0)

  return (
    <>
      <AdminBar session={session} />
      <main className="shell page">
        <header className="stack">
          <h1 className="page__title">成本看板</h1>
          <p className="lede">平台真实花出去的钱（付给 provider 的账单），不是向商户的计费额。</p>
        </header>

        <section className="cost-head">
          <div className="cost-strip" data-testid="cost-strip">
            {summary ? (
              <>
                <div className="cost-total">
                  <p className="cost-total__label">总花费</p>
                  <p className="cost-total__value" data-testid="cost-total">
                    {money(summary.total_cents)}
                  </p>
                </div>
                <dl className="cost-facts">
                  <div className="cost-fact">
                    <dt>生成数</dt>
                    <dd data-testid="cost-gen">{summary.gen_count}</dd>
                  </div>
                  <div className="cost-fact">
                    <dt>成功</dt>
                    <dd data-testid="cost-success">{summary.success_count}</dd>
                  </div>
                  <div className="cost-fact">
                    <dt>失败</dt>
                    <dd data-testid="cost-fail">{summary.fail_count}</dd>
                  </div>
                  <div className="cost-fact">
                    <dt>均价</dt>
                    <dd data-testid="cost-avg">{money(summary.avg_cents)}</dd>
                  </div>
                </dl>
              </>
            ) : (
              <p className="lede" data-testid="cost-loading">
                正在加载…
              </p>
            )}
          </div>

          {/* 区间控件**不单独占一行**：它缩在总额带右侧 */}
          <div className="cost-range" aria-label="统计区间">
            <div className="field">
              <label className="field__label" htmlFor="cost-from">
                起
              </label>
              <input
                id="cost-from"
                type="date"
                className="field__input"
                value={range.from}
                onChange={(event) => setRange((prev) => ({ ...prev, from: event.target.value }))}
              />
            </div>
            <div className="field">
              <label className="field__label" htmlFor="cost-to">
                止
              </label>
              <input
                id="cost-to"
                type="date"
                className="field__input"
                value={range.to}
                onChange={(event) => setRange((prev) => ({ ...prev, to: event.target.value }))}
              />
            </div>
            <div className="row cost-range__presets">
              {PRESETS.map((preset) => (
                <button
                  key={preset.key}
                  type="button"
                  className="btn btn--quiet"
                  onClick={() => setRange(preset.of())}
                >
                  {preset.label}
                </button>
              ))}
            </div>
          </div>
        </section>

        {rangeError ? (
          <p className="note note--error" role="alert" data-testid="cost-range-error">
            {rangeError}
          </p>
        ) : null}
        {loadError ? (
          <p className="note note--error" role="alert" data-testid="cost-load-error">
            {loadError}
          </p>
        ) : null}
        {busy ? (
          <p className="cost-busy" role="status" data-testid="cost-busy">
            正在刷新，上面是上一个区间的数字…
          </p>
        ) : null}

        <section className="cost-section">
          <h2 className="cost-section__title">按模型</h2>
          {providers.length === 0 ? (
            <p className="cost-empty" data-testid="cost-provider-empty">
              这个区间没有任何调用。
            </p>
          ) : (
            <table className="cost-table">
              <thead>
                <tr>
                  <th scope="col">模型</th>
                  <th scope="col">花费</th>
                  <th scope="col">次数</th>
                  <th scope="col">均价</th>
                  <th scope="col">成功率</th>
                </tr>
              </thead>
              <tbody>
                {providers.map((row) => (
                  <tr key={row.provider} data-testid={`cost-provider-${row.provider}`}>
                    <th scope="row" data-testid="cost-provider-name">
                      {row.provider}
                    </th>
                    <td data-testid="cost-provider-cost">{money(row.total_cents)}</td>
                    <td data-testid="cost-provider-count">{row.gen_count}</td>
                    <td data-testid="cost-provider-avg">{money(row.avg_cents)}</td>
                    <td data-testid="cost-provider-rate">{percent(row.success_rate)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>

        {/* 「仅在有数据时画」：空条带与「花费为 0」长得一样，画出来只会让人以为服务挂了 */}
        {days.length > 0 ? (
          <section className="cost-section" data-testid="cost-trend">
            <h2 className="cost-section__title">按日趋势</h2>
            <ol className="cost-bars">
              {days.map((day) => (
                <li
                  key={day.date}
                  className="cost-bar"
                  data-testid={`cost-bar-${day.date}`}
                  aria-label={`${day.date} ${money(day.total_cents)}`}
                >
                  <span
                    className="cost-bar__fill"
                    style={{ height: `${peak ? Math.round((day.total_cents / peak) * 100) : 0}%` }}
                  />
                  <span className="cost-bar__label">{day.date.slice(5)}</span>
                </li>
              ))}
            </ol>
          </section>
        ) : null}

        <section className="cost-section">
          <h2 className="cost-section__title">按商户</h2>
          {merchants.length === 0 ? (
            <p className="cost-empty" data-testid="cost-merchant-empty">
              这个区间还没有商户产生花费。
            </p>
          ) : (
            <table className="cost-table">
              <thead>
                <tr>
                  <th scope="col">店名</th>
                  <th scope="col">花费</th>
                  <th scope="col">生成数</th>
                  <th scope="col">视频</th>
                  <th scope="col">文案</th>
                  <th scope="col">
                    <span className="cost-table__hint">明细</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {merchants.map((row) => {
                  const open = expanded === row.merchant_id
                  const detail = details[row.merchant_id]
                  return (
                    <Fragment key={row.merchant_id}>
                      <tr data-testid={`cost-merchant-${row.merchant_id}`}>
                        <th scope="row" data-testid="cost-merchant-name">
                          {shopLabel(row.shop_name, row.merchant_id)}
                        </th>
                        <td data-testid="cost-merchant-cost">{money(row.total_cents)}</td>
                        <td data-testid="cost-merchant-gen">{row.gen_count}</td>
                        <td data-testid="cost-merchant-video">{row.video_count}</td>
                        <td data-testid="cost-merchant-copy">{row.copy_count}</td>
                        <td>
                          <button
                            type="button"
                            className="btn btn--text"
                            aria-expanded={open}
                            onClick={() => setExpanded(open ? null : row.merchant_id)}
                            data-testid={`cost-merchant-open-${row.merchant_id}`}
                          >
                            {open ? '收起' : '展开'}
                          </button>
                        </td>
                      </tr>
                      {open ? (
                        <tr
                          className="cost-mdetail"
                          data-testid={`cost-merchant-detail-${row.merchant_id}`}
                        >
                          <td colSpan={6}>
                            {detail ? (
                              <>
                                <p className="cost-mdetail__line">
                                  {detail.by_provider
                                    .map((item) => `${item.provider} ${money(item.total_cents)}`)
                                    .join(' · ') || '这个区间没有调用'}
                                </p>
                                <ul className="cost-mdetail__jobs">
                                  {detail.recent_jobs.map((job) => (
                                    <li key={job.job_id} data-testid={`cost-mdetail-job-${job.job_id}`}>
                                      {KIND_LABEL[job.kind] ?? job.kind} · {job.status} ·{' '}
                                      {money(job.cost_cents)}
                                    </li>
                                  ))}
                                </ul>
                              </>
                            ) : detail === null ? (
                              '明细加载失败。'
                            ) : (
                              '正在加载明细…'
                            )}
                          </td>
                        </tr>
                      ) : null}
                    </Fragment>
                  )
                })}
              </tbody>
            </table>
          )}
        </section>

        {/* 空了就整段不渲染：空表占位会把「没有告警」读成「加载失败」 */}
        {alerts.length > 0 ? (
          <section className="cost-section" data-testid="cost-alerts">
            <h2 className="cost-section__title">熔断告警</h2>
            <table className="cost-table">
              <thead>
                <tr>
                  <th scope="col">店名</th>
                  <th scope="col">{ALERT_DATE_LABEL}</th>
                  <th scope="col">当日花费</th>
                  <th scope="col">当日预算</th>
                </tr>
              </thead>
              <tbody>
                {alerts.map((row) => (
                  <tr key={`${row.merchant_id}-${row.alert_date}`} data-testid={`cost-alert-${row.merchant_id}`}>
                    <th scope="row" data-testid="cost-alert-shop">
                      {shopLabel(row.shop_name, row.merchant_id)}
                    </th>
                    <td data-testid="cost-alert-date">{row.alert_date}</td>
                    <td data-testid="cost-alert-spend">{money(row.spend_cents)}</td>
                    <td data-testid="cost-alert-limit">{money(row.limit_cents)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>
        ) : null}
      </main>
    </>
  )
}
