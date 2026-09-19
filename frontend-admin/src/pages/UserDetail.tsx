import { useEffect, useState, type FormEvent } from 'react'
import { useLocation, useNavigate, useParams } from 'react-router-dom'

import { ApiError, apiFetch } from '../api/client'
import { useSession } from '../auth/useSession'
import { AdminBar } from '../components/AdminBar'
import { formatCst } from '../format'

const USERS = '/api/admin/users'
const LOGS = '/api/admin/action-logs'
/** 审计只翻最近的：翻更早的账要去后台直接查库，不是这一页的事 */
const LOG_PAGE_SIZE = 20

/**
 * 与后端 `MIN_BAN_REASON_CHARS` 同一条下界。前端先拦一道**不是为了省一次
 * 请求**——是为了让「理由太短」和「账号已被封禁」两种红字不会在同一个
 * 位置长得一样，运营不必猜自己错在哪。
 */
const MIN_REASON_CHARS = 5

const ROLE_LABEL: Record<string, string> = {
  merchant: '商户',
  customer: '客户',
  admin: '管理员',
}
const STATUS_LABEL: Record<string, string> = {
  active: '正常',
  banned: '已封禁',
  deleted: '已注销',
}
const ACTION_LABEL: Record<string, string> = {
  ban_user: '封禁',
  unban_user: '解封',
  resolve_content: '处理内容',
  resolve_ocr: '处理截图',
  appeal_accept: '申诉通过',
  appeal_reject: '申诉驳回',
}

interface UserRow {
  id: number
  account: string | null
  email: string | null
  nickname: string | null
  role: string
  status: string
  shop_name: string | null
  created_at: string
}

interface MerchantProfile {
  shop_name: string | null
  category: string | null
  address: string | null
  logo_url: string | null
  description: string | null
  contact: string | null
}

interface Detail {
  user: UserRow
  stats: {
    task_count: number
    claim_count: number
    job_count: number
    points_balance: number
  }
  /** **只对商户出现**——客户详情里连这个键都没有，故不写「恒为 null」的分支 */
  merchant_profile?: MerchantProfile | null
}

interface LogRow {
  id: number
  admin_id: number
  action: string
  target_type: string
  target_id: number
  detail: Record<string, unknown> | null
  created_at: string
}

interface LogPage {
  items: LogRow[]
  total: number
}

/** 一条日志能读出来的那句话。`detail` 是 jsonb，键随动作变，故只挑认识的。 */
function logSummary(detail: Record<string, unknown> | null): string {
  if (!detail) return ''
  const parts: string[] = []
  const reason = detail.reason
  const note = detail.note
  const before = detail.before
  const after = detail.after
  if (typeof reason === 'string' && reason) parts.push(reason)
  if (typeof note === 'string' && note) parts.push(note)
  if (typeof before === 'string' && typeof after === 'string') {
    parts.push(`${STATUS_LABEL[before] ?? before} → ${STATUS_LABEL[after] ?? after}`)
  }
  return parts.join(' · ')
}

