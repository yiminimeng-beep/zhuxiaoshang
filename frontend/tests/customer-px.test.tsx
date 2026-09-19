/**
 * PX · 工坊 / 回填视觉地板与不编造（追加 C）
 *
 * vitest `css: false`——算不出真实布局，地板靠「DOM class + CSS 源码契约」兜。
 */

import { readdirSync, readFileSync } from 'node:fs'
import { join } from 'node:path'

import { screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it } from 'vitest'

import { claimItem, meClaimsList } from './fixtures'
import { mockMatchMedia } from './match-media'
import { renderApp } from './render-app'
import { seedAuth } from './seed-auth'
import { stubApi } from './stub-api'

const WIDTHS = [320, 375, 414, 768, 1280] as const
const CSS = readFileSync(join(process.cwd(), 'src', 'index.css'), 'utf8')
const LONG =
  '超长不断词文案ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789'.repeat(4)

function jobDetail(over: {
  status?: string
  outputs?: unknown[]
  id?: number
} = {}) {
  return {
    job: {
      id: over.id ?? 7,
      task_id: 12,
      claim_id: 101,
      status: over.status ?? 'chatting',
      fail_reason: null,
      retry_count: 0,
      kind: 'copy',
    },
    inputs: [{ id: 1, url: '/api/uploads/a.png', mime: 'image/png' }],
    prompt_draft: {
      raw_prompt: '原始提示',
      optimized_prompt: '优化提示',
      quality_score: null,
      rewrite_failed: false,
    },
    outputs: over.outputs ?? [],
  }
}

function setViewport(width: number) {
  Object.defineProperty(window, 'innerWidth', {
    configurable: true,
    writable: true,
    value: width,
  })
}

function assertNoBadTokens(text: string) {
  expect(text).not.toMatch(/\bNaN\b/)
  expect(text).not.toMatch(/\bundefined\b/)
  expect(text).not.toMatch(/\bnull\b/)
}

function assertNoFabricated(text: string) {
  expect(text).not.toMatch(/AI\s*生成了/)
  expect(text).not.toMatch(/已为你节省/)
}

function listSourceFiles(dir: string): string[] {
  const out: string[] = []
  for (const name of readdirSync(dir, { withFileTypes: true })) {
    const full = join(dir, name.name)
    if (name.isDirectory()) out.push(...listSourceFiles(full))
    else if (/\.(tsx|ts)$/.test(name.name)) out.push(full)
  }
  return out
}

beforeEach(() => {
  mockMatchMedia(false)
  setViewport(375)
})

