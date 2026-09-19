import { useEffect, useState, type FormEvent } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'

import { ApiError, apiFetch } from '../api/client'
import { useSession } from '../auth/useSession'
import { AdminBar } from '../components/AdminBar'

const USERS = '/api/admin/users'
/** 全局约定 #4：`size` 上限 100 */
const PAGE_SIZE = 100

const ROLES = [
  { value: '', label: '全部角色' },
  { value: 'merchant', label: '商户' },
  { value: 'customer', label: '客户' },
] as const

const STATUSES = [
  { value: '', label: '全部状态' },
  { value: 'active', label: '正常' },
  { value: 'banned', label: '已封禁' },
] as const

const ROLE_LABEL: Record<string, string> = {
  merchant: '商户',
  customer: '客户',
  admin: '管理员',
}
const STATUS_LABEL: Record<string, string> = {
  active: '正常',
  banned: '已封禁',
  deleted: '已注销',
}

interface UserRow {
  id: number
  account: string | null
  nickname: string | null
  role: string
  status: string
  shop_name: string | null
}

interface UserPage {
  items: UserRow[]
  total: number
  page: number
  size: number
}

/**
 * 筛选与页码存在 **URL 查询串**里，不是组件 `useState`。
 *
 * 这样「进详情再返回，筛选还在」是浏览器历史给的，不用另存一份状态——
 * 而且列表本身也顺带可直链、可刷新。
 */
export function UserList() {
  const session = useSession()
  const navigate = useNavigate()
  const [params, setParams] = useSearchParams()

  const keyword = params.get('keyword') ?? ''
  const role = params.get('role') ?? ''
  const status = params.get('status') ?? ''
  const page = Math.max(1, Number(params.get('page') ?? '1') || 1)

  const [items, setItems] = useState<UserRow[]>([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)

  useEffect(() => {
    let alive = true
    setLoading(true)
    setLoadError(null)

    const qs = new URLSearchParams()
    // 关键词原样传：后端已经转义了 `%` `_`，前端再拼一层通配符等于把它拆了
    if (keyword) qs.set('keyword', keyword)
    if (role) qs.set('role', role)
    if (status) qs.set('status', status)
    qs.set('page', String(page))
    qs.set('size', String(PAGE_SIZE))

    void (async () => {
      try {
        const data = await apiFetch<UserPage>(`${USERS}?${qs}`)
        if (!alive) return
        setItems(data.items)
        setTotal(data.total)
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
  }, [keyword, role, status, page])

  /**
   * 三个筛选条件**一起交**。下拉不即时生效是因为这一条查询串管着两件事：
   * 屏幕上是什么、请求打的是什么。分开交的话，选一次角色和选一次状态
   * 各打一次请求，中间那两个状态屏幕上看不出区别，却被记进了浏览器历史——
   * 于是「后退」会退到一个用户从没见过的组合。
   */
  function search(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const data = new FormData(event.currentTarget)
    const next = new URLSearchParams()
    const nextKeyword = String(data.get('keyword') ?? '').trim()
    if (nextKeyword) next.set('keyword', nextKeyword)
    const nextRole = String(data.get('role') ?? '')
    if (nextRole) next.set('role', nextRole)
    const nextStatus = String(data.get('status') ?? '')
    if (nextStatus) next.set('status', nextStatus)
    next.set('page', '1')
    setParams(next)
  }

  function goPage(next: number) {
    const merged = new URLSearchParams(params)
    merged.set('page', String(next))
    setParams(merged)
  }

  function reset() {
    setParams(new URLSearchParams())
  }

  if (!session) return null

  const lastPage = Math.max(1, Math.ceil(total / PAGE_SIZE))

  return (
    <>
      <AdminBar session={session} />
      <main className="shell page">
        <header className="stack">
          <h1 className="page__title">用户管理</h1>
          <p className="lede">按账号、昵称或店名找人。后台只管封禁与解封。</p>
        </header>

        {/* `key` 换一次 = 整张表单跟着 URL 重挂一次：返回列表、点「重置」、
            甚至改地址栏，控件都会自己回到 URL 说的那个样子（URL 是唯一真相） */}
        <form
          key={`${keyword}|${role}|${status}`}
          className="usr-search"
          onSubmit={search}
          aria-label="搜索用户"
        >
          <div className="field">
            <label className="field__label" htmlFor="usr-keyword">
              关键词
            </label>
            <input
              id="usr-keyword"
              name="keyword"
              type="search"
              className="field__input"
              defaultValue={keyword}
              placeholder="账号 / 昵称 / 店名"
            />
          </div>

          <div className="field">
            <label className="field__label" htmlFor="usr-role">
              角色
            </label>
            <select
              id="usr-role"
              name="role"
              className="field__select"
              defaultValue={role}
            >
              {ROLES.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </div>

          <div className="field">
            <label className="field__label" htmlFor="usr-status">
              状态
            </label>
            <select
              id="usr-status"
              name="status"
              className="field__select"
              defaultValue={status}
            >
              {STATUSES.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </div>

          <div className="row usr-search__act">
            <button type="submit" className="btn btn--primary">
              搜索
            </button>
            <button type="button" className="btn btn--quiet" onClick={reset}>
              重置
            </button>
          </div>
        </form>

        {loadError ? (
          <div className="stack" data-testid="usr-load-error">
            <p className="note note--error" role="alert">
              {loadError}
            </p>
          </div>
        ) : loading ? (
          <p className="lede">正在加载…</p>
        ) : items.length === 0 ? (
          <p className="usr-empty" data-testid="usr-empty">
            没有匹配的用户
          </p>
        ) : (
          <>
            <table className="usr-table">
              <thead>
                <tr>
                  <th scope="col">账号</th>
                  <th scope="col">昵称</th>
                  <th scope="col">角色</th>
                  <th scope="col">状态</th>
                  <th scope="col">店名</th>
                </tr>
              </thead>
              <tbody>
                {items.map((row) => (
                  <tr
                    key={row.id}
                    className="usr-row"
                    data-testid={`usr-row-${row.id}`}
                    onClick={() => navigate(`/users/${row.id}`)}
                  >
                    <th scope="row" data-testid="usr-account">
                      <Link to={`/users/${row.id}`} onClick={(event) => event.stopPropagation()}>
                        {row.account ?? '—'}
                      </Link>
                    </th>
                    <td data-testid="usr-nickname">{row.nickname ?? '—'}</td>
                    <td>
                      <span className="badge" data-testid="usr-role">
                        {ROLE_LABEL[row.role] ?? row.role}
                      </span>
                    </td>
                    <td data-testid="usr-status">{STATUS_LABEL[row.status] ?? row.status}</td>
                    <td data-testid="usr-shop">{row.shop_name ?? '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>

            <div className="row usr-pager">
              <button
                type="button"
                className="btn btn--quiet"
                disabled={page <= 1}
                onClick={() => goPage(page - 1)}
              >
                上一页
              </button>
              <span className="usr-pager__at" data-testid="usr-page">
                第 {page} 页 · 共 {total} 人
              </span>
              <button
                type="button"
                className="btn btn--quiet"
                disabled={page >= lastPage}
                onClick={() => goPage(page + 1)}
              >
                下一页
              </button>
            </div>
          </>
        )}
      </main>
    </>
  )
}
