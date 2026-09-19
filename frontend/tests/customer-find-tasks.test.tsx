/**
 * FT · 客户首页「找任务」入口（08 追加 H）
 */

import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import { meClaims, meCoupons, mePoints } from './fixtures'
import { mockMatchMedia } from './match-media'
import { currentPath, renderApp } from './render-app'
import { seedAuth } from './seed-auth'
import { stubApi } from './stub-api'

const HOME = {
  'GET /api/me/claims': { status: 200, body: meClaims({ total: 1 }) },
  'GET /api/me/points': { status: 200, body: mePoints({ balance: 0 }) },
  'GET /api/me/coupons': { status: 200, body: meCoupons({ total: 0 }) },
  'GET /api/me/jobs': { status: 200, body: { items: [], total: 0 } },
  'GET /api/tasks': { status: 200, body: { items: [{ id: 5 }], total: 3 } },
}

describe('FT · 找任务入口', () => {
  it('FT-01/02 首页入口指向 /customer/tasks，可点进列表', async () => {
    mockMatchMedia(false)
    seedAuth({ role: 'customer' })
    const api = stubApi({
      ...HOME,
      'GET /api/tasks': [
        { status: 200, body: { items: [{ id: 5 }], total: 3 } },
        { status: 200, body: { items: [{ id: 5 }], total: 3 } },
      ],
    })
    renderApp({ route: '/customer' })
    const entry = await screen.findByTestId('customer-entry-tasks')
    expect(entry).toHaveAttribute('href', '/customer/tasks')
    expect(entry).toHaveTextContent('找任务')
    expect(within(entry).getByText('3')).toBeInTheDocument()

    await userEvent.setup().click(entry)
    await waitFor(() => expect(currentPath()).toBe('/customer/tasks'))
    expect(api.count('GET', '/api/tasks')).toBeGreaterThanOrEqual(1)
  })

  it('FT-03 宽屏侧栏与窄屏底栏都有入口', async () => {
    seedAuth({ role: 'customer' })
    stubApi(HOME)

    mockMatchMedia(true)
    const wide = renderApp({ route: '/customer' })
    const aside = await screen.findByTestId('customer-aside')
    expect(within(aside).getByTestId('customer-entry-tasks')).toBeInTheDocument()
    wide.unmount()

    mockMatchMedia(false)
    renderApp({ route: '/customer' })
    const footer = await screen.findByTestId('customer-footer-nav')
    expect(within(footer).getByTestId('customer-entry-tasks')).toBeInTheDocument()
  })
})
