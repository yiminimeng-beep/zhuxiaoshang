import { useCallback, useEffect, useState } from 'react'

import { ApiError, apiFetch } from '../api/client'
import { useSession } from '../auth/useSession'
import { AdminBar } from '../components/AdminBar'
import { formatCst } from '../format'

const LIST_PATH = '/api/admin/feedback'
/** 全局约定 #4：`size` 上限 100，后端会对 101 报 422 */
const PAGE_SIZE = 100

const ROLES = [
  { value: '', label: '全部角色' },
  { value: 'merchant', label: '商家' },
  { value: 'customer', label: '用户' },
] as const

const STATUSES = [
  { value: '', label: '全部状态' },
  { value: 'open', label: '未处理' },
  { value: 'resolved', label: '已处理' },
] as const

const CATEGORIES = [
  { value: '', label: '全部类型' },
  { value: 'bug', label: '功能异常' },
  { value: 'suggestion', label: '功能建议' },
  { value: 'other', label: '其他' },
] as const

const ROLE_LABEL: Record<string, string> = { merchant: '商家', customer: '用户' }
const CATEGORY_LABEL: Record<string, string> = {
  bug: '功能异常',
  suggestion: '功能建议',
  other: '其他',
}
const STATUS_LABEL: Record<string, string> = { open: '未处理', resolved: '已处理' }

interface FeedbackItem {
  id: number
  user_id: number
  account: string | null
  nickname: string | null
  role: string
  category: string
  content: string
  contact: string | null
  status: string
  resolved_at: string | null
  resolved_by: number | null
  created_at: string
}

interface FeedbackPage {
  items: FeedbackItem[]
  total: number
  page: number
  size: number
}

/** 列表里的一行摘要。完整内容在展开区里，一个字都不丢。 */
function excerpt(content: string, limit = 60): string {
  const flat = content.replace(/\s+/g, ' ').trim()
  return flat.length > limit ? `${flat.slice(0, limit)}…` : flat
}

interface Filters {
  role: string
  status: string
  category: string
}

/** 默认筛「未处理」——这是运营打开就想要的队列，不是全部历史 */
const DEFAULT_FILTERS: Filters = { role: '', status: 'open', category: '' }

