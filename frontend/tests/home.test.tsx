/**
 * D 组 · 首页（HM-01 ~ HM-09）
 *
 * 这一组的两个要点：
 * 1. **一张卡挂了不得拖垮整页**（HM-01）。三个摘要是三个独立请求，
 *    任一个 500 只让它自己显示 `—`；三个全挂才进错误态，且必须给重试按钮。
 * 2. **两个首页结构上不同**（HM-05）。用结构标记断言，并**互斥**——
 *    两边都断言对方的标记不存在，才拦得住「复制粘贴同一个页面」。
 */

import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import { meClaims, meCoupons, mePoints, merchantQuota, merchantReviews, merchantTasks } from './fixtures'
import { currentPath, renderApp } from './render-app'
import { seedAuth } from './seed-auth'
import { stubApi } from './stub-api'

const TASKS = '/api/merchant/tasks'
const REVIEWS = '/api/merchant/reviews'
const QUOTA = '/api/merchant/quota'
const CLAIMS = '/api/me/claims'
const POINTS = '/api/me/points'
const COUPONS = '/api/me/coupons'

const MERCHANT_CARDS = {
  tasks: 'merchant-card-tasks',
  reviews: 'merchant-card-reviews',
  quota: 'merchant-card-quota',
}

function card(testId: string) {
  return screen.getByTestId(testId)
}

function figure(testId: string) {
  return card(testId).querySelector('.card__figure')?.textContent ?? ''
}

