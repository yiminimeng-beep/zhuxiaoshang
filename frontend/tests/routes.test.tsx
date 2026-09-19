/**
 * A 组 · 路由与准入（RT-01 ~ RT-12）
 *
 * 这一组盯的是「谁能看见哪一页」。最容易漏的一条是 RT-07/08：
 * 登录成功后**回跳到原目标**，而不是一律跳首页——写死成跳首页时，
 * 用户体感是「我点了客户页，登完却到了商户页」。
 */

import { screen, waitFor } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { loginOk } from './fixtures'
import { currentPath, renderApp } from './render-app'
import { readRedirect, seedAuth, seedRedirect } from './seed-auth'
import { signIn } from './sign-in'
import { stubApi } from './stub-api'

describe('RT · 路由与准入', () => {
  it('RT-01 未登录访问 /merchant → 重定向到 /login', () => {
    stubApi()
    renderApp({ route: '/merchant' })
    expect(currentPath()).toBe('/login')
  })

  it('RT-02 未登录访问 /customer → 重定向到 /login', () => {
    stubApi()
    renderApp({ route: '/customer' })
    expect(currentPath()).toBe('/login')
  })

  it('RT-03 未登录访问 / → 重定向到 /login', () => {
    stubApi()
    renderApp({ route: '/' })
    expect(currentPath()).toBe('/login')
  })

  it('RT-04 商户令牌访问 /customer → 落到 /merchant，且不报错', () => {
    seedAuth({ role: 'merchant' })
    stubApi({
      'GET /api/merchant/tasks': { status: 200, body: { items: [], total: 0 } },
      'GET /api/merchant/reviews': { status: 200, body: { items: [] } },
      'GET /api/merchant/quota': { status: 200, body: { balance: 0 } },
    })
    renderApp({ route: '/customer' })
    expect(currentPath()).toBe('/merchant')
    // 静默跳，不是「报错后再跳」
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it('RT-05 客户令牌访问 /merchant → 落到 /customer，且不报错', () => {
    seedAuth({ role: 'customer' })
    stubApi({
      'GET /api/me/claims': { status: 200, body: { items: [], total: 0 } },
      'GET /api/me/points': { status: 200, body: { balance: 0 } },
      'GET /api/me/coupons': { status: 200, body: { items: [], total: 0 } },
    })
    renderApp({ route: '/merchant' })
    expect(currentPath()).toBe('/customer')
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it('RT-06 已登录访问 /login → 直接落到自己的首页', () => {
    seedAuth({ role: 'merchant' })
    stubApi({
      'GET /api/merchant/tasks': { status: 200, body: { items: [], total: 0 } },
      'GET /api/merchant/reviews': { status: 200, body: { items: [] } },
      'GET /api/merchant/quota': { status: 200, body: { balance: 0 } },
    })
    renderApp({ route: '/login' })
    expect(currentPath()).toBe('/merchant')
  })

  it('RT-07 未登录访问 /customer 被拦后登录成功 → 回跳 /customer', async () => {
    stubApi({
      'POST /api/auth/login': { status: 200, body: loginOk('customer') },
      'GET /api/me/claims': { status: 200, body: { items: [], total: 0 } },
      'GET /api/me/points': { status: 200, body: { balance: 0 } },
      'GET /api/me/coupons': { status: 200, body: { items: [], total: 0 } },
    })
    renderApp({ route: '/customer' })
    expect(currentPath()).toBe('/login')
    expect(readRedirect()).toBe('/customer')

    await signIn('customer', { account: 'user0001' })
    await waitFor(() => expect(currentPath()).toBe('/customer'))
  })

  it('RT-08 未登录访问 /merchant 被拦后登录成功 → 回跳 /merchant', async () => {
    stubApi({
      'POST /api/auth/login': { status: 200, body: loginOk('merchant') },
      'GET /api/merchant/tasks': { status: 200, body: { items: [], total: 0 } },
      'GET /api/merchant/reviews': { status: 200, body: { items: [] } },
      'GET /api/merchant/quota': { status: 200, body: { balance: 0 } },
    })
    renderApp({ route: '/merchant' })
    expect(readRedirect()).toBe('/merchant')

    await signIn('merchant')
    expect(currentPath()).toBe('/merchant')
  })

  it('RT-09 已登录 + 已记住的目标 → 回跳目标，不被「已登录就跳首页」抢走', () => {
    seedRedirect('/customer')
    seedAuth({ role: 'customer' })
    stubApi({
      'GET /api/me/claims': { status: 200, body: { items: [], total: 0 } },
      'GET /api/me/points': { status: 200, body: { balance: 0 } },
      'GET /api/me/coupons': { status: 200, body: { items: [], total: 0 } },
    })
    renderApp({ route: '/login' })
    expect(currentPath()).toBe('/customer')
  })

  it('RT-10 未知路径 /nope → 渲染 404 页', () => {
    seedAuth({ role: 'merchant' })
    stubApi({
      'GET /api/merchant/tasks': { status: 200, body: { items: [], total: 0 } },
      'GET /api/merchant/reviews': { status: 200, body: { items: [] } },
      'GET /api/merchant/quota': { status: 200, body: { balance: 0 } },
    })
    renderApp({ route: '/nope' })
    expect(screen.getByText('页面不存在')).toBeInTheDocument()
  })

  it('RT-11 记住的目标是 /admin 这类不存在的路径 → 落到自己的首页，不白屏', async () => {
    seedRedirect('/admin')
    stubApi({
      'POST /api/auth/login': { status: 200, body: loginOk('merchant') },
      'GET /api/merchant/tasks': { status: 200, body: { items: [], total: 0 } },
      'GET /api/merchant/reviews': { status: 200, body: { items: [] } },
      'GET /api/merchant/quota': { status: 200, body: { balance: 0 } },
    })
    renderApp({ route: '/login' })

    await signIn('merchant')
    expect(currentPath()).toBe('/merchant')
    expect(screen.getByText('商户')).toBeInTheDocument()
  })

  it('RT-12 未登录访问 *（404 页本身）→ /login（准入先于 404）', () => {
    stubApi()
    renderApp({ route: '/nope' })
    expect(currentPath()).toBe('/login')
    expect(screen.queryByText('页面不存在')).toBeNull()
  })
})
