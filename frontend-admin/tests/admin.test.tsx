/**
 * F 组 · 后台反馈收件箱（AF-01 ~ AF-27）
 *
 * 三条最贵的断言：
 * - `AF-19` —— 摘要可以截、**详情不许截**。写成「详情里对内容做 slice」测试也会绿，
 *   用户看到的是「我写了 500 字，后台只显示 200 字」。
 * - `AF-14` —— 「未处理 N」是**全局待办数**，不跟筛选条走。改成用当前列表的 total
 *   测试也会绿，用户看到的是「一筛就以为没积压了」。
 * - `AF-22` —— 处理后**就地改这一行**，不整页刷新。断言「列表请求数不变」，
 *   因为「重取整个列表」在屏幕上看起来一模一样。
 *
 * 「计数」与「列表」打同一个路径、只差查询串，故 `stubApi` 按**完整 url** 匹配，
 * 用例也按 `?status=open&size=1` / `?status=open&size=100` 分别数请求。
 */

import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import {
  adminLoginOk,
  countPage,
  feedbackPage,
  merchantLoginOk,
  refreshOk,
  reopenOk,
  resolveOk,
} from './fixtures'
import { currentPath, renderApp } from './render-app'
import { readAuth, readRedirect, seedAdmin, seedForeignSession } from './seed-auth'
import { stubApi, type StubSpec } from './stub-api'
import { TOKENS } from './tokens'

const LIST = '/api/admin/feedback'
/** 默认筛选：`status=open`，参数顺序 role → status → category → size */
const LIST_DEFAULT = `${LIST}?status=open&size=100`
/** 「未处理 N」那条独立的小请求 */
const COUNT_OPEN = `${LIST}?status=open&size=1`
const LOGIN = '/api/auth/login'
const REFRESH = '/api/auth/refresh'
const LOGOUT = '/api/auth/logout'

/** 8001（商家/用户端）的键。后台**不许**把它认成登录态——见 AF-03 */
const MAIN_AUTH_KEY = 'zxs.auth'

function inbox(over: Record<string, StubSpec> = {}): Record<string, StubSpec> {
  return {
    [`GET ${LIST_DEFAULT}`]: { status: 200, body: feedbackPage([], {}) },
    [`GET ${COUNT_OPEN}`]: { status: 200, body: countPage(0) },
    ...over,
  }
}

async function signIn(account = 'admin', password = 'pw123456') {
  const user = userEvent.setup()
  await user.type(screen.getByLabelText('账号'), account)
  await user.type(screen.getByLabelText('密码'), password)
  await user.click(screen.getByRole('button', { name: '登录' }))
}

/** 从列表里取「产品请求」——把计数那条同路径的请求滤掉 */
function listCalls(api: { callsTo: (m: string, u: string) => { url: string }[] }) {
  return api.callsTo('GET', LIST).filter((c) => c.url !== COUNT_OPEN)
}

describe('AF · 准入', () => {
  it('AF-01 未登录打开 /feedback → 落 /login，且记下原目标', async () => {
    stubApi({})
    renderApp({ route: '/feedback' })

    await waitFor(() => expect(currentPath()).toBe('/login'))
    expect(readRedirect()).toBe('/feedback')
  })

  it('AF-02 后台键里塞的是商家令牌 → 也算没登录', async () => {
    seedForeignSession()
    stubApi({})
    renderApp({ route: '/feedback' })

    await waitFor(() => expect(currentPath()).toBe('/login'))
  })

  it('AF-03 8001 的键里有登录态 → 后台仍是未登录（两个键必须分开）', async () => {
    // 这里塞的**故意是 admin 令牌**，不是商家令牌。塞商家令牌的话，
    // 「键共用」与「键分开」两种实现都会把它拦在角色校验那一关，
    // 这条用例就对「键到底共不共用」不敏感了（反向验证实测过）。
    // 用 admin 令牌，能被拦住就只能是因为后台压根没读这个键。
    localStorage.setItem(
      MAIN_AUTH_KEY,
      JSON.stringify({
        access: 'seeded-admin-access',
        refresh: 'seeded-admin-refresh',
        role: 'admin',
        nickname: '运营小张',
      }),
    )
    stubApi({})
    renderApp({ route: '/feedback' })

    await waitFor(() => expect(currentPath()).toBe('/login'))
    // 后台键确实是空的——不是「读错了键所以恰好跳了登录页」
    expect(readAuth()).toBeNull()
  })

  it('AF-04 有 admin 登录态 → 直接进 /feedback，不经过 /login', async () => {
    seedAdmin()
    const api = stubApi(inbox())
    renderApp({ route: '/feedback' })

    expect(currentPath()).toBe('/feedback')
    expect(await screen.findByText('反馈收件箱')).toBeInTheDocument()
    expect(api.count('POST', LOGIN)).toBe(0)
  })
})

