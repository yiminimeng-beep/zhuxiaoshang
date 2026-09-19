/**
 * 只有一个出口的 HTTP 客户端：带令牌、`401` 刷新一次并**重放原请求**。
 *
 * 重放这一步最容易漏。写成「刷新成功但整页重载」测试也会绿，
 * 用户看到的是闪一下、数据从头再来。
 */

import { clearSession, readSession, writeSession } from './session'

const BASE = (import.meta.env.VITE_API_BASE as string | undefined) ?? ''

/**
 * 这两个端点自己不参与刷新重试：登录时本来就没有令牌；
 * 刷新失败再去刷新就是死循环。
 */
const NO_REFRESH = ['/api/auth/login', '/api/auth/refresh']

export class ApiError extends Error {
  readonly status: number
  readonly payload: unknown

  constructor(status: number, payload: unknown) {
    super(`请求失败（${status}）`)
    this.name = 'ApiError'
    this.status = status
    this.payload = payload
  }

  /** 后端 `{"detail": "..."}` 里的那句话（403 的「账号已注销」等） */
  get detail(): string | null {
    const detail = (this.payload as { detail?: unknown } | null)?.detail
    return typeof detail === 'string' ? detail : null
  }

  /**
   * 阶梯校验的 422：违规说明在 **`detail.violations`**（一个数组）里，
   * 不是 `detail` 字符串。读错地方的表现是「422 弹了一句 undefined」——
   * 屏幕上看着像有错误提示，实际什么都没说。
   */
  get violations(): string[] {
    const detail = (this.payload as { detail?: unknown } | null)?.detail
    const list = (detail as { violations?: unknown } | null)?.violations
    return Array.isArray(list) ? list.map(String) : []
  }

  /** 422 的字段级错误：`detail[].loc` 的末位就是字段名 */
  get fieldErrors(): Record<string, string> {
    const out: Record<string, string> = {}
    const detail = (this.payload as { detail?: unknown } | null)?.detail
    if (!Array.isArray(detail)) return out
    for (const item of detail) {
      const loc = (item as { loc?: unknown })?.loc
      const msg = (item as { msg?: unknown })?.msg
      if (!Array.isArray(loc) || typeof msg !== 'string') continue
      const cleaned = msg.replace(/^Value error,\s*/i, '')
      const field = String(loc[loc.length - 1] ?? '')
      // 模型级校验常落在 loc=["body"]，文案里却写着字段名——贴回对应输入
      if (field === 'body' || field === '') {
        if (cleaned.includes('start_at')) out.start_at = cleaned
        else if (cleaned.includes('end_at')) out.end_at = cleaned
        else out._form = cleaned
        continue
      }
      if (field) out[field] = cleaned
    }
    return out
  }
}

/**
 * 给用户看的那句话。
 *
 * 后端的 `detail` **优先原样显示**——它比任何前端文案都更清楚这一刀为什么落下
 * （「已发布的任务需先关闭才能删除」这种，前端编不出来）。
 * 没有 `detail` 才退回自己的话。
 */
export function failureMessage(error: unknown, fallback = '操作失败，请稍后再试'): string {
  if (error instanceof ApiError) {
    if (error.detail) return error.detail
    const fields = error.fieldErrors
    if (fields._form) return fields._form
    if (fields.start_at) return fields.start_at
    if (fields.end_at) return fields.end_at
    const first = Object.values(fields)[0]
    if (first) return first
    return `请求失败（${error.status}）`
  }
  return fallback
}

async function parseBody(response: Response): Promise<unknown> {
  const text = await response.text()
  if (!text) return null
  try {
    return JSON.parse(text)
  } catch {
    return text
  }
}

async function send(
  path: string,
  method: string,
  body: unknown,
  withAuth: boolean,
): Promise<unknown> {
  const headers: Record<string, string> = {}
  const session = readSession()
  if (withAuth && session) headers.authorization = `Bearer ${session.access}`
  // FormData 必须让浏览器自己带 multipart boundary；不能手写 content-type
  const isForm = typeof FormData !== 'undefined' && body instanceof FormData
  if (body !== undefined && !isForm) headers['content-type'] = 'application/json'

  const response = await fetch(`${BASE}${path}`, {
    method,
    headers,
    body:
      body === undefined
        ? undefined
        : isForm
          ? (body as FormData)
          : JSON.stringify(body),
  })

  const payload = await parseBody(response)
  if (!response.ok) throw new ApiError(response.status, payload)
  return payload
}

async function tryRefresh(): Promise<boolean> {
  const session = readSession()
  if (!session) return false
  try {
    const data = (await send(
      '/api/auth/refresh',
      'POST',
      { refresh_token: session.refresh },
      false,
    )) as { access_token?: string; refresh_token?: string } | null
    if (!data?.access_token) return false
    // 刷新是新令牌也算「写回本地」的一次状态变更，UI 要跟着知道
    writeSession({
      ...session,
      access: data.access_token,
      refresh: data.refresh_token ?? session.refresh,
    })
    return true
  } catch {
    return false
  }
}

export interface RequestOptions {
  method?: string
  body?: unknown
  /** `false` 表示这个请求不带令牌、也不参与刷新重试（登录 / 刷新自己） */
  auth?: boolean
}

export async function apiFetch<T = unknown>(
  path: string,
  options: RequestOptions = {},
): Promise<T> {
  const method = options.method ?? 'GET'
  const withAuth = options.auth ?? true

  try {
    return (await send(path, method, options.body, withAuth)) as T
  } catch (error) {
    if (!(error instanceof ApiError) || error.status !== 401) throw error
    if (!withAuth || NO_REFRESH.some((prefix) => path.startsWith(prefix))) throw error
    if (!(await tryRefresh())) {
      // 清空即跳转：守卫看到没有登录态，自然把用户送到 /login。
      // 不在这里命令式导航——那样测试里的 MemoryRouter 会被绕过。
      clearSession()
      throw error
    }
    return (await send(path, method, options.body, true)) as T
  }
}

/** 原始 Response（SSE 用）。鉴权与 401 刷新规则与 `apiFetch` 一致。 */
export async function apiStream(
  path: string,
  options: RequestOptions = {},
): Promise<Response> {
  const method = options.method ?? 'GET'
  const withAuth = options.auth ?? true

  const once = async (auth: boolean): Promise<Response> => {
    const headers: Record<string, string> = {}
    const session = readSession()
    if (auth && session) headers.authorization = `Bearer ${session.access}`
    const isForm = typeof FormData !== 'undefined' && options.body instanceof FormData
    if (options.body !== undefined && !isForm) headers['content-type'] = 'application/json'
    return fetch(`${BASE}${path}`, {
      method,
      headers,
      body:
        options.body === undefined
          ? undefined
          : isForm
            ? (options.body as FormData)
            : JSON.stringify(options.body),
    })
  }

  let response = await once(withAuth)
  if (response.status === 401 && withAuth && !NO_REFRESH.some((p) => path.startsWith(p))) {
    if (await tryRefresh()) response = await once(true)
    else {
      clearSession()
      throw new ApiError(401, await parseBody(response))
    }
  }
  if (!response.ok) throw new ApiError(response.status, await parseBody(response))
  return response
}

/** `POST /api/uploads`：字段名必须是 `file`（03 追加 A）。 */
export async function apiUpload<T = unknown>(file: File): Promise<T> {
  const form = new FormData()
  form.append('file', file)
  return apiFetch<T>('/api/uploads', { method: 'POST', body: form })
}
