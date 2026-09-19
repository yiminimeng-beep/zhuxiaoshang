/**
 * PF / PS / PA · 我的作品与回填（追加 C）
 */

import { act, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { claimItem, meClaims, meClaimsList, meCoupons, mePoints } from './fixtures'
import { mockMatchMedia } from './match-media'
import { currentPath, renderApp } from './render-app'
import { seedAuth } from './seed-auth'
import { stubApi } from './stub-api'

function postRow(over: Record<string, unknown> = {}) {
  return {
    id: 1,
    claim_id: 101,
    job_id: 7,
    platform: 'xhs',
    post_url: 'https://www.xiaohongshu.com/explore/abc',
    status: 'pending',
    countdown_seconds: 7200,
    latest_snapshot: {
      likes: 1,
      collects: 2,
      comments: 3,
      shares: 0,
      engagement: 6,
    },
    ...over,
  }
}

beforeEach(() => {
  mockMatchMedia(false)
  vi.useRealTimers()
})

afterEach(() => {
  vi.useRealTimers()
})

describe('PF · 回填列表', () => {
  it('PF-01/05/06 进页打 posts+claims；峰值提示；缺标题显示 —', async () => {
    seedAuth({ role: 'customer' })
    const api = stubApi({
      'GET /api/me/posts': {
        status: 200,
        body: { items: [postRow({ claim_id: 999 })] },
      },
      'GET /api/me/claims': {
        status: 200,
        body: meClaimsList([claimItem({ claim: { id: 101 } })]),
      },
    })
    renderApp({ route: '/customer/posts' })
    expect(await screen.findByTestId('peak-hint')).toHaveTextContent(/互动量峰值/)
    expect(api.count('GET', '/api/me/posts')).toBe(1)
    expect(api.count('GET', '/api/me/claims')).toBe(1)
    expect(screen.getByTestId('post-1')).toHaveTextContent('—')
    expect(screen.getByTestId('post-1').textContent).not.toMatch(/job_id|7/)
  })

  it('PF-08 无 ready job → 不弹层', async () => {
    seedAuth({ role: 'customer' })
    const api = stubApi({
      'GET /api/me/posts': { status: 200, body: { items: [] } },
      'GET /api/me/claims': { status: 200, body: { items: [] } },
      'GET /api/me/jobs': {
        status: 200,
        body: { items: [{ id: 1, status: 'chatting', claim_id: 1, task_id: 1 }] },
      },
    })
    renderApp({ route: '/customer/posts' })
    await userEvent.setup().click(await screen.findByTestId('fill-open'))
    expect(await screen.findByTestId('posts-note')).toHaveTextContent(/创作/)
    expect(screen.queryByTestId('fill-picker')).toBeNull()
    expect(api.count('GET', '/api/me/jobs')).toBe(1)
  })

  it('PF-10/11/13 本地拦 URL；体不含 user_id；平台错误落在平台下', async () => {
    seedAuth({ role: 'customer' })
    const api = stubApi({
      'GET /api/me/posts': { status: 200, body: { items: [] } },
      'GET /api/me/claims': { status: 200, body: { items: [] } },
      'GET /api/me/jobs': {
        status: 200,
        body: {
          items: [{ id: 7, status: 'ready', claim_id: 101, task_id: 12, kind: 'copy' }],
        },
      },
      'POST /api/posts': {
        status: 422,
        body: { detail: '链接域名与所选平台 xhs 不符' },
      },
    })
    renderApp({ route: '/customer/posts' })
    await userEvent.setup().click(await screen.findByTestId('fill-open'))
    await screen.findByTestId('fill-picker')
    await userEvent.setup().type(screen.getByTestId('fill-url'), 'not-a-url')
    await userEvent.setup().click(screen.getByTestId('fill-submit'))
    expect(api.count('POST', '/api/posts')).toBe(0)

    await userEvent.setup().clear(screen.getByTestId('fill-url'))
    await userEvent
      .setup()
      .type(screen.getByTestId('fill-url'), 'https://www.douyin.com/video/1')
    await userEvent.setup().click(screen.getByTestId('fill-submit'))
    expect(await screen.findByTestId('platform-error')).toHaveTextContent(/平台/)
    const body = api.callsTo('POST', '/api/posts')[0].body as Record<string, unknown>
    expect(body.source).toBe('manual')
    expect(body).not.toHaveProperty('user_id')
    expect(body).not.toHaveProperty('engagement')
  })

  it('PF-14 更新数据不含 engagement，就地更新', async () => {
    seedAuth({ role: 'customer' })
    const api = stubApi({
      'GET /api/me/posts': { status: 200, body: { items: [postRow()] } },
      'GET /api/me/claims': {
        status: 200,
        body: meClaimsList([claimItem({ claim: { id: 101 } })]),
      },
      'POST /api/posts/1/metrics': {
        status: 201,
        body: {
          snapshot: {
            likes: 9,
            collects: 1,
            comments: 1,
            shares: 0,
            engagement: 11,
          },
        },
      },
    })
    renderApp({ route: '/customer/posts' })
    await userEvent.setup().click(await screen.findByText('更新数据'))
    await userEvent.setup().clear(screen.getByTestId('metric-likes'))
    await userEvent.setup().type(screen.getByTestId('metric-likes'), '9')
    await userEvent.setup().click(screen.getByTestId('metrics-submit'))
    await waitFor(() => expect(screen.getByTestId('post-1')).toHaveTextContent('互动 11'))
    expect(api.count('GET', '/api/me/posts')).toBe(1)
    expect(api.callsTo('POST', '/api/posts/1/metrics')[0].body).not.toHaveProperty(
      'engagement',
    )
  })
})

describe('PS · 截图 OCR', () => {
  it('PS-01 21MB 本地拦', async () => {
    seedAuth({ role: 'customer' })
    const api = stubApi({
      'GET /api/me/posts': { status: 200, body: { items: [postRow()] } },
      'GET /api/me/claims': {
        status: 200,
        body: meClaimsList([claimItem({ claim: { id: 101 } })]),
      },
    })
    renderApp({ route: '/customer/posts' })
    await screen.findByTestId('post-1')
    const big = new File([new Uint8Array(21 * 1024 * 1024)], 'a.png', {
      type: 'image/png',
    })
    await userEvent.upload(screen.getByTestId('shot-1'), big)
    expect(await screen.findByTestId('posts-note')).toHaveTextContent(/20MB/)
    expect(api.count('POST', '/api/uploads')).toBe(0)
  })

  it('PS-07 parsed=null 显示 — 不出现 0', async () => {
    seedAuth({ role: 'customer' })
    stubApi({
      'GET /api/me/posts': { status: 200, body: { items: [postRow()] } },
      'GET /api/me/claims': {
        status: 200,
        body: meClaimsList([claimItem({ claim: { id: 101 } })]),
      },
      'POST /api/uploads': {
        status: 201,
        body: { url: '/api/uploads/x.png', mime: 'image/png', size_bytes: 10 },
      },
      'POST /api/posts/1/screenshot': { status: 202, body: { ocr_result_id: 1 } },
      'GET /api/ocr/1': {
        status: 200,
        body: { parsed: null, confidence: null, mismatch_flag: false },
      },
    })
    vi.useFakeTimers({ shouldAdvanceTime: true })
    renderApp({ route: '/customer/posts' })
    await screen.findByTestId('shot-1')
    const file = new File([new Uint8Array([0x89, 0x50])], 'a.png', { type: 'image/png' })
    await userEvent.upload(screen.getByTestId('shot-1'), file)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2500)
    })
    const box = await screen.findByTestId('ocr-result')
    expect(box).toHaveTextContent(/没认出来/)
    expect(within(box).getAllByText(/—/).length).toBeGreaterThan(0)
    // 四个数位是 —，不要把「置信度」里的数字误判成互动量 0
    expect(box.textContent).not.toMatch(/赞 0|藏 0|评 0|转 0/)
  })

  it('PS-08 选图后标明上传完成，不把打开文件当成已传完', async () => {
    seedAuth({ role: 'customer' })
    stubApi({
      'GET /api/me/posts': { status: 200, body: { items: [postRow()] } },
      'GET /api/me/claims': {
        status: 200,
        body: meClaimsList([claimItem({ claim: { id: 101 } })]),
      },
      'POST /api/uploads': {
        status: 201,
        body: { url: '/api/uploads/shot.png', mime: 'image/png', size_bytes: 10 },
      },
      'POST /api/posts/1/screenshot': { status: 202, body: { ocr_result_id: 1 } },
      'GET /api/ocr/1': { status: 409, body: { detail: '识别中' } },
    })
    renderApp({ route: '/customer/posts' })
    await screen.findByTestId('shot-1')
    const file = new File([new Uint8Array([0x89, 0x50])], '审核截图.png', {
      type: 'image/png',
    })
    const create = URL.createObjectURL
    const revoke = URL.revokeObjectURL
    URL.createObjectURL = () => 'blob:shot'
    URL.revokeObjectURL = () => {}
    await userEvent.upload(screen.getByTestId('shot-1'), file)
    const status = await screen.findByTestId('shot-status-1')
    await waitFor(() => expect(status).toHaveTextContent(/已上传/))
    expect(status).toHaveTextContent(/审核截图\.png/)
    expect(status.querySelector('img')).toBeTruthy()
    URL.createObjectURL = create
    URL.revokeObjectURL = revoke
  })

  it('识别没有结果时停下来，提示手填，不再空等', async () => {
    seedAuth({ role: 'customer' })
    const api = stubApi({
      'GET /api/me/posts': { status: 200, body: { items: [postRow()] } },
      'GET /api/me/claims': {
        status: 200,
        body: meClaimsList([claimItem({ claim: { id: 101 } })]),
      },
      'POST /api/uploads': {
        status: 201,
        body: { url: '/api/uploads/shot.png', mime: 'image/png', size_bytes: 10 },
      },
      'POST /api/posts/1/screenshot': { status: 202, body: { ocr_result_id: null } },
    })
    renderApp({ route: '/customer/posts' })
    await screen.findByTestId('shot-1')
    const file = new File([new Uint8Array([0x89, 0x50])], 'a.png', { type: 'image/png' })
    await userEvent.upload(screen.getByTestId('shot-1'), file)
    expect(await screen.findByTestId('posts-note')).toHaveTextContent(/更新数据/)
    expect(api.count('GET', '/api/ocr/1')).toBe(0)
  })
})