describe('AF · 登录页', () => {
  it('AF-05 admin 账号登录 → 写后台登录态并落 /feedback', async () => {
    const api = stubApi({ [`POST ${LOGIN}`]: { status: 200, body: adminLoginOk() }, ...inbox() })
    renderApp({ route: '/login' })

    await signIn()

    await waitFor(() => expect(currentPath()).toBe('/feedback'))
    expect(readAuth()?.role).toBe('admin')
    expect(readAuth()?.access).toBe('admin-access-token')
    // 登录后回跳的目标被消费掉，不会留在 sessionStorage 里骗下一次登录
    expect(readRedirect()).toBeNull()
    expect(api.count('POST', LOGIN)).toBe(1)
  })

  it('AF-06 商家账号在后台登录 → 提示且**不写入登录态**', async () => {
    stubApi({ [`POST ${LOGIN}`]: { status: 200, body: merchantLoginOk() } })
    renderApp({ route: '/login' })

    await signIn('shop0001')

    expect(await screen.findByRole('alert')).toHaveTextContent('该账号不是管理员账号')
    // 关键：令牌**没有**落进后台的键——否则商家的令牌会以「后台已登录」留在本地
    expect(readAuth()).toBeNull()
    expect(currentPath()).toBe('/login')
  })

  it('AF-07 登录回 401 → 显示「账号或密码错误」', async () => {
    stubApi({ [`POST ${LOGIN}`]: { status: 401, body: { detail: '未登录或令牌无效' } } })
    renderApp({ route: '/login' })

    await signIn('admin', 'wrong')

    // 401 用本地通用文案，**不**把后端那句「未登录或令牌无效」透给用户
    expect(await screen.findByRole('alert')).toHaveTextContent('账号或密码错误')
  })

  it('AF-08 登录回 403 带 detail → 显示后端那句话', async () => {
    stubApi({ [`POST ${LOGIN}`]: { status: 403, body: { detail: '账号已注销' } } })
    renderApp({ route: '/login' })

    await signIn()

    expect(await screen.findByRole('alert')).toHaveTextContent('账号已注销')
  })
})

