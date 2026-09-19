/**
 * C 组 · 令牌（TK-01 ~ TK-08）
 *
 * 最贵的一条是 TK-02 里的「**重放原请求**」：写成「刷新成功但整页重载」，
 * 测试会绿，用户看到的是闪一下、数据从头再来。所以要断言原路径**出现 2 次**，
 * 而不是只断言「没跳 /login」。
 */

import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import { loginOk, merchantQuota, merchantReviews, merchantTasks, refreshOk } from './fixtures'
import { currentPath, renderApp } from './render-app'
import { readAuth, seedAuth } from './seed-auth'
import { ROTATED, TOKENS } from './tokens'
import { stubApi } from './stub-api'

const TASKS = '/api/merchant/tasks'
const REVIEWS = '/api/merchant/reviews'
const QUOTA = '/api/merchant/quota'
const REFRESH = '/api/auth/refresh'
const LOGOUT = '/api/auth/logout'

function merchantHomeStubs() {
  return {
    [`GET ${REVIEWS}`]: { status: 200, body: merchantReviews({ count: 1 }) },
    [`GET ${QUOTA}`]: { status: 200, body: merchantQuota({ balance: 7 }) },
  }
}

describe('TK · 令牌', () => {
  it('TK-01 刷新页面后登录态仍在：直接渲染首页，不经过 /login', () => {
    seedAuth({ role: 'merchant', access: TOKENS.access, refresh: TOKENS.refresh })
    stubApi({
      [`GET ${TASKS}`]: { status: 200, body: merchantTasks({ total: 3 }) },
      ...merchantHomeStubs(),
    })
    renderApp({ route: '/' })
    expect(currentPath()).toBe('/merchant')
  })

  it('TK-02 首个业务请求 401 → refresh 一次，成功后重放原请求，页面不跳', async () => {
    seedAuth({ role: 'merchant', access: TOKENS.access, refresh: TOKENS.refresh })
    const api = stubApi({
      [`GET ${TASKS}`]: [
        { status: 401, body: { detail: '令牌过期' } },
        { status: 200, body: merchantTasks({ total: 3 }) },
      ],
      ...merchantHomeStubs(),
      [`POST ${REFRESH}`]: { status: 200, body: refreshOk() },
    })

    renderApp({ route: '/merchant' })

    // 先等数据落下来（刷新 → 重放 → 渲染整条链走完），再数请求
    expect(await screen.findByText('3')).toBeInTheDocument()
    expect(api.count('POST', REFRESH)).toBe(1)
    // 重放：原路径出现 2 次
    expect(api.count('GET', TASKS)).toBe(2)
    expect(currentPath()).toBe('/merchant')
  })

  it('TK-03 refresh 也失败 → 清空本地并落到 /login', async () => {
    seedAuth({ role: 'merchant', access: TOKENS.access, refresh: TOKENS.refresh })
    stubApi({
      [`GET ${TASKS}`]: { status: 401, body: { detail: '令牌过期' } },
      ...merchantHomeStubs(),
      [`POST ${REFRESH}`]: { status: 401, body: { detail: '刷新令牌无效' } },
    })

    renderApp({ route: '/merchant' })

    await waitFor(() => expect(currentPath()).toBe('/login'))
    expect(readAuth()).toBeNull()
  })

  it('TK-04 refresh 成功后新令牌写回本地，重放带的是新 access_token', async () => {
    seedAuth({ role: 'merchant', access: TOKENS.access, refresh: TOKENS.refresh })
    const api = stubApi({
      [`GET ${TASKS}`]: [
        { status: 401, body: { detail: '令牌过期' } },
        { status: 200, body: merchantTasks({ total: 4 }) },
      ],
      ...merchantHomeStubs(),
      [`POST ${REFRESH}`]: { status: 200, body: refreshOk() },
    })

    renderApp({ route: '/merchant' })
    await screen.findByText('4')

    expect(readAuth()?.access).toBe(ROTATED.access)
    const replayed = api.callsTo('GET', TASKS)[1]
    expect(replayed.headers.authorization).toBe(`Bearer ${ROTATED.access}`)
  })

  it('TK-05 业务请求带 Authorization: Bearer <access_token>', () => {
    seedAuth({ role: 'merchant', access: TOKENS.access, refresh: TOKENS.refresh })
    const api = stubApi({
      [`GET ${TASKS}`]: { status: 200, body: merchantTasks({ total: 0 }) },
      ...merchantHomeStubs(),
    })
    renderApp({ route: '/merchant' })

    expect(api.callsTo('GET', TASKS)[0]?.headers.authorization).toBe(
      `Bearer ${TOKENS.access}`,
    )
  })

  it('TK-06 退出登录 → 调 logout、清空本地、落到 /login', async () => {
    seedAuth({ role: 'merchant', access: TOKENS.access, refresh: TOKENS.refresh })
    const api = stubApi({
      [`GET ${TASKS}`]: { status: 200, body: merchantTasks({ total: 0 }) },
      ...merchantHomeStubs(),
      [`POST ${LOGOUT}`]: { status: 204 },
    })
    renderApp({ route: '/merchant' })

    await userEvent.setup().click(screen.getByRole('button', { name: '退出登录' }))

    await waitFor(() => expect(currentPath()).toBe('/login'))
    expect(api.count('POST', LOGOUT)).toBe(1)
    expect(readAuth()).toBeNull()
  })

  it('TK-07 logout 接口回 500 → 照样清空本地并跳 /login', async () => {
    seedAuth({ role: 'merchant', access: TOKENS.access, refresh: TOKENS.refresh })
    stubApi({
      [`GET ${TASKS}`]: { status: 200, body: merchantTasks({ total: 0 }) },
      ...merchantHomeStubs(),
      [`POST ${LOGOUT}`]: { status: 500, body: { detail: '炸了' } },
    })
    renderApp({ route: '/merchant' })

    await userEvent.setup().click(screen.getByRole('button', { name: '退出登录' }))

    await waitFor(() => expect(currentPath()).toBe('/login'))
    expect(readAuth()).toBeNull()
  })

  it('TK-08 退出后再访问 /merchant → 被拦回 /login', async () => {
    seedAuth({ role: 'merchant', access: TOKENS.access, refresh: TOKENS.refresh })
    stubApi({
      [`GET ${TASKS}`]: { status: 200, body: merchantTasks({ total: 0 }) },
      ...merchantHomeStubs(),
      [`POST ${LOGOUT}`]: { status: 204 },
    })
    const first = renderApp({ route: '/merchant' })
    await userEvent.setup().click(screen.getByRole('button', { name: '退出登录' }))
    await waitFor(() => expect(currentPath()).toBe('/login'))
    first.unmount()

    renderApp({ route: '/merchant' })
    expect(currentPath()).toBe('/login')
  })
})

describe('TK · 登录端点不参与刷新重试', () => {
  it('登录返回 401 不会去调 refresh（本地本来就没有令牌）', async () => {
    const api = stubApi({
      'POST /api/auth/login': { status: 401, body: { detail: '密码错误' } },
      [`POST ${REFRESH}`]: { status: 200, body: loginOk('merchant') },
    })
    renderApp({ route: '/login' })

    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: '商家登录' }))
    await user.type(screen.getByLabelText('账号'), 'shop0001')
    await user.type(screen.getByLabelText('密码'), 'pw123456')
    await user.click(screen.getByRole('button', { name: '登录' }))

    expect(api.count('POST', REFRESH)).toBe(0)
  })
})
