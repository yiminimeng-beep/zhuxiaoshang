/**
 * CL / CD / CC · 找任务、详情、我的领取（拍 1）
 */

import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import {
  claimItem,
  meClaimsList,
  publishedTasks,
  taskDetail,
  taskRow,
} from './fixtures'
import { currentPath, renderApp } from './render-app'
import { seedAuth } from './seed-auth'
import { stubApi } from './stub-api'

describe('CL · 找任务', () => {
  it('CL-01 进页 → 1 次 GET /api/tasks', async () => {
    seedAuth({ role: 'customer' })
    const api = stubApi({
      'GET /api/tasks': { status: 200, body: publishedTasks() },
    })
    renderApp({ route: '/customer/tasks' })
    await screen.findByText('新品奶茶试喝')
    expect(api.count('GET', '/api/tasks')).toBe(1)
  })

  it('CL-02 卡上有标题·商户·品类·时间窗·名额·付费', async () => {
    seedAuth({ role: 'customer' })
    stubApi({ 'GET /api/tasks': { status: 200, body: publishedTasks() } })
    renderApp({ route: '/customer/tasks' })
    const row = await screen.findByTestId('task-row-1')
    expect(within(row).getByText('新品奶茶试喝')).toBeInTheDocument()
    expect(within(row).getByText('餐饮')).toBeInTheDocument()
    expect(within(row).getByText('商户付费')).toBeInTheDocument()
    expect(within(row).getByText('3 / 10')).toBeInTheDocument()
    expect(within(row).getAllByText('—').length).toBeGreaterThan(0)
  })

  it('CL-03 quota=null → 不限', async () => {
    seedAuth({ role: 'customer' })
    stubApi({
      'GET /api/tasks': {
        status: 200,
        body: publishedTasks([taskRow({ status: 'published', quota: null, claimed_count: 2 })]),
      },
    })
    renderApp({ route: '/customer/tasks' })
    expect(await screen.findByText('不限')).toBeInTheDocument()
  })

  it('CL-04 user_pay_reimburse 标需垫付', async () => {
    seedAuth({ role: 'customer' })
    stubApi({
      'GET /api/tasks': {
        status: 200,
        body: publishedTasks([
          taskRow({ status: 'published', pay_mode: 'user_pay_reimburse' }),
        ]),
      },
    })
    renderApp({ route: '/customer/tasks' })
    expect(await screen.findByText(/需垫付，过审后报销/)).toBeInTheDocument()
  })

  it('CL-05 搜索带 keyword', async () => {
    seedAuth({ role: 'customer' })
    const api = stubApi({
      'GET /api/tasks': { status: 200, body: publishedTasks() },
      'GET /api/tasks?keyword=%E5%A5%B6%E8%8C%B6': {
        status: 200,
        body: publishedTasks(),
      },
    })
    renderApp({ route: '/customer/tasks' })
    await screen.findByText('新品奶茶试喝')
    await userEvent.setup().type(screen.getByLabelText('关键词'), '奶茶')
    await userEvent.setup().click(screen.getByRole('button', { name: '搜索' }))
    await waitFor(() => {
      expect(api.calls.some((c) => c.url.includes('keyword='))).toBe(true)
    })
  })

  it('CL-06 领取成功就地变已领取', async () => {
    seedAuth({ role: 'customer' })
    const api = stubApi({
      'GET /api/tasks': { status: 200, body: publishedTasks() },
      'POST /api/tasks/1/claim': { status: 201, body: { claim: { id: 9 } } },
    })
    renderApp({ route: '/customer/tasks' })
    await screen.findByTestId('task-row-1')
    await userEvent.setup().click(screen.getByRole('button', { name: '领取' }))
    expect(await screen.findByText('已领取')).toBeInTheDocument()
    expect(api.count('POST', '/api/tasks/1/claim')).toBe(1)
  })

  it('CL-07 领取 409 原样显示', async () => {
    seedAuth({ role: 'customer' })
    stubApi({
      'GET /api/tasks': { status: 200, body: publishedTasks() },
      'POST /api/tasks/1/claim': { status: 409, body: { detail: '已经领取过了' } },
    })
    renderApp({ route: '/customer/tasks' })
    await userEvent.setup().click(await screen.findByRole('button', { name: '领取' }))
    expect(await screen.findByText('已经领取过了')).toBeInTheDocument()
  })

  it('CL-08 列表 500 → 错误态+重试', async () => {
    seedAuth({ role: 'customer' })
    stubApi({ 'GET /api/tasks': { status: 500, body: { detail: '炸了' } } })
    renderApp({ route: '/customer/tasks' })
    expect(await screen.findByRole('button', { name: '重试' })).toBeInTheDocument()
  })

  it('CL-09 空列表', async () => {
    seedAuth({ role: 'customer' })
    stubApi({
      'GET /api/tasks': { status: 200, body: { items: [], total: 0, page: 1, size: 20 } },
    })
    renderApp({ route: '/customer/tasks' })
    expect(await screen.findByText('还没有任务')).toBeInTheDocument()
  })
})

