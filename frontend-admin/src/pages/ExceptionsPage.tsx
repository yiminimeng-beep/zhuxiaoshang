import { useCallback, useEffect, useState } from 'react'

import { ApiError, apiFetch } from '../api/client'
import { useSession } from '../auth/useSession'
import { AdminBar } from '../components/AdminBar'
import { formatCst } from '../format'

const EXC = '/api/admin/exceptions'
const APPEALS = '/api/admin/appeals'
/** 全局约定 #4：`size` 上限 100 */
const PAGE_SIZE = 100

/**
 * 四段各有各的数据源与动作集（见 `admin_exception.py` 的 `EXCEPTION_TYPES`）。
 *
 * 「计数」与「列表」是**两条不同的请求**：计数用 `?size=1` 只取 `total`，
 * 页面上的数字因此是「一共有多少」，不是「这一页有几行」——分页之后
 * 这两个数就不再相等，而队列恰恰是最常见会超过一页的地方。
 */
const SEGMENTS = [
  { type: 'content_review', label: '内容待审' },
  { type: 'ocr_low_confidence', label: '截图低置信' },
  { type: 'ocr_mismatch', label: '数据不符' },
  { type: 'appeal', label: '申诉' },
] as const

type SegType = (typeof SEGMENTS)[number]['type']

const CONTENT_ACTIONS = [
  { action: 'approve', label: '放行' },
  { action: 'discard', label: '作废' },
] as const

const OCR_ACTIONS = [
  { action: 'accept', label: '采纳' },
  { action: 'reject', label: '驳回' },
] as const

const APPEAL_ACTIONS = [
  { action: 'accept', label: '通过' },
  { action: 'reject', label: '驳回' },
] as const

const ACTIONS: Record<SegType, readonly { action: string; label: string }[]> = {
  content_review: CONTENT_ACTIONS,
  ocr_low_confidence: OCR_ACTIONS,
  ocr_mismatch: OCR_ACTIONS,
  appeal: APPEAL_ACTIONS,
}

const KIND_LABEL: Record<string, string> = { copy: '文案', video: '视频' }

/** 四个段的行是四种形状；按段取 id，别猜「第几个数字是 id」 */
const ID_KEY: Record<SegType, string> = {
  content_review: 'job_id',
  ocr_low_confidence: 'ocr_id',
  ocr_mismatch: 'ocr_id',
  appeal: 'appeal_id',
}

interface Row {
  job_id?: number
  task_id?: number
  user_id?: number
  kind?: string
  status?: string
  fail_reason?: string | null
  ocr_id?: number
  post_id?: number
  image_url?: string
  confidence?: number
  mismatch_flag?: boolean
  model?: string
  appeal_id?: number
  reason?: string
  created_at: string
}

interface Page {
  items: Row[]
  total: number
}

function rowId(type: SegType, row: Row): number {
  return Number((row as unknown as Record<string, unknown>)[ID_KEY[type]] ?? 0)
}

function resolvePath(type: SegType, id: number): string {
  if (type === 'appeal') return `${APPEALS}/${id}/decide`
  if (type === 'content_review') return `${EXC}/content/${id}/resolve`
  return `${EXC}/ocr/${id}/resolve`
}

