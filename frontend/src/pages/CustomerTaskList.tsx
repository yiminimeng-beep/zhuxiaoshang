/**
 * 找任务列表：搜索 + 领取。
 * `GET /api/tasks` 免登录；领取需鉴权。列表无 shop_name → 显示 —。
 */

import { useCallback, useEffect, useState, type FormEvent } from 'react'
import { Link, useSearchParams } from 'react-router-dom'

import { apiFetch, failureMessage } from '../api/client'
import { AppBar } from '../components/AppBar'
import { useSession } from '../auth/useSession'
import { payLabel, quotaLabel, type TaskRow } from '../lib/tasks'
import { windowLabel } from '../lib/time'

type LoadState = 'loading' | 'ok' | 'error'

export function CustomerTaskList() {
  const session = useSession()
  const [params, setParams] = useSearchParams()
  const keyword = params.get('keyword') ?? ''

  const [rows, setRows] = useState<TaskRow[]>([])
  const [state, setState] = useState<LoadState>('loading')
  const [attempt, setAttempt] = useState(0)
  const [draft, setDraft] = useState(keyword)
  const [claimed, setClaimed] = useState<Record<number, boolean>>({})
  const [actionError, setActionError] = useState<string | null>(null)

  const query = keyword.trim() ? `?keyword=${encodeURIComponent(keyword.trim())}` : ''
  const path = `/api/tasks${query}`

  const loadKey = `${path}#${attempt}`
  const [loadedKey, setLoadedKey] = useState(loadKey)
  if (loadedKey !== loadKey) {
    setLoadedKey(loadKey)
    setRows([])
    setState('loading')
  }

  useEffect(() => {
    let alive = true
    void (async () => {
      try {
        const data = await apiFetch<{ items?: TaskRow[] }>(path, { auth: false })
        if (!alive) return
        setRows(data.items ?? [])
        setState('ok')
      } catch {
        if (!alive) return
        setState('error')
      }
    })()
    return () => {
      alive = false
    }
  }, [path, attempt])

  const retry = useCallback(() => setAttempt((n) => n + 1), [])

  function search(event: FormEvent) {
    event.preventDefault()
    const next = draft.trim()
    setParams(next ? { keyword: next } : {})
  }

  async function claim(taskId: number) {
    setActionError(null)
    try {
      await apiFetch(`/api/tasks/${taskId}/claim`, { method: 'POST', body: {} })
      setClaimed((prev) => ({ ...prev, [taskId]: true }))
    } catch (error) {
      setActionError(failureMessage(error))
    }
  }

  if (!session) return null

  return (
    <>
      <AppBar session={session} />
      <main className="shell page page--app">
        <p className="crumb">
          <Link className="link" to="/customer">
            返回
          </Link>
        </p>
        <header className="page-head">
          <h1 className="page__title">找任务</h1>
          <p className="lede">领一个任务，再用创作台开工。</p>
        </header>

        <form className="search-row" onSubmit={search}>
          <div className="field search-row__field">
            <label className="field__label" htmlFor="task-keyword">
              关键词
            </label>
            <input
              id="task-keyword"
              className="field__input"
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
            />
          </div>
          <button type="submit" className="btn btn--quiet search-row__submit">
            搜索
          </button>
        </form>

        {actionError ? (
          <p className="note note--error" role="alert">
            {actionError}
          </p>
        ) : null}

        {state === 'error' ? (
          <div className="panel stack" role="alert">
            <p>任务列表暂时取不到。</p>
            <button type="button" className="btn btn--quiet" onClick={retry}>
              重试
            </button>
          </div>
        ) : null}

        {state === 'ok' && rows.length === 0 ? (
          <p className="lede">还没有任务</p>
        ) : null}

        <ul className="task-list">
          {rows.map((task) => {
            const done = claimed[task.id]
            return (
              <li key={task.id} className="task-item" data-testid={`task-row-${task.id}`}>
                <div className="task-item__head">
                  <Link className="task-item__title" to={`/customer/tasks/${task.id}`}>
                    {task.title}
                  </Link>
                  <span className="badge">{payLabel(task.pay_mode)}</span>
                </div>
                <dl className="task-item__facts">
                  <div className="fact">
                    <dt className="fact__key">商户</dt>
                    <dd className="fact__value">—</dd>
                  </div>
                  <div className="fact">
                    <dt className="fact__key">品类</dt>
                    <dd className="fact__value">{task.category || '—'}</dd>
                  </div>
                  <div className="fact">
                    <dt className="fact__key">时间窗</dt>
                    <dd className="fact__value">{windowLabel(task.start_at, task.end_at)}</dd>
                  </div>
                  <div className="fact">
                    <dt className="fact__key">名额</dt>
                    <dd className="fact__value">{quotaLabel(task)}</dd>
                  </div>
                </dl>
                {task.pay_mode === 'user_pay_reimburse' ? (
                  <p className="note">需垫付，过审后报销</p>
                ) : null}
                <div className="row">
                  {done ? (
                    <span className="badge badge--merchant">已领取</span>
                  ) : (
                    <button
                      type="button"
                      className="btn btn--primary"
                      onClick={() => void claim(task.id)}
                    >
                      领取
                    </button>
                  )}
                  <Link className="btn btn--text" to={`/customer/tasks/${task.id}`}>
                    详情
                  </Link>
                </div>
              </li>
            )
          })}
        </ul>
      </main>
    </>
  )
}
