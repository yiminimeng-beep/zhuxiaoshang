/**
 * G 组 · 后台另外三个页面（`NV-01` ~ `EX-14`，53 条）
 *
 * 三条最贵的断言：
 * - `CT-15` —— 总额带的主数字**必须**来自 `/summary`。前端拿「按商户」的
 *   items 自己加一遍，屏幕上数字一样（后端保证同源），但一分页就少算，
 *   而且永远不报错。
 * - `CT-09` —— 熔断告警为空时**整段不渲染**。「没有告警」与「加载失败」
 *   在一张空表上长得一模一样，所以断的是「该段不在文档里」。
 * - `EX-06` —— 处理成功后该行就地消失、计数 −1、列表**不重取**。
 *   「重取整个列表」在屏幕上看起来完全一样。
 *
 * 还有一条契约级的：申诉的动作体字段是 `admin_note`，**不是** `note`
 * （`DecideIn(extra="forbid")`，传错直接 422）——`EX-12` 钉住它。
 */

import { cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import {
  actionLogPage,
  appealExcItem,
  banOk,
  budgetAlerts,
  contentExcItem,
  costDays,
  costSummary,
  costSummaryEmpty,
  excCount,
  excPage,
  merchantCostDetail,
  merchantCosts,
  ocrExcItem,
  providerCosts,
  unbanOk,
  userDetail,
  userPage,
} from './fixtures'
import { currentPath, renderApp } from './render-app'
import { seedAdmin } from './seed-auth'
import { stubApi, type StubSpec } from './stub-api'

const COST = '/api/admin/cost'
const SUMMARY = `${COST}/summary`
const BY_PROVIDER = `${COST}/by-provider`
const BY_MERCHANT = `${COST}/by-merchant`
/** 按商户明细挂在 `merchants/{id}/detail` 下，**不是** `by-merchant/{id}/detail` */
const detailUrl = (id: number) => `${COST}/merchants/${id}/detail`
const BY_DAY = `${COST}/by-day`
const ALERTS = `${COST}/budget-alerts`
const USERS = '/api/admin/users'
const LOGS = '/api/admin/action-logs'
const EXC = '/api/admin/exceptions'
const APPEALS = '/api/admin/appeals'

const SEGMENTS = ['content_review', 'ocr_low_confidence', 'ocr_mismatch', 'appeal'] as const

const excListUrl = (type: string) => `${EXC}?type=${type}&size=100`
const excCountUrl = (type: string) => `${EXC}?type=${type}&size=1`

/** 从一条请求的 url 里取查询串（断言参数用，不依赖参数顺序） */
function query(url: string): URLSearchParams {
  return new URLSearchParams(url.split('?')[1] ?? '')
}

/** 路由探针上的查询串（用户管理页的筛选存在 URL 里） */
function routeQuery(): URLSearchParams {
  return query(currentPath())
}

/* -------------------------------------------------------------------------- */
/* 成本看板                                                                    */
/* -------------------------------------------------------------------------- */

/** 五段全绿的默认桩。区间参数由页面自己拼，故按**路径**打桩即可 */
function costStubs(over: Record<string, StubSpec> = {}): Record<string, StubSpec> {
  return {
    [`GET ${SUMMARY}`]: { status: 200, body: costSummary() },
    [`GET ${BY_PROVIDER}`]: { status: 200, body: providerCosts() },
    [`GET ${BY_MERCHANT}`]: { status: 200, body: merchantCosts() },
    [`GET ${BY_DAY}`]: { status: 200, body: costDays() },
    [`GET ${ALERTS}`]: { status: 200, body: budgetAlerts() },
    ...over,
  }
}

async function goCost() {
  seedAdmin()
  const api = stubApi(costStubs())
  renderApp({ route: '/cost' })
  await screen.findByTestId('cost-total')
  return api
}

/* -------------------------------------------------------------------------- */
/* 用户管理                                                                    */
/* -------------------------------------------------------------------------- */

function userStubs(over: Record<string, StubSpec> = {}): Record<string, StubSpec> {
  return {
    [`GET ${USERS}`]: { status: 200, body: userPage([{}]) },
    [`GET ${USERS}/7`]: { status: 200, body: userDetail() },
    [`GET ${LOGS}`]: { status: 200, body: actionLogPage() },
    ...over,
  }
}

/* -------------------------------------------------------------------------- */
/* 异常处理                                                                    */
/* -------------------------------------------------------------------------- */

function excStubs(over: Record<string, StubSpec> = {}): Record<string, StubSpec> {
  const base: Record<string, StubSpec> = {
    [`GET ${excListUrl('content_review')}`]: {
      status: 200,
      body: excPage([contentExcItem()]),
    },
    [`GET ${excListUrl('ocr_low_confidence')}`]: { status: 200, body: excPage([]) },
    [`GET ${excListUrl('ocr_mismatch')}`]: { status: 200, body: excPage([]) },
    [`GET ${excListUrl('appeal')}`]: { status: 200, body: excPage([]) },
  }
  for (const type of SEGMENTS) {
    base[`GET ${excCountUrl(type)}`] = { status: 200, body: excCount(0) }
  }
  return { ...base, ...over }
}

/* ========================================================================== */
/* NV · 路由与页签轨                                                           */
/* ========================================================================== */

describe('NV · 路由与页签轨', () => {
  it('NV-01 未登录打开 /cost → 落 /login，并记下原目标', async () => {
    stubApi({})
    renderApp({ route: '/cost' })

    await waitFor(() => expect(currentPath()).toBe('/login'))
    expect(sessionStorage.getItem('zxs.admin.redirect')).toBe('/cost')
  })

  it('NV-02 有 admin 登录态 → /cost 直接进，不经过 /login', async () => {
    const api = await goCost()

    expect(currentPath()).toBe('/cost')
    expect(api.count('POST', '/api/auth/login')).toBe(0)
  })

  it('NV-03 页签轨四个目的地，当前项 aria-current=page', async () => {
    await goCost()

    const tabs = within(screen.getByTestId('app-tabs'))
    for (const name of ['反馈', '成本', '用户', '异常']) {
      expect(tabs.getByRole('link', { name })).toBeInTheDocument()
    }
    expect(tabs.getByRole('link', { name: '成本' })).toHaveAttribute('aria-current', 'page')
    expect(tabs.getByRole('link', { name: '反馈' })).not.toHaveAttribute('aria-current')
  })

  it('NV-04 点页签轨「用户」→ /users', async () => {
    await goCost()
    const api = stubApi(userStubs())

    await userEvent.setup().click(within(screen.getByTestId('app-tabs')).getByRole('link', { name: '用户' }))

    await waitFor(() => expect(currentPath()).toBe('/users'))
    expect(api.count('GET', USERS)).toBe(1)
  })

  it('NV-05 未知路径 /nope → 落 /feedback（三页不得改坏既有回退）', async () => {
    seedAdmin()
    stubApi({ ['GET /api/admin/feedback']: { status: 200, body: { items: [], total: 0, page: 1, size: 100 } } })
    renderApp({ route: '/nope' })

    await waitFor(() => expect(currentPath()).toBe('/feedback'))
  })

  it('NV-06 被拦在 /cost → 登录成功后回跳 /cost（不是 /feedback）', async () => {
    stubApi({
      'POST /api/auth/login': {
        status: 200,
        body: {
          access_token: 'a',
          refresh_token: 'r',
          user: { id: 9, role: 'admin', nickname: '运营小张' },
        },
      },
      ...costStubs(),
    })
    renderApp({ route: '/cost' })
    await waitFor(() => expect(currentPath()).toBe('/login'))

    const user = userEvent.setup()
    await user.type(screen.getByLabelText('账号'), 'admin')
    await user.type(screen.getByLabelText('密码'), 'pw123456')
    await user.click(screen.getByRole('button', { name: '登录' }))

    await waitFor(() => expect(currentPath()).toBe('/cost'))
  })

  it('NV-07 被拦在 /users/12 → 登录成功后回跳 /users/12（深链目标也要认）', async () => {
    stubApi({
      'POST /api/auth/login': {
        status: 200,
        body: {
          access_token: 'a',
          refresh_token: 'r',
          user: { id: 9, role: 'admin', nickname: '运营小张' },
        },
      },
      ...userStubs({ [`GET ${USERS}/12`]: { status: 200, body: userDetail() } }),
    })
    renderApp({ route: '/users/12' })
    await waitFor(() => expect(currentPath()).toBe('/login'))

    const user = userEvent.setup()
    await user.type(screen.getByLabelText('账号'), 'admin')
    await user.type(screen.getByLabelText('密码'), 'pw123456')
    await user.click(screen.getByRole('button', { name: '登录' }))

    await waitFor(() => expect(currentPath()).toBe('/users/12'))
  })
})

/* ========================================================================== */
/* CT · 成本看板                                                               */
/* ========================================================================== */

describe('CT · 成本看板', () => {
  it('CT-01 首次进页 → 五段各打一次', async () => {
    const api = await goCost()

    expect(api.count('GET', SUMMARY)).toBe(1)
    expect(api.count('GET', BY_PROVIDER)).toBe(1)
    expect(api.count('GET', BY_MERCHANT)).toBe(1)
    expect(api.count('GET', BY_DAY)).toBe(1)
    expect(api.count('GET', ALERTS)).toBe(1)
    // 分页上限（全局约定 4）
    expect(query(api.callsTo('GET', BY_MERCHANT)[0].url).get('size')).toBe('100')
  })

  it('CT-02 主数字按分渲染：123456 分 → ¥1234.56', async () => {
    await goCost()

    expect(screen.getByTestId('cost-total')).toHaveTextContent('¥1234.56')
  })

  it('CT-03 四个次数字齐全，均价也按分渲染', async () => {
    await goCost()

    const strip = within(screen.getByTestId('cost-strip'))
    expect(strip.getByTestId('cost-gen')).toHaveTextContent('42')
    expect(strip.getByTestId('cost-success')).toHaveTextContent('40')
    expect(strip.getByTestId('cost-fail')).toHaveTextContent('2')
    expect(strip.getByTestId('cost-avg')).toHaveTextContent('¥29.39')
  })

  it('CT-04 空集 → ¥0.00 与 0，不出现 NaN / null', async () => {
    seedAdmin()
    stubApi(costStubs({ [`GET ${SUMMARY}`]: { status: 200, body: costSummaryEmpty() } }))
    renderApp({ route: '/cost' })

    const strip = await screen.findByTestId('cost-strip')
    expect(screen.getByTestId('cost-total')).toHaveTextContent('¥0.00')
    expect(within(strip).getByTestId('cost-avg')).toHaveTextContent('¥0.00')
    expect(strip.textContent).not.toContain('NaN')
    expect(strip.textContent).not.toContain('null')
  })

  it('CT-05 按模型表五列，成功率 0.5 → 50%、0 → 0%', async () => {
    seedAdmin()
    stubApi(
      costStubs({
        [`GET ${BY_PROVIDER}`]: {
          status: 200,
          body: providerCosts([
            { provider: 'doubao', success_rate: 0.5, avg_cents: 3333, gen_count: 30, total_cents: 100000 },
            { provider: 'qwen', success_rate: 0, avg_cents: 0, gen_count: 2, total_cents: 0 },
          ]),
        },
      }),
    )
    renderApp({ route: '/cost' })

    const row = within(await screen.findByTestId('cost-provider-doubao'))
    expect(row.getByTestId('cost-provider-name')).toHaveTextContent('doubao')
    expect(row.getByTestId('cost-provider-cost')).toHaveTextContent('¥1000.00')
    expect(row.getByTestId('cost-provider-count')).toHaveTextContent('30')
    expect(row.getByTestId('cost-provider-avg')).toHaveTextContent('¥33.33')
    expect(row.getByTestId('cost-provider-rate')).toHaveTextContent('50%')

    const zero = within(screen.getByTestId('cost-provider-qwen'))
    expect(zero.getByTestId('cost-provider-rate')).toHaveTextContent('0%')
    expect(zero.getByTestId('cost-provider-rate').textContent).not.toContain('NaN')
  })

  it('CT-06 按商户表五列；shop_name 为 null 时不留空', async () => {
    seedAdmin()
    stubApi(
      costStubs({
        [`GET ${BY_MERCHANT}`]: {
          status: 200,
          body: merchantCosts([
            { merchant_id: 7, total_cents: 60000, gen_count: 18, video_count: 6, copy_count: 12 },
            { merchant_id: 12, shop_name: null, total_cents: 100, gen_count: 1, video_count: 0, copy_count: 1 },
          ]),
        },
      }),
    )
    renderApp({ route: '/cost' })

    const row = within(await screen.findByTestId('cost-merchant-7'))
    expect(row.getByTestId('cost-merchant-name')).toHaveTextContent('巷口咖啡')
    expect(row.getByTestId('cost-merchant-cost')).toHaveTextContent('¥600.00')
    expect(row.getByTestId('cost-merchant-gen')).toHaveTextContent('18')
    expect(row.getByTestId('cost-merchant-video')).toHaveTextContent('6')
    expect(row.getByTestId('cost-merchant-copy')).toHaveTextContent('12')

    // 店名为 null 时不是空白格——运营拿 id 还能查，空白只能猜
    expect(within(screen.getByTestId('cost-merchant-12')).getByTestId('cost-merchant-name')).toHaveTextContent(
      '商户 #12',
    )
  })

  it('CT-07 按日趋势：根数 = 后端给的天数（前端不插空日期）', async () => {
    await goCost()

    const bars = screen.getAllByTestId(/^cost-bar-\d{4}-/)
    expect(bars).toHaveLength(3)
    expect(bars.map((bar) => bar.getAttribute('data-testid'))).toEqual([
      'cost-bar-2026-09-14',
      'cost-bar-2026-09-15',
      'cost-bar-2026-09-16',
    ])
    expect(bars[0]).toHaveAttribute('aria-label', expect.stringContaining('¥10.00'))
  })

  it('CT-08 按日趋势 items 为空 → 整段不渲染', async () => {
    seedAdmin()
    stubApi(costStubs({ [`GET ${BY_DAY}`]: { status: 200, body: { items: [] } } }))
    renderApp({ route: '/cost' })

    await screen.findByTestId('cost-total')
    expect(screen.queryByTestId('cost-trend')).not.toBeInTheDocument()
  })

  it('CT-09 熔断告警为空 → 整段不渲染（不留空表占位）', async () => {
    seedAdmin()
    stubApi(costStubs({ [`GET ${ALERTS}`]: { status: 200, body: { items: [] } } }))
    renderApp({ route: '/cost' })

    await screen.findByTestId('cost-total')
    // 「没有告警」与「加载失败」在一张空表上长得一模一样，所以断的是「不在文档里」
    expect(screen.queryByTestId('cost-alerts')).not.toBeInTheDocument()
  })

  it('CT-10 熔断告警有数据 → 四列', async () => {
    await goCost()

    const row = within(await screen.findByTestId('cost-alert-7'))
    expect(row.getByTestId('cost-alert-shop')).toHaveTextContent('巷口咖啡')
    expect(row.getByTestId('cost-alert-date')).toHaveTextContent('2026-09-16')
    expect(row.getByTestId('cost-alert-spend')).toHaveTextContent('¥3200.00')
    expect(row.getByTestId('cost-alert-limit')).toHaveTextContent('¥3000.00')
  })

  it('CT-11 点「按商户」行 → 就地展开该商户明细，再点收起', async () => {
    seedAdmin()
    const api = stubApi(
      costStubs({ [`GET ${detailUrl(7)}`]: { status: 200, body: merchantCostDetail() } }),
    )
    renderApp({ route: '/cost' })
    await screen.findByTestId('cost-total')
    // 明细只在点开之后才取——一进页面就把每家的明细都拉一遍是另一种写法
    expect(api.count('GET', detailUrl(7))).toBe(0)

    const user = userEvent.setup()
    await user.click(screen.getByTestId('cost-merchant-open-7'))

    const detail = within(await screen.findByTestId('cost-merchant-detail-7'))
    expect(await detail.findByText(/doubao/)).toBeInTheDocument()
    expect(detail.getByTestId('cost-mdetail-job-301')).toHaveTextContent('文案')
    expect(api.count('GET', detailUrl(7))).toBe(1)

    await user.click(screen.getByTestId('cost-merchant-open-7'))
    expect(screen.queryByTestId('cost-merchant-detail-7')).not.toBeInTheDocument()
  })

  it('CT-12 切「近 7 天」→ 五段一起重取，且五条的 from/to 完全相同', async () => {
    const api = await goCost()

    await userEvent.setup().click(screen.getByRole('button', { name: '近 7 天' }))

    await waitFor(() => expect(api.count('GET', SUMMARY)).toBe(2))
    const windows = [SUMMARY, BY_PROVIDER, BY_MERCHANT, BY_DAY, ALERTS].map((path) => {
      const q = query(api.callsTo('GET', path).at(-1)!.url)
      return `${q.get('from')}|${q.get('to')}`
    })
    // 五段必须看同一个时间窗——各自算一个窗口的看板，数字永远对不上
    expect(new Set(windows).size).toBe(1)
    expect(query(api.callsTo('GET', SUMMARY).at(-1)!.url).get('from')).toMatch(/^\d{4}-\d{2}-\d{2}$/)
  })

  it('CT-13 切「今天」→ from 与 to 相等', async () => {
    const api = await goCost()

    await userEvent.setup().click(screen.getByRole('button', { name: '今天' }))

    await waitFor(() => expect(api.count('GET', SUMMARY)).toBe(2))
    const q = query(api.callsTo('GET', SUMMARY).at(-1)!.url)
    expect(q.get('from')).toBe(q.get('to'))
  })

  it('CT-14 区间 422 → 就地报错，其余段保留上一次成功的数字', async () => {
    seedAdmin()
    stubApi(
      costStubs({
        [`GET ${SUMMARY}`]: [
          { status: 200, body: costSummary({ total_cents: 123456 }) },
          { status: 422, body: { detail: '时间跨度不得超过 366 天' } },
        ],
      }),
    )
    renderApp({ route: '/cost' })
    await screen.findByTestId('cost-total')

    await userEvent.setup().click(screen.getByRole('button', { name: '近 30 天' }))

    expect(await screen.findByTestId('cost-range-error')).toHaveTextContent('时间跨度')
    // 关键：不是整段清空、不是白屏——上一个窗口的数字还在
    expect(screen.getByTestId('cost-total')).toHaveTextContent('¥1234.56')
  })

  it('CT-15 主数字取自 /summary，不由「按商户」合计推出来', async () => {
    seedAdmin()
    stubApi(
      costStubs({
        [`GET ${SUMMARY}`]: { status: 200, body: costSummary({ total_cents: 123456 }) },
        // 按商户这一页的合计是 60000 分：前端若自己加一遍，屏幕上就是 ¥600.00
        [`GET ${BY_MERCHANT}`]: {
          status: 200,
          body: merchantCosts([{ total_cents: 60000 }], { total: 99 }),
        },
      }),
    )
    renderApp({ route: '/cost' })

    expect(await screen.findByTestId('cost-total')).toHaveTextContent('¥1234.56')
  })

  it('CT-16 切区间请求未回来时 → 总额带仍是上一层数字 + 轻提示', async () => {
    seedAdmin()
    stubApi(
      costStubs({
        [`GET ${SUMMARY}`]: [
          { status: 200, body: costSummary({ total_cents: 123456 }) },
          { status: 200, body: costSummary({ total_cents: 999 }), pending: true },
        ],
      }),
    )
    renderApp({ route: '/cost' })
    await screen.findByTestId('cost-total')

    await userEvent.setup().click(screen.getByRole('button', { name: '近 7 天' }))

    expect(await screen.findByTestId('cost-busy')).toBeInTheDocument()
    // 不留白、不闪 0
    expect(screen.getByTestId('cost-total')).toHaveTextContent('¥1234.56')
  })
})

/* ========================================================================== */
/* UM · 用户管理                                                               */
/* ========================================================================== */

describe('UM · 用户管理', () => {
  it('UM-01 /users 首次请求 ?page=1&size=100，不带任何筛选', async () => {
    seedAdmin()
    const api = stubApi(userStubs())
    renderApp({ route: '/users' })

    await screen.findByTestId('usr-row-7')
    const q = query(api.callsTo('GET', USERS)[0].url)
    expect(q.get('size')).toBe('100')
    expect(q.get('page')).toBe('1')
    expect(q.get('keyword')).toBeNull()
    expect(q.get('role')).toBeNull()
    expect(q.get('status')).toBeNull()
  })

  it('UM-02 结果表每行五列：账号 · 昵称 · 角色 · 状态 · 店名', async () => {
    seedAdmin()
    stubApi(
      userStubs({
        [`GET ${USERS}`]: {
          status: 200,
          body: userPage([
            { id: 7, role: 'merchant', status: 'active' },
            { id: 8, account: 'user0001', nickname: '小林', role: 'customer', status: 'banned', shop_name: null },
          ]),
        },
      }),
    )
    renderApp({ route: '/users' })

    const merchant = within(await screen.findByTestId('usr-row-7'))
    expect(merchant.getByTestId('usr-account')).toHaveTextContent('shop0001')
    expect(merchant.getByTestId('usr-nickname')).toHaveTextContent('巷口咖啡')
    expect(merchant.getByTestId('usr-role')).toHaveTextContent('商户')
    expect(merchant.getByTestId('usr-status')).toHaveTextContent('正常')
    expect(merchant.getByTestId('usr-shop')).toHaveTextContent('巷口咖啡')

    const customer = within(screen.getByTestId('usr-row-8'))
    expect(customer.getByTestId('usr-role')).toHaveTextContent('客户')
    expect(customer.getByTestId('usr-status')).toHaveTextContent('已封禁')
  })

  it('UM-03 关键词原样传（前端不自己拼通配符）', async () => {
    seedAdmin()
    const api = stubApi(userStubs())
    renderApp({ route: '/users' })
    await screen.findByTestId('usr-row-7')

    const user = userEvent.setup()
    await user.type(screen.getByLabelText('关键词'), '咖啡')
    await user.click(screen.getByRole('button', { name: '搜索' }))

    await waitFor(() => expect(api.count('GET', USERS)).toBe(2))
    const q = query(api.callsTo('GET', USERS).at(-1)!.url)
    // 原样：带 % 的话后端会把模式加宽，搜 a_b 会命中 axb
    expect(q.get('keyword')).toBe('咖啡')
    expect(q.get('keyword')).not.toContain('%')
  })

  it('UM-04 角色与状态下拉 → role / status 参数', async () => {
    seedAdmin()
    const api = stubApi(userStubs())
    renderApp({ route: '/users' })
    await screen.findByTestId('usr-row-7')

    const user = userEvent.setup()
    await user.selectOptions(screen.getByLabelText('角色'), 'merchant')
    await user.selectOptions(screen.getByLabelText('状态'), 'banned')
    await user.click(screen.getByRole('button', { name: '搜索' }))

    await waitFor(() => expect(api.count('GET', USERS)).toBe(2))
    const q = query(api.callsTo('GET', USERS).at(-1)!.url)
    expect(q.get('role')).toBe('merchant')
    expect(q.get('status')).toBe('banned')
  })

  it('UM-05 空结果 → 「没有匹配的用户」', async () => {
    seedAdmin()
    stubApi(userStubs({ [`GET ${USERS}`]: { status: 200, body: userPage([]) } }))
    renderApp({ route: '/users' })

    expect(await screen.findByTestId('usr-empty')).toHaveTextContent('没有匹配的用户')
  })

  it('UM-06 点行 → /users/{id}，并取详情', async () => {
    seedAdmin()
    const api = stubApi(userStubs())
    renderApp({ route: '/users' })
    await screen.findByTestId('usr-row-7')

    await userEvent.setup().click(screen.getByTestId('usr-row-7'))

    await waitFor(() => expect(currentPath()).toBe('/users/7'))
    expect(api.count('GET', `${USERS}/7`)).toBe(1)
  })

  it('UM-07 详情：资料 + 4 个统计数', async () => {
    seedAdmin()
    stubApi(userStubs())
    renderApp({ route: '/users/7' })

    const card = within(await screen.findByTestId('usr-profile'))
    expect(card.getByTestId('usr-account')).toHaveTextContent('shop0001')
    expect(card.getByTestId('usr-email')).toHaveTextContent('shop0001@example.com')
    expect(card.getByTestId('usr-role')).toHaveTextContent('商户')
    expect(card.getByTestId('usr-status-code')).toHaveTextContent('正常')
    expect(card.getByTestId('usr-shop')).toHaveTextContent('巷口咖啡')

    const stats = within(screen.getByTestId('usr-stats'))
    expect(stats.getByTestId('usr-stat-task')).toHaveTextContent('3')
    expect(stats.getByTestId('usr-stat-claim')).toHaveTextContent('5')
    expect(stats.getByTestId('usr-stat-job')).toHaveTextContent('8')
    expect(stats.getByTestId('usr-stat-points')).toHaveTextContent('1200')
  })

  it('UM-08 商户有档案区；客户没有（后端本来就不返回那个键）', async () => {
    seedAdmin()
    stubApi(userStubs())
    renderApp({ route: '/users/7' })
    expect(await screen.findByTestId('usr-merchant-profile')).toBeInTheDocument()

    // 两趟渲染必须真正拆开，否则第一趟的档案区还挂在 DOM 里，
    // 下面那句「客户没有」就会红在一个与实现无关的原因上
    cleanup()
    stubApi(
      userStubs({
        [`GET ${USERS}/8`]: {
          status: 200,
          body: userDetail({ role: 'customer', user: { id: 8 }, withProfile: false }),
        },
      }),
    )
    renderApp({ route: '/users/8' })
    await screen.findByTestId('usr-profile')
    // 不是显示一片空白——是整块不出现
    expect(screen.queryByTestId('usr-merchant-profile')).not.toBeInTheDocument()
  })

  it('UM-09 操作日志取 user 维度的那一页，按后端顺序渲染', async () => {
    seedAdmin()
    const api = stubApi(
      userStubs({
        [`GET ${LOGS}`]: {
          status: 200,
          body: actionLogPage([
            { id: 2, action: 'unban_user', detail: { before: 'banned', after: 'active' } },
            { id: 1, action: 'ban_user', detail: { reason: '刷单' } },
          ]),
        },
      }),
    )
    renderApp({ route: '/users/7' })

    await screen.findByTestId('usr-logs')
    const q = query(api.callsTo('GET', LOGS)[0].url)
    expect(q.get('target_type')).toBe('user')
    expect(q.get('target_id')).toBe('7')

    expect(screen.getAllByTestId(/^usr-log-\d+$/).map((row) => row.getAttribute('data-testid'))).toEqual([
      'usr-log-2',
      'usr-log-1',
    ])
    expect(screen.getByTestId('usr-log-1')).toHaveTextContent('刷单')
  })

  it('UM-10 从详情返回 → 列表的筛选与页码保留', async () => {
    seedAdmin()
    const api = stubApi(userStubs())
    renderApp({ route: '/users' })
    await screen.findByTestId('usr-row-7')

    const user = userEvent.setup()
    await user.type(screen.getByLabelText('关键词'), '咖啡')
    await user.click(screen.getByRole('button', { name: '搜索' }))
    await waitFor(() => expect(api.count('GET', USERS)).toBe(2))

    await user.click(screen.getByTestId('usr-row-7'))
    await waitFor(() => expect(currentPath()).toBe('/users/7'))

    await user.click(await screen.findByRole('button', { name: '返回' }))

    await waitFor(() => expect(currentPath()).toContain('/users'))
    expect(routeQuery().get('keyword')).toBe('咖啡')
    await waitFor(() => expect(query(api.callsTo('GET', USERS).at(-1)!.url).get('keyword')).toBe('咖啡'))
  })

  it('UM-11 封禁理由 < 5 字 → 前端先拦，一次请求都不发', async () => {
    seedAdmin()
    const api = stubApi(userStubs())
    renderApp({ route: '/users/7' })
    await screen.findByTestId('usr-profile')

    const user = userEvent.setup()
    await user.type(screen.getByLabelText('封禁理由'), '刷单')
    await user.click(screen.getByRole('button', { name: '封禁' }))

    expect(await screen.findByTestId('usr-error')).toHaveTextContent('5')
    expect(api.count('POST', `${USERS}/7/ban`)).toBe(0)
  })

  it('UM-12 封禁成功 → 就地变已封禁 + 按钮变解封，并重取详情', async () => {
    seedAdmin()
    const api = stubApi(
      userStubs({
        [`GET ${USERS}/7`]: [
          { status: 200, body: userDetail({ user: { id: 7, status: 'active' } }) },
          { status: 200, body: userDetail({ user: { id: 7, status: 'banned' } }) },
        ],
        [`POST ${USERS}/7/ban`]: { status: 200, body: banOk(7) },
      }),
    )
    renderApp({ route: '/users/7' })
    await screen.findByTestId('usr-profile')

    const user = userEvent.setup()
    // 理由必须**够 5 字**才走到后端——差一字的那一刀由 UM-11 守着
    await user.type(screen.getByLabelText('封禁理由'), '刷单多次违规')
    await user.click(screen.getByRole('button', { name: '封禁' }))

    await waitFor(() => expect(screen.getByTestId('usr-status-code')).toHaveTextContent('已封禁'))
    expect(screen.getByRole('button', { name: '解封' })).toBeInTheDocument()
    expect(api.count('GET', `${USERS}/7`)).toBe(2)
  })

  it('UM-13 409 已是 banned → 就地提示 + 重取详情刷状态', async () => {
    seedAdmin()
    const api = stubApi(
      userStubs({
        // 本地这一份是**过期的**（别处刚封过）：于是按钮还写着「封禁」，
        // 打过去才 409。这正是「刷新该行状态」要处理的那个场景。
        [`GET ${USERS}/7`]: [
          { status: 200, body: userDetail({ user: { id: 7, status: 'active' } }) },
          { status: 200, body: userDetail({ user: { id: 7, status: 'banned' } }) },
        ],
        [`POST ${USERS}/7/ban`]: { status: 409, body: { detail: '该账号已被封禁' } },
      }),
    )
    renderApp({ route: '/users/7' })
    await screen.findByTestId('usr-profile')

    const user = userEvent.setup()
    await user.type(screen.getByLabelText('封禁理由'), '刷单多次违规')
    await user.click(screen.getByRole('button', { name: '封禁' }))

    expect(await screen.findByTestId('usr-error')).toHaveTextContent('已被封禁')
    // 重取详情，把屏幕上那份过期状态换成真实的
    await waitFor(() => expect(screen.getByTestId('usr-status-code')).toHaveTextContent('已封禁'))
    expect(api.count('GET', `${USERS}/7`)).toBe(2)
  })

  it('UM-14 409 目标是 admin → 原样显示后端文案', async () => {
    seedAdmin()
    stubApi(
      userStubs({
        [`GET ${USERS}/9`]: { status: 200, body: userDetail({ role: 'admin', user: { id: 9 }, withProfile: false }) },
        [`POST ${USERS}/9/ban`]: { status: 409, body: { detail: '不能封禁管理员账号' } },
      }),
    )
    renderApp({ route: '/users/9' })
    await screen.findByTestId('usr-profile')

    const user = userEvent.setup()
    await user.type(screen.getByLabelText('封禁理由'), '测试一下账号')
    await user.click(screen.getByRole('button', { name: '封禁' }))

    // 泛泛的「操作失败」会让人去查一个不存在的问题
    expect(await screen.findByTestId('usr-error')).toHaveTextContent('不能封禁管理员账号')
  })

  it('UM-15 解封非 banned → 409 就地提示', async () => {
    seedAdmin()
    const api = stubApi(
      userStubs({
        // 同样是过期状态：本地显示已封禁（按钮是「解封」），后端其实已经放开了
        [`GET ${USERS}/7`]: { status: 200, body: userDetail({ user: { id: 7, status: 'banned' } }) },
        [`POST ${USERS}/7/unban`]: { status: 409, body: { detail: '该账号当前不是封禁状态' } },
      }),
    )
    renderApp({ route: '/users/7' })
    await screen.findByTestId('usr-profile')

    await userEvent.setup().click(screen.getByRole('button', { name: '解封' }))

    expect(await screen.findByTestId('usr-error')).toHaveTextContent('不是封禁状态')
    expect(api.count('POST', `${USERS}/7/unban`)).toBe(1)
  })

  it('UM-17 解封成功 → 状态回到正常，并重取详情', async () => {
    seedAdmin()
    const api = stubApi(
      userStubs({
        [`GET ${USERS}/7`]: [
          { status: 200, body: userDetail({ user: { id: 7, status: 'banned' } }) },
          { status: 200, body: userDetail({ user: { id: 7, status: 'active' } }) },
        ],
        [`POST ${USERS}/7/unban`]: { status: 200, body: unbanOk(7) },
      }),
    )
    renderApp({ route: '/users/7' })
    await screen.findByTestId('usr-profile')

    await userEvent.setup().click(screen.getByRole('button', { name: '解封' }))

    // 封禁有「成功」与「409」两条，解封也得有——只测冲突那条会把
    // 「解封根本没生效」放过去
    await waitFor(() => expect(screen.getByTestId('usr-status-code')).toHaveTextContent('正常'))
    expect(api.count('POST', `${USERS}/7/unban`)).toBe(1)
    expect(api.count('GET', `${USERS}/7`)).toBe(2)
  })

  it('UM-16 非法 id / 后端 404 → 「用户不存在」，不白屏', async () => {
    seedAdmin()
    const api = stubApi(userStubs())
    renderApp({ route: '/users/abc' })
    expect(await screen.findByTestId('usr-missing')).toHaveTextContent('用户不存在')
    // 非数字 id 压根不该发请求
    expect(api.count('GET', `${USERS}/abc`)).toBe(0)
    expect(api.count('GET', LOGS)).toBe(0)

    cleanup()
    stubApi(userStubs({ [`GET ${USERS}/999`]: { status: 404, body: { detail: '用户不存在' } } }))
    renderApp({ route: '/users/999' })
    expect(await screen.findByTestId('usr-missing')).toHaveTextContent('用户不存在')
  })
})

/* ========================================================================== */
/* EX · 异常处理                                                               */
/* ========================================================================== */

async function goExceptions() {
  seedAdmin()
  const api = stubApi(excStubs())
  renderApp({ route: '/exceptions' })
  await screen.findByTestId('exc-seg-content_review')
  return api
}

describe('EX · 异常处理', () => {
  it('EX-01 四段计数各一条 ?size=1 请求', async () => {
    const api = await goExceptions()

    for (const type of SEGMENTS) {
      expect(api.count('GET', excCountUrl(type))).toBe(1)
    }
  })

  it('EX-02 分段控件四段文案 + 计数', async () => {
    seedAdmin()
    stubApi(
      excStubs({
        [`GET ${excCountUrl('content_review')}`]: { status: 200, body: excCount(7) },
        [`GET ${excCountUrl('ocr_low_confidence')}`]: { status: 200, body: excCount(2) },
        [`GET ${excCountUrl('ocr_mismatch')}`]: { status: 200, body: excCount(3) },
        [`GET ${excCountUrl('appeal')}`]: { status: 200, body: excCount(5) },
      }),
    )
    renderApp({ route: '/exceptions' })

    await waitFor(() => expect(screen.getByTestId('exc-count-content_review')).toHaveTextContent('7'))
    expect(screen.getByTestId('exc-seg-content_review')).toHaveTextContent('内容待审')
    expect(screen.getByTestId('exc-count-ocr_low_confidence')).toHaveTextContent('2')
    expect(screen.getByTestId('exc-seg-ocr_low_confidence')).toHaveTextContent('截图低置信')
    expect(screen.getByTestId('exc-count-ocr_mismatch')).toHaveTextContent('3')
    expect(screen.getByTestId('exc-seg-ocr_mismatch')).toHaveTextContent('数据不符')
    expect(screen.getByTestId('exc-count-appeal')).toHaveTextContent('5')
    expect(screen.getByTestId('exc-seg-appeal')).toHaveTextContent('申诉')
  })

  it('EX-03 默认段「内容待审」列表请求 ?type=content_review&size=100', async () => {
    const api = await goExceptions()

    const calls = api.callsTo('GET', EXC).filter((call) => query(call.url).get('size') === '100')
    expect(calls).toHaveLength(1)
    expect(query(calls[0].url).get('type')).toBe('content_review')
    expect(screen.getByTestId('exc-seg-content_review')).toHaveAttribute('aria-pressed', 'true')
  })

  it('EX-04 内容段列：job id · 类型 · 任务 id · 用户 id · 时间；两个动作', async () => {
    await goExceptions()

    const row = within(await screen.findByTestId('exc-row-301'))
    expect(row.getByTestId('exc-id')).toHaveTextContent('301')
    expect(row.getByTestId('exc-kind')).toHaveTextContent('文案')
    expect(row.getByTestId('exc-task')).toHaveTextContent('12')
    expect(row.getByTestId('exc-user')).toHaveTextContent('55')
    expect(row.getByTestId('exc-time')).toHaveTextContent('2026-09-16 10:00')
    expect(row.getByTestId('exc-act-301-approve')).toHaveTextContent('放行')
    expect(row.getByTestId('exc-act-301-discard')).toHaveTextContent('作废')
  })

  it('EX-05 点「放行」→ POST content/{job_id}/resolve，体 {action:approve}', async () => {
    const api = await goExceptions()

    await userEvent.setup().click(await screen.findByTestId('exc-act-301-approve'))

    await waitFor(() => expect(api.count('POST', `${EXC}/content/301/resolve`)).toBe(1))
    expect(api.callsTo('POST', `${EXC}/content/301/resolve`)[0].body).toEqual({ action: 'approve' })
  })

  it('EX-06 处理成功 → 该行就地消失、计数 −1、列表不重取', async () => {
    seedAdmin()
    const api = stubApi(
      excStubs({
        [`GET ${excCountUrl('content_review')}`]: { status: 200, body: excCount(7) },
        [`POST ${EXC}/content/301/resolve`]: { status: 200, body: { job: { job_id: 301, status: 'ready' } } },
      }),
    )
    renderApp({ route: '/exceptions' })
    await waitFor(() => expect(screen.getByTestId('exc-count-content_review')).toHaveTextContent('7'))

    await userEvent.setup().click(screen.getByTestId('exc-act-301-approve'))

    await waitFor(() => expect(screen.queryByTestId('exc-row-301')).not.toBeInTheDocument())
    expect(screen.getByTestId('exc-count-content_review')).toHaveTextContent('6')
    // 队列：处理完就离开清单。「重取整个列表」在屏幕上看起来完全一样
    expect(api.callsTo('GET', EXC).filter((call) => query(call.url).get('size') === '100')).toHaveLength(1)
  })

  it('EX-07 切「截图低置信」→ 只重取该段列表，四段计数不重取', async () => {
    const api = await goExceptions()

    await userEvent.setup().click(screen.getByTestId('exc-seg-ocr_low_confidence'))

    await waitFor(() =>
      expect(api.count('GET', excListUrl('ocr_low_confidence'))).toBe(1),
    )
    for (const type of SEGMENTS) {
      expect(api.count('GET', excCountUrl(type))).toBe(1)
    }
  })

  it('EX-08 切回已取过计数的段 → 计数不闪 0（不重取计数）', async () => {
    seedAdmin()
    const api = stubApi(
      excStubs({
        [`GET ${excCountUrl('content_review')}`]: { status: 200, body: excCount(7) },
      }),
    )
    renderApp({ route: '/exceptions' })
    await waitFor(() => expect(screen.getByTestId('exc-count-content_review')).toHaveTextContent('7'))

    const user = userEvent.setup()
    await user.click(screen.getByTestId('exc-seg-appeal'))
    await user.click(screen.getByTestId('exc-seg-content_review'))

    expect(screen.getByTestId('exc-count-content_review')).toHaveTextContent('7')
    expect(api.count('GET', excCountUrl('content_review'))).toBe(1)
  })

  it('EX-09 409 已被别人处理 → 就地提示 + 重取该段（列表与计数各 +1）', async () => {
    seedAdmin()
    const api = stubApi(
      excStubs({
        [`GET ${excCountUrl('content_review')}`]: { status: 200, body: excCount(7) },
        [`POST ${EXC}/content/301/resolve`]: { status: 409, body: { detail: '当前状态 ready 不在待处理队列里' } },
      }),
    )
    renderApp({ route: '/exceptions' })
    await screen.findByTestId('exc-row-301')

    await userEvent.setup().click(screen.getByTestId('exc-act-301-approve'))

    expect(await screen.findByTestId('exc-error')).toHaveTextContent('不在待处理队列里')
    await waitFor(() =>
      expect(api.callsTo('GET', EXC).filter((call) => query(call.url).get('size') === '100')).toHaveLength(2),
    )
    expect(api.count('GET', excCountUrl('content_review'))).toBe(2)
  })

  it('EX-10 422 → 就地提示，不静默吞', async () => {
    seedAdmin()
    stubApi(
      excStubs({
        [`POST ${EXC}/content/301/resolve`]: { status: 422, body: { detail: "action 只能是 ('approve', 'discard')" } },
      }),
    )
    renderApp({ route: '/exceptions' })
    await screen.findByTestId('exc-row-301')

    await userEvent.setup().click(screen.getByTestId('exc-act-301-approve'))

    expect(await screen.findByTestId('exc-error')).toHaveTextContent('action 只能是')
  })

  it('EX-11 空队列 → 「这一队清空了」', async () => {
    seedAdmin()
    stubApi(excStubs({ [`GET ${excListUrl('content_review')}`]: { status: 200, body: excPage([]) } }))
    renderApp({ route: '/exceptions' })

    expect(await screen.findByTestId('exc-empty')).toHaveTextContent('这一队清空了')
  })

  it('EX-12 申诉：reason 全文不截断，且动作体是 admin_note（不是 note）', async () => {
    seedAdmin()
    const long = '申'.repeat(500)
    const api = stubApi(
      excStubs({
        [`GET ${excListUrl('appeal')}`]: {
          status: 200,
          body: excPage([appealExcItem({ appeal_id: 501, reason: long })]),
        },
        [`POST ${APPEALS}/501/decide`]: { status: 200, body: { appeal_id: 501, status: 'rejected' } },
      }),
    )
    renderApp({ route: '/exceptions' })
    await userEvent.setup().click(await screen.findByTestId('exc-seg-appeal'))

    const row = within(await screen.findByTestId('exc-row-501'))
    // 截断会丢掉判断依据——申诉的 reason 是用户写的原文
    expect(row.getByTestId('exc-reason').textContent).toContain(long)

    const user = userEvent.setup()
    await user.type(screen.getByTestId('exc-note-501'), '已核实')
    await user.click(row.getByTestId('exc-act-501-reject'))

    await waitFor(() => expect(api.count('POST', `${APPEALS}/501/decide`)).toBe(1))
    const body = api.callsTo('POST', `${APPEALS}/501/decide`)[0].body as Record<string, unknown>
    // `DecideIn(extra="forbid")`：传 note 会 422
    expect(body.admin_note).toBe('已核实')
    expect(body).not.toHaveProperty('note')
  })

  it('EX-13 缩略图加载失败 → 占位块 + image_url 文本，不留破图', async () => {
    seedAdmin()
    stubApi(
      excStubs({
        [`GET ${excListUrl('ocr_low_confidence')}`]: {
          status: 200,
          body: excPage([ocrExcItem({ ocr_id: 801, image_url: 'https://cdn.example.com/shot-1.png' })]),
        },
      }),
    )
    renderApp({ route: '/exceptions' })
    await userEvent.setup().click(await screen.findByTestId('exc-seg-ocr_low_confidence'))

    const thumb = await screen.findByTestId('exc-thumb-801')
    fireEvent.error(thumb)

    const fallback = within(await screen.findByTestId('exc-thumb-fallback-801'))
    expect(fallback.getByText('https://cdn.example.com/shot-1.png')).toBeInTheDocument()
  })

  it('EX-14 计数取自 ?size=1 的 total，不是当前页行数', async () => {
    seedAdmin()
    stubApi(
      excStubs({
        [`GET ${excCountUrl('content_review')}`]: { status: 200, body: excCount(7) },
        [`GET ${excListUrl('content_review')}`]: {
          status: 200,
          body: excPage([contentExcItem({ job_id: 301 })], { total: 7 }),
        },
      }),
    )
    renderApp({ route: '/exceptions' })

    // 清单只有 1 行，控件上却是 7——两个数字来自两条不同的请求
    await screen.findByTestId('exc-row-301')
    expect(screen.getByTestId('exc-count-content_review')).toHaveTextContent('7')
  })
})