export function ExceptionsPage() {
  const session = useSession()
  const [type, setType] = useState<SegType>('content_review')
  const [counts, setCounts] = useState<Partial<Record<SegType, number>>>({})
  const [items, setItems] = useState<Row[]>([])
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [notes, setNotes] = useState<Record<number, string>>({})
  const [brokenThumbs, setBrokenThumbs] = useState<number[]>([])
  /** 段内重取（被别处抢先处理时用），换段本身由 `type` 触发 */
  const [token, setToken] = useState(0)

  const loadCount = useCallback(async (which: SegType) => {
    try {
      const data = await apiFetch<Page>(`${EXC}?type=${which}&size=1`)
      setCounts((prev) => ({ ...prev, [which]: data.total }))
    } catch {
      // 计数是辅助信息：取不到就不显示，不因为它把整页变成错误态
    }
  }, [])

  // 四段计数各取一次。**不跟着当前段走**——切回已取过的段不该再打一次，
  // 否则屏幕上会先闪一个 0（旧值被清掉了）再跳回真数
  useEffect(() => {
    void Promise.all(SEGMENTS.map((segment) => loadCount(segment.type)))
  }, [loadCount])

  useEffect(() => {
    let alive = true
    setLoading(true)
    setLoadError(null)

    void (async () => {
      try {
        const data = await apiFetch<Page>(`${EXC}?type=${type}&size=${PAGE_SIZE}`)
        if (alive) setItems(data.items)
      } catch (error) {
        if (!alive) return
        // 401 时令牌已被清空，守卫会把人送到 /login——这里不要再写状态
        if (error instanceof ApiError && error.status === 401) return
        setLoadError(
          error instanceof ApiError && error.detail ? error.detail : '加载失败，请稍后再试',
        )
      } finally {
        if (alive) setLoading(false)
      }
    })()

    return () => {
      alive = false
    }
  }, [type, token])

  async function act(row: Row, action: string) {
    if (busy) return
    const id = rowId(type, row)
    setBusy(true)
    setActionError(null)

    const note = (notes[id] ?? '').trim()
    // 申诉的动作体字段是 `admin_note`（`DecideIn` 带 `extra="forbid"`，传 `note` 直接 422）
    const body: Record<string, unknown> = { action }
    if (type === 'appeal' && note) body.admin_note = note

    try {
      await apiFetch(resolvePath(type, id), { method: 'POST', body })
      // 队列：处理完就离开清单。这里**不重取列表**——重取在屏幕上看起来
      // 一模一样，却会把别人刚处理的、你还没看见的那几条一起换掉
      setItems((prev) => prev.filter((item) => rowId(type, item) !== id))
      setCounts((prev) => ({
        ...prev,
        [type]: Math.max(0, (prev[type] ?? 1) - 1),
      }))
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) return
      setActionError(
        error instanceof ApiError && error.detail ? error.detail : '操作失败，请稍后再试',
      )
      // 409 = 别人先动过手：这一行已经不在真实队列里了，重取该段才看得到
      if (error instanceof ApiError && error.status === 409) {
        setToken((n) => n + 1)
        void loadCount(type)
      }
    } finally {
      setBusy(false)
    }
  }

  function thumbFailed(id: number) {
    setBrokenThumbs((prev) => (prev.includes(id) ? prev : [...prev, id]))
  }

  if (!session) return null

  return (
    <>
      <AdminBar session={session} />
      <main className="shell page">
        <header className="stack">
          <h1 className="page__title">异常处理</h1>
          <p className="lede">
            四类需要人工过目的单子。放行与作废都会改内容状态，动作会记进审计。
          </p>
        </header>

        <div className="exc-segs" role="group" aria-label="异常类型">
          {SEGMENTS.map((segment) => {
            const on = segment.type === type
            return (
              <button
                key={segment.type}
                type="button"
                className={`exc-seg${on ? ' exc-seg--on' : ''}`}
                aria-pressed={on}
                onClick={() => {
                  setActionError(null)
                  setType(segment.type)
                }}
                data-testid={`exc-seg-${segment.type}`}
              >
                <span className="exc-seg__label">{segment.label}</span>
                <span className="exc-seg__count" data-testid={`exc-count-${segment.type}`}>
                  {counts[segment.type] ?? '—'}
                </span>
              </button>
            )
          })}
        </div>

        {loadError ? (
          <p className="note note--error" role="alert" data-testid="exc-load-error">
            {loadError}
          </p>
        ) : null}
        {actionError ? (
          <p className="note note--error" role="alert" data-testid="exc-error">
            {actionError}
          </p>
        ) : null}

        {loading ? (
          <p className="lede">正在加载…</p>
        ) : loadError ? null : items.length === 0 ? (
          <p className="exc-empty" data-testid="exc-empty">
            这一队清空了
          </p>
        ) : (
          <ul className="exc-list">
            {items.map((row) => {
              const id = rowId(type, row)
              return (
                <li key={`${type}-${id}`} className="exc-row" data-testid={`exc-row-${id}`}>
                  <div className="exc-row__facts">
                    {type === 'content_review' ? (
                      <>
                        <span className="exc-row__id" data-testid="exc-id">
                          {row.job_id}
                        </span>
                        <span className="badge" data-testid="exc-kind">
                          {KIND_LABEL[row.kind ?? ''] ?? row.kind}
                        </span>
                        <span data-testid="exc-task">任务 {row.task_id}</span>
                        <span data-testid="exc-user">用户 {row.user_id}</span>
                        <span className="exc-row__time" data-testid="exc-time">
                          {formatCst(row.created_at)}
                        </span>
                      </>
                    ) : null}

                    {type === 'ocr_low_confidence' || type === 'ocr_mismatch' ? (
                      <>
                        <span className="exc-row__id" data-testid="exc-id">
                          {row.ocr_id}
                        </span>
                        {brokenThumbs.includes(id) ? (
                          // 破图比空白更像「坏掉了」；留 URL 至少能手工打开看
                          <span className="exc-thumb exc-thumb--broken" data-testid={`exc-thumb-fallback-${id}`}>
                            {row.image_url}
                          </span>
                        ) : (
                          <img
                            className="exc-thumb"
                            src={row.image_url}
                            alt=""
                            data-testid={`exc-thumb-${id}`}
                            onError={() => thumbFailed(id)}
                          />
                        )}
                        <span data-testid="exc-post">作品 {row.post_id}</span>
                        <span data-testid="exc-confidence">
                          置信度 {Math.round((row.confidence ?? 0) * 100)}%
                        </span>
                        <span data-testid="exc-model">{row.model}</span>
                        {row.mismatch_flag ? <span className="badge">数据不符</span> : null}
                        <span className="exc-row__time" data-testid="exc-time">
                          {formatCst(row.created_at)}
                        </span>
                      </>
                    ) : null}

                    {type === 'appeal' ? (
                      <>
                        <span className="exc-row__id" data-testid="exc-id">
                          {row.appeal_id}
                        </span>
                        <span data-testid="exc-post">作品 {row.post_id}</span>
                        <span data-testid="exc-user">用户 {row.user_id}</span>
                        <span className="exc-row__time" data-testid="exc-time">
                          {formatCst(row.created_at)}
                        </span>
                      </>
                    ) : null}
                  </div>

                  {/* 申诉的 reason 是用户写的原文：**不截断**，截了就丢掉判断依据 */}
                  {type === 'appeal' ? (
                    <p className="exc-row__reason" data-testid="exc-reason">
                      {row.reason}
                    </p>
                  ) : null}

                  <div className="exc-row__act">
                    {type === 'appeal' ? (
                      <input
                        type="text"
                        className="field__input exc-row__note"
                        value={notes[id] ?? ''}
                        onChange={(event) =>
                          setNotes((prev) => ({ ...prev, [id]: event.target.value }))
                        }
                        placeholder="裁决备注（选填，会记进审计）"
                        aria-label="裁决备注"
                        data-testid={`exc-note-${id}`}
                      />
                    ) : null}
                    {ACTIONS[type].map((option) => (
                      <button
                        key={option.action}
                        type="button"
                        className="btn btn--quiet"
                        disabled={busy}
                        onClick={() => void act(row, option.action)}
                        data-testid={`exc-act-${id}-${option.action}`}
                      >
                        {option.label}
                      </button>
                    ))}
                  </div>
                </li>
              )
            })}
          </ul>
        )}
      </main>
    </>
  )
}