describe('PX · 视觉地板', () => {
  it('PX-01 五档无页面级横向滚动', async () => {
    expect(CSS).toMatch(/html,\s*\n\s*body\s*\{[\s\S]*?overflow-x:\s*clip/)
    expect(CSS).toMatch(/\.page--studio,\s*\n\s*\.page--posts\s*\{[\s\S]*?overflow-x:\s*clip/)

    seedAuth({ role: 'customer' })
    for (const width of WIDTHS) {
      setViewport(width)
      stubApi({
        'GET /api/jobs/7': {
          status: 200,
          body: jobDetail({
            status: 'ready',
            outputs: [
              {
                id: 1,
                type: 'copy',
                content: LONG,
                url: null,
                judge_score: 80,
                judge_detail: null,
                is_active: true,
              },
            ],
          }),
        },
        'GET /api/tasks/12': {
          status: 200,
          body: { task: { id: 12, pay_mode: 'merchant_pay' } },
        },
      })
      const studio = renderApp({ route: '/customer/studio/7' })
      const studioMain = await screen.findByRole('main')
      expect(studioMain.className).toMatch(/page--studio/)
      expect(window.innerWidth).toBe(width)
      await screen.findByTestId('studio-ready')
      studio.unmount()

      stubApi({
        'GET /api/me/posts': {
          status: 200,
          body: {
            items: [
              {
                id: 1,
                claim_id: 101,
                job_id: 7,
                platform: 'xhs',
                post_url: `https://www.xiaohongshu.com/explore/${'a'.repeat(80)}`,
                status: 'pending',
                countdown_seconds: 7200,
                latest_snapshot: {
                  likes: 1,
                  collects: 2,
                  comments: 3,
                  shares: 0,
                  engagement: 6,
                },
              },
            ],
          },
        },
        'GET /api/me/claims': {
          status: 200,
          body: meClaimsList([
            claimItem({
              claim: { id: 101 },
              task: { id: 12, title: LONG.slice(0, 40) },
            }),
          ]),
        },
      })
      const posts = renderApp({ route: '/customer/posts' })
      const postsMain = await screen.findByRole('main')
      expect(postsMain.className).toMatch(/page--posts/)
      await screen.findByTestId('posts-list')
      posts.unmount()
    }
  })

  it('PX-02 375 宽对话气泡与产物卡单列不溢出', async () => {
    expect(CSS).toMatch(/\.studio-panel--chat,\s*\n\s*\.studio-panel--ready\s*\{[\s\S]*?max-width:\s*40rem/)
    expect(CSS).toMatch(/\.chat-bubble\s*\{[\s\S]*?overflow-wrap:\s*anywhere/)
    expect(CSS).toMatch(/\.copy-out\s*\{[\s\S]*?overflow:\s*auto/)

    setViewport(375)
    seedAuth({ role: 'customer' })
    stubApi({
      'GET /api/jobs/7': { status: 200, body: jobDetail({ status: 'chatting' }) },
      'GET /api/jobs/7/chat': {
        status: 200,
        body: {
          messages: [
            { id: 1, role: 'user', content: LONG },
            { id: 2, role: 'assistant', content: LONG },
          ],
        },
      },
      'GET /api/tasks/12': {
        status: 200,
        body: { task: { id: 12, pay_mode: 'merchant_pay' } },
      },
    })
    const chatView = renderApp({ route: '/customer/studio/7' })
    const chat = await screen.findByTestId('studio-chat')
    expect(chat.className).toMatch(/studio-panel--chat/)
    const bubbles = [
      ...(await within(chat).findAllByTestId('chat-user')),
      ...(await within(chat).findAllByTestId('chat-assistant')),
    ]
    expect(bubbles.length).toBeGreaterThanOrEqual(2)
    for (const bubble of bubbles) {
      expect(bubble.className).toMatch(/chat-bubble/)
    }
    chatView.unmount()

    stubApi({
      'GET /api/jobs/7': {
        status: 200,
        body: jobDetail({
          status: 'ready',
          outputs: [
            {
              id: 1,
              type: 'copy',
              content: LONG,
              url: null,
              judge_score: 80,
              judge_detail: null,
              is_active: true,
            },
          ],
        }),
      },
      'GET /api/tasks/12': {
        status: 200,
        body: { task: { id: 12, pay_mode: 'merchant_pay' } },
      },
    })
    renderApp({ route: '/customer/studio/7' })
    const ready = await screen.findByTestId('studio-ready')
    expect(ready.className).toMatch(/studio-panel--ready/)
    expect(within(ready).getByTestId('copy-content').className).toMatch(/copy-out/)
  })

  it('PX-03 标题与按钮均为正体', async () => {
    expect(CSS).toMatch(/h1,\s*\n\s*h2,[\s\S]*?font-style:\s*normal/)
    expect(CSS).toMatch(/\.btn\s*\{[\s\S]*?font-style:\s*normal/)

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
              content: '文案',
              url: null,
              judge_score: 80,
              judge_detail: null,
              is_active: true,
            },
          ],
        }),
      },
      'GET /api/tasks/12': {
        status: 200,
        body: { task: { id: 12, pay_mode: 'merchant_pay' } },
      },
    })
    renderApp({ route: '/customer/studio/7' })
    const title = await screen.findByRole('heading', { level: 1 })
    expect(title.className).toMatch(/page__title/)
    expect(title.tagName).toBe('H1')
    for (const btn of screen.getAllByRole('button')) {
      expect(btn.className).toMatch(/\bbtn\b/)
    }
    expect(screen.getByTestId('goto-posts').className).toMatch(/\bbtn\b/)
  })

  it('PX-04 组件无内联 #hex / oklch() / 裸 font-family', () => {
    const roots = [
      join(process.cwd(), 'src', 'pages'),
      join(process.cwd(), 'src', 'components'),
      join(process.cwd(), 'src', 'lib'),
    ]
    const files = roots.flatMap(listSourceFiles)
    expect(files.length).toBeGreaterThan(10)
    for (const file of files) {
      const src = readFileSync(file, 'utf8')
      expect(src, file).not.toMatch(/#[0-9a-fA-F]{3,8}\b/)
      expect(src, file).not.toMatch(/oklch\s*\(/)
      expect(src, file).not.toMatch(/fontFamily\s*:/)
      expect(src, file).not.toMatch(/font-family\s*:/)
      expect(src, file).not.toMatch(/style=\{\{/)
    }
  })

  it('PX-05 整页文本不出现 NaN / undefined / null', async () => {
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
              content: '成品文案',
              url: null,
              judge_score: null,
              judge_detail: { reasons: ['略短'] },
              is_active: true,
            },
          ],
        }),
      },
      'GET /api/tasks/12': {
        status: 200,
        body: { task: { id: 12, pay_mode: 'merchant_pay' } },
      },
    })
    const studio = renderApp({ route: '/customer/studio/7' })
    await screen.findByTestId('studio-ready')
    assertNoBadTokens(document.body.textContent ?? '')
    studio.unmount()

    stubApi({
      'GET /api/me/posts': {
        status: 200,
        body: {
          items: [
            {
              id: 1,
              claim_id: 999,
              job_id: 7,
              platform: 'xhs',
              post_url: 'https://www.xiaohongshu.com/explore/abc',
              status: 'pending',
              countdown_seconds: 7200,
              latest_snapshot: null,
            },
          ],
        },
      },
      'GET /api/me/claims': {
        status: 200,
        body: meClaimsList([claimItem({ claim: { id: 101 } })]),
      },
    })
    renderApp({ route: '/customer/posts' })
    await screen.findByTestId('posts-list')
    assertNoBadTokens(document.body.textContent ?? '')
  })

  it('PX-06 prefers-reduced-motion 下进度无位移动画', async () => {
    expect(CSS).toMatch(
      /@media \(prefers-reduced-motion:\s*reduce\)[\s\S]*?\.progress-bar__fill\s*\{[\s\S]*?animation:\s*none/,
    )
    expect(CSS).toMatch(
      /@media \(prefers-reduced-motion:\s*reduce\)[\s\S]*?\.studio-panel\s*\{[\s\S]*?animation:\s*none/,
    )
    expect(CSS).toMatch(/@keyframes studio-panel-in[\s\S]*?opacity/)
    expect(CSS).not.toMatch(/@keyframes studio-panel-in[\s\S]*?transform:\s*translate/)

    seedAuth({ role: 'customer' })
    stubApi({
      'GET /api/jobs/7': { status: 200, body: jobDetail({ status: 'generating' }) },
      'GET /api/tasks/12': {
        status: 200,
        body: { task: { id: 12, pay_mode: 'merchant_pay' } },
      },
      'GET /api/jobs/7/events': {
        status: 200,
        sse: 'event: stage\ndata: {"stage":"generate"}\n\n',
      },
    })
    renderApp({ route: '/customer/studio/7' })
    const progress = await screen.findByTestId('studio-progress')
    expect(progress.querySelector('.progress-bar__fill')).toBeTruthy()
  })

  it('PX-07 两页不出现后端没给的编造数', async () => {
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
              content: '成品',
              url: null,
              judge_score: 80,
              judge_detail: null,
              is_active: true,
            },
          ],
        }),
      },
      'GET /api/tasks/12': {
        status: 200,
        body: { task: { id: 12, pay_mode: 'merchant_pay' } },
      },
    })
    const studio = renderApp({ route: '/customer/studio/7' })
    await waitFor(() => expect(screen.getByTestId('studio-ready')).toBeTruthy())
    assertNoFabricated(document.body.textContent ?? '')
    studio.unmount()

    stubApi({
      'GET /api/me/posts': { status: 200, body: { items: [] } },
      'GET /api/me/claims': { status: 200, body: { items: [] } },
    })
    renderApp({ route: '/customer/posts' })
    await screen.findByTestId('fill-open')
    assertNoFabricated(document.body.textContent ?? '')
  })
})
