/**
 * SH / CT · 客户壳层与路由（拍 1）
 */

import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import {
  loginOk,
  meClaims,
  meClaimsList,
  claimItem,
  meCoupons,
  mePoints,
  merchantQuota,
  merchantReviews,
  merchantTasks,
  taskDetail,
} from './fixtures'
import { mockMatchMedia } from './match-media'
import { currentPath, renderApp } from './render-app'
import { seedAuth } from './seed-auth'
import { signIn } from './sign-in'
import { stubApi } from './stub-api'

const CLAIMS = '/api/me/claims'
const POINTS = '/api/me/points'
const COUPONS = '/api/me/coupons'

function customerHomeOk(over: { claims?: number; points?: number; coupons?: number } = {}) {
  return {
    [`GET ${CLAIMS}`]: { status: 200, body: meClaims({ total: over.claims ?? 2 }) },
    [`GET ${POINTS}`]: { status: 200, body: mePoints({ balance: over.points ?? 120 }) },
    [`GET ${COUPONS}`]: { status: 200, body: meCoupons({ total: over.coupons ?? 1 }) },
  }
}

describe('SH · 客户壳层', () => {
  it('SH-01 宽屏：创作台 + 侧栏，无下方入口', async () => {
    mockMatchMedia(true)
    seedAuth({ role: 'customer' })
    stubApi(customerHomeOk())
    renderApp({ route: '/customer' })
    expect(await screen.findByTestId('customer-composer')).toBeInTheDocument()
    expect(screen.getByTestId('customer-aside')).toBeInTheDocument()
    expect(screen.queryByTestId('customer-footer-nav')).toBeNull()
  })

  it('SH-02 窄屏：创作台 + 下方入口，无侧栏', async () => {
    mockMatchMedia(false)
    seedAuth({ role: 'customer' })
    stubApi(customerHomeOk())
    renderApp({ route: '/customer' })
    expect(await screen.findByTestId('customer-composer')).toBeInTheDocument()
    expect(screen.getByTestId('customer-footer-nav')).toBeInTheDocument()
    expect(screen.queryByTestId('customer-aside')).toBeNull()
  })

  it('SH-03 没有旧三卡标记', async () => {
    mockMatchMedia(false)
    seedAuth({ role: 'customer' })
    stubApi(customerHomeOk())
    renderApp({ route: '/customer' })
    await screen.findByTestId('customer-composer')
    expect(screen.queryByTestId('customer-stack')).toBeNull()
    expect(screen.queryByTestId('customer-card-points')).toBeNull()
    expect(screen.queryByTestId('customer-card-coupons')).toBeNull()
  })

  it('SH-04 入口数字来自三个端点', async () => {
    mockMatchMedia(false)
    seedAuth({ role: 'customer' })
    stubApi(customerHomeOk({ claims: 2, points: 120, coupons: 1 }))
    renderApp({ route: '/customer' })
    const claims = await screen.findByTestId('customer-entry-claims')
    const rewards = screen.getByTestId('customer-entry-rewards')
    expect(within(claims).getByText('2')).toBeInTheDocument()
    expect(within(rewards).getByText(/120 积分/)).toBeInTheDocument()
    expect(within(rewards).getByText(/1 张券/)).toBeInTheDocument()
  })

  it('SH-05 单接口 500 → 仅该入口数字为 —，仍可点', async () => {
    mockMatchMedia(false)
    seedAuth({ role: 'customer' })
    stubApi({
      [`GET ${CLAIMS}`]: { status: 500, body: { detail: '炸了' } },
      [`GET ${POINTS}`]: { status: 200, body: mePoints({ balance: 120 }) },
      [`GET ${COUPONS}`]: { status: 200, body: meCoupons({ total: 1 }) },
    })
    renderApp({ route: '/customer' })
    const claims = await screen.findByTestId('customer-entry-claims')
    expect(within(claims).getByText('—')).toBeInTheDocument()
    expect(within(screen.getByTestId('customer-entry-rewards')).getByText(/120/)).toBeInTheDocument()
    await userEvent.setup().click(claims)
    expect(currentPath()).toBe('/customer/claims')
  })

  it('SH-06 点入口跳转 claims / rewards', async () => {
    mockMatchMedia(false)
    seedAuth({ role: 'customer' })
    stubApi({
      ...customerHomeOk(),
      'GET /api/me/claims': { status: 200, body: { items: [], total: 0 } },
      'GET /api/me/rewards/by-merchant': { status: 200, body: { items: [], total: 0 } },
    })
    const view = renderApp({ route: '/customer' })
    await screen.findByTestId('customer-entry-claims')
    await userEvent.setup().click(screen.getByTestId('customer-entry-claims'))
    expect(currentPath()).toBe('/customer/claims')
    view.unmount()

    seedAuth({ role: 'customer' })
    stubApi({
      ...customerHomeOk(),
      'GET /api/me/rewards/by-merchant': { status: 200, body: { items: [], total: 0 } },
    })
    renderApp({ route: '/customer' })
    await userEvent.setup().click(await screen.findByTestId('customer-entry-rewards'))
    expect(currentPath()).toBe('/customer/rewards')
    expect(await screen.findByTestId('rewards-empty')).toHaveTextContent('还没有奖励')
  })

  it('SH-07 未领任务点开始创作 → 不发 jobs，引导找任务', async () => {
    mockMatchMedia(false)
    seedAuth({ role: 'customer' })
    const api = stubApi(customerHomeOk({ claims: 0 }))
    renderApp({ route: '/customer' })
    await screen.findByText('还没有数据')
    await userEvent.setup().click(screen.getByRole('button', { name: '开始创作' }))
    expect(screen.getByText(/先领一个任务/)).toBeInTheDocument()
    expect(api.count('POST', '/api/jobs')).toBe(0)
    expect(api.count('POST', '/api/uploads')).toBe(0)
  })

  it('SH-08 已有领取未选任务 → 打开选择弹层，不直接建 job', async () => {
    mockMatchMedia(false)
    seedAuth({ role: 'customer' })
    const api = stubApi({
      ...customerHomeOk({ claims: 2 }),
      'GET /api/me/claims': {
        status: 200,
        body: meClaimsList([
          claimItem({ claim: { id: 101 }, task: { id: 12, title: '周末探店' } }),
        ]),
      },
    })
    renderApp({ route: '/customer' })
    await screen.findByTestId('customer-entry-claims')
    await userEvent.setup().click(screen.getByRole('button', { name: '开始创作' }))
    expect(await screen.findByTestId('claim-picker')).toBeInTheDocument()
    expect(api.count('POST', '/api/jobs')).toBe(0)
  })

  it('SH-09 ?task=12 预选任务并恰好打一次详情', async () => {
    mockMatchMedia(false)
    seedAuth({ role: 'customer' })
    const api = stubApi({
      ...customerHomeOk(),
      'GET /api/tasks/12': {
        status: 200,
        body: taskDetail({
          task: { id: 12, title: '周末探店' },
        }),
      },
    })
    renderApp({ route: '/customer?task=12' })
    expect(await screen.findByTestId('composer-task')).toHaveTextContent('巷口咖啡')
    expect(screen.getByTestId('composer-task')).toHaveTextContent('周末探店')
    expect(api.count('GET', '/api/tasks/12')).toBe(1)
  })

  it('SH-10 客户首页零次 merchant API', async () => {
    mockMatchMedia(false)
    seedAuth({ role: 'customer' })
    const api = stubApi(customerHomeOk())
    renderApp({ route: '/customer' })
    await screen.findByTestId('customer-composer')
    expect(api.calls.every((c) => !c.path.startsWith('/api/merchant'))).toBe(true)
  })
})

