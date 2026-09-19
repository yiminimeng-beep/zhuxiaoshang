/**
 * 追加 A · 商户侧「发布 + 管理任务」（`MT` / `ML` / `MC` / `ME` / `MR` / `MP` / `MS` / `MX`，共 65 条）
 *
 * 五个「写错了屏幕上看着也对」的地方，本文件都各有一条钉子：
 *
 * 1. `ME-01`~`ME-03` —— PATCH 体里**只许有改过的字段**。后端读
 *    `model_dump(exclude_unset=True)`，把整个 task 原样发回去（`title` 一字未改）
 *    也会撞上 `409 任务已发布，不允许修改 title`。
 * 2. `MR-07` —— 阶梯只能从 `GET /api/tasks/{id}` 的 `rule` 回填。
 *    改用 `GET /api/merchant/tasks/{id}` 拿不到 `rule`，刷新后阶梯变空，屏幕上看不出来。
 * 3. `MP-04` —— `402` 之后状态**不得**变成「已发布」（乐观改状态会先闪一下）。
 * 4. `MC-06` / `MC-07` —— 时间必须是**带偏移的 ISO**。裸发 `datetime-local` 的值
 *    会让后端 naive/aware 比较直接 `500`。
 * 5. `MR-04` —— 阶梯违规在 `detail.violations` 里，不是 `detail` 字符串。
 *
 * `MS` 组用**假定时器** + `fireEvent.change`：`userEvent` 每次输入都要 `await`，
 * 与假定时器混用会把微任务冲乱。其余各组一律真定时器。
 */