describe('PA · 申诉', () => {
  it('PA-02/03/04/05/06 两秒门闩', async () => {
    seedAuth({ role: 'customer' })
    const api = stubApi({
      'GET /api/me/posts': {
        status: 200,
        body: { items: [postRow({ status: 'rejected' })] },
      },
      'GET /api/me/claims': {
        status: 200,
        body: meClaimsList([claimItem({ claim: { id: 101 } })]),
      },
    })
    vi.useFakeTimers({ shouldAdvanceTime: true })
    renderApp({ route: '/customer/posts' })
    await userEvent.setup().click(await screen.findByTestId('appeal-1'))
    expect(screen.getByTestId('appeal-dialog')).toHaveTextContent(/申诉机会仅有一次/)
    expect(screen.getByTestId('appeal-confirm')).toBeDisabled()
    expect(screen.getByTestId('appeal-cancel')).toBeDisabled()
    await userEvent.setup().click(screen.getByTestId('appeal-confirm'))
    expect(api.count('POST', '/api/posts/1/appeal')).toBe(0)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2100)
    })
    expect(screen.getByTestId('appeal-confirm')).toBeEnabled()
    expect(screen.getByTestId('appeal-cancel')).toBeEnabled()
    await userEvent.setup().click(screen.getByTestId('appeal-cancel'))
    expect(screen.queryByTestId('appeal-dialog')).toBeNull()
    expect(api.count('POST', '/api/posts/1/appeal')).toBe(0)
  })
})

describe('客户首页 · 我的作品入口', () => {
  it('能从首页进已回填列表，数字是作品条数', async () => {
    mockMatchMedia(false)
    seedAuth({ role: 'customer' })
    stubApi({
      'GET /api/me/claims': { status: 200, body: meClaims({ total: 1 }) },
      'GET /api/me/points': { status: 200, body: mePoints({ balance: 0 }) },
      'GET /api/me/coupons': { status: 200, body: meCoupons({ total: 0 }) },
      'GET /api/me/jobs': { status: 200, body: { items: [], total: 0 } },
      'GET /api/tasks': { status: 200, body: { items: [], total: 0 } },
      'GET /api/me/posts': { status: 200, body: { items: [{ id: 1 }, { id: 2 }] } },
    })
    renderApp({ route: '/customer' })
    const entry = await screen.findByTestId('customer-entry-posts')
    expect(entry).toHaveAttribute('href', '/customer/posts')
    expect(entry).toHaveTextContent('我的作品')
    expect(within(entry).getByText('2')).toBeInTheDocument()
    await userEvent.setup().click(entry)
    expect(currentPath()).toBe('/customer/posts')
  })
})