describe('AF · 列表与筛选', () => {
  it('AF-09 首次进页面默认带 status=open 请求列表', async () => {
    seedAdmin()
    const api = stubApi(inbox())
    renderApp({ route: '/feedback' })

    await screen.findByText('反馈收件箱')

    await waitFor(() => expect(listCalls(api)).toHaveLength(1))
    expect(listCalls(api)[0].url).toBe(LIST_DEFAULT)
  })

  it('AF-10 按后端给的顺序渲染，前端不重排', async () => {
    seedAdmin()
    stubApi(
      inbox({
        [`GET ${LIST_DEFAULT}`]: {
          status: 200,
          body: feedbackPage([{ id: 3 }, { id: 1 }, { id: 2 }]),
        },
      }),
    )
    renderApp({ route: '/feedback' })

    await screen.findByTestId('fb-row-3')
    expect(
      screen.getAllByTestId(/^fb-row-\d+$/).map((row) => row.getAttribute('data-testid')),
    ).toEqual(['fb-row-3', 'fb-row-1', 'fb-row-2'])
  })

  it('AF-11 每行六件套：角色 · 类型 · 摘要 · 提交人 · 时间 · 状态', async () => {
    seedAdmin()
    stubApi(inbox({ [`GET ${LIST_DEFAULT}`]: { status: 200, body: feedbackPage([{ id: 1 }]) } }))
    renderApp({ route: '/feedback' })

    const row = within(await screen.findByTestId('fb-row-1'))
    expect(row.getByTestId('fb-role-1')).toHaveTextContent('商家')
    expect(row.getByTestId('fb-category-1')).toHaveTextContent('功能建议')
    expect(row.getByText('希望支持批量导出')).toBeInTheDocument()
    expect(row.getByTestId('fb-who-1')).toHaveTextContent('shop0001')
    expect(row.getByTestId('fb-who-1')).toHaveTextContent('巷口咖啡')
    // 固定时刻 2026-09-16T02:00Z = 北京 10:00——按北京时间显示，不跟运行机器时区走
    expect(row.getByTestId('fb-time-1')).toHaveTextContent('2026-09-16 10:00')
    expect(row.getByTestId('fb-status-1')).toHaveTextContent('未处理')
  })

  it('AF-12 角色徽章文案区分商家与用户', async () => {
    seedAdmin()
    stubApi(
      inbox({
        [`GET ${LIST_DEFAULT}`]: {
          status: 200,
          body: feedbackPage([
            { id: 1, role: 'merchant' },
            { id: 2, role: 'customer', account: 'user0001', nickname: '小林' },
          ]),
        },
      }),
    )
    renderApp({ route: '/feedback' })

    await screen.findByTestId('fb-row-1')
    expect(screen.getByTestId('fb-role-1')).toHaveTextContent('商家')
    expect(screen.getByTestId('fb-role-2')).toHaveTextContent('用户')
  })

  it('AF-13 「未处理 N」取全局 status=open 的 total', async () => {
    seedAdmin()
    stubApi(
      inbox({
        [`GET ${LIST_DEFAULT}`]: { status: 200, body: feedbackPage([{ id: 1 }], { total: 1 }) },
        [`GET ${COUNT_OPEN}`]: { status: 200, body: countPage(7) },
      }),
    )
    renderApp({ route: '/feedback' })

    // 列表只有 1 条，计数却是 7——两个数字来自两条不同的请求
    expect(await screen.findByTestId('fb-count')).toHaveTextContent('未处理 7')
  })

  it('AF-14 改「类型」后「未处理 N」不变（它是待办数，不是当前页条数）', async () => {
    seedAdmin()
    const api = stubApi(
      inbox({
        [`GET ${LIST_DEFAULT}`]: { status: 200, body: feedbackPage([{ id: 1 }], { total: 1 }) },
        [`GET ${COUNT_OPEN}`]: { status: 200, body: countPage(7) },
        [`GET ${LIST}?status=open&category=bug&size=100`]: {
          status: 200,
          body: feedbackPage([], { total: 0 }),
        },
      }),
    )
    renderApp({ route: '/feedback' })

    await screen.findByTestId('fb-count')
    await userEvent.setup().selectOptions(screen.getByLabelText('类型'), 'bug')

    await waitFor(() => expect(screen.getByTestId('fb-empty')).toBeInTheDocument())
    expect(screen.getByTestId('fb-count')).toHaveTextContent('未处理 7')
    // 计数请求没有跟着筛走动：全程只发了一次
    expect(api.count('GET', COUNT_OPEN)).toBe(1)
  })

  it('AF-15 改「类型」→ 重新请求且带 category（status 仍在）', async () => {
    seedAdmin()
    const api = stubApi(
      inbox({
        [`GET ${LIST}?status=open&category=bug&size=100`]: {
          status: 200,
          body: feedbackPage([{ id: 9, category: 'bug' }]),
        },
      }),
    )
    renderApp({ route: '/feedback' })

    await screen.findByTestId('fb-empty')
    await userEvent.setup().selectOptions(screen.getByLabelText('类型'), 'bug')

    await screen.findByTestId('fb-row-9')
    const last = listCalls(api).at(-1)
    expect(last?.url).toBe(`${LIST}?status=open&category=bug&size=100`)
  })

  it('AF-16 改「角色」→ 重新请求且带 role', async () => {
    seedAdmin()
    const api = stubApi(
      inbox({
        [`GET ${LIST}?role=merchant&status=open&size=100`]: {
          status: 200,
          body: feedbackPage([{ id: 5 }]),
        },
      }),
    )
    renderApp({ route: '/feedback' })

    await screen.findByTestId('fb-empty')
    await userEvent.setup().selectOptions(screen.getByLabelText('角色'), 'merchant')

    await screen.findByTestId('fb-row-5')
    expect(listCalls(api).at(-1)?.url).toBe(`${LIST}?role=merchant&status=open&size=100`)
  })

  it('AF-17 空列表 → 显示「暂无反馈」', async () => {
    seedAdmin()
    stubApi(inbox())
    renderApp({ route: '/feedback' })

    expect(await screen.findByTestId('fb-empty')).toHaveTextContent('暂无反馈')
  })
})