describe('CD · 任务详情', () => {
  it('CD-01 进页只打一次详情', async () => {
    seedAuth({ role: 'customer' })
    const api = stubApi({
      'GET /api/tasks/1': { status: 200, body: taskDetail() },
    })
    renderApp({ route: '/customer/tasks/1' })
    await screen.findByText('新品奶茶试喝')
    expect(api.count('GET', '/api/tasks/1')).toBe(1)
  })

  it('CD-02 阶梯显示上下限与奖励', async () => {
    seedAuth({ role: 'customer' })
    stubApi({ 'GET /api/tasks/1': { status: 200, body: taskDetail() } })
    renderApp({ route: '/customer/tasks/1' })
    expect(await screen.findByTestId('tier-0')).toHaveTextContent('0')
    expect(screen.getByTestId('tier-0')).toHaveTextContent('99')
    expect(screen.getByTestId('tier-1')).toHaveTextContent('以上')
  })

  it('CD-03 无 metric 选择器', async () => {
    seedAuth({ role: 'customer' })
    stubApi({ 'GET /api/tasks/1': { status: 200, body: taskDetail() } })
    renderApp({ route: '/customer/tasks/1' })
    await screen.findByText(/互动量/)
    expect(screen.queryByRole('combobox')).toBeNull()
  })

  it('CD-04 claimed_by_me 决定按钮', async () => {
    seedAuth({ role: 'customer' })
    stubApi({
      'GET /api/tasks/1': { status: 200, body: taskDetail({ claimed_by_me: false }) },
    })
    const view = renderApp({ route: '/customer/tasks/1' })
    expect(await screen.findByRole('button', { name: '领取' })).toBeInTheDocument()
    view.unmount()

    seedAuth({ role: 'customer' })
    stubApi({
      'GET /api/tasks/1': { status: 200, body: taskDetail({ claimed_by_me: true }) },
    })
    renderApp({ route: '/customer/tasks/1' })
    expect(await screen.findByRole('link', { name: '去创作' })).toHaveAttribute(
      'href',
      '/customer?task=1',
    )
  })

  it('CD-07 404 → 任务不存在', async () => {
    seedAuth({ role: 'customer' })
    stubApi({ 'GET /api/tasks/9': { status: 404, body: { detail: '任务不存在' } } })
    renderApp({ route: '/customer/tasks/9' })
    expect(await screen.findByText('任务不存在')).toBeInTheDocument()
  })
})

describe('CC · 我领取的任务', () => {
  it('CC-01 进页一次 claims', async () => {
    seedAuth({ role: 'customer' })
    const api = stubApi({
      'GET /api/me/claims': { status: 200, body: meClaimsList([claimItem()]) },
    })
    renderApp({ route: '/customer/claims' })
    await screen.findByTestId('claim-row-101')
    expect(api.count('GET', '/api/me/claims')).toBe(1)
  })

  it('CC-02 四要素在行内', async () => {
    seedAuth({ role: 'customer' })
    stubApi({
      'GET /api/me/claims': { status: 200, body: meClaimsList([claimItem()]) },
    })
    renderApp({ route: '/customer/claims' })
    const row = await screen.findByTestId('claim-row-101')
    expect(within(row).getByText('新品奶茶试喝')).toBeInTheDocument()
    expect(within(row).getByText('进行中')).toBeInTheDocument()
    expect(within(row).getByText(/2026-09-16/)).toBeInTheDocument()
  })

  it('CC-03 去创作跳转不发请求', async () => {
    seedAuth({ role: 'customer' })
    const api = stubApi({
      'GET /api/me/claims': { status: 200, body: meClaimsList([claimItem()]) },
      ...{
        'GET /api/me/claims': { status: 200, body: meClaimsList([claimItem()]) },
        'GET /api/me/points': { status: 200, body: { balance: 0 } },
        'GET /api/me/coupons': { status: 200, body: { items: [], total: 0 } },
        'GET /api/tasks/1': { status: 200, body: taskDetail() },
      },
    })
    renderApp({ route: '/customer/claims' })
    await userEvent.setup().click(await screen.findByRole('link', { name: '去创作' }))
    expect(currentPath()).toBe('/customer?task=1')
    expect(api.count('POST', '/api/jobs')).toBe(0)
  })

  it('CC-04 放弃两步确认', async () => {
    seedAuth({ role: 'customer' })
    const api = stubApi({
      'GET /api/me/claims': { status: 200, body: meClaimsList([claimItem()]) },
      'DELETE /api/me/claims/101': { status: 204, body: null },
    })
    renderApp({ route: '/customer/claims' })
    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: '放弃' }))
    expect(api.count('DELETE', '/api/me/claims/101')).toBe(0)
    await user.click(screen.getByRole('button', { name: '确认放弃' }))
    expect(api.count('DELETE', '/api/me/claims/101')).toBe(1)
  })

  it('CC-05 放弃成功行消失不重取', async () => {
    seedAuth({ role: 'customer' })
    const api = stubApi({
      'GET /api/me/claims': { status: 200, body: meClaimsList([claimItem()]) },
      'DELETE /api/me/claims/101': { status: 204, body: null },
    })
    renderApp({ route: '/customer/claims' })
    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: '放弃' }))
    await user.click(screen.getByRole('button', { name: '确认放弃' }))
    await waitFor(() => expect(screen.queryByTestId('claim-row-101')).toBeNull())
    expect(api.count('GET', '/api/me/claims')).toBe(1)
  })

  it('CC-06 空列表', async () => {
    seedAuth({ role: 'customer' })
    stubApi({ 'GET /api/me/claims': { status: 200, body: { items: [], total: 0 } } })
    renderApp({ route: '/customer/claims' })
    expect(await screen.findByText('还没有领取任务')).toBeInTheDocument()
  })
})