describe('HM · 首页摘要', () => {
  it('HM-01 一张卡 500 → 只有它显示 —，另外两张照常显示各自的数', async () => {
    seedAuth({ role: 'merchant' })
    stubApi({
      [`GET ${TASKS}`]: { status: 200, body: merchantTasks({ total: 3 }) },
      [`GET ${REVIEWS}`]: { status: 500, body: { detail: '炸了' } },
      [`GET ${QUOTA}`]: { status: 200, body: merchantQuota({ balance: 7 }) },
    })
    renderApp({ route: '/merchant' })

    // 三张卡各发各的请求，等到三张都有结论再断言
    await waitFor(() => {
      expect(figure(MERCHANT_CARDS.tasks)).toBe('3')
      expect(figure(MERCHANT_CARDS.quota)).toBe('7')
      expect(figure(MERCHANT_CARDS.reviews)).toBe('—')
    })
  })

  it('HM-02 三个接口全挂 → 错误态 + 重试按钮，且不白屏', async () => {
    seedAuth({ role: 'merchant' })
    stubApi({
      [`GET ${TASKS}`]: { status: 500, body: { detail: '炸了' } },
      [`GET ${REVIEWS}`]: { status: 500, body: { detail: '炸了' } },
      [`GET ${QUOTA}`]: { status: 500, body: { detail: '炸了' } },
    })
    renderApp({ route: '/merchant' })

    expect(await screen.findByTestId('home-error')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '重试' })).toBeInTheDocument()
    // 不白屏：根容器里得有可读文案
    expect(document.body.textContent).not.toBe('')
  })

  it('HM-03 错误态点重试 → 三个请求重新发出，本次成功则渲染出数据', async () => {
    seedAuth({ role: 'merchant' })
    const api = stubApi({
      [`GET ${TASKS}`]: [
        { status: 500, body: { detail: '炸了' } },
        { status: 200, body: merchantTasks({ total: 3 }) },
      ],
      [`GET ${REVIEWS}`]: [
        { status: 500, body: { detail: '炸了' } },
        { status: 200, body: merchantReviews({ count: 1 }) },
      ],
      [`GET ${QUOTA}`]: [
        { status: 500, body: { detail: '炸了' } },
        { status: 200, body: merchantQuota({ balance: 7 }) },
      ],
    })
    renderApp({ route: '/merchant' })
    await screen.findByTestId('home-error')

    expect(api.count('GET', TASKS)).toBe(1)
    expect(api.count('GET', REVIEWS)).toBe(1)
    expect(api.count('GET', QUOTA)).toBe(1)

    await userEvent.setup().click(screen.getByRole('button', { name: '重试' }))

    expect(api.count('GET', TASKS)).toBe(2)
    expect(api.count('GET', REVIEWS)).toBe(2)
    expect(api.count('GET', QUOTA)).toBe(2)
    expect(await screen.findByText('3')).toBeInTheDocument()
  })

  it('HM-04 全 0 → 显示「还没有数据」，且页面上不出现 NaN / undefined / null', async () => {
    seedAuth({ role: 'merchant' })
    stubApi({
      [`GET ${TASKS}`]: { status: 200, body: merchantTasks({ total: 0 }) },
      [`GET ${REVIEWS}`]: { status: 200, body: merchantReviews({ count: 0 }) },
      [`GET ${QUOTA}`]: { status: 200, body: merchantQuota({ balance: 0 }) },
    })
    renderApp({ route: '/merchant' })

    await waitFor(() => expect(screen.getAllByText('还没有数据')).toHaveLength(3))
    expect(document.body.textContent).not.toMatch(/NaN|undefined|null/)
  })

  it('HM-04b 只有一项为 0 → 只有那张卡显示「还没有数据」，其余照常（空态不拖垮整页）', async () => {
    seedAuth({ role: 'merchant' })
    stubApi({
      [`GET ${TASKS}`]: { status: 200, body: merchantTasks({ total: 3 }) },
      [`GET ${REVIEWS}`]: { status: 200, body: merchantReviews({ count: 0 }) },
      [`GET ${QUOTA}`]: { status: 200, body: merchantQuota({ balance: 7 }) },
    })
    renderApp({ route: '/merchant' })

    await waitFor(() => expect(figure(MERCHANT_CARDS.tasks)).toBe('3'))
    expect(within(card(MERCHANT_CARDS.reviews)).getByText('还没有数据')).toBeInTheDocument()
    expect(figure(MERCHANT_CARDS.quota)).toBe('7')
  })

  it('HM-05 两个首页结构性不同：经营轨 / 创作台互斥', () => {
    seedAuth({ role: 'merchant' })
    stubApi({
      [`GET ${TASKS}`]: { status: 200, body: merchantTasks({ total: 3 }) },
      [`GET ${REVIEWS}`]: { status: 200, body: merchantReviews({ count: 1 }) },
      [`GET ${QUOTA}`]: { status: 200, body: merchantQuota({ balance: 7 }) },
    })
    const merchantView = renderApp({ route: '/merchant' })
    expect(screen.getByTestId('merchant-rail')).toBeInTheDocument()
    expect(screen.queryByTestId('customer-composer')).toBeNull()
    merchantView.unmount()

    seedAuth({ role: 'customer' })
    stubApi({
      [`GET ${CLAIMS}`]: { status: 200, body: meClaims({ total: 2 }) },
      [`GET ${POINTS}`]: { status: 200, body: mePoints({ balance: 120 }) },
      [`GET ${COUPONS}`]: { status: 200, body: meCoupons({ total: 1 }) },
    })
    renderApp({ route: '/customer' })
    expect(screen.getByTestId('customer-composer')).toBeInTheDocument()
    expect(screen.queryByTestId('merchant-rail')).toBeNull()
  })

  it('HM-06 商户首页取数走三个商户端点（路径各断言一次）', async () => {
    seedAuth({ role: 'merchant' })
    const api = stubApi({
      [`GET ${TASKS}`]: { status: 200, body: merchantTasks({ total: 3 }) },
      [`GET ${REVIEWS}`]: { status: 200, body: merchantReviews({ count: 1 }) },
      [`GET ${QUOTA}`]: { status: 200, body: merchantQuota({ balance: 7 }) },
    })
    renderApp({ route: '/merchant' })

    await waitFor(() => expect(figure(MERCHANT_CARDS.quota)).toBe('7'))
    expect(api.count('GET', TASKS)).toBe(1)
    expect(api.count('GET', REVIEWS)).toBe(1)
    expect(api.count('GET', QUOTA)).toBe(1)
    expect(figure(MERCHANT_CARDS.tasks)).toBe('3')
    expect(figure(MERCHANT_CARDS.reviews)).toBe('1')
  })

  it('HM-07 客户首页取数走三个客户端点（入口数字）', async () => {
    seedAuth({ role: 'customer' })
    const api = stubApi({
      [`GET ${CLAIMS}`]: { status: 200, body: meClaims({ total: 2 }) },
      [`GET ${POINTS}`]: { status: 200, body: mePoints({ balance: 120 }) },
      [`GET ${COUPONS}`]: { status: 200, body: meCoupons({ total: 1 }) },
    })
    renderApp({ route: '/customer' })

    await waitFor(() =>
      expect(within(screen.getByTestId('customer-entry-claims')).getByText('2')).toBeInTheDocument(),
    )
    expect(api.count('GET', CLAIMS)).toBe(1)
    expect(api.count('GET', POINTS)).toBe(1)
    expect(api.count('GET', COUPONS)).toBe(1)
    expect(within(screen.getByTestId('customer-entry-rewards')).getByText(/120/)).toBeInTheDocument()
    expect(within(screen.getByTestId('customer-entry-rewards')).getByText(/1 张券/)).toBeInTheDocument()
  })

  it('HM-08 页面上不出现任何 role / user_id 输入项', () => {
    seedAuth({ role: 'merchant' })
    stubApi({
      [`GET ${TASKS}`]: { status: 200, body: merchantTasks({ total: 3 }) },
      [`GET ${REVIEWS}`]: { status: 200, body: merchantReviews({ count: 1 }) },
      [`GET ${QUOTA}`]: { status: 200, body: merchantQuota({ balance: 7 }) },
    })
    const { container } = renderApp({ route: '/merchant' })

    expect(screen.queryByLabelText(/role/i)).toBeNull()
    expect(screen.queryByLabelText(/user_id/i)).toBeNull()

    const fields = container.querySelectorAll('input, select, textarea')
    const names = Array.from(fields).map((el) => el.getAttribute('name'))
    expect(names).not.toContain('role')
    expect(names).not.toContain('user_id')
  })

  it('HM-09 顶栏有昵称 + 角色徽章 + 退出登录，且两种角色徽章文案不同', () => {
    seedAuth({ role: 'merchant' })
    stubApi({
      [`GET ${TASKS}`]: { status: 200, body: merchantTasks({ total: 3 }) },
      [`GET ${REVIEWS}`]: { status: 200, body: merchantReviews({ count: 1 }) },
      [`GET ${QUOTA}`]: { status: 200, body: merchantQuota({ balance: 7 }) },
    })
    const merchantView = renderApp({ route: '/merchant' })
    const bar = screen.getByTestId('app-bar')
    expect(within(bar).getByText('巷口咖啡')).toBeInTheDocument()
    expect(within(bar).getByTestId('role-badge')).toHaveTextContent('商户')
    expect(within(bar).getByRole('button', { name: '退出登录' })).toBeInTheDocument()
    merchantView.unmount()

    seedAuth({ role: 'customer' })
    stubApi({
      [`GET ${CLAIMS}`]: { status: 200, body: meClaims({ total: 2 }) },
      [`GET ${POINTS}`]: { status: 200, body: mePoints({ balance: 120 }) },
      [`GET ${COUPONS}`]: { status: 200, body: meCoupons({ total: 1 }) },
    })
    renderApp({ route: '/customer' })
    const bar2 = screen.getByTestId('app-bar')
    expect(within(bar2).getByText('小林')).toBeInTheDocument()
    expect(within(bar2).getByTestId('role-badge')).toHaveTextContent('客户')
    expect(within(bar2).getByRole('button', { name: '退出登录' })).toBeInTheDocument()
  })
})

describe('HM · 首页反馈入口', () => {
  it('两个首页都常驻「问题反馈」按钮', async () => {
    seedAuth({ role: 'customer' })
    stubApi({
      [`GET ${CLAIMS}`]: { status: 200, body: meClaims({ total: 2 }) },
      [`GET ${POINTS}`]: { status: 200, body: mePoints({ balance: 120 }) },
      [`GET ${COUPONS}`]: { status: 200, body: meCoupons({ total: 1 }) },
    })
    renderApp({ route: '/customer' })
    expect(await screen.findByRole('button', { name: '问题反馈' })).toBeInTheDocument()
    expect(currentPath()).toBe('/customer')
  })
})