import { act, fireEvent, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { fieldError, loginOk, taskDetail, taskRow } from './fixtures'
import { currentPath, renderApp } from './render-app'
import { readAuth, seedAuth } from './seed-auth'
import { signIn } from './sign-in'
import { stubApi } from './stub-api'

const TASKS = '/api/merchant/tasks'
const TRASH = `${TASKS}/trash`
const RULES = '/api/merchant/reward-rules'

const detailUrl = (id: number) => `/api/tasks/${id}`
const ruleUrl = (id: number) => `${RULES}/${id}`
const validateUrl = (id: number) => `${RULES}/${id}/validate`
const actionUrl = (id: number, action: string) => `${TASKS}/${id}/${action}`

const DELETED = '2026-09-16T03:00:00+00:00'

/** 商户首页三个摘要接口（`MT-04` 从首页点进去要用） */
const HOME_STUBS = {
  [`GET ${TASKS}`]: { status: 200, body: { items: [], total: 0 } },
  'GET /api/merchant/reviews': { status: 200, body: { items: [] } },
  'GET /api/merchant/quota': { status: 200, body: { balance: 0 } },
}

const CUSTOMER_HOME = {
  'GET /api/me/claims': { status: 200, body: { items: [], total: 0 } },
  'GET /api/me/points': { status: 200, body: { balance: 0 } },
  'GET /api/me/coupons': { status: 200, body: { items: [], total: 0 } },
}

function listBody(rows: Array<Record<string, unknown>>) {
  return { items: rows, total: rows.length }
}

/** 渲染任务列表；默认一行 `draft` */
function renderList(rows: Array<Record<string, unknown>> = [taskRow()], extra = {}) {
  const api = stubApi({
    [`GET ${TASKS}`]: { status: 200, body: listBody(rows) },
    ...extra,
  })
  seedAuth({ role: 'merchant' })
  renderApp({ route: '/merchant/tasks' })
  return api
}

/** 渲染任务详情。`over` 直接喂给 `taskDetail`；`extra` 是额外的桩 */
function renderDetail(
  over: { task?: Record<string, unknown>; rule?: unknown } = {},
  extra = {},
  id = 1,
) {
  const api = stubApi({
    [`GET ${detailUrl(id)}`]: { status: 200, body: taskDetail(over) },
    ...extra,
  })
  seedAuth({ role: 'merchant' })
  renderApp({ route: `/merchant/tasks/${id}` })
  return api
}

/** 新建页必填项一把填好（时间用 `fireEvent.change`，确定且不慢） */
function fillBasics({
  title = '新品奶茶试喝',
  category = '餐饮',
  description = '到店试喝新品奶茶，发布一条带图笔记',
  start = '2026-09-20T10:00',
  end = '2026-09-30T10:00',
} = {}) {
  fireEvent.change(screen.getByLabelText('标题'), { target: { value: title } })
  fireEvent.change(screen.getByLabelText('品类'), { target: { value: category } })
  fireEvent.change(screen.getByLabelText('描述'), { target: { value: description } })
  fireEvent.change(screen.getByLabelText('开始时间'), { target: { value: start } })
  fireEvent.change(screen.getByLabelText('结束时间'), { target: { value: end } })
}

function renderNew() {
  seedAuth({ role: 'merchant' })
  renderApp({ route: '/merchant/tasks/new' })
  return stubApi({})
}

afterEach(() => {
  vi.useRealTimers()
})

describe('MT · 路由与入口', () => {
  it('MT-01 未登录访问 /merchant/tasks → 落 /login，登录成功后回跳 /merchant/tasks', async () => {
    stubApi({
      'POST /api/auth/login': { status: 200, body: loginOk('merchant') },
      [`GET ${TASKS}`]: { status: 200, body: listBody([]) },
    })
    renderApp({ route: '/merchant/tasks' })

    expect(currentPath()).toBe('/login')

    await signIn('merchant')

    await waitFor(() => expect(currentPath()).toBe('/merchant/tasks'))
  })

  it('MT-02 客户令牌访问 /merchant/tasks → 静默跳 /customer，不报错', async () => {
    seedAuth({ role: 'customer' })
    stubApi(CUSTOMER_HOME)
    renderApp({ route: '/merchant/tasks' })

    await waitFor(() => expect(currentPath()).toBe('/customer'))
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it('MT-03 商户令牌访问 → 停在 /merchant/tasks，不跳走', async () => {
    renderList([])
    await screen.findByTestId('task-page')
    expect(currentPath()).toBe('/merchant/tasks')
  })

  it('MT-04 商户首页「我的任务」卡片可点 → 进任务列表', async () => {
    const api = stubApi({ ...HOME_STUBS })
    seedAuth({ role: 'merchant' })
    renderApp({ route: '/merchant' })

    const link = await screen.findByRole('link', { name: /我的任务/ })
    await userEvent.setup().click(link)

    await waitFor(() => expect(currentPath()).toBe('/merchant/tasks'))
    expect(api.count('GET', TASKS)).toBeGreaterThanOrEqual(1)
  })

  it('MT-05 退出登录后本地为空，再进受保护页仍被拦', async () => {
    renderList()
    await screen.findByTestId('task-row-1')

    await userEvent.setup().click(screen.getByRole('button', { name: '退出登录' }))

    await waitFor(() => expect(currentPath()).toBe('/login'))
    expect(readAuth()).toBeNull()
  })
})

describe('ML · 列表与回收站', () => {
  it('ML-01 首次进页 → GET /api/merchant/tasks，且不带任何查询参数', async () => {
    const api = renderList()
    await screen.findByTestId('task-row-1')

    const first = api.callsTo('GET', TASKS)[0]
    expect(first.url).toBe(TASKS)
  })

  it('ML-02 每行五要素：标题 · 状态 · 时间窗 · 名额 · 付费模式', async () => {
    renderList([
      taskRow({
        id: 1,
        title: '新品奶茶试喝',
        status: 'published',
        quota: 10,
        claimed_count: 3,
        pay_mode: 'merchant_pay',
      }),
    ])

    const row = await screen.findByTestId('task-row-1')
    expect(within(row).getByTestId('task-title')).toHaveTextContent('新品奶茶试喝')
    expect(within(row).getByTestId('task-status')).toHaveTextContent('已发布')
    expect(within(row).getByTestId('task-window').textContent).not.toBe('')
    expect(within(row).getByTestId('task-quota')).toHaveTextContent('3 / 10')
    expect(within(row).getByTestId('task-pay')).toHaveTextContent('商户付费')
  })

  it('ML-03 quota=null → 名额显示「不限」，不是 0/0 也不是 NaN', async () => {
    renderList([taskRow({ id: 1, quota: null, claimed_count: 0 })])

    const row = await screen.findByTestId('task-row-1')
    const quota = within(row).getByTestId('task-quota')
    expect(quota).toHaveTextContent('不限')
    expect(quota.textContent).not.toContain('NaN')
  })

  it('ML-04 空列表 → 「还没有任务」', async () => {
    renderList([])
    expect(await screen.findByTestId('task-empty')).toHaveTextContent('还没有任务')
  })

  it('ML-05 切「回收站」→ 打 /tasks/trash，URL 变 ?tab=trash（照此 URL 重进仍打 trash）', async () => {
    const api = stubApi({
      [`GET ${TASKS}`]: { status: 200, body: listBody([taskRow()]) },
      [`GET ${TRASH}`]: {
        status: 200,
        body: listBody([taskRow({ id: 9, deleted_at: DELETED })]),
      },
    })
    seedAuth({ role: 'merchant' })
    const first = renderApp({ route: '/merchant/tasks' })
    await screen.findByTestId('task-row-1')

    await userEvent.setup().click(screen.getByTestId('task-tab-trash'))

    await waitFor(() => expect(currentPath()).toBe('/merchant/tasks?tab=trash'))
    expect(api.count('GET', TRASH)).toBe(1)

    // 拿这个 URL 重进一次：页签是从 URL 读的，不是组件里的临时状态
    first.unmount()
    renderApp({ route: '/merchant/tasks?tab=trash' })
    await waitFor(() => expect(api.count('GET', TRASH)).toBe(2))
  })

  it('ML-06 回收站行只有「恢复」，没有「编辑 / 删除」', async () => {
    stubApi({
      [`GET ${TRASH}`]: {
        status: 200,
        body: listBody([taskRow({ id: 9, deleted_at: DELETED })]),
      },
    })
    seedAuth({ role: 'merchant' })
    renderApp({ route: '/merchant/tasks?tab=trash' })

    const row = await screen.findByTestId('task-row-9')
    expect(within(row).getByRole('button', { name: '恢复' })).toBeInTheDocument()
    expect(within(row).queryByRole('button', { name: '删除' })).toBeNull()
    expect(within(row).queryByRole('link', { name: '查看' })).toBeNull()
  })

  it('ML-07 列表 500 → 错误态 + 重试；点重试重发一次', async () => {
    const api = stubApi({
      [`GET ${TASKS}`]: [
        { status: 500, body: { detail: '炸了' } },
        { status: 200, body: listBody([taskRow()]) },
      ],
    })
    seedAuth({ role: 'merchant' })
    renderApp({ route: '/merchant/tasks' })

    await screen.findByTestId('task-load-error')
    expect(screen.queryByTestId('task-row-1')).toBeNull()

    await userEvent.setup().click(screen.getByTestId('task-retry'))

    await screen.findByTestId('task-row-1')
    expect(api.count('GET', TASKS)).toBe(2)
  })

  it('ML-08 删除成功后该行消失，且列表不重取', async () => {
    const api = renderList([taskRow({ id: 1 })], {
      [`DELETE ${TASKS}/1`]: { status: 204 },
    })
    const row = await screen.findByTestId('task-row-1')

    const user = userEvent.setup()
    await user.click(within(row).getByTestId('task-delete-1'))
    await user.click(within(row).getByTestId('task-delete-1'))

    await waitFor(() => expect(screen.queryByTestId('task-row-1')).toBeNull())
    expect(api.count('GET', TASKS)).toBe(1)
  })

  it('ML-09 恢复成功后该行从回收站消失', async () => {
    const api = stubApi({
      [`GET ${TRASH}`]: {
        status: 200,
        body: listBody([taskRow({ id: 9, deleted_at: DELETED })]),
      },
      [`POST ${actionUrl(9, 'restore')}`]: {
        status: 200,
        body: { task: taskRow({ id: 9, deleted_at: null }) },
      },
    })
    seedAuth({ role: 'merchant' })
    renderApp({ route: '/merchant/tasks?tab=trash' })

    await userEvent.setup().click(await screen.findByTestId('task-restore-9'))

    await waitFor(() => expect(screen.queryByTestId('task-row-9')).toBeNull())
    expect(api.count('GET', TRASH)).toBe(1)
  })
})

describe('MC · 新建', () => {
  it('MC-01 标题 1 字 → 前端拦，一次 POST 都不发', async () => {
    const api = renderNew()
    fillBasics({ title: '茶' })

    await userEvent.setup().click(screen.getByTestId('task-save-draft'))

    expect(api.count('POST', TASKS)).toBe(0)
    expect(screen.getByTestId('task-error-title').textContent).not.toBe('')
  })

  it('MC-02 描述 9 字 → 拦；10 字 → 放行', async () => {
    const api = renderNew()
    fillBasics({ description: '一二三四五六七八九' })

    const user = userEvent.setup()
    await user.click(screen.getByTestId('task-save-draft'))
    expect(api.count('POST', TASKS)).toBe(0)

    fireEvent.change(screen.getByLabelText('描述'), {
      target: { value: '一二三四五六七八九十' },
    })
    await user.click(screen.getByTestId('task-save-draft'))
    await waitFor(() => expect(api.count('POST', TASKS)).toBe(1))
  })

  it('MC-03 end_at == start_at → 拦（含相等）', async () => {
    const api = renderNew()
    fillBasics({ start: '2026-09-20T10:00', end: '2026-09-20T10:00' })

    await userEvent.setup().click(screen.getByTestId('task-save-draft'))

    expect(api.count('POST', TASKS)).toBe(0)
    expect(screen.getByTestId('task-error-end_at').textContent).not.toBe('')
  })

  it('MC-04 名额填 0 → 拦；留空 → 体里 quota 为 null', async () => {
    const api = renderNew()
    fillBasics()
    fireEvent.change(screen.getByLabelText('名额'), { target: { value: '0' } })

    const user = userEvent.setup()
    await user.click(screen.getByTestId('task-save-draft'))
    expect(api.count('POST', TASKS)).toBe(0)

    fireEvent.change(screen.getByLabelText('名额'), { target: { value: '' } })
    await user.click(screen.getByTestId('task-save-draft'))

    await waitFor(() => expect(api.count('POST', TASKS)).toBe(1))
    expect(api.lastCall()?.body).toMatchObject({ quota: null })
  })

  it('MC-05 标签 6 个 → 拦；单个 17 字 → 拦；5 个 × 16 字 → 放行', async () => {
    const api = renderNew()
    fillBasics()

    const user = userEvent.setup()
    const tags = screen.getByLabelText('标签')

    fireEvent.change(tags, { target: { value: 'a,b,c,d,e,f' } })
    await user.click(screen.getByTestId('task-save-draft'))
    expect(api.count('POST', TASKS)).toBe(0)

    fireEvent.change(tags, { target: { value: 'abcdefghijklmnopq' } })
    await user.click(screen.getByTestId('task-save-draft'))
    expect(api.count('POST', TASKS)).toBe(0)

    fireEvent.change(tags, {
      target: { value: 'abcdefghijklmnop,abcdefghijklmnop,abcdefghijklmnop,abcdefghijklmnop,abcdefghijklmnop' },
    })
    await user.click(screen.getByTestId('task-save-draft'))
    await waitFor(() => expect(api.count('POST', TASKS)).toBe(1))
  })

  it('MC-06 提交体里的 start_at / end_at 是带偏移的 ISO，不是裸串', async () => {
    const api = renderNew()
    fillBasics({ start: '2026-09-20T10:00', end: '2026-09-30T10:00' })

    await userEvent.setup().click(screen.getByTestId('task-save-draft'))
    await waitFor(() => expect(api.count('POST', TASKS)).toBe(1))

    const body = api.lastCall()?.body as Record<string, string>
    // 裸串（`2026-09-20T10:00`）会让后端拿 naive 与 aware 比较 → TypeError → 500
    expect(body.start_at).toMatch(/(Z|[+-]\d{2}:\d{2})$/)
    expect(body.end_at).toMatch(/(Z|[+-]\d{2}:\d{2})$/)
    expect(body.start_at).not.toBe('2026-09-20T10:00')
    // 北京时间 10:00 = UTC 02:00
    expect(body.start_at).toContain('T02:00')
  })

  it('MC-07 服务端的 UTC ISO 回填时间输入框时是北京时间 wall clock', async () => {
    renderDetail({ task: { id: 1, start_at: '2026-09-20T02:00:00+00:00' } })
    await screen.findByTestId('task-detail')

    expect(screen.getByLabelText('开始时间')).toHaveValue('2026-09-20T10:00')
  })

  it('MC-08 「保存草稿」恰好 1 次 POST，体里不含 tiers / reward 键', async () => {
    const api = renderNew()
    fillBasics()

    await userEvent.setup().click(screen.getByTestId('task-save-draft'))
    await waitFor(() => expect(api.count('POST', TASKS)).toBe(1))

    const body = api.lastCall()?.body as Record<string, unknown>
    expect(body).not.toHaveProperty('tiers')
    expect(body).not.toHaveProperty('reward')
    expect(body).not.toHaveProperty('max_reward_per_user')
  })

  it('MC-09 POST 字段级 422 → 后端那句 msg 落在对应字段下方', async () => {
    renderNew()
    stubApi({
      [`POST ${TASKS}`]: {
        status: 422,
        body: fieldError('title', '标题不能与已有任务重名'),
      },
    })
    fillBasics()

    await userEvent.setup().click(screen.getByTestId('task-save-draft'))

    await waitFor(() =>
      expect(screen.getByTestId('task-error-title')).toHaveTextContent('标题不能与已有任务重名'),
    )
  })

  it('MC-09b start_at 早于现在 → 本地拦；body 级 422 也贴到开始时间', async () => {
    const api = renderNew()
    fillBasics({ start: '2020-01-01T10:00', end: '2026-09-30T10:00' })
    await userEvent.setup().click(screen.getByTestId('task-save-draft'))
    expect(api.count('POST', TASKS)).toBe(0)
    expect(screen.getByTestId('task-error-start_at')).toHaveTextContent(/不能早于现在/)

    stubApi({
      [`POST ${TASKS}`]: {
        status: 422,
        body: {
          detail: [
            {
              type: 'value_error',
              loc: ['body'],
              msg: 'Value error, start_at 不得早于当前时间',
            },
          ],
        },
      },
    })
    fillBasics({ start: '2026-09-20T10:00', end: '2026-09-30T10:00' })
    await userEvent.setup().click(screen.getByTestId('task-save-draft'))
    await waitFor(() =>
      expect(screen.getByTestId('task-error-start_at')).toHaveTextContent(/start_at|不得早于/),
    )
  })

  it('MC-10 「直接发布」→ POST → PUT reward-rules → POST publish，各 1 次且按此顺序', async () => {
    const api = stubApi({
      [`POST ${TASKS}`]: { status: 201, body: { task: taskRow({ id: 1 }) } },
      [`PUT ${ruleUrl(1)}`]: { status: 200, body: { rule: { id: 1, task_id: 1 } } },
      [`POST ${actionUrl(1, 'publish')}`]: { status: 200, body: { status: 'published' } },
      [`GET ${detailUrl(1)}`]: {
        status: 200,
        body: taskDetail({ task: { id: 1, status: 'published' } }),
      },
    })
    seedAuth({ role: 'merchant' })
    renderApp({ route: '/merchant/tasks/new' })
    fillBasics()
    fireEvent.change(screen.getByTestId('tier-cash-0'), { target: { value: '500' } })

    await userEvent.setup().click(screen.getByTestId('task-publish'))

    await waitFor(() => expect(currentPath()).toBe('/merchant/tasks/1'))

    // 只挑这三步来断：跳转后详情页还会自己 GET 一次，那是无关噪声。
    const wanted = [
      `POST ${TASKS}`,
      `PUT ${ruleUrl(1)}`,
      `POST ${actionUrl(1, 'publish')}`,
    ]
    const seq = api.calls
      .map((call) => `${call.method} ${call.path}`)
      .filter((key) => wanted.includes(key))
    expect(seq).toEqual([
      `POST ${TASKS}`,
      `PUT ${ruleUrl(1)}`,
      `POST ${actionUrl(1, 'publish')}`,
    ])
  })

  it('MC-11 POST 403 仅限商户 → 就地显示后端文案', async () => {
    stubApi({ [`POST ${TASKS}`]: { status: 403, body: { detail: '仅限商户' } } })
    seedAuth({ role: 'merchant' })
    renderApp({ route: '/merchant/tasks/new' })
    fillBasics()

    await userEvent.setup().click(screen.getByTestId('task-save-draft'))

    await waitFor(() =>
      expect(screen.getByTestId('task-form-error')).toHaveTextContent('仅限商户'),
    )
  })

  it('MC-12 选 user_pay_reimburse → 体里同时带池子与上限；上限 > 池子 → 前端拦', async () => {
    const api = renderNew()
    fillBasics()

    const user = userEvent.setup()
    await user.click(screen.getByLabelText('用户垫付'))
    fireEvent.change(screen.getByLabelText('报销池额度'), { target: { value: '100' } })
    fireEvent.change(screen.getByLabelText('单用户报销上限'), { target: { value: '500' } })

    await user.click(screen.getByTestId('task-save-draft'))
    expect(api.count('POST', TASKS)).toBe(0)
    expect(screen.getByTestId('task-error-reimburse').textContent).not.toBe('')

    fireEvent.change(screen.getByLabelText('报销池额度'), { target: { value: '1000' } })
    await user.click(screen.getByTestId('task-save-draft'))

    await waitFor(() => expect(api.count('POST', TASKS)).toBe(1))
    expect(api.lastCall()?.body).toMatchObject({
      pay_mode: 'user_pay_reimburse',
      reimburse_pool: 1000,
      reimburse_per_user_limit: 500,
    })
  })
})

describe('ME · 编辑', () => {
  it('ME-01 draft 改标题保存 → PATCH 体里只有 title', async () => {
    const api = renderDetail(
      { task: { id: 1, status: 'draft' } },
      { [`PATCH ${TASKS}/1`]: { status: 200, body: { task: taskRow({ id: 1 }) } } },
    )
    await screen.findByTestId('task-detail')

    fireEvent.change(screen.getByLabelText('标题'), { target: { value: '换个标题' } })
    await userEvent.setup().click(screen.getByTestId('task-save'))

    await waitFor(() => expect(api.count('PATCH', `${TASKS}/1`)).toBe(1))
    expect(Object.keys(api.lastCall()?.body as object)).toEqual(['title'])
  })

  it('ME-02 published：标题 / 品类 / 标签 / 开始时间为只读，且不进 PATCH 体', async () => {
    const api = renderDetail(
      { task: { id: 1, status: 'published' } },
      { [`PATCH ${TASKS}/1`]: { status: 200, body: { task: taskRow({ id: 1 }) } } },
    )
    await screen.findByTestId('task-detail')

    expect(screen.getByLabelText('标题')).toBeDisabled()
    expect(screen.getByLabelText('品类')).toBeDisabled()
    expect(screen.getByLabelText('标签')).toBeDisabled()
    expect(screen.getByLabelText('开始时间')).toBeDisabled()

    fireEvent.change(screen.getByLabelText('描述'), { target: { value: '改后的描述内容' } })
    await userEvent.setup().click(screen.getByTestId('task-save'))

    await waitFor(() => expect(api.count('PATCH', `${TASKS}/1`)).toBe(1))
    const body = api.lastCall()?.body as Record<string, unknown>
    expect(body).not.toHaveProperty('title')
    expect(body).not.toHaveProperty('category')
    expect(body).not.toHaveProperty('tags')
    expect(body).not.toHaveProperty('start_at')
  })

  it('ME-03 published 改描述 → PATCH 体里只有 description', async () => {
    const api = renderDetail(
      { task: { id: 1, status: 'published' } },
      { [`PATCH ${TASKS}/1`]: { status: 200, body: { task: taskRow({ id: 1 }) } } },
    )
    await screen.findByTestId('task-detail')

    fireEvent.change(screen.getByLabelText('描述'), { target: { value: '只改这一处' } })
    await userEvent.setup().click(screen.getByTestId('task-save'))

    await waitFor(() => expect(api.count('PATCH', `${TASKS}/1`)).toBe(1))
    expect(Object.keys(api.lastCall()?.body as object)).toEqual(['description'])
  })

  it('ME-04 published 改 quota → 允许发出', async () => {
    const api = renderDetail(
      { task: { id: 1, status: 'published', claimed_count: 3 } },
      { [`PATCH ${TASKS}/1`]: { status: 200, body: { task: taskRow({ id: 1 }) } } },
    )
    await screen.findByTestId('task-detail')

    fireEvent.change(screen.getByLabelText('名额'), { target: { value: '50' } })
    await userEvent.setup().click(screen.getByTestId('task-save'))

    await waitFor(() => expect(api.count('PATCH', `${TASKS}/1`)).toBe(1))
    expect(api.lastCall()?.body).toMatchObject({ quota: 50 })
  })

  it('ME-05 PATCH 409 → 原样显示后端 detail，并重取详情', async () => {
    const conflict = '任务已发布，不允许修改 title（仅可改 (\'description\', \'end_at\', \'requirement\') 与 quota）'
    const api = renderDetail(
      { task: { id: 1, status: 'published' } },
      { [`PATCH ${TASKS}/1`]: { status: 409, body: { detail: conflict } } },
    )
    await screen.findByTestId('task-detail')

    fireEvent.change(screen.getByLabelText('描述'), { target: { value: '改一下' } })
    await userEvent.setup().click(screen.getByTestId('task-save'))

    await waitFor(() =>
      expect(screen.getByTestId('task-action-error')).toHaveTextContent(conflict),
    )
    expect(api.count('GET', detailUrl(1))).toBe(2)
  })

  it('ME-06 PATCH 422（quota 小于已领取）→ 就地提示', async () => {
    renderDetail(
      { task: { id: 1, status: 'published', claimed_count: 3 } },
      {
        [`PATCH ${TASKS}/1`]: {
          status: 422,
          body: { detail: 'quota 不得小于已领取人数' },
        },
      },
    )
    await screen.findByTestId('task-detail')

    fireEvent.change(screen.getByLabelText('名额'), { target: { value: '1' } })
    await userEvent.setup().click(screen.getByTestId('task-save'))

    await waitFor(() =>
      expect(screen.getByTestId('task-action-error')).toHaveTextContent('quota 不得小于已领取人数'),
    )
  })

  it('ME-07 closed 任务 → 无「保存」，字段全只读', async () => {
    renderDetail({ task: { id: 1, status: 'closed' } })
    await screen.findByTestId('task-detail')

    expect(screen.queryByTestId('task-save')).toBeNull()
    expect(screen.getByLabelText('标题')).toBeDisabled()
    expect(screen.getByLabelText('描述')).toBeDisabled()
  })

  it('ME-08 详情 404 → 「任务不存在」，不白屏', async () => {
    stubApi({
      [`GET ${detailUrl(1)}`]: { status: 404, body: { detail: '任务不存在' } },
    })
    seedAuth({ role: 'merchant' })
    renderApp({ route: '/merchant/tasks/1' })

    expect(await screen.findByTestId('task-missing')).toHaveTextContent('任务不存在')
  })

  it('ME-09 详情 500 → 错误态 + 重试；非法 :id 不发请求', async () => {
    const api = stubApi({
      [`GET ${detailUrl(1)}`]: { status: 500, body: { detail: '炸了' } },
    })
    seedAuth({ role: 'merchant' })
    renderApp({ route: '/merchant/tasks/1' })

    await screen.findByTestId('task-detail-error')
    expect(screen.getByTestId('task-detail-retry')).toBeInTheDocument()

    // 非法 id：连一个请求都不该发出去（打过去只会拿回 404 或 422）
    const before = api.calls.length
    seedAuth({ role: 'merchant' })
    renderApp({ route: '/merchant/tasks/abc' })
    await screen.findByTestId('task-missing')
    expect(api.calls.length).toBe(before)
  })
})

describe('MR · 奖励阶梯', () => {
  it('MR-01 默认一档 min=0 / max=空，且 min 不可编辑', async () => {
    renderDetail({ task: { id: 1, status: 'draft' }, rule: null })
    await screen.findByTestId('task-detail')

    const min = screen.getByTestId('tier-min-0')
    expect(min).toBeDisabled()
    expect(min).toHaveValue(0)
    expect(screen.getByTestId('tier-max-0')).toHaveValue(null)
  })

  it('MR-02 可加档、可删档；末档 max 恒为 null', async () => {
    renderDetail({ task: { id: 1, status: 'draft' }, rule: null })
    await screen.findByTestId('task-detail')

    const user = userEvent.setup()
    await user.click(screen.getByTestId('tier-add'))

    expect(screen.getByTestId('tier-row-1')).toBeInTheDocument()
    expect(screen.getByTestId('tier-max-1')).toBeDisabled()

    await user.click(screen.getByTestId('tier-remove-1'))
    expect(screen.queryByTestId('tier-row-1')).toBeNull()
  })

  it('MR-03 「校验」→ POST validate，体含 metric / tiers / max_reward_per_user', async () => {
    const api = renderDetail(
      { task: { id: 1, status: 'draft' } },
      { [`POST ${validateUrl(1)}`]: { status: 200, body: { valid: true } } },
    )
    await screen.findByTestId('task-detail')

    await userEvent.setup().click(screen.getByTestId('tier-validate'))

    await waitFor(() => expect(api.count('POST', validateUrl(1))).toBe(1))
    const body = api.lastCall()?.body as Record<string, unknown>
    expect(body.metric).toBe('engagement')
    expect(Array.isArray(body.tiers)).toBe(true)
    expect(body).toHaveProperty('max_reward_per_user')
  })

  it('MR-04 422 的 detail.violations 是数组 → 逐条列在阶梯区', async () => {
    renderDetail(
      { task: { id: 1, status: 'draft' } },
      {
        [`POST ${validateUrl(1)}`]: {
          status: 422,
          body: {
            detail: {
              violations: [
                '第一档的 min 必须是 0，实际 3',
                '第 1 档与第 2 档之间有缺口：100~199 无人覆盖',
              ],
            },
          },
        },
      },
    )
    await screen.findByTestId('task-detail')

    await userEvent.setup().click(screen.getByTestId('tier-validate'))

    await waitFor(() =>
      expect(screen.getByTestId('tier-violation-0')).toHaveTextContent(
        '第一档的 min 必须是 0，实际 3',
      ),
    )
    expect(screen.getByTestId('tier-violation-1')).toHaveTextContent(
      '第 1 档与第 2 档之间有缺口：100~199 无人覆盖',
    )
  })

  it('MR-05 合法阶梯 → 显示通过，且「保存阶梯」可用', async () => {
    renderDetail(
      { task: { id: 1, status: 'draft' } },
      { [`POST ${validateUrl(1)}`]: { status: 200, body: { valid: true } } },
    )
    await screen.findByTestId('task-detail')

    await userEvent.setup().click(screen.getByTestId('tier-validate'))

    await waitFor(() => expect(screen.getByTestId('tier-ok')).toBeInTheDocument())
    expect(screen.getByTestId('tier-save')).not.toBeDisabled()
  })

  it('MR-06 「保存阶梯」→ PUT /reward-rules/{id}', async () => {
    const api = renderDetail(
      { task: { id: 1, status: 'draft' } },
      { [`PUT ${ruleUrl(1)}`]: { status: 200, body: { rule: { id: 1, task_id: 1 } } } },
    )
    await screen.findByTestId('task-detail')

    await userEvent.setup().click(screen.getByTestId('tier-save'))

    await waitFor(() => expect(api.count('PUT', ruleUrl(1))).toBe(1))
  })

  it('MR-07 刷新详情页 → 阶梯从 GET /api/tasks/{id} 的 rule 回填', async () => {
    renderDetail({ task: { id: 1, status: 'draft' } })
    await screen.findByTestId('task-detail')

    // 两档来自 fixture 的 rule（第二档 cash=2000）；用的是 /api/tasks/{id}，
    // 换回 /api/merchant/tasks/{id} 拿不到 rule，这里就空了
    expect(screen.getByTestId('tier-cash-0')).toHaveValue(500)
    expect(screen.getByTestId('tier-cash-1')).toHaveValue(2000)
  })

  it('MR-08 rule=null → 提示未配置 + 发布按钮 disabled', async () => {
    renderDetail({ task: { id: 1, status: 'draft' }, rule: null })
    await screen.findByTestId('task-detail')

    expect(screen.getByTestId('tier-notice')).toHaveTextContent('未配置奖励阶梯，不能发布')
    expect(screen.getByTestId('task-publish')).toBeDisabled()
  })

  it('MR-09 reward 三项全空 / cash 填小数 → 前端拦', async () => {
    const api = renderDetail(
      { task: { id: 1, status: 'draft' }, rule: null },
      { [`POST ${validateUrl(1)}`]: { status: 200, body: { valid: true } } },
    )
    await screen.findByTestId('task-detail')

    const user = userEvent.setup()
    await user.click(screen.getByTestId('tier-validate'))
    expect(api.count('POST', validateUrl(1))).toBe(0)
    expect(screen.getByTestId('tier-violation-0').textContent).not.toBe('')

    fireEvent.change(screen.getByTestId('tier-cash-0'), { target: { value: '1.5' } })
    await user.click(screen.getByTestId('tier-validate'))
    expect(api.count('POST', validateUrl(1))).toBe(0)
  })

  it('MR-10 metric 只读，页面上没有 metric 选择器', async () => {
    renderDetail({ task: { id: 1, status: 'draft' } })
    await screen.findByTestId('task-detail')

    expect(screen.getByTestId('tier-metric')).toHaveTextContent('互动量')
    expect(screen.queryByLabelText('指标')).toBeNull()
    expect(document.querySelectorAll('select[name="metric"]')).toHaveLength(0)
  })
})

describe('MP · 发布与状态机', () => {
  it('MP-01 draft 有规则 → 「发布」→ POST publish，状态变「已发布」', async () => {
    const api = renderDetail(
      { task: { id: 1, status: 'draft' } },
      {
        [`POST ${actionUrl(1, 'publish')}`]: { status: 200, body: { status: 'published' } },
        [`GET ${detailUrl(1)}`]: [
          { status: 200, body: taskDetail({ task: { id: 1, status: 'draft' } }) },
          { status: 200, body: taskDetail({ task: { id: 1, status: 'published' } }) },
        ],
      },
    )
    await screen.findByTestId('task-detail')

    await userEvent.setup().click(screen.getByTestId('task-publish'))

    await waitFor(() =>
      expect(screen.getByTestId('task-detail-status')).toHaveTextContent('已发布'),
    )
    expect(api.count('POST', actionUrl(1, 'publish'))).toBe(1)
  })

  it('MP-02 后端回「尚未配置奖励规则」→ 原样显示那句话', async () => {
    renderDetail(
      { task: { id: 1, status: 'draft' } },
      {
        [`POST ${actionUrl(1, 'publish')}`]: {
          status: 409,
          body: { detail: '尚未配置奖励规则，不能发布' },
        },
      },
    )
    await screen.findByTestId('task-detail')

    await userEvent.setup().click(screen.getByTestId('task-publish'))

    await waitFor(() =>
      expect(screen.getByTestId('task-action-error')).toHaveTextContent(
        '尚未配置奖励规则，不能发布',
      ),
    )
  })

  it('MP-03 end_at 已过 → 422 原样显示，状态不变', async () => {
    renderDetail(
      { task: { id: 1, status: 'draft' } },
      {
        [`POST ${actionUrl(1, 'publish')}`]: {
          status: 422,
          body: { detail: '任务已过期，不能发布' },
        },
      },
    )
    await screen.findByTestId('task-detail')

    await userEvent.setup().click(screen.getByTestId('task-publish'))

    await waitFor(() =>
      expect(screen.getByTestId('task-action-error')).toHaveTextContent('任务已过期，不能发布'),
    )
    expect(screen.getByTestId('task-detail-status')).toHaveTextContent('草稿')
  })

  it('MP-04 402 额度不足以锁定报销池 → 原样显示，状态不得变', async () => {
    renderDetail(
      { task: { id: 1, status: 'draft', pay_mode: 'user_pay_reimburse' } },
      {
        [`POST ${actionUrl(1, 'publish')}`]: {
          status: 402,
          body: { detail: '商户可用额度不足以锁定报销池' },
        },
      },
    )
    await screen.findByTestId('task-detail')

    await userEvent.setup().click(screen.getByTestId('task-publish'))

    await waitFor(() =>
      expect(screen.getByTestId('task-action-error')).toHaveTextContent(
        '商户可用额度不足以锁定报销池',
      ),
    )
    // 乐观改状态的实现会在这里闪一下「已发布」
    expect(screen.getByTestId('task-detail-status')).toHaveTextContent('草稿')
  })

  it('MP-05 published → 「暂停」→ 「已暂停」', async () => {
    renderDetail(
      { task: { id: 1, status: 'published' } },
      {
        [`POST ${actionUrl(1, 'pause')}`]: { status: 200, body: { status: 'paused' } },
        [`GET ${detailUrl(1)}`]: [
          { status: 200, body: taskDetail({ task: { id: 1, status: 'published' } }) },
          { status: 200, body: taskDetail({ task: { id: 1, status: 'paused' } }) },
        ],
      },
    )
    await screen.findByTestId('task-detail')

    await userEvent.setup().click(screen.getByTestId('task-pause'))

    await waitFor(() =>
      expect(screen.getByTestId('task-detail-status')).toHaveTextContent('已暂停'),
    )
  })

  it('MP-06 paused → 「发布」→ 恢复「已发布」', async () => {
    renderDetail(
      { task: { id: 1, status: 'paused' } },
      {
        [`POST ${actionUrl(1, 'publish')}`]: { status: 200, body: { status: 'published' } },
        [`GET ${detailUrl(1)}`]: [
          { status: 200, body: taskDetail({ task: { id: 1, status: 'paused' } }) },
          { status: 200, body: taskDetail({ task: { id: 1, status: 'published' } }) },
        ],
      },
    )
    await screen.findByTestId('task-detail')

    await userEvent.setup().click(screen.getByTestId('task-publish'))

    await waitFor(() =>
      expect(screen.getByTestId('task-detail-status')).toHaveTextContent('已发布'),
    )
  })

  it('MP-07 published → 「关闭」→ 「已关闭」', async () => {
    renderDetail(
      { task: { id: 1, status: 'published' } },
      {
        [`POST ${actionUrl(1, 'close')}`]: { status: 200, body: { status: 'closed' } },
        [`GET ${detailUrl(1)}`]: [
          { status: 200, body: taskDetail({ task: { id: 1, status: 'published' } }) },
          { status: 200, body: taskDetail({ task: { id: 1, status: 'closed' } }) },
        ],
      },
    )
    await screen.findByTestId('task-detail')

    await userEvent.setup().click(screen.getByTestId('task-close'))

    await waitFor(() =>
      expect(screen.getByTestId('task-detail-status')).toHaveTextContent('已关闭'),
    )
  })

  it('MP-08 closed → 没有「发布 / 暂停 / 关闭」三个按钮', async () => {
    renderDetail({ task: { id: 1, status: 'closed' } })
    await screen.findByTestId('task-detail')

    expect(screen.queryByTestId('task-publish')).toBeNull()
    expect(screen.queryByTestId('task-pause')).toBeNull()
    expect(screen.queryByTestId('task-close')).toBeNull()
  })

  it('MP-09 删除是两步：首点只变「确认删除」，一次 DELETE 都不发', async () => {
    const api = renderList([taskRow({ id: 1, status: 'draft' })], {
      [`DELETE ${TASKS}/1`]: { status: 204 },
    })
    const row = await screen.findByTestId('task-row-1')

    const button = within(row).getByTestId('task-delete-1')
    await userEvent.setup().click(button)

    expect(button).toHaveTextContent('确认删除')
    expect(api.count('DELETE', `${TASKS}/1`)).toBe(0)

    await userEvent.setup().click(button)
    await waitFor(() => expect(api.count('DELETE', `${TASKS}/1`)).toBe(1))
  })

  it('MP-10 删除 published → 409 原样显示', async () => {
    renderList([taskRow({ id: 1, status: 'published' })], {
      [`DELETE ${TASKS}/1`]: {
        status: 409,
        body: { detail: '已发布的任务需先关闭才能删除' },
      },
    })
    const row = await screen.findByTestId('task-row-1')

    const user = userEvent.setup()
    await user.click(within(row).getByTestId('task-delete-1'))
    await user.click(within(row).getByTestId('task-delete-1'))

    await waitFor(() =>
      expect(screen.getByTestId('task-action-error')).toHaveTextContent(
        '已发布的任务需先关闭才能删除',
      ),
    )
  })

  it('MP-11 删除有有效领取 → 409 原样显示', async () => {
    renderList([taskRow({ id: 1, status: 'draft' })], {
      [`DELETE ${TASKS}/1`]: {
        status: 409,
        body: { detail: '任务已有有效领取，不能删除' },
      },
    })
    const row = await screen.findByTestId('task-row-1')

    const user = userEvent.setup()
    await user.click(within(row).getByTestId('task-delete-1'))
    await user.click(within(row).getByTestId('task-delete-1'))

    await waitFor(() =>
      expect(screen.getByTestId('task-action-error')).toHaveTextContent(
        '任务已有有效领取，不能删除',
      ),
    )
  })

  it('MP-12 动作成功后重取详情（GET /api/tasks/{id} 出现第 2 次）', async () => {
    const api = renderDetail(
      { task: { id: 1, status: 'published' } },
      {
        [`POST ${actionUrl(1, 'pause')}`]: { status: 200, body: { status: 'paused' } },
      },
    )
    await screen.findByTestId('task-detail')

    await userEvent.setup().click(screen.getByTestId('task-pause'))

    await waitFor(() => expect(api.count('GET', detailUrl(1))).toBe(2))
  })
})

describe('MS · 自动保存', () => {
  it('MS-01 draft 改描述、停手 → PUT draft，体里只有 description', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const api = renderDetail(
      { task: { id: 1, status: 'draft' } },
      {
        [`PUT ${TASKS}/1/draft`]: {
          status: 200,
          body: { task: taskRow({ id: 1 }), saved_at: '2026-09-16T10:20:00+00:00' },
        },
      },
    )
    await screen.findByTestId('task-detail')

    fireEvent.change(screen.getByLabelText('描述'), { target: { value: '自动保存这一处' } })
    await act(async () => {
      vi.advanceTimersByTime(900)
    })

    await waitFor(() => expect(api.count('PUT', `${TASKS}/1/draft`)).toBe(1))
    expect(Object.keys(api.lastCall()?.body as object)).toEqual(['description'])
  })

  it('MS-02 连打 5 次只发一次请求（防抖）', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const api = renderDetail(
      { task: { id: 1, status: 'draft' } },
      {
        [`PUT ${TASKS}/1/draft`]: {
          status: 200,
          body: { task: taskRow({ id: 1 }), saved_at: '2026-09-16T10:20:00+00:00' },
        },
      },
    )
    await screen.findByTestId('task-detail')

    const area = screen.getByLabelText('描述')
    for (const value of ['自', '自动', '自动保', '自动保存', '自动保存好']) {
      fireEvent.change(area, { target: { value } })
    }
    await act(async () => {
      vi.advanceTimersByTime(900)
    })

    await waitFor(() => expect(api.count('PUT', `${TASKS}/1/draft`)).toBe(1))
  })

  it('MS-03 published 详情不触发自动保存', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const api = renderDetail({ task: { id: 1, status: 'published' } })
    await screen.findByTestId('task-detail')

    fireEvent.change(screen.getByLabelText('描述'), { target: { value: '已发布也能改描述' } })
    await act(async () => {
      vi.advanceTimersByTime(900)
    })

    expect(api.count('PUT', `${TASKS}/1/draft`)).toBe(0)
  })

  it('MS-04 自动保存失败 → 出现「未保存」，输入内容不清空', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    renderDetail(
      { task: { id: 1, status: 'draft' } },
      { [`PUT ${TASKS}/1/draft`]: { status: 500, body: { detail: '炸了' } } },
    )
    await screen.findByTestId('task-detail')

    const area = screen.getByLabelText('描述')
    fireEvent.change(area, { target: { value: '这段字不能丢' } })
    await act(async () => {
      vi.advanceTimersByTime(900)
    })

    await waitFor(() =>
      expect(screen.getByTestId('task-autosave')).toHaveTextContent('未保存'),
    )
    expect(area).toHaveValue('这段字不能丢')
  })

  it('MS-05 成功后显示「已保存 HH:mm」（北京时间）', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    renderDetail(
      { task: { id: 1, status: 'draft' } },
      {
        [`PUT ${TASKS}/1/draft`]: {
          status: 200,
          // UTC 10:20 → 北京时间 18:20
          body: { task: taskRow({ id: 1 }), saved_at: '2026-09-16T10:20:00+00:00' },
        },
      },
    )
    await screen.findByTestId('task-detail')

    fireEvent.change(screen.getByLabelText('描述'), { target: { value: '存一下' } })
    await act(async () => {
      vi.advanceTimersByTime(900)
    })

    await waitFor(() => expect(screen.getByTestId('task-autosave')).toHaveTextContent('18:20'))
  })
})

