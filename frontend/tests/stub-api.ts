/**
 * `global.fetch` 桩。响应体按 fixture 的固定形状给（spec：不依赖后端在线）。
 *
 * 每次调用都记进 `calls`——「不带 role」「原路径出现 2 次」这类断言
 * 全靠它，而不是靠看屏幕上的字。
 *
 * 没打桩的请求回 **599**（不是 404）：这条用例要是漏打了桩，
 * 红得会很响，而不是伪装成一个正常的 404 混过去。
 */

import { vi } from 'vitest'

export interface StubResponse {
  status: number
  body?: unknown
  /** SSE 原文；有则回 `text/event-stream` ReadableStream */
  sse?: string
}

/** 给数组 = 按顺序逐次返回（如 `[401, 200]` 模拟「先过期、刷新后成功」） */
export type StubSpec = StubResponse | StubResponse[]

export type StubMap = Record<string, StubSpec>

export interface RecordedCall {
  method: string
  path: string
  /** 路径 + 查询串。同一路径带不同查询串的请求（如「计数」vs「列表」）靠它区分 */
  url: string
  body: unknown
  headers: Record<string, string>
}

export interface StubApi {
  calls: RecordedCall[]
  /** 出入参都接受「路径」或「路径+查询串」；后者用于区分同路径的不同请求 */
  count(method: string, pathOrUrl: string): number
  callsTo(method: string, pathOrUrl: string): RecordedCall[]
  lastCall(): RecordedCall | undefined
}

export const UNSTUBBED = 599

function stubKey(method: string, path: string): string {
  return `${method.toUpperCase()} ${path}`
}

function readHeaders(init?: RequestInit): Record<string, string> {
  const raw = init?.headers
  const out: Record<string, string> = {}
  if (!raw) return out
  if (Array.isArray(raw)) {
    for (const [k, v] of raw) out[String(k).toLowerCase()] = String(v)
    return out
  }
  const maybeIterable = raw as { forEach?: unknown }
  if (typeof maybeIterable.forEach === 'function') {
    ;(raw as Headers).forEach((v, k) => {
      out[String(k).toLowerCase()] = String(v)
    })
    return out
  }
  for (const [k, v] of Object.entries(raw as Record<string, string>)) {
    out[k.toLowerCase()] = String(v)
  }
  return out
}

/**
 * 不依赖环境的 `Response` 替身。
 *
 * jsdom 不实现 fetch / Response，各版本对这几个全局的暴露也不一致；
 * 我们的客户端只用到 `ok` / `status` / `json()`，就只造这三样。
 */
function makeResponse(body: unknown, status: number, sse?: string): Response {
  if (sse !== undefined) {
    const encoder = new TextEncoder()
    const stream = new ReadableStream({
      start(controller) {
        controller.enqueue(encoder.encode(sse))
        controller.close()
      },
    })
    return {
      ok: status >= 200 && status < 300,
      status,
      statusText: '',
      headers: {
        get: (name: string) =>
          name.toLowerCase() === 'content-type' ? 'text/event-stream' : null,
      },
      body: stream,
      json: async () => null,
      text: async () => sse,
    } as unknown as Response
  }
  const text = body === undefined ? '' : JSON.stringify(body)
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: '',
    headers: { get: () => 'application/json' },
    json: async () => (text === '' ? null : JSON.parse(text)),
    text: async () => text,
  } as unknown as Response
}

export function stubApi(map: StubMap = {}): StubApi {
  const queue = new Map<string, StubResponse[]>()
  for (const [k, spec] of Object.entries(map)) {
    queue.set(k, Array.isArray(spec) ? [...spec] : [spec])
  }

  const calls: RecordedCall[] = []

  const fetchMock = vi.fn(async (input: unknown, init?: RequestInit) => {
    const url =
      typeof input === 'string'
        ? input
        : input instanceof URL
          ? input.pathname
          : String((input as { url?: string }).url ?? '')

    const method = (
      init?.method ?? (input as { method?: string })?.method ?? 'GET'
    ).toUpperCase()
    const path = url.split('?')[0]

    let body: unknown
    if (typeof FormData !== 'undefined' && init?.body instanceof FormData) {
      body = init.body
    } else if (typeof init?.body === 'string' && init.body.length > 0) {
      try {
        body = JSON.parse(init.body)
      } catch {
        body = init.body
      }
    }

    const key = stubKey(method, path)
    calls.push({ method, path, url, body, headers: readHeaders(init) })

    // 先按「方法 + 完整 url（含查询串）」精确匹配，再退回「方法 + 路径」。
    // 同一路径带不同查询串的请求（如反馈收件箱的「计数」vs「列表」）靠前者区分；
    // 只写了路径的桩仍然命中，不必逐条补查询串。
    const spec = queue.get(`${method} ${url}`) ?? queue.get(key)
    if (!spec || spec.length === 0) {
      return makeResponse({ detail: `未打桩的请求：${key}` }, UNSTUBBED)
    }
    // 多条时逐次消耗；只剩一条时反复使用（「一直是 500」这种要用得上）
    const step = spec.length > 1 ? spec.shift()! : spec[0]
    return makeResponse(step.body, step.status, step.sse)
  })

  vi.stubGlobal('fetch', fetchMock)

  const matches = (c: RecordedCall, method: string, pathOrUrl: string) =>
    c.method === method.toUpperCase() && (c.path === pathOrUrl || c.url === pathOrUrl)

  const api: StubApi = {
    calls,
    count: (method, pathOrUrl) => api.callsTo(method, pathOrUrl).length,
    callsTo: (method, pathOrUrl) => calls.filter((c) => matches(c, method, pathOrUrl)),
    lastCall: () => calls[calls.length - 1],
  }
  return api
}
