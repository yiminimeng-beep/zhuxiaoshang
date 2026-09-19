/**
 * CR · 我的奖励（拍 2）
 */

import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import { mockMatchMedia } from './match-media'
import { currentPath, renderApp } from './render-app'
import { seedAuth } from './seed-auth'
import { stubApi } from './stub-api'

const LIST = {
  'GET /api/me/rewards/by-merchant': {
    status: 200,
    body: {
      items: [
        {
          merchant_id: 5,
          merchant_name: '巷口咖啡',
          logo_url: null,
          points_earned: 80,
          coupon_total: 2,
          coupon_unused: 1,
        },
        {
          merchant_id: 8,
          merchant_name: '',
          logo_url: null,
          points_earned: 0,
          coupon_total: 1,
          coupon_unused: 1,
        },
      ],
      total: 2,
      page: 1,
      size: 20,
    },
  },
}

describe('CR · 我的奖励', () => {
  it('CR-01/02/03 进页拉列表；0 积分也显示；空名显示 —', async () => {
    mockMatchMedia(false)
    seedAuth({ role: 'customer' })
    const api = stubApi(LIST)
    renderApp({ route: '/customer/rewards' })
    expect(await screen.findByTestId('rewards-list')).toBeInTheDocument()
    expect(api.count('GET', '/api/me/rewards/by-merchant')).toBe(1)
    expect(screen.getByText('巷口咖啡')).toBeInTheDocument()
    expect(screen.getByTestId('reward-card-5')).toHaveTextContent(/80 积分/)
    expect(screen.getByTestId('reward-card-5')).toHaveTextContent(/1\/2 张券/)
    expect(screen.getByTestId('reward-card-8')).toHaveTextContent(/^0 积分|[^0-9]0 积分/)
    expect(screen.getByTestId('reward-card-8')).toHaveTextContent('0 积分')
    expect(screen.getByTestId('reward-card-8')).toHaveTextContent('—')
  })

  it('有待发现金时标出待线下发放，不当成余额', async () => {
    mockMatchMedia(false)
    seedAuth({ role: 'customer' })
    stubApi({
      'GET /api/me/rewards/by-merchant': {
        status: 200,
        body: {
          items: [
            {
              merchant_id: 5,
              merchant_name: '巷口咖啡',
              logo_url: null,
              points_earned: 0,
              coupon_total: 0,
              coupon_unused: 0,
              cash_pending: 800,
            },
          ],
          total: 1,
          page: 1,
          size: 20,
        },
      },
    })
    renderApp({ route: '/customer/rewards' })
    const card = await screen.findByTestId('reward-card-5')
    expect(card).toHaveTextContent('800')
    expect(card).toHaveTextContent('待线下发放')
  })

  it('CR-04/05/06/07 点卡进详情；大字积分；面额与北京时间', async () => {
    mockMatchMedia(false)
    seedAuth({ role: 'customer' })
    stubApi({
      ...LIST,
      'GET /api/me/rewards/by-merchant/5': {
        status: 200,
        body: {
          merchant: { merchant_id: 5, merchant_name: '巷口咖啡', logo_url: null },
          points_earned: 80,
          coupons: [
            {
              id: 1,
              status: 'unused',
              expire_at: '2026-09-20T04:00:00+00:00',
              coupon: {
                id: 9,
                name: '满20减5',
                type: 'cash_off',
                value: 500,
                min_amount: 2000,
                status: 'active',
              },
            },
          ],
          coupons_total: 1,
          page: 1,
          size: 20,
        },
      },
    })
    renderApp({ route: '/customer/rewards' })
    await userEvent.setup().click(await screen.findByTestId('reward-card-5'))
    await waitFor(() => expect(currentPath()).toBe('/customer/rewards/5'))
    expect(await screen.findByTestId('reward-points')).toHaveTextContent('80')
    expect(screen.getByText('满20减5')).toBeInTheDocument()
    expect(screen.getByText('满 20 减 5')).toBeInTheDocument()
    // 2026-09-20T04:00:00Z → 北京 12:00
    expect(screen.getByText(/2026-09-20 12:00/)).toBeInTheDocument()
  })

  it('CR-08 详情 404', async () => {
    seedAuth({ role: 'customer' })
    stubApi({
      'GET /api/me/rewards/by-merchant/9': {
        status: 404,
        body: { detail: '与该商家无往来' },
      },
    })
    renderApp({ route: '/customer/rewards/9' })
    expect(await screen.findByTestId('reward-missing')).toHaveTextContent(
      '这个商家还没有你的记录',
    )
  })

  it('CT-04 rewards/abc → 商家不存在，不发请求', async () => {
    seedAuth({ role: 'customer' })
    const api = stubApi()
    renderApp({ route: '/customer/rewards/abc' })
    expect(await screen.findByTestId('reward-bad-id')).toHaveTextContent('商家不存在')
    expect(api.calls.filter((c) => c.path.includes('/rewards/by-merchant'))).toHaveLength(0)
  })

  it('CR-09 券空态', async () => {
    seedAuth({ role: 'customer' })
    stubApi({
      'GET /api/me/rewards/by-merchant/5': {
        status: 200,
        body: {
          merchant: { merchant_id: 5, merchant_name: '巷口咖啡', logo_url: null },
          points_earned: 10,
          coupons: [],
          coupons_total: 0,
          page: 1,
          size: 20,
        },
      },
    })
    renderApp({ route: '/customer/rewards/5' })
    expect(await screen.findByTestId('reward-coupons-empty')).toHaveTextContent(
      '还没有这个商家的券',
    )
  })
})