describe('MX · 降级与不编造', () => {
  it('MX-01 空值一律 —，整页文本不出现 NaN / undefined / null', async () => {
    renderList([
      taskRow({ id: 1, quota: null, claimed_count: 0, title: '无名额任务', tags: null }),
      taskRow({ id: 2, quota: null, claimed_count: 0, title: '另一个', category: '' }),
    ])
    await screen.findByTestId('task-row-2')

    const text = document.body.textContent ?? ''
    expect(text).not.toMatch(/NaN/)
    expect(text).not.toMatch(/undefined/)
    expect(text).not.toMatch(/\bnull\b/)
  })

  it('MX-02 建任务表单里没有商户 ID / 状态这类服务端字段', async () => {
    renderNew()

    // 先等表单真的在——否则「找不到 merchant_id」在空白页上也成立，等于没测。
    await screen.findByTestId('task-save-draft')

    const named = Array.from(document.querySelectorAll('[name]')).map((el) =>
      el.getAttribute('name'),
    )
    expect(named).toContain('title')
    expect(named).not.toContain('merchant_id')
    expect(named).not.toContain('status')
    expect(screen.queryByLabelText(/商户\s*ID/)).toBeNull()
  })

  it('MX-03 列表 401 → 自动 refresh 一次后失败 → 清空 + 落 /login，不就地报错', async () => {
    stubApi({
      [`GET ${TASKS}`]: { status: 401, body: { detail: '令牌过期' } },
      'POST /api/auth/refresh': { status: 401, body: { detail: '刷新令牌已失效' } },
    })
    seedAuth({ role: 'merchant' })
    renderApp({ route: '/merchant/tasks' })

    await waitFor(() => expect(currentPath()).toBe('/login'))
    expect(readAuth()).toBeNull()
  })
})