export function UserDetail() {
  const session = useSession()
  const navigate = useNavigate()
  const location = useLocation()
  const params = useParams()

  // 非数字 id 压根不该发请求：`/users/abc` 打过去只会拿回 404 或 422，
  // 屏幕上那句话一模一样，白白多一条日志
  const id = Number(params.id ?? '')
  const valid = Number.isInteger(id) && id > 0

  const [detail, setDetail] = useState<Detail | null>(null)
  const [logs, setLogs] = useState<LogRow[]>([])
  const [missing, setMissing] = useState(!valid)
  const [loading, setLoading] = useState(valid)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [reason, setReason] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  // 动作之后重取一次详情：屏幕上那份状态可能已经被别处改过，
  // 而「本地改一改就行」的写法在 409 之后会把过期状态一直留在屏幕上
  const [token, setToken] = useState(0)

  useEffect(() => {
    if (!valid) return
    let alive = true
    setLoading(true)
    setLoadError(null)

    void (async () => {
      try {
        const [data, logPage] = await Promise.all([
          apiFetch<Detail>(`${USERS}/${id}`),
          apiFetch<LogPage>(`${LOGS}?target_type=user&target_id=${id}&size=${LOG_PAGE_SIZE}`),
        ])
        if (!alive) return
        setDetail(data)
        setLogs(logPage.items)
        setMissing(false)
      } catch (error) {
        if (!alive) return
        // 401 时令牌已被清空，守卫会把人送到 /login——这里不要再写状态
        if (error instanceof ApiError && error.status === 401) return
        if (error instanceof ApiError && error.status === 404) {
          setMissing(true)
          return
        }
        setLoadError(
          error instanceof ApiError && error.detail ? error.detail : '加载失败，请稍后再试',
        )
      } finally {
        if (alive) setLoading(false)
      }
    })()

    return () => {
      alive = false
    }
  }, [id, valid, token])

  /** 深链进来（`location.key === 'default'`）背后没有列表可回，就回列表页 */
  function back() {
    if (location.key !== 'default') navigate(-1)
    else navigate('/users')
  }

  function fail(error: unknown) {
    if (error instanceof ApiError && error.status === 401) return
    setError(
      error instanceof ApiError && error.detail ? error.detail : '操作失败，请稍后再试',
    )
    setToken((n) => n + 1)
  }

  async function ban(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (busy) return
    const trimmed = reason.trim()
    if (trimmed.length < MIN_REASON_CHARS) {
      setError(`封禁理由至少 ${MIN_REASON_CHARS} 字——执法必须能追溯`)
      return
    }
    setBusy(true)
    setError(null)
    try {
      await apiFetch(`${USERS}/${id}/ban`, { method: 'POST', body: { reason: trimmed } })
      setReason('')
      setToken((n) => n + 1)
    } catch (error) {
      fail(error)
    } finally {
      setBusy(false)
    }
  }

  async function unban() {
    if (busy) return
    setBusy(true)
    setError(null)
    try {
      await apiFetch(`${USERS}/${id}/unban`, { method: 'POST' })
      setToken((n) => n + 1)
    } catch (error) {
      fail(error)
    } finally {
      setBusy(false)
    }
  }

  if (!session) return null

  const user = detail?.user

  return (
    <>
      <AdminBar session={session} />
      <main className="shell page">
        <div className="row">
          <button type="button" className="btn btn--quiet" onClick={back}>
            返回
          </button>
        </div>

        {missing ? (
          <p className="usr-empty" data-testid="usr-missing">
            用户不存在
          </p>
        ) : loadError ? (
          <div className="stack" data-testid="usr-load-error">
            <p className="note note--error" role="alert">
              {loadError}
            </p>
          </div>
        ) : loading || !user || !detail ? (
          <p className="lede">正在加载…</p>
        ) : (
          <>
            <section className="panel usr-profile" data-testid="usr-profile">
              <header className="usr-profile__head">
                <h1 className="page__title" data-testid="usr-account">
                  {user.account ?? '—'}
                </h1>
                <span className="badge" data-testid="usr-role">
                  {ROLE_LABEL[user.role] ?? user.role}
                </span>
              </header>

              <dl className="usr-facts">
                <div className="usr-fact">
                  <dt>邮箱</dt>
                  <dd data-testid="usr-email">{user.email ?? '—'}</dd>
                </div>
                <div className="usr-fact">
                  <dt>昵称</dt>
                  <dd>{user.nickname ?? '—'}</dd>
                </div>
                <div className="usr-fact">
                  <dt>状态</dt>
                  <dd data-testid="usr-status-code">{STATUS_LABEL[user.status] ?? user.status}</dd>
                </div>
                <div className="usr-fact">
                  <dt>店名</dt>
                  <dd data-testid="usr-shop">{user.shop_name ?? '—'}</dd>
                </div>
                <div className="usr-fact">
                  <dt>注册时间</dt>
                  <dd>{formatCst(user.created_at)}</dd>
                </div>
              </dl>

              <dl className="usr-stats" data-testid="usr-stats">
                <div className="usr-stat">
                  <dt>发布任务</dt>
                  <dd data-testid="usr-stat-task">{detail.stats.task_count}</dd>
                </div>
                <div className="usr-stat">
                  <dt>领取任务</dt>
                  <dd data-testid="usr-stat-claim">{detail.stats.claim_count}</dd>
                </div>
                <div className="usr-stat">
                  <dt>内容产出</dt>
                  <dd data-testid="usr-stat-job">{detail.stats.job_count}</dd>
                </div>
                <div className="usr-stat">
                  <dt>积分余额</dt>
                  <dd data-testid="usr-stat-points">{detail.stats.points_balance}</dd>
                </div>
              </dl>
            </section>

            {/* 客户详情里连 `merchant_profile` 这个键都没有，所以整块不出现 */}
            {detail.merchant_profile ? (
              <section
                className="panel usr-merchant"
                data-testid="usr-merchant-profile"
              >
                <h2 className="section-title">商户档案</h2>
                <dl className="usr-facts">
                  <div className="usr-fact">
                    <dt>店名</dt>
                    <dd>{detail.merchant_profile.shop_name ?? '—'}</dd>
                  </div>
                  <div className="usr-fact">
                    <dt>品类</dt>
                    <dd>{detail.merchant_profile.category ?? '—'}</dd>
                  </div>
                  <div className="usr-fact">
                    <dt>地址</dt>
                    <dd>{detail.merchant_profile.address ?? '—'}</dd>
                  </div>
                  <div className="usr-fact">
                    <dt>联系方式</dt>
                    <dd>{detail.merchant_profile.contact ?? '—'}</dd>
                  </div>
                  <div className="usr-fact usr-fact--wide">
                    <dt>简介</dt>
                    <dd>{detail.merchant_profile.description ?? '—'}</dd>
                  </div>
                </dl>
              </section>
            ) : null}

            <section className="panel" aria-label="封禁操作">
              {user.status === 'banned' ? (
                <div className="row">
                  <button
                    type="button"
                    className="btn btn--primary"
                    onClick={() => void unban()}
                    disabled={busy}
                  >
                    解封
                  </button>
                  <p className="lede">解封后该账号立即恢复登录。</p>
                </div>
              ) : user.status === 'active' ? (
                <form className="usr-ban" onSubmit={ban}>
                  <div className="field">
                    <label className="field__label" htmlFor="usr-reason">
                      封禁理由
                    </label>
                    <input
                      id="usr-reason"
                      name="reason"
                      type="text"
                      className="field__input"
                      value={reason}
                      onChange={(event) => setReason(event.target.value)}
                      placeholder={`至少 ${MIN_REASON_CHARS} 字，会记进审计`}
                    />
                  </div>
                  <button type="submit" className="btn btn--primary" disabled={busy}>
                    封禁
                  </button>
                </form>
              ) : (
                <p className="lede">该账号已注销，封禁没有意义。</p>
              )}

              {error ? (
                <p className="note note--error" role="alert" data-testid="usr-error">
                  {error}
                </p>
              ) : null}
            </section>

            <section className="usr-logs" data-testid="usr-logs" aria-label="操作日志">
              <h2 className="section-title">操作日志</h2>
              {logs.length === 0 ? (
                <p className="lede">这个账号还没有被处理过。</p>
              ) : (
                <ul className="usr-log-list">
                  {logs.map((log) => (
                    <li key={log.id} className="usr-log" data-testid={`usr-log-${log.id}`}>
                      <span className="badge">{ACTION_LABEL[log.action] ?? log.action}</span>
                      <span className="usr-log__detail">{logSummary(log.detail)}</span>
                      <span className="usr-log__time">
                        {formatCst(log.created_at)} · 管理员 #{log.admin_id}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </section>
          </>
        )}
      </main>
    </>
  )
}
