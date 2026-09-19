/**
 * 鉴权 SSE：`EventSource` 带不了 Authorization，必须用 fetch + ReadableStream。
 */

export type SseHandler = (event: string, data: unknown) => void

/** 把 `event: X\\ndata: {...}\\n\\n` 块拆开喂给回调。 */
export function parseSseChunk(buffer: string, onEvent: SseHandler): string {
  const parts = buffer.split('\n\n')
  const rest = parts.pop() ?? ''
  for (const block of parts) {
    let event = 'message'
    const dataLines: string[] = []
    for (const line of block.split('\n')) {
      if (line.startsWith('event:')) event = line.slice(6).trim()
      else if (line.startsWith('data:')) dataLines.push(line.slice(5).trimStart())
    }
    if (dataLines.length === 0) continue
    const raw = dataLines.join('\n')
    let data: unknown = raw
    try {
      data = JSON.parse(raw)
    } catch {
      /* 保持原文 */
    }
    onEvent(event, data)
  }
  return rest
}

/**
 * 读完一条 SSE 响应。网络中断抛错；正常收流结束则 resolve。
 */
export async function readSseResponse(
  response: Response,
  onEvent: SseHandler,
): Promise<void> {
  if (!response.body) throw new Error('SSE 响应没有 body')
  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buf = ''
  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buf += decoder.decode(value, { stream: true })
    buf = parseSseChunk(buf, onEvent)
  }
  if (buf.trim()) parseSseChunk(buf + '\n\n', onEvent)
}
