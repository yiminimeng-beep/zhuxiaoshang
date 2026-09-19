/**
 * SJ / SC / SW / SG / SD / SB · 内容工坊（追加 C）
 */

import { act, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { loginOk, meClaims, meCoupons, mePoints, taskDetail } from './fixtures'
import { mockMatchMedia } from './match-media'
import { currentPath, renderApp } from './render-app'
import { seedAuth } from './seed-auth'
import { signIn } from './sign-in'
import { stubApi } from './stub-api'

function jobDetail(over: {
  status?: string
  fail_reason?: string | null
  outputs?: unknown[]
  prompt_draft?: unknown
  inputs?: unknown[]
  id?: number
} = {}) {
  return {
    job: {
      id: over.id ?? 7,
      task_id: 12,
      claim_id: 101,
      status: over.status ?? 'chatting',
      fail_reason: over.fail_reason ?? null,
      retry_count: 0,
      kind: 'copy',
    },
    inputs: over.inputs ?? [{ id: 1, url: '/api/uploads/a.png', mime: 'image/png' }],
    prompt_draft:
      'prompt_draft' in over
        ? over.prompt_draft
        : {
            raw_prompt: '原始提示',
            optimized_prompt: '优化提示',
            quality_score: null,
            rewrite_failed: false,
          },
    outputs: over.outputs ?? [],
  }
}

beforeEach(() => {
  mockMatchMedia(false)
  vi.useRealTimers()
})

afterEach(() => {
  vi.useRealTimers()
})

describe('SJ · 工坊外壳', () => {
  it('SJ-01 未登录 → login，登录后回跳', async () => {
    stubApi({
      'POST /api/auth/login': { status: 200, body: loginOk('customer') },
      'GET /api/jobs/1': { status: 200, body: jobDetail({ status: 'ready', id: 1 }) },
      'GET /api/me/claims': { status: 200, body: meClaims({ total: 0 }) },
      'GET /api/me/points': { status: 200, body: mePoints({ balance: 0 }) },
      'GET /api/me/coupons': { status: 200, body: meCoupons({ total: 0 }) },
      'GET /api/tasks/12': { status: 200, body: taskDetail({ task: { id: 12 } }) },
    })
    renderApp({ route: '/customer/studio/1' })
    expect(currentPath()).toBe('/login')
    await signIn('customer')
    await waitFor(() => expect(currentPath()).toBe('/customer/studio/1'))
  })

  it('SJ-02 403 → 不属于你', async () => {
    seedAuth({ role: 'customer' })
    stubApi({ 'GET /api/jobs/9': { status: 403, body: { detail: '无权' } } })
    renderApp({ route: '/customer/studio/9' })
    expect(await screen.findByTestId('studio-forbidden')).toHaveTextContent(
      '这个作品不属于你',
    )
  })

  it('SJ-03 jobId=abc → 不发请求', async () => {
    seedAuth({ role: 'customer' })
    const api = stubApi()
    renderApp({ route: '/customer/studio/abc' })
    expect(await screen.findByTestId('studio-missing')).toHaveTextContent('作品不存在')
    expect(api.count('GET', '/api/jobs/abc')).toBe(0)
  })

  it('SJ-04/05 guarding 轮询，409×2 后 200 停下', async () => {
    seedAuth({ role: 'customer' })
    const api = stubApi({
      'GET /api/jobs/7': [
        { status: 200, body: jobDetail({ status: 'guarding' }) },
        { status: 200, body: jobDetail({ status: 'chatting' }) },
      ],
      'GET /api/jobs/7/guard': [
        { status: 409, body: { detail: '预检尚未完成' } },
        { status: 409, body: { detail: '预检尚未完成' } },
        { status: 200, body: { passed: true, reason: null } },
      ],
      'GET /api/jobs/7/chat': { status: 200, body: { messages: [] } },
      'GET /api/tasks/12': { status: 200, body: taskDetail({ task: { id: 12 } }) },
    })
    vi.useFakeTimers({ shouldAdvanceTime: true })
    renderApp({ route: '/customer/studio/7' })
    expect(await screen.findByTestId('studio-guarding')).toBeInTheDocument()
    await act(async () => {
      await vi.advanceTimersByTimeAsync(4500)
    })
    await waitFor(() => expect(api.count('GET', '/api/jobs/7/guard')).toBeGreaterThanOrEqual(3))
    expect(await screen.findByTestId('studio-chat')).toBeInTheDocument()
  })

  it('SJ-06/07 guard 未过 + guard_failed 重试禁用', async () => {
    seedAuth({ role: 'customer' })
    stubApi({
      'GET /api/jobs/7': { status: 200, body: jobDetail({ status: 'guard_failed' }) },
      'GET /api/tasks/12': { status: 200, body: taskDetail({ task: { id: 12 } }) },
    })
    renderApp({ route: '/customer/studio/7' })
    expect(await screen.findByTestId('studio-guard-fail')).toBeInTheDocument()
    expect(screen.getByTestId('studio-retry')).toBeDisabled()
    expect(screen.getByTestId('studio-delete')).toBeEnabled()
  })

  it('SJ-08 ready 有产物无删除；SJ-09 need_review 无下载', async () => {
    seedAuth({ role: 'customer' })
    stubApi({
      'GET /api/jobs/7': {
        status: 200,
        body: jobDetail({
          status: 'ready',
          outputs: [
            {
              id: 1,
              type: 'copy',
              content: '好文案',
              url: null,
              judge_score: 88,
              judge_detail: { reasons: ['清晰'] },
              is_active: true,
            },
            {
              id: 2,
              type: 'copy',
              content: '旧版',
              url: null,
              judge_score: 10,
              judge_detail: null,
              is_active: false,
            },
          ],
        }),
      },
      'GET /api/tasks/12': { status: 200, body: taskDetail({ task: { id: 12 } }) },
    })
    const v1 = renderApp({ route: '/customer/studio/7' })
    expect(await screen.findByTestId('copy-content')).toHaveTextContent('好文案')
    expect(screen.queryByText('旧版')).toBeNull()
    expect(screen.queryByTestId('studio-delete')).toBeNull()
    expect(screen.queryByRole('video')).toBeNull()
    v1.unmount()

    seedAuth({ role: 'customer' })
    stubApi({
      'GET /api/jobs/8': { status: 200, body: jobDetail({ status: 'need_review', id: 8 }) },
      'GET /api/tasks/12': { status: 200, body: taskDetail({ task: { id: 12 } }) },
    })
    renderApp({ route: '/customer/studio/8' })
    expect(await screen.findByTestId('studio-review')).toHaveTextContent('已转人工复核')
    expect(screen.queryByTestId('download-txt')).toBeNull()
  })

  it('SJ-10 failed 显示原因可重试', async () => {
    seedAuth({ role: 'customer' })
    stubApi({
      'GET /api/jobs/7': {
        status: 200,
        body: jobDetail({ status: 'failed', fail_reason: '模型超时' }),
      },
      'GET /api/tasks/12': { status: 200, body: taskDetail({ task: { id: 12 } }) },
    })
    renderApp({ route: '/customer/studio/7' })
    expect(await screen.findByTestId('studio-failed')).toHaveTextContent('模型超时')
    expect(screen.getByTestId('studio-retry')).toBeEnabled()
  })

  it('SJ-11 卸载后轮询停', async () => {
    seedAuth({ role: 'customer' })
    const api = stubApi({
      'GET /api/jobs/7': { status: 200, body: jobDetail({ status: 'guarding' }) },
      'GET /api/jobs/7/guard': { status: 409, body: { detail: '预检尚未完成' } },
      'GET /api/tasks/12': { status: 200, body: taskDetail({ task: { id: 12 } }) },
    })
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const view = renderApp({ route: '/customer/studio/7' })
    await screen.findByTestId('studio-guarding')
    const before = api.calls.length
    view.unmount()
    await act(async () => {
      await vi.advanceTimersByTimeAsync(6000)
    })
    expect(api.calls.length).toBe(before)
  })
})

describe('SC · 对话', () => {
  it('SC-01/02/03/09 delta 拼接且带 Authorization', async () => {
    seedAuth({ role: 'customer' })
    const api = stubApi({
      'GET /api/jobs/7': { status: 200, body: jobDetail({ status: 'chatting' }) },
      'GET /api/jobs/7/chat': {
        status: 200,
        body: {
          messages: [
            { id: 1, role: 'user', content: '你好' },
            { id: 2, role: 'assistant', content: '嗨' },
          ],
        },
      },
      'POST /api/jobs/7/chat': {
        status: 200,
        sse: 'event: delta\ndata: {"text":"很"}\n\nevent: delta\ndata: {"text":"好"}\n\nevent: done\ndata: {"chars":2}\n\n',
      },
      'GET /api/tasks/12': { status: 200, body: taskDetail({ task: { id: 12 } }) },
    })
    renderApp({ route: '/customer/studio/7' })
    expect(await screen.findByText('你好')).toBeInTheDocument()
    await userEvent.setup().type(screen.getByTestId('chat-input'), '再聊')
    await userEvent.setup().click(screen.getByTestId('chat-send'))
    await waitFor(() => expect(screen.getByText('很好')).toBeInTheDocument())
    const post = api.callsTo('POST', '/api/jobs/7/chat')[0]
    expect(post.body).toEqual({ message: '再聊' })
    expect(post.headers.authorization).toMatch(/^Bearer /)
  })

  it('SC-08 空消息不发', async () => {
    seedAuth({ role: 'customer' })
    const api = stubApi({
      'GET /api/jobs/7': { status: 200, body: jobDetail({ status: 'chatting' }) },
      'GET /api/jobs/7/chat': { status: 200, body: { messages: [] } },
      'GET /api/tasks/12': { status: 200, body: taskDetail({ task: { id: 12 } }) },
    })
    renderApp({ route: '/customer/studio/7' })
    await screen.findByTestId('chat-send')
    expect(screen.getByTestId('chat-send')).toBeDisabled()
    expect(api.count('POST', '/api/jobs/7/chat')).toBe(0)
  })
})

describe('SW / SG / SD / SB', () => {
  it('SW-01/04/08 复写与手改后生成', async () => {
    seedAuth({ role: 'customer' })
    const api = stubApi({
      'GET /api/jobs/7': { status: 200, body: jobDetail({ status: 'chatting' }) },
      'GET /api/jobs/7/chat': { status: 200, body: { messages: [] } },
      'POST /api/jobs/7/rewrite-prompt': {
        status: 200,
        body: { optimized_prompt: '优化后', quality_score: 90, rewrite_failed: false },
      },
      'POST /api/jobs/7/generate': { status: 202, body: { status: 'generating' } },
      'GET /api/jobs/7/events': { status: 200, sse: '' },
      'GET /api/tasks/12': { status: 200, body: taskDetail({ task: { id: 12 } }) },
    })
    renderApp({ route: '/customer/studio/7' })
    await screen.findByTestId('prompt-input')
    // quality_score was null → score line must not say 上次
    await userEvent.setup().clear(screen.getByTestId('prompt-input'))
    await userEvent.setup().type(screen.getByTestId('prompt-input'), '新提示词内容')
    await userEvent.setup().click(screen.getByTestId('rewrite-btn'))
    await waitFor(() =>
      expect(screen.getByTestId('score-line')).toHaveTextContent('90 分'),
    )
    expect(screen.getByTestId('score-line').textContent).not.toContain('上次')
    await userEvent.setup().clear(screen.getByTestId('prompt-input'))
    await userEvent.setup().type(screen.getByTestId('prompt-input'), '手改过的')
    await userEvent.setup().click(screen.getByTestId('generate-btn'))
    await waitFor(() => expect(api.count('POST', '/api/jobs/7/generate')).toBe(1))
    expect(api.callsTo('POST', '/api/jobs/7/generate')[0].body).toEqual({
      prompt: '手改过的',
    })
  })

  it('SD-03/05/08 下载不走 download 端点；去回填带 job_id', async () => {
    seedAuth({ role: 'customer' })
    const api = stubApi({
      'GET /api/jobs/7': {
        status: 200,
        body: jobDetail({
          status: 'ready',
          outputs: [
            {
              id: 3,
              type: 'copy',
              content: '全文',
              url: 'key',
              judge_score: 70,
              judge_detail: { reasons: ['a', 'b'] },
              is_active: true,
            },
          ],
        }),
      },
      'GET /api/tasks/12': { status: 200, body: taskDetail({ task: { id: 12 } }) },
      'GET /api/me/posts': { status: 200, body: { items: [] } },
      'GET /api/me/claims': { status: 200, body: { items: [] } },
      'GET /api/me/jobs': { status: 200, body: { items: [] } },
    })
    renderApp({ route: '/customer/studio/7' })
    await screen.findByTestId('download-txt')
    expect(api.count('GET', '/api/jobs/7/download/3')).toBe(0)
    await userEvent.setup().click(screen.getByTestId('download-txt'))
    expect(api.count('GET', '/api/jobs/7/download/3')).toBe(0)
    await userEvent.setup().click(screen.getByTestId('goto-posts'))
    expect(currentPath()).toBe('/customer/posts?job_id=7')
  })

  it('SD-10 删除两步确认', async () => {
    seedAuth({ role: 'customer' })
    const api = stubApi({
      'GET /api/jobs/7': { status: 200, body: jobDetail({ status: 'failed', fail_reason: 'x' }) },
      'DELETE /api/jobs/7': { status: 204, body: null },
      'GET /api/tasks/12': { status: 200, body: taskDetail({ task: { id: 12 } }) },
      'GET /api/me/claims': { status: 200, body: { items: [], total: 0 } },
    })
    renderApp({ route: '/customer/studio/7' })
    await userEvent.setup().click(await screen.findByTestId('studio-delete'))
    expect(api.count('DELETE', '/api/jobs/7')).toBe(0)
    expect(screen.getByTestId('studio-delete')).toHaveTextContent('确认删除')
    await userEvent.setup().click(screen.getByTestId('studio-delete'))
    await waitFor(() => expect(currentPath()).toBe('/customer/claims'))
  })

  it('SB-01/02 预扣行', async () => {
    seedAuth({ role: 'customer' })
    sessionStorage.setItem(
      'zxs.studio.reserved.7',
      JSON.stringify({ reserved_points: 50, task_id: 12 }),
    )
    stubApi({
      'GET /api/jobs/7': { status: 200, body: jobDetail({ status: 'chatting' }) },
      'GET /api/jobs/7/chat': { status: 200, body: { messages: [] } },
      'GET /api/tasks/12': { status: 200, body: taskDetail({ task: { id: 12 } }) },
    })
    const v1 = renderApp({ route: '/customer/studio/7' })
    expect(await screen.findByTestId('studio-reserve')).toHaveTextContent('本次预扣 50 点')
    v1.unmount()

    sessionStorage.setItem(
      'zxs.studio.reserved.8',
      JSON.stringify({ reserved_points: 0, task_id: 12 }),
    )
    seedAuth({ role: 'customer' })
    stubApi({
      'GET /api/jobs/8': { status: 200, body: jobDetail({ status: 'chatting', id: 8 }) },
      'GET /api/jobs/8/chat': { status: 200, body: { messages: [] } },
      'GET /api/tasks/12': { status: 200, body: taskDetail({ task: { id: 12 } }) },
    })
    renderApp({ route: '/customer/studio/8' })
    await screen.findByTestId('studio-chat')
    expect(screen.queryByTestId('studio-reserve')).toBeNull()
  })

  it('SB-05 零次 merchant API', async () => {
    seedAuth({ role: 'customer' })
    const api = stubApi({
      'GET /api/jobs/7': { status: 200, body: jobDetail({ status: 'chatting' }) },
      'GET /api/jobs/7/chat': { status: 200, body: { messages: [] } },
      'GET /api/tasks/12': { status: 200, body: taskDetail({ task: { id: 12 } }) },
    })
    renderApp({ route: '/customer/studio/7' })
    await screen.findByTestId('studio-chat')
    expect(api.calls.every((c) => !c.path.startsWith('/api/merchant'))).toBe(true)
    expect(document.body.textContent).not.toMatch(/商户扣了/)
  })
})