export function FeedbackInbox() {
  const session = useSession()
  const [filters, setFilters] = useState<Filters>(DEFAULT_FILTERS)
  const [items, setItems] = useState<FeedbackItem[]>([])
  const [total, setTotal] = useState(0)
  const [openCount, setOpenCount] = useState<number | null>(null)
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [expanded, setExpanded] = useState<number | null>(null)
  const [busyId, setBusyId] = useState<number | null>(null)
  const [rowErrors, setRowErrors] = useState<Record<number, string>>({})
  const [reloadToken, setReloadToken] = useState(0)
  // 「未处理 N」单独一条计数请求：它**不跟筛选条走**（见下面注释），
  // 但每次列表重载、以及每次处理动作之后都要跟着动，故有自己的 token
  const [countToken, setCountToken] = useState(0)

  const reload = useCallback(() => {
    setReloadToken((n) => n + 1)
    setCountToken((n) => n + 1)
  }, [])

  // 列表：跟着筛选走
  useEffect(() => {
    let alive = true
    setLoading(true)
    setLoadError(null)

    const params = new URLSearchParams()
    if (filters.role) params.set('role', filters.role)
    if (filters.status) params.set('status', filters.status)
    if (filters.category) params.set('category', filters.category)
    params.set('size', String(PAGE_SIZE))

    void (async () => {
      try {
        const data = await apiFetch<FeedbackPage>(`${LIST_PATH}?${params}`)
        if (!alive) return
        setItems(data.items)
        setTotal(data.total)
      } catch (error) {
        if (!alive) return
        // 401 时令牌已被清空，守卫会把用户送到 /login——这里不要再写状态
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
  }, [filters, reloadToken])

  // 「未处理 N」是**全局**待办数，不跟着筛选条走：它是「还剩多少没做」，
  // 筛到某个类型时它不该变小，否则一筛就看不见积压了。
  useEffect(() => {
    let alive = true
    void (async () => {
      try {
        const data = await apiFetch<FeedbackPage>(`${LIST_PATH}?status=open&size=1`)
        if (alive) setOpenCount(data.total)
      } catch {
        // 这个数字是辅助信息，取不到就不显示，不因为它把整页变成错误态
        if (alive) setOpenCount(null)
      }
    })()
    return () => {
      alive = false
    }
  }, [countToken])

  function patch(next: Partial<Filters>) {
    setNotice(null)
    setFilters((prev) => ({ ...prev, ...next }))
  }

  async function act(item: FeedbackItem, action: 'resolve' | 'reopen') {
    if (busyId !== null) return
    setBusyId(item.id)
    setNotice(null)
    setRowErrors((prev) => {
      const next = { ...prev }
      delete next[item.id]
      return next
    })
    try {
      const data = await apiFetch<{
        id: number
        status: string
        resolved_at: string | null
        resolved_by: number | null
      }>(`${LIST_PATH}/${item.id}/${action}`, { method: 'POST' })

      // 就地改这一行——**不重取列表**（spec：不整页刷新）
      setItems((prev) =>
        prev.map((row) =>
          row.id === item.id
            ? {
                ...row,
                status: data.status,
                resolved_at: data.resolved_at,
                resolved_by: data.resolved_by,
              }
            : row,
        ),
      )
      // 待办数是另一条独立的小请求，跟着动一下即可（列表本身不重取）
      setCountToken((n) => n + 1)
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) return
      if (error instanceof ApiError && error.status === 409) {
        // 别人先处理了：这一行在「未处理」筛选下会消失，逐行提示会跟着没。
        // 所以升到页面级提示，并把列表重新拉一遍拿到真实状态。
        setNotice('该反馈已被其他管理员处理，列表已刷新')
        reload()
      } else if (error instanceof ApiError && error.detail) {
        setRowErrors((prev) => ({ ...prev, [item.id]: error.detail as string }))
      } else {
        setRowErrors((prev) => ({ ...prev, [item.id]: '操作失败，请稍后再试' }))
      }
    } finally {
      setBusyId(null)
    }
  }

  if (!session) return null

  const overflowed = total > items.length

  return (
    <>
      <AdminBar session={session} />
      <main className="shell page">
        <header className="stack">
          <h1 className="page__title">反馈收件箱</h1>
          <p className="lede">商家与用户提交的问题都在这儿，按角色可筛。</p>
        </header>

        <section className="fb-filters" aria-label="筛选">
          <div className="field">
            <label className="field__label" htmlFor="fb-role">
              角色
            </label>
            <select
              id="fb-role"
              className="field__select"
              value={filters.role}
              onChange={(event) => patch({ role: event.target.value })}
            >
              {ROLES.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </div>

          <div className="field">
            <label className="field__label" htmlFor="fb-status">
              处理状态
            </label>
            <select
              id="fb-status"
              className="field__select"
              value={filters.status}
              onChange={(event) => patch({ status: event.target.value })}
            >
              {STATUSES.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </div>

          <div className="field">
            <label className="field__label" htmlFor="fb-category">
              类型
            </label>
            <select
              id="fb-category"
              className="field__select"
              value={filters.category}
              onChange={(event) => patch({ category: event.target.value })}
            >
              {CATEGORIES.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </div>

          {openCount === null ? null : (
            <p className="fb-count" data-testid="fb-count">
              未处理 {openCount}
            </p>
          )}
        </section>

        {notice ? (
          <p className="note note--ok" role="status" data-testid="fb-notice">
            {notice}
          </p>
        ) : null}

        {loadError ? (
          <div className="stack" data-testid="fb-load-error">
            <p className="note note--error" role="alert">
              {loadError}
            </p>
            <div className="row">
              <button type="button" className="btn btn--quiet" onClick={reload}>
                重试
              </button>
            </div>
          </div>
        ) : loading ? (
          <p className="lede">正在加载…</p>
        ) : items.length === 0 ? (
          <p className="fb-empty" data-testid="fb-empty">
            暂无反馈
          </p>
        ) : (
          <div className="stack">
            <ul className="fb-list">
              {items.map((item) => {
                const isOpen = expanded === item.id
                const resolved = item.status === 'resolved'
                return (
                  <li
                    key={item.id}
                    className="fb-row"
                    data-testid={`fb-row-${item.id}`}
                    data-open={isOpen ? 'true' : 'false'}
                  >
                    <div className="fb-row__line">
                      <button
                        type="button"
                        className="fb-row__main"
                        aria-expanded={isOpen}
                        aria-controls={`fb-detail-${item.id}`}
                        onClick={() => setExpanded(isOpen ? null : item.id)}
                        data-testid={`fb-open-${item.id}`}
                      >
                        <span
                          className={`badge${item.role === 'merchant' ? ' badge--merchant' : ''}`}
                          data-testid={`fb-role-${item.id}`}
                        >
                          {ROLE_LABEL[item.role] ?? item.role}
                        </span>
                        <span className="fb-row__cat" data-testid={`fb-category-${item.id}`}>
                          {CATEGORY_LABEL[item.category] ?? item.category}
                        </span>
                        <span className="fb-row__excerpt">
                          {excerpt(item.content)}
                        </span>
                        <span className="fb-row__who" data-testid={`fb-who-${item.id}`}>
                          {item.account ?? '—'}
                          {item.nickname ? ` · ${item.nickname}` : ''}
                        </span>
                        <span className="fb-row__time" data-testid={`fb-time-${item.id}`}>
                          {formatCst(item.created_at)}
                        </span>
                        <span
                          className={`badge${resolved ? ' badge--resolved' : ''}`}
                          data-testid={`fb-status-${item.id}`}
                        >
                          {STATUS_LABEL[item.status] ?? item.status}
                        </span>
                      </button>

                      <div className="fb-row__act">
                        <button
                          type="button"
                          className="btn btn--quiet"
                          disabled={busyId !== null}
                          onClick={() => void act(item, resolved ? 'reopen' : 'resolve')}
                          data-testid={`fb-action-${item.id}`}
                        >
                          {busyId === item.id
                            ? '处理中…'
                            : resolved
                              ? '撤销已处理'
                              : '标记已处理'}
                        </button>
                      </div>
                    </div>

                    {isOpen ? (
                      <div
                        className="fb-row__detail"
                        id={`fb-detail-${item.id}`}
                        data-testid={`fb-detail-${item.id}`}
                      >
                        {/* 完整内容，不截断、不丢字 */}
                        <p className="fb-row__body">{item.content}</p>
                        <div className="fb-row__meta">
                          <span>联系方式：{item.contact ?? '未留'}</span>
                          <span>提交账号：{item.account ?? '—'}</span>
                          <span>提交时间：{formatCst(item.created_at)}</span>
                        </div>
                      </div>
                    ) : null}

                    {rowErrors[item.id] ? (
                      <div className="fb-row__errors">
                        <p className="note note--error" role="alert">
                          {rowErrors[item.id]}
                        </p>
                      </div>
                    ) : null}
                  </li>
                )
              })}
            </ul>

            {overflowed ? (
              <p className="fb-more" data-testid="fb-more">
                仅显示前 {items.length} 条，共 {total} 条——请用筛选缩小范围。
              </p>
            ) : null}
          </div>
        )}
      </main>
    </>
  )
}