describe('AF · 详情', () => {
  it('AF-18 点行 → aria-expanded=true，出现完整内容与联系方式', async () => {
    seedAdmin()
    stubApi(
      inbox({
        [`GET ${LIST_DEFAULT}`]: {
          status: 200,
          body: feedbackPage([{ id: 1, content: '希望支持批量导出', contact: '13800000000' }]),
        },
      }),
    )
    renderApp({ route: '/feedback' })

    const opener = await screen.findByTestId('fb-open-1')
    expect(opener).toHaveAttribute('aria-expanded', 'false')
    await userEvent.setup().click(opener)

    expect(screen.getByTestId('fb-open-1')).toHaveAttribute('aria-expanded', 'true')
    const detail = screen.getByTestId('fb-detail-1')
    expect(detail).toHaveTextContent('希望支持批量导出')
    expect(detail).toHaveTextContent('13800000000')
  })

  it('AF-19 500 字反馈展开后一个字不丢', async () => {
    seedAdmin()
    const long = '反'.repeat(500)
    stubApi(inbox({ [`GET ${LIST_DEFAULT}`]: { status: 200, body: feedbackPage([{ id: 1, content: long }]) } }))
    renderApp({ route: '/feedback' })

    await userEvent.setup().click(await screen.findByTestId('fb-open-1'))

    // 摘要可以截（视觉上被省略号截掉），详情**不许**截
    expect(screen.getByTestId('fb-detail-1').textContent).toContain(long)
  })

  it('AF-20 再点一次 → 收起，详情从文档里消失', async () => {
    seedAdmin()
    stubApi(inbox({ [`GET ${LIST_DEFAULT}`]: { status: 200, body: feedbackPage([{ id: 1 }]) } }))
    renderApp({ route: '/feedback' })

    const opener = await screen.findByTestId('fb-open-1')
    const user = userEvent.setup()
    await user.click(opener)
    expect(screen.getByTestId('fb-detail-1')).toBeInTheDocument()

    await user.click(screen.getByTestId('fb-open-1'))
    expect(screen.queryByTestId('fb-detail-1')).not.toBeInTheDocument()
    expect(screen.getByTestId('fb-open-1')).toHaveAttribute('aria-expanded', 'false')
  })
})

