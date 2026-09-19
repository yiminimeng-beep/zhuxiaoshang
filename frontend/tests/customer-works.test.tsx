/**
 * WK · 我的创作列表（08 追加 G）
 */

import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import { meClaims, meCoupons, mePoints } from './fixtures'
import { mockMatchMedia } from './match-media'
import { renderApp } from './render-app'
import { seedAuth } from './seed-auth'
import { stubApi } from './stub-api'

const HOME = {
  'GET /api/me/claims': { status: 200, body: meClaims({ total: 1 }) },
  'GET /api/me/points': { status: 200, body: mePoints({ balance: 0 }) },
  'GET /api/me/coupons': { status: 200, body: meCoupons({ total: 0 }) },
}

const JOBS = '/api/me/jobs?page=1&size=100'

describe('WK · 我的创作', () => {
  it('WK-01 首页有入口，指向 /customer/works', async () => {
    mockMatchMedia(false)
    seedAuth({ role: 'customer' })
    stubApi({
      ...HOME,
      'GET /api/me/jobs': { status: 200, body: { items: [], total: 1 } },
    })
    renderApp({ route: '/customer' })
    const entry = await screen.findByTestId('customer-entry-works')
    expect(entry).toHaveAttribute('href', '/customer/works')
    expect(entry).toHaveTextContent('我的创作')
    expect(entry).toHaveTextContent('1')
  })

  it('WK-02/03 进页一次列表，失败的文案可打开工坊', async () => {
    mockMatchMedia(false)
    seedAuth({ role: 'customer' })
    const api = stubApi({
      [`GET ${JOBS}`]: {
        status: 200,
        body: {
          items: [
            {
              id: 1,
              kind: 'copy',
              status: 'failed',
              fail_reason: '预检失败',
              created_at: '2026-09-18T04:00:00Z',
            },
          ],
          total: 1,
          page: 1,
          size: 100,
        },
      },
    })
    renderApp({ route: '/customer/works' })
    const row = await screen.findByTestId('work-row-1')
    expect(api.count('GET', JOBS)).toBe(1)
    expect(row).toHaveTextContent('作品 #1')
    expect(row).toHaveTextContent('文案')
    expect(row).toHaveTextContent('失败')
    expect(row).toHaveAttribute('href', '/customer/studio/1')
  })

  it('WK-04 空列表 → 还没有创作，不渲染列表', async () => {
    mockMatchMedia(false)
    seedAuth({ role: 'customer' })
    stubApi({
      [`GET ${JOBS}`]: { status: 200, body: { items: [], total: 0, page: 1, size: 100 } },
    })
    renderApp({ route: '/customer/works' })
    expect(await screen.findByText('还没有创作')).toBeInTheDocument()
    expect(screen.queryByTestId('works-list')).toBeNull()
    expect(screen.getByRole('link', { name: '去创作' })).toHaveAttribute('href', '/customer')
  })

  it('WK-05 列表失败 → 提示 + 重试，标题还在', async () => {
    mockMatchMedia(false)
    seedAuth({ role: 'customer' })
    const api = stubApi({
      [`GET ${JOBS}`]: { status: 500, body: { detail: '坏了' } },
    })
    renderApp({ route: '/customer/works' })
    expect(await screen.findByRole('alert')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: '我的创作' })).toBeInTheDocument()
    await userEvent.setup().click(screen.getByRole('button', { name: '重试' }))
    await waitFor(() => expect(api.count('GET', JOBS)).toBe(2))
  })
})
