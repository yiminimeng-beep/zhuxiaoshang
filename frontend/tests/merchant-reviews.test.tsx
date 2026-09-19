/**
 * 商户待审队列。
 */

import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import { renderApp } from './render-app'
import { seedAuth } from './seed-auth'
import { stubApi } from './stub-api'

const ITEM = {
  id: 7,
  platform: 'xhs',
  post_url: 'https://www.xiaohongshu.com/explore/abc',
  post_title: '奶茶探店',
  status: 'pending',
  countdown_seconds: 3600,
}

describe('商户待审', () => {
  it('首页「待我审核」可点进审核页', async () => {
    seedAuth({ role: 'merchant' })
    stubApi({
      'GET /api/merchant/tasks': { status: 200, body: { items: [], total: 0 } },
      'GET /api/merchant/reviews': { status: 200, body: { items: [ITEM] } },
      'GET /api/merchant/quota': {
        status: 200,
        body: { balance: 1, reserved: 0, debt: 0, status: 'active' },
      },
    })
    const user = userEvent.setup()
    renderApp({ route: '/merchant' })
    const card = await screen.findByTestId('merchant-card-reviews')
    expect(card).toHaveAttribute('href', '/merchant/reviews')
    await user.click(card)
    expect(await screen.findByRole('heading', { name: '待我审核' })).toBeInTheDocument()
    expect(screen.getByText('奶茶探店')).toBeInTheDocument()
  })

  it('通过后从列表拿掉，并打 approve', async () => {
    seedAuth({ role: 'merchant' })
    const api = stubApi({
      'GET /api/merchant/reviews': { status: 200, body: { items: [ITEM] } },
      'POST /api/merchant/reviews/7/approve': { status: 200, body: { post: { id: 7 } } },
    })
    const user = userEvent.setup()
    renderApp({ route: '/merchant/reviews' })
    await screen.findByText('奶茶探店')
    await user.click(screen.getByTestId('review-approve-7'))
    expect(api.count('POST', '/api/merchant/reviews/7/approve')).toBe(0)
    expect(screen.getByTestId('review-confirm-7')).toBeInTheDocument()
    await user.click(screen.getByTestId('review-confirm-send-7'))
    await waitFor(() => expect(api.count('POST', '/api/merchant/reviews/7/approve')).toBe(1))
    expect(screen.queryByText('奶茶探店')).toBeNull()
  })

  it('驳回理由不足 10 字不发请求', async () => {
    seedAuth({ role: 'merchant' })
    const api = stubApi({
      'GET /api/merchant/reviews': { status: 200, body: { items: [ITEM] } },
    })
    const user = userEvent.setup()
    renderApp({ route: '/merchant/reviews' })
    await screen.findByText('奶茶探店')
    await user.click(screen.getByTestId('review-reject-7'))
    await user.type(screen.getByTestId('review-reason-7'), '太短')
    await user.click(screen.getByTestId('review-reject-send-7'))
    expect(await screen.findByTestId('review-note')).toHaveTextContent(/10/)
    expect(api.count('POST', '/api/merchant/reviews/7/reject')).toBe(0)
  })

  it('取消通过不发请求', async () => {
    seedAuth({ role: 'merchant' })
    const api = stubApi({
      'GET /api/merchant/reviews': { status: 200, body: { items: [ITEM] } },
    })
    const user = userEvent.setup()
    renderApp({ route: '/merchant/reviews' })
    await screen.findByText('奶茶探店')
    await user.click(screen.getByTestId('review-approve-7'))
    await user.click(screen.getByTestId('review-confirm-cancel-7'))
    expect(screen.queryByTestId('review-confirm-7')).toBeNull()
    expect(api.count('POST', '/api/merchant/reviews/7/approve')).toBe(0)
  })
})