describe('AF · 处理动作', () => {
  it('AF-21 未处理行的按钮是「标记已处理」，点它发 resolve', async () => {
    seedAdmin()
    const api = stubApi(
      inbox({
        [`GET ${LIST_DEFAULT}`]: { status: 200, body: feedbackPage([{ id: 1 }], { total: 1 }) },
        [`GET ${COUNT_OPEN}`]: { status: 200, body: countPage(1) },
        [`POST ${LIST}/1/resolve`]: { status: 200, body: resolveOk(1) },
      }),
    )
    renderApp({ route: '/feedback' })

    const action = await screen.findByTestId('fb-action-1')
    expect(action).toHaveTextContent('标记已处理')
    await userEvent.setup().click(action)

    await waitFor(() => expect(api.count('POST', `${LIST}/1/resolve`)).toBe(1))
  })

  it('AF-22 成功后该行就地变「已处理」，**且不重取列表**', async () => {
    seedAdmin()
    const api = stubApi(
      inbox({
        [`GET ${LIST_DEFAULT}`]: { status: 200, body: feedbackPage([{ id: 1 }], { total: 1 }) },
        [`GET ${COUNT_OPEN}`]: { status: 200, body: countPage(1) },
        [`POST ${LIST}/1/resolve`]: { status: 200, body: resolveOk(1) },
      }),
    )
    renderApp({ route: '/feedback' })

    await userEvent.setup().click(await screen.findByTestId('fb-action-1'))

    await waitFor(() => expect(screen.getByTestId('fb-status-1')).toHaveTextContent('已处理'))
    expect(screen.getByTestId('fb-action-1')).toHaveTextContent('撤销已处理')
    // 就地改行，不是整页刷新——「重取整个列表」在屏幕上看起来一模一样
    expect(listCalls(api)).toHaveLength(1)
  })

  it('AF-23 已处理行点「撤销已处理」→ reopen → 就地变回未处理', async () => {
    seedAdmin()
    const api = stubApi(
      inbox({
        [`GET ${LIST_DEFAULT}`]: {
          status: 200,
          body: feedbackPage(
            [{ id: 1, status: 'resolved', resolved_at: '2026-09-16T03:00:00Z', resolved_by: 9 }],
            { total: 1 },
          ),
        },
        [`POST ${LIST}/1/reopen`]: { status: 200, body: reopenOk(1) },
      }),
    )
    renderApp({ route: '/feedback' })

    const action = await screen.findByTestId('fb-action-1')
    expect(action).toHaveTextContent('撤销已处理')
    await userEvent.setup().click(action)

    await waitFor(() => expect(screen.getByTestId('fb-status-1')).toHaveTextContent('未处理'))
    expect(api.count('POST', `${LIST}/1/reopen`)).toBe(1)
  })

  it('AF-24 409 → 页面级提示 + 重新拉列表', async () => {
    seedAdmin()
    const api = stubApi(
      inbox({
        [`GET ${LIST_DEFAULT}`]: [
          { status: 200, body: feedbackPage([{ id: 1 }], { total: 1 }) },
          { status: 200, body: feedbackPage([], { total: 0 }) },
        ],
        [`GET ${COUNT_OPEN}`]: { status: 200, body: countPage(1) },
        [`POST ${LIST}/1/resolve`]: { status: 409, body: { detail: '该反馈已被处理' } },
      }),
    )
    renderApp({ route: '/feedback' })

    await userEvent.setup().click(await screen.findByTestId('fb-action-1'))

    expect(await screen.findByTestId('fb-notice')).toHaveTextContent('该反馈已被其他管理员处理')
    // 这一行在当前筛选下已经消失，逐行提示会跟着没，所以必须重拉
    await waitFor(() => expect(listCalls(api)).toHaveLength(2))
    expect(screen.getByTestId('fb-empty')).toBeInTheDocument()
  })

  it('AF-25 500 → 提示落在该行内，行仍在，整页不变成错误态', async () => {
    seedAdmin()
    stubApi(
      inbox({
        [`GET ${LIST_DEFAULT}`]: { status: 200, body: feedbackPage([{ id: 1 }], { total: 1 }) },
        [`GET ${COUNT_OPEN}`]: { status: 200, body: countPage(1) },
        [`POST ${LIST}/1/resolve`]: { status: 500, body: { detail: '服务器开小差了' } },
      }),
    )
    renderApp({ route: '/feedback' })

    await userEvent.setup().click(await screen.findByTestId('fb-action-1'))

    const row = within(await screen.findByTestId('fb-row-1'))
    expect(await row.findByRole('alert')).toHaveTextContent('服务器开小差了')
    expect(screen.queryByTestId('fb-notice')).not.toBeInTheDocument()
    expect(screen.queryByTestId('fb-load-error')).not.toBeInTheDocument()
  })
})

describe('AF · 令牌与顶栏', () => {
  it('AF-26 业务请求 401 → refresh 一次，成功后重放原请求', async () => {
    seedAdmin({ access: TOKENS.access, refresh: TOKENS.refresh })
    const api = stubApi(
      inbox({
        [`GET ${LIST_DEFAULT}`]: [
          { status: 401, body: { detail: '令牌过期' } },
          { status: 200, body: feedbackPage([{ id: 1 }], { total: 1 }) },
        ],
        [`POST ${REFRESH}`]: { status: 200, body: refreshOk() },
      }),
    )
    renderApp({ route: '/feedback' })

    expect(await screen.findByTestId('fb-row-1')).toBeInTheDocument()
    expect(api.count('POST', REFRESH)).toBe(1)
    // 重放：原路径出现 2 次。写成「刷新成功但整页重载」这里就只有 1 次
    expect(api.count('GET', LIST_DEFAULT)).toBe(2)
    expect(currentPath()).toBe('/feedback')
  })

  it('AF-27 顶栏有昵称与「管理员」徽章；退出 → logout、清空、落 /login', async () => {
    seedAdmin({ nickname: '运营小张' })
    const api = stubApi(inbox({ [`POST ${LOGOUT}`]: { status: 204 } }))
    renderApp({ route: '/feedback' })

    const bar = within(await screen.findByTestId('app-bar'))
    expect(bar.getByText('运营小张')).toBeInTheDocument()
    expect(bar.getByTestId('role-badge')).toHaveTextContent('管理员')

    await userEvent.setup().click(bar.getByRole('button', { name: '退出登录' }))

    await waitFor(() => expect(currentPath()).toBe('/login'))
    expect(api.count('POST', LOGOUT)).toBe(1)
    expect(readAuth()).toBeNull()
  })
})
