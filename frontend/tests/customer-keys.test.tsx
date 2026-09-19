/**
 * CK / KG · 客户密钥页与缺 Key 弹窗（08 追加 F）
 */

import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import { claimItem, meClaims, meClaimsList, meCoupons, mePoints, taskDetail } from './fixtures'
import { mockMatchMedia } from './match-media'
import { currentPath, renderApp } from './render-app'
import { seedAuth } from './seed-auth'
import { stubApi } from './stub-api'

const HOME = {
  'GET /api/me/claims': { status: 200, body: meClaims({ total: 1 }) },
  'GET /api/me/points': { status: 200, body: mePoints({ balance: 0 }) },
  'GET /api/me/coupons': { status: 200, body: meCoupons({ total: 0 }) },
}

function pngFile() {
  const buf = new Uint8Array(16)
  buf[0] = 0x89
  buf[1] = 0x50
  buf[2] = 0x4e
  buf[3] = 0x47
  return new File([buf], 'a.png', { type: 'image/png' })
}

describe('CK · 密钥页', () => {
  it('CK-01 未登录 → login', async () => {
    mockMatchMedia(false)
    stubApi({})
    renderApp({ route: '/customer/keys' })
    expect(currentPath()).toBe('/login')
  })

  it('CK-02/03/04 列表 + 新增掩码 + 首页入口', async () => {
    mockMatchMedia(false)
    seedAuth({ role: 'customer' })
    const api = stubApi({
      ...HOME,
      'GET /api/me/model-keys': [
        { status: 200, body: { items: [] } },
        {
          status: 200,
          body: {
            items: [
              {
                id: 9,
                provider: 'deepseek',
                label: null,
                key_masked: 'sk-****zzzz',
                status: 'active',
              },
            ],
          },
        },
      ],
      'GET /api/models': { status: 200, body: { items: [] } },
      'POST /api/me/model-keys': {
        status: 201,
        body: {
          id: 9,
          provider: 'deepseek',
          key_masked: 'sk-****zzzz',
          status: 'active',
        },
      },
    })
    renderApp({ route: '/customer' })
    expect(await screen.findByTestId('customer-entry-keys')).toBeInTheDocument()
    await userEvent.setup().click(screen.getByTestId('customer-entry-keys'))
    await waitFor(() => expect(currentPath()).toBe('/customer/keys'))
    expect(api.count('GET', '/api/me/model-keys')).toBeGreaterThanOrEqual(1)
    expect(api.count('GET', '/api/models')).toBeGreaterThanOrEqual(1)

    await userEvent.setup().type(screen.getByTestId('keys-api-key'), 'sk-secret-plaintext')
    await userEvent.setup().click(screen.getByTestId('keys-save'))
    expect(await screen.findByTestId('keys-list')).toHaveTextContent('sk-****zzzz')
    expect(document.body.innerHTML).not.toContain('sk-secret-plaintext')
  })
})

describe('KG · 缺 Key 弹窗', () => {
  it('KG-01/02/05 BYOK 无 Key → 弹窗，0 次 jobs；无暂未开放', async () => {
    mockMatchMedia(false)
    seedAuth({ role: 'customer' })
    const api = stubApi({
      ...HOME,
      'GET /api/tasks/12': {
        status: 200,
        body: taskDetail({ task: { id: 12 }, claimed_by_me: true }),
      },
      'GET /api/me/claims': {
        status: 200,
        body: meClaimsList([claimItem({ claim: { id: 101 }, task: { id: 12 } })]),
      },
      'GET /api/models': {
        status: 200,
        body: { items: [{ provider: 'deepseek', has_my_key: false }] },
      },
      'GET /api/me/model-keys': { status: 200, body: { items: [] } },
    })
    renderApp({ route: '/customer?task=12' })
    await screen.findByTestId('composer-task')
    expect(screen.queryByText('暂未开放')).toBeNull()
    await userEvent.upload(screen.getByTestId('composer-file'), pngFile())
    await userEvent.setup().click(screen.getByRole('button', { name: '开始创作' }))
    expect(await screen.findByTestId('key-guide-dialog')).toBeInTheDocument()
    expect(api.count('POST', '/api/jobs')).toBe(0)
    await userEvent.setup().click(screen.getByTestId('key-guide-go'))
    await waitFor(() => expect(currentPath()).toMatch(/\/customer\/keys/))
  })

  it('KG-03 jobs 422 缺 Key → 弹窗含 detail', async () => {
    mockMatchMedia(false)
    seedAuth({ role: 'customer' })
    stubApi({
      ...HOME,
      'GET /api/tasks/12': {
        status: 200,
        body: taskDetail({ task: { id: 12 }, claimed_by_me: true }),
      },
      'GET /api/me/claims': {
        status: 200,
        body: meClaimsList([claimItem({ claim: { id: 101 }, task: { id: 12 } })]),
      },
      'GET /api/models': {
        status: 200,
        body: { items: [{ provider: 'deepseek', has_my_key: true }] },
      },
      'GET /api/me/model-keys': {
        status: 200,
        body: {
          items: [
            {
              id: 1,
              provider: 'deepseek',
              key_masked: 'sk-****',
              status: 'active',
              label: null,
            },
          ],
        },
      },
      'POST /api/uploads': {
        status: 201,
        body: { url: '/api/uploads/a.png', mime: 'image/png', size_bytes: 10 },
      },
      'POST /api/jobs': {
        status: 422,
        body: { detail: '未配置 deepseek 的有效 Key，无法使用 BYOK' },
      },
    })
    renderApp({ route: '/customer?task=12' })
    await screen.findByTestId('composer-task')
    await userEvent.upload(screen.getByTestId('composer-file'), pngFile())
    await userEvent.setup().click(screen.getByRole('button', { name: '开始创作' }))
    expect(await screen.findByTestId('key-guide-dialog')).toBeInTheDocument()
    expect(screen.getByTestId('key-guide-detail')).toHaveTextContent(/未配置 deepseek/)
  })
})
