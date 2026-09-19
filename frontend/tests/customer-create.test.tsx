/**
 * CU · 上传与建 job（拍 2）
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
  'GET /api/models': {
    status: 200,
    body: {
      items: [
        { provider: 'deepseek', model: 'deepseek-v4-flash', op: 'chat', has_my_key: true },
        { provider: 'jimeng', model: 'jimeng-video', op: 'generate', has_my_key: true },
      ],
    },
  },
  'GET /api/me/model-keys': {
    status: 200,
    body: {
      items: [
        {
          id: 1,
          provider: 'deepseek',
          label: null,
          key_masked: 'sk-****abcd',
          status: 'active',
        },
        {
          id: 2,
          provider: 'jimeng',
          label: null,
          key_masked: 'jm-****efgh',
          status: 'active',
        },
      ],
    },
  },
}

function pngFile(name = 'a.png', size = 64) {
  const buf = new Uint8Array(size)
  buf[0] = 0x89
  buf[1] = 0x50
  buf[2] = 0x4e
  buf[3] = 0x47
  return new File([buf], name, { type: 'image/png' })
}

async function pickFiles(files: File[]) {
  const input = screen.getByTestId('composer-file') as HTMLInputElement
  await userEvent.upload(input, files)
}

describe('CU · 上传与建 job', () => {
  it('CU-01/02 选图只本地预览，显示张数，选图不发请求', async () => {
    mockMatchMedia(false)
    seedAuth({ role: 'customer' })
    const api = stubApi(HOME)
    renderApp({ route: '/customer' })
    await screen.findByTestId('customer-composer')
    const before = api.calls.length
    await pickFiles([pngFile('1.png'), pngFile('2.png'), pngFile('3.png')])
    expect(screen.getByRole('button', { name: /图片 3\/9/ })).toBeInTheDocument()
    expect(api.calls.length).toBe(before)
  })

  it('CH-11 选了任务没选图 → 不发 jobs', async () => {
    mockMatchMedia(false)
    seedAuth({ role: 'customer' })
    const api = stubApi({
      ...HOME,
      'GET /api/tasks/12': {
        status: 200,
        body: taskDetail({ task: { id: 12, title: '周末探店' }, claimed_by_me: true }),
      },
      'GET /api/me/claims': {
        status: 200,
        body: meClaimsList([
          claimItem({ claim: { id: 101 }, task: { id: 12, title: '周末探店' } }),
        ]),
      },
    })
    renderApp({ route: '/customer?task=12' })
    await screen.findByTestId('composer-task')
    await userEvent.setup().click(screen.getByRole('button', { name: '开始创作' }))
    expect(await screen.findByText(/请先选择至少一张图片/)).toBeInTheDocument()
    expect(api.count('POST', '/api/jobs')).toBe(0)
    expect(api.count('POST', '/api/uploads')).toBe(0)
  })

  it('CU-03~09 串行上传 → 建 job → 跳 studio；kind=copy；带 claim_id', async () => {
    mockMatchMedia(false)
    seedAuth({ role: 'customer' })
    const api = stubApi({
      ...HOME,
      'GET /api/tasks/12': {
        status: 200,
        body: taskDetail({ task: { id: 12, title: '周末探店' }, claimed_by_me: true }),
      },
      'GET /api/me/claims': {
        status: 200,
        body: meClaimsList([
          claimItem({ claim: { id: 101 }, task: { id: 12, title: '周末探店' } }),
        ]),
      },
      'POST /api/uploads': [
        { status: 201, body: { url: '/api/uploads/a.png', mime: 'image/png', size_bytes: 10 } },
        { status: 201, body: { url: '/api/uploads/b.png', mime: 'image/png', size_bytes: 11 } },
      ],
      'POST /api/jobs': { status: 201, body: { job_id: 77, status: 'guarding' } },
    })
    renderApp({ route: '/customer?task=12' })
    await screen.findByTestId('composer-task')
    await pickFiles([pngFile('a.png'), pngFile('b.png')])
    await userEvent.setup().click(screen.getByRole('button', { name: '开始创作' }))

    await waitFor(() => expect(currentPath()).toBe('/customer/studio/77'))
    expect(api.count('POST', '/api/uploads')).toBe(2)
    expect(api.count('POST', '/api/jobs')).toBe(1)
    const jobBody = api.callsTo('POST', '/api/jobs')[0].body as Record<string, unknown>
    expect(jobBody.kind).toBe('copy')
    expect(jobBody.claim_id).toBe(101)
    expect(jobBody.task_id).toBe(12)
    expect(jobBody).not.toHaveProperty('merchant_id')
    expect(jobBody).not.toHaveProperty('user_id')
    const assets = jobBody.assets as { url: string; mime: string; size_bytes: number }[]
    expect(assets).toEqual([
      { url: '/api/uploads/a.png', mime: 'image/png', size_bytes: 10 },
      { url: '/api/uploads/b.png', mime: 'image/png', size_bytes: 11 },
    ])
  })

  it('CU-04 第 2 张 415 → 停住，第 3 张不发', async () => {
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
      'POST /api/uploads': [
        { status: 201, body: { url: '/api/uploads/a.png', mime: 'image/png', size_bytes: 10 } },
        { status: 415, body: { detail: '只接受 jpeg / png / webp 图片' } },
      ],
    })
    renderApp({ route: '/customer?task=12' })
    await screen.findByTestId('composer-task')
    await pickFiles([pngFile('1.png'), pngFile('2.png'), pngFile('3.png')])
    await userEvent.setup().click(screen.getByRole('button', { name: '开始创作' }))
    expect(await screen.findByText(/只接受 jpeg/)).toBeInTheDocument()
    expect(api.count('POST', '/api/uploads')).toBe(2)
    expect(api.count('POST', '/api/jobs')).toBe(0)
  })

  it('CU-08/11/12 可选视频；切换不发请求；kind 进 jobs', async () => {
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
      'POST /api/uploads': {
        status: 201,
        body: { url: '/api/uploads/a.png', mime: 'image/png', size_bytes: 10 },
      },
      'POST /api/jobs': { status: 201, body: { job_id: 88 } },
    })
    renderApp({ route: '/customer?task=12' })
    await screen.findByTestId('customer-composer')
    expect(screen.queryByText('暂未开放')).toBeNull()
    const copy = screen.getByTestId('kind-copy')
    const video = screen.getByTestId('kind-video')
    expect(copy).not.toBeDisabled()
    expect(video).not.toBeDisabled()
    expect(copy).toHaveAttribute('aria-pressed', 'true')
    const before = api.calls.length
    await userEvent.setup().click(video)
    expect(api.calls.length).toBe(before)
    expect(video).toHaveAttribute('aria-pressed', 'true')
    await pickFiles([pngFile()])
    await userEvent.setup().click(screen.getByRole('button', { name: '开始创作' }))
    await waitFor(() => expect(currentPath()).toBe('/customer/studio/88'))
    const jobBody = api.callsTo('POST', '/api/jobs')[0].body as Record<string, unknown>
    expect(jobBody.kind).toBe('video')
    expect(jobBody.provider).toBe('jimeng')
  })

  it('CU-10 402 → 就地提示，不跳页', async () => {
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
      'POST /api/uploads': {
        status: 201,
        body: { url: '/api/uploads/a.png', mime: 'image/png', size_bytes: 10 },
      },
      'POST /api/jobs': { status: 402, body: { detail: '可用额度不足' } },
    })
    renderApp({ route: '/customer?task=12' })
    await screen.findByTestId('composer-task')
    await pickFiles([pngFile()])
    await userEvent.setup().click(screen.getByRole('button', { name: '开始创作' }))
    expect(await screen.findByText('可用额度不足')).toBeInTheDocument()
    expect(currentPath()).toMatch(/^\/customer/)
    expect(currentPath()).not.toMatch(/studio/)
  })
})
