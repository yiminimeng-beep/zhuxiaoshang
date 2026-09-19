/**
 * 我领取的任务：列表 + 去创作 + 两步放弃。
 */

import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'

import { apiFetch, failureMessage } from '../api/client'
import { AppBar } from '../components/AppBar'
import { useSession } from '../auth/useSession'
import { claimStatusLabel } from '../lib/customer'
import { formatCst } from '../lib/time'
import type { TaskRow } from '../lib/tasks'

interface ClaimRow {
  id: number
  task_id: number
  claimed_at: string | null
  status: string
  task: TaskRow | null
}

type LoadState = 'loading' | 'ok' | 'error'

export function CustomerClaims() {
  const session = useSession()
  const [rows, setRows] = useState<ClaimRow[]>([])
  const [state, setState] = useState<LoadState>('loading')
  const [attempt, setAttempt] = useState(0)
  const [confirmId, setConfirmId] = useState<number | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)

  useEffect(() => {
    let alive = true
    void (async () => {
      try {
        const data = await apiFetch<{ items?: ClaimRow[] }>('/api/me/claims')
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
  }, [attempt])

  const retry = useCallback(() => setAttempt((n) => n + 1), [])

  async function abandon(id: number) {
    if (confirmId !== id) {
      setConfirmId(id)
      return
    }
    setActionError(null)
    try {
      await apiFetch(`/api/me/claims/${id}`, { method: 'DELETE' })
      setRows((prev) => prev.filter((row) => row.id !== id))
      setConfirmId(null)
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
          <h1 className="page__title">我领取的任务</h1>
          <p className="lede">去创作，或放弃不再做的任务。</p>
        </header>

        {actionError ? (
          <p className="note note--error" role="alert">
            {actionError}
          </p>
        ) : null}

        {state === 'error' ? (
          <div className="panel stack" role="alert">
            <p>领取列表暂时取不到。</p>
            <button type="button" className="btn btn--quiet" onClick={retry}>
              重试
            </button>
          </div>
        ) : null}

        {state === 'ok' && rows.length === 0 ? (
          <p className="lede">还没有领取任务</p>
        ) : null}

        <ul className="task-list">
          {rows.map((row) => (
            <li key={row.id} className="task-item" data-testid={`claim-row-${row.id}`}>
              <div className="task-item__head">
                <span className="task-item__title">{row.task?.title ?? '—'}</span>
                <span className="badge">{claimStatusLabel(row.status)}</span>
              </div>
              <dl className="task-item__facts">
                <div className="fact">
                  <dt className="fact__key">商户</dt>
                  <dd className="fact__value">—</dd>
                </div>
                <div className="fact">
                  <dt className="fact__key">领取时间</dt>
                  <dd className="fact__value">{formatCst(row.claimed_at)}</dd>
                </div>
              </dl>
              <div className="row">
                <Link className="btn btn--primary" to={`/customer?task=${row.task_id}`}>
                  去创作
                </Link>
                <button
                  type="button"
                  className="btn btn--quiet"
                  onClick={() => void abandon(row.id)}
                >
                  {confirmId === row.id ? '确认放弃' : '放弃'}
                </button>
              </div>
            </li>
          ))}
        </ul>
      </main>
    </>
  )
}
