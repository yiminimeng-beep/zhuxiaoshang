/**
 * B 组 · 登录（LG-01 ~ LG-10）
 *
 * 两个按钮**不是 UI 摆设**：LG-03 / LG-04 断言的是「**没**写入令牌」。
 * 只断言「提示出现了」会漏掉「令牌其实已经存进去了」这种坏法——
 * 那才是真会把人放进错端的 bug。
 */

import { fireEvent, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import { loginOk } from './fixtures'
import { currentPath, renderApp } from './render-app'
import { readAuth, seedAuth } from './seed-auth'
import { chooseIdentity, fillAndSubmit, signIn } from './sign-in'
import { stubApi } from './stub-api'

const MERCHANT_HOME = {
  'GET /api/merchant/tasks': { status: 200, body: { items: [], total: 0 } },
  'GET /api/merchant/reviews': { status: 200, body: { items: [] } },
  'GET /api/merchant/quota': { status: 200, body: { balance: 0 } },
}

const CUSTOMER_HOME = {
  'GET /api/me/claims': { status: 200, body: { items: [], total: 0 } },
  'GET /api/me/points': { status: 200, body: { balance: 0 } },
  'GET /api/me/coupons': { status: 200, body: { items: [], total: 0 } },
}

describe('LG · 登录', () => {
  it('LG-01 首屏不选中任何身份：表单未展开，两个按钮都在', () => {
    stubApi()
    renderApp({ route: '/login' })
    expect(screen.getByRole('button', { name: '商家登录' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '用户登录' })).toBeInTheDocument()
    expect(screen.queryByLabelText('账号')).toBeNull()
    expect(screen.queryByLabelText('密码')).toBeNull()
  })

  it('LG-02 未选身份就提交 → 提示且一个请求都不发', async () => {
    const api = stubApi()
    renderApp({ route: '/login' })

    await userEvent.setup().click(screen.getByRole('button', { name: '登录' }))

    expect(screen.getByText('请先选择登录身份')).toBeInTheDocument()
    expect(api.calls).toHaveLength(0)
  })

  it('LG-03 商户账号点「用户登录」→ 只提示密码错误，且没写入令牌', async () => {
    stubApi({
      'POST /api/auth/login': { status: 200, body: loginOk('merchant') },
    })
    renderApp({ route: '/login' })
    await signIn('customer', { account: 'shop0001' })

    expect(screen.getByText('密码错误')).toBeInTheDocument()
    expect(screen.queryByText(/该账号是商户账号/)).toBeNull()
    expect(readAuth()).toBeNull()
    expect(currentPath()).toBe('/login')
  })

  it('LG-04 admin 账号 → 提示走平台后台，且没写入令牌', async () => {
    stubApi({ 'POST /api/auth/login': { status: 200, body: loginOk('admin') } })
    renderApp({ route: '/login' })
    await signIn('merchant', { account: 'admin' })

    expect(screen.getByText('管理员请使用平台后台')).toBeInTheDocument()
    expect(readAuth()).toBeNull()
    expect(currentPath()).toBe('/login')
  })

  it('LG-05 商户账号点「商家登录」→ /merchant 且写入 merchant 令牌', async () => {
    stubApi({
      'POST /api/auth/login': { status: 200, body: loginOk('merchant') },
      ...MERCHANT_HOME,
    })
    renderApp({ route: '/login' })
    await signIn('merchant')

    // 跳转是 `<Navigate>` 的一次 effect；同步断言在机器忙时会抢在它前面，
    // 报出来的却是「没跳」——那是**假的红**
    await waitFor(() => expect(currentPath()).toBe('/merchant'))
    expect(readAuth()?.role).toBe('merchant')
  })

  it('LG-06 客户账号点「用户登录」→ /customer 且写入 customer 令牌', async () => {
    stubApi({
      'POST /api/auth/login': { status: 200, body: loginOk('customer') },
      ...CUSTOMER_HOME,
    })
    renderApp({ route: '/login' })
    await signIn('customer', { account: 'user0001' })

    await waitFor(() => expect(currentPath()).toBe('/customer'))
    expect(readAuth()?.role).toBe('customer')
  })

  it('LG-07 401 → 显示「账号或密码错误」，本地仍然为空', async () => {
    stubApi({
      'POST /api/auth/login': { status: 401, body: { detail: '密码错误' } },
    })
    renderApp({ route: '/login' })
    await signIn('merchant')

    expect(screen.getByText('账号或密码错误')).toBeInTheDocument()
    expect(readAuth()).toBeNull()
  })

  it('LG-08 403 且 detail=账号已注销 → 显示后端那句话，不用通用文案', async () => {
    stubApi({
      'POST /api/auth/login': { status: 403, body: { detail: '账号已注销' } },
    })
    renderApp({ route: '/login' })
    await signIn('merchant')

    expect(screen.getByText('账号已注销')).toBeInTheDocument()
    expect(readAuth()).toBeNull()
  })

  it('LG-09 提交中按钮 disabled；连点两次只发一条登录请求', async () => {
    const api = stubApi({
      'POST /api/auth/login': { status: 200, body: loginOk('merchant') },
      ...MERCHANT_HOME,
    })
    renderApp({ route: '/login' })
    await chooseIdentity('merchant')

    const user = userEvent.setup()
    await user.type(screen.getByLabelText('账号'), 'shop0001')
    await user.type(screen.getByLabelText('密码'), 'pw123456')

    const submit = screen.getByRole('button', { name: '登录' })
    // 同步两连击：第一次的 handler 立刻置 loading，第二次应当打在 disabled 上
    fireEvent.click(submit)
    fireEvent.click(submit)

    expect(api.count('POST', '/api/auth/login')).toBe(1)
  })

  it('LG-10 选中后可一键切回重选，按新的身份校验', async () => {
    stubApi({
      'POST /api/auth/login': { status: 200, body: loginOk('merchant') },
      ...MERCHANT_HOME,
    })
    renderApp({ route: '/login' })
    await chooseIdentity('customer')

    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: '重新选择身份' }))

    // 切回重选后表单收起，身份按钮重新出现
    expect(screen.getByRole('button', { name: '商家登录' })).toBeInTheDocument()

    await chooseIdentity('merchant')
    await fillAndSubmit()
    await waitFor(() => expect(currentPath()).toBe('/merchant'))
    expect(readAuth()?.role).toBe('merchant')
  })
})

describe('LG · 已登录时的 /login', () => {
  it('已登录不会看到登录页（RT-06 的客户侧）', () => {
    seedAuth({ role: 'customer' })
    stubApi(CUSTOMER_HOME)
    renderApp({ route: '/login' })
    expect(currentPath()).toBe('/customer')
  })
})