describe('CT · 客户路由拍 1', () => {
  it('CT-01 未登录 /customer/tasks → login，登录后回跳', async () => {
    stubApi({
      'POST /api/auth/login': { status: 200, body: loginOk('customer') },
      ...customerHomeOk(),
      'GET /api/tasks': { status: 200, body: { items: [], total: 0, page: 1, size: 20 } },
    })
    renderApp({ route: '/customer/tasks' })
    expect(currentPath()).toBe('/login')
    await signIn('customer')
    await waitFor(() => expect(currentPath()).toBe('/customer/tasks'))
  })

  it('CT-02 商户令牌访问 /customer/claims → /merchant', () => {
    seedAuth({ role: 'merchant' })
    stubApi({
      'GET /api/merchant/tasks': { status: 200, body: merchantTasks({ total: 0 }) },
      'GET /api/merchant/reviews': { status: 200, body: merchantReviews({ count: 0 }) },
      'GET /api/merchant/quota': { status: 200, body: merchantQuota({ balance: 0 }) },
    })
    renderApp({ route: '/customer/claims' })
    expect(currentPath()).toBe('/merchant')
  })

  it('CT-03 客户令牌停在 tasks / claims / rewards', () => {
    seedAuth({ role: 'customer' })
    stubApi({
      ...customerHomeOk(),
      'GET /api/tasks': { status: 200, body: { items: [], total: 0, page: 1, size: 20 } },
      'GET /api/me/claims': { status: 200, body: { items: [], total: 0 } },
      'GET /api/me/rewards/by-merchant': { status: 200, body: { items: [], total: 0 } },
    })
    for (const route of ['/customer/tasks', '/customer/claims', '/customer/rewards']) {
      const view = renderApp({ route })
      expect(currentPath()).toBe(route)
      view.unmount()
      seedAuth({ role: 'customer' })
    }
  })

  it('CT-04 客户访问 /merchant/tasks → /customer', () => {
    seedAuth({ role: 'customer' })
    stubApi(customerHomeOk())
    renderApp({ route: '/merchant/tasks' })
    expect(currentPath()).toBe('/customer')
  })

  it('CT-05 顶栏在客户页都在', async () => {
    mockMatchMedia(false)
    seedAuth({ role: 'customer' })
    stubApi({
      ...customerHomeOk(),
      'GET /api/tasks': { status: 200, body: { items: [], total: 0, page: 1, size: 20 } },
      'GET /api/me/claims': { status: 200, body: { items: [], total: 0 } },
      'GET /api/me/rewards/by-merchant': { status: 200, body: { items: [], total: 0 } },
    })
    for (const route of ['/customer', '/customer/tasks', '/customer/claims']) {
      const view = renderApp({ route })
      expect(await screen.findByTestId('app-bar')).toBeInTheDocument()
      view.unmount()
      seedAuth({ role: 'customer' })
    }
  })

  it('CT-06 rewards 空态不白屏', async () => {
    seedAuth({ role: 'customer' })
    stubApi({
      'GET /api/me/rewards/by-merchant': { status: 200, body: { items: [], total: 0 } },
    })
    renderApp({ route: '/customer/rewards' })
    expect(await screen.findByTestId('rewards-empty')).toHaveTextContent('还没有奖励')
    expect(document.body.textContent).not.toBe('')
  })
})
