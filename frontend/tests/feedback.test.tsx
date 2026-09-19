/**
 * E 组 · 反馈（FD-01 ~ FD-09）
 *
 * 最贵的一条是 `FD-06`：**请求体里没有 `role` / `user_id`**。它与 06 的
 * `FB-04` / `FB-05` 是一对——后端拒绝收，前端也得保证不发。
 * 任一侧漏了，另一侧只是「恰好没触发」。
 */

import { fireEvent, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import {
  feedbackOk,
  fieldError,
  merchantQuota,
  merchantReviews,
  merchantTasks,
} from './fixtures'
import { currentPath, renderApp } from './render-app'
import { readAuth, seedAuth } from './seed-auth'
import { stubApi } from './stub-api'

const TASKS = '/api/merchant/tasks'
const REVIEWS = '/api/merchant/reviews'
const QUOTA = '/api/merchant/quota'
const FEEDBACK = '/api/feedback'
const REFRESH = '/api/auth/refresh'

const CONTACT = '联系方式（选填）'

function merchantStubs() {
  return {
    [`GET ${TASKS}`]: { status: 200, body: merchantTasks({ total: 3 }) },
    [`GET ${REVIEWS}`]: { status: 200, body: merchantReviews({ count: 1 }) },
    [`GET ${QUOTA}`]: { status: 200, body: merchantQuota({ balance: 7 }) },
  }
}

async function openFeedback() {
  seedAuth({ role: 'merchant' })
  renderApp({ route: '/merchant' })
  await userEvent.setup().click(await screen.findByRole('button', { name: '问题反馈' }))
  return screen.getByRole('dialog')
}

function dialog() {
  return screen.getByRole('dialog')
}

function contentBox() {
  return screen.getByLabelText('反馈内容') as HTMLTextAreaElement
}

function submitButton() {
  return screen.getByRole('button', { name: '提交反馈' })
}

describe('FD · 反馈弹层', () => {
  it('FD-01 点「问题反馈」→ 打开弹层；类型默认选中「功能建议」', async () => {
    stubApi(merchantStubs())
    await openFeedback()

    expect(screen.getByRole('radio', { name: '功能建议' })).toBeChecked()
    expect(screen.getByRole('radio', { name: '功能异常' })).not.toBeChecked()
    expect(screen.getByRole('radio', { name: '其他' })).not.toBeChecked()
  })

  it('FD-02 内容 4 字 → 提交按钮禁用，并提示「至少 5 字」', async () => {
    stubApi(merchantStubs())
    await openFeedback()

    await userEvent.setup().type(contentBox(), '四个字啊')

    expect(submitButton()).toBeDisabled()
    expect(within(dialog()).getByText(/至少 5 字/)).toBeInTheDocument()
  })

  it('FD-03 内容 5 字可提交；501 字被前端拦（两端钉死）', async () => {
    stubApi(merchantStubs())
    await openFeedback()

    const user = userEvent.setup()
    await user.type(contentBox(), '五个字内容')
    expect(submitButton()).toBeEnabled()

    // 501 个字逐个敲要 5 秒以上，且超时后**残留的按键会漏进下一条用例**。
    // 这里一次性换值：验的是边界判定，不是输入速度。
    fireEvent.change(contentBox(), { target: { value: '啊'.repeat(501) } })
    expect(submitButton()).toBeDisabled()
  })

  it('FD-04 实时字数：输入 5 字时页面上出现「5 / 500」', async () => {
    stubApi(merchantStubs())
    await openFeedback()

    await userEvent.setup().type(contentBox(), '五个字内容')

    expect(within(dialog()).getByText('5 / 500')).toBeInTheDocument()
  })

  it('FD-05 提交成功 → 弹层关闭、内容清空、出现成功提示', async () => {
    stubApi({
      ...merchantStubs(),
      [`POST ${FEEDBACK}`]: { status: 201, body: feedbackOk() },
    })
    await openFeedback()

    const user = userEvent.setup()
    await user.type(contentBox(), '希望支持批量导出')
    await user.click(submitButton())

    expect(await screen.findByText('已收到，感谢你的建议')).toBeInTheDocument()
    expect(screen.queryByRole('dialog')).toBeNull()

    // 内容清空：重新打开，框里应当是空的
    await user.click(screen.getByRole('button', { name: '问题反馈' }))
    expect(contentBox().value).toBe('')
  })

  it('FD-06 提交请求体不含 role，也不含 user_id', async () => {
    const api = stubApi({
      ...merchantStubs(),
      [`POST ${FEEDBACK}`]: { status: 201, body: feedbackOk() },
    })
    await openFeedback()

    const user = userEvent.setup()
    await user.type(contentBox(), '希望支持批量导出')
    await user.click(submitButton())
    await screen.findByText('已收到，感谢你的建议')

    const sent = api.callsTo('POST', FEEDBACK)[0].body as Record<string, unknown>
    expect(sent).not.toBeNull()
    expect(Object.keys(sent)).not.toContain('role')
    expect(Object.keys(sent)).not.toContain('user_id')
    // 该发的还得在
    expect(sent.content).toBe('希望支持批量导出')
    expect(sent.category).toBe('suggestion')
  })

  it('FD-07 提交中按钮禁用；连点两次只发一条反馈请求', async () => {
    const api = stubApi({
      ...merchantStubs(),
      [`POST ${FEEDBACK}`]: { status: 201, body: feedbackOk() },
    })
    await openFeedback()

    await userEvent.setup().type(contentBox(), '希望支持批量导出')

    const submit = submitButton()
    // 同步两连击：第一次的 handler 立刻置 loading，第二次应当打在 disabled 上
    fireEvent.click(submit)
    fireEvent.click(submit)

    expect(api.count('POST', FEEDBACK)).toBe(1)
  })

  it('FD-08 后端 422 带字段级错误 → 该字段下方显示错误，弹层不关闭', async () => {
    stubApi({
      ...merchantStubs(),
      [`POST ${FEEDBACK}`]: {
        status: 422,
        body: fieldError('content', '内容长度需在 5 到 500 之间'),
      },
    })
    await openFeedback()

    const user = userEvent.setup()
    await user.type(contentBox(), '希望支持批量导出')
    await user.click(submitButton())

    expect(await screen.findByText('内容长度需在 5 到 500 之间')).toBeInTheDocument()
    expect(screen.getByRole('dialog')).toBeInTheDocument()
    // 用户的字没丢
    expect(contentBox().value).toBe('希望支持批量导出')
  })

  it('FD-09 提交时回 401 → 清空本地并落到 /login', async () => {
    const api = stubApi({
      ...merchantStubs(),
      [`POST ${FEEDBACK}`]: { status: 401, body: { detail: '令牌过期' } },
      // 401 会先被统一拦截器拿去换令牌；刷新也失败 → 才清空跳登录
      [`POST ${REFRESH}`]: { status: 401, body: { detail: '刷新令牌无效' } },
    })
    await openFeedback()

    const user = userEvent.setup()
    await user.type(contentBox(), '希望支持批量导出')
    await user.click(submitButton())

    expect(api.count('POST', REFRESH)).toBe(1)
    expect(readAuth()).toBeNull()
    expect(currentPath()).toBe('/login')
  })
})

describe('FD · 反馈形态的其他约束', () => {
  it('联系方式选填；不填也能提交', async () => {
    const api = stubApi({
      ...merchantStubs(),
      [`POST ${FEEDBACK}`]: { status: 201, body: feedbackOk() },
    })
    await openFeedback()

    await userEvent.setup().type(contentBox(), '希望支持批量导出')
    await userEvent.setup().click(submitButton())
    await screen.findByText('已收到，感谢你的建议')

    const sent = api.callsTo('POST', FEEDBACK)[0].body as Record<string, unknown>
    expect(sent.contact ?? null).toBeNull()
  })

  it('联动的类型会一起发出去', async () => {
    const api = stubApi({
      ...merchantStubs(),
      [`POST ${FEEDBACK}`]: { status: 201, body: feedbackOk() },
    })
    await openFeedback()

    const user = userEvent.setup()
    await user.click(screen.getByRole('radio', { name: '功能异常' }))
    await user.type(contentBox(), '希望支持批量导出')
    await user.type(screen.getByLabelText(CONTACT), 'me@example.com')
    await user.click(submitButton())
    await screen.findByText('已收到，感谢你的建议')

    const sent = api.callsTo('POST', FEEDBACK)[0].body as Record<string, unknown>
    expect(sent.category).toBe('bug')
    expect(sent.contact).toBe('me@example.com')
  })
})
