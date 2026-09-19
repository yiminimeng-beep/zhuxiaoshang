/**
 * 商户待审：客户回填的作品。通过 / 驳回走 04 已有端点。
 */

import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'

import { apiFetch, failureMessage } from '../api/client'
import { AppBar } from '../components/AppBar'
import { useSession } from '../auth/useSession'

interface ReviewItem {
  id: number
  platform: string
  post_url: string
  post_title: string | null
  status: string
  countdown_seconds: number
  latest_snapshot: { engagement: number } | null
}

const PLATFORM: Record<string, string> = {
  xhs: '小红书',
  douyin: '抖音',
  kuaishou: '快手',
  bilibili: 'B 站',
  channels: '视频号',
}

type LoadState = 'loading' | 'ok' | 'error'

export function MerchantReviews() {
  const session = useSession()
  const [rows, setRows] = useState<ReviewItem[]>([])
  const [state, setState] = useState<LoadState>('loading')
  const [attempt, setAttempt] = useState(0)
  const [note, setNote] = useState<string | null>(null)
  const [rejectFor, setRejectFor] = useState<number | null>(null)
  const [confirmFor, setConfirmFor] = useState<number | null>(null)
  const [reason, setReason] = useState('')
  const [busyId, setBusyId] = useState<number | null>(null)

  useEffect(() => {
    let alive = true
    void (async () => {
      try {
        const data = await apiFetch<{ items?: ReviewItem[] }>('/api/merchant/reviews')
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

  async function approve(id: number) {
    setNote(null)
    setBusyId(id)
    try {
      await apiFetch(`/api/merchant/reviews/${id}/approve`, { method: 'POST' })
      setRows((prev) => prev.filter((row) => row.id !== id))
    } catch (error) {
      setNote(failureMessage(error))
    } finally {
      setBusyId(null)
    }
  }

  async function reject(id: number) {
    const text = reason.trim()
    if (text.length < 10) {
      setNote('驳回理由须 10 字以上')
      return
    }
    setNote(null)
    setBusyId(id)
    try {
      await apiFetch(`/api/merchant/reviews/${id}/reject`, {
        method: 'POST',
        body: { reason: text },
      })
      setRows((prev) => prev.filter((row) => row.id !== id))
      setRejectFor(null)
      setReason('')
    } catch (error) {
      setNote(failureMessage(error))
    } finally {
      setBusyId(null)
    }
  }

  if (!session) return null

  return (
    <>
      <AppBar session={session} />
      <main className="shell page page--app">
        <p className="crumb">
          <Link className="link" to="/merchant">
            返回
          </Link>
        </p>
        <header className="page-head">
          <h1 className="page__title">待我审核</h1>
          <p className="lede">客户回填的作品。72 小时不审会自动通过。</p>
        </header>

        {note ? (
          <p className="note note--error" role="alert" data-testid="review-note">
            {note}
          </p>
        ) : null}

        {state === 'error' ? (
          <div className="panel stack" role="alert">
            <p>审核列表暂时取不到。</p>
            <button type="button" className="btn btn--quiet" onClick={retry}>
              重试
            </button>
          </div>
        ) : null}

        {state === 'ok' && rows.length === 0 ? (
          <p className="lede">还没有待审作品</p>
        ) : null}

        {state === 'ok' && rows.length > 0 ? (
          <ul className="task-list" data-testid="review-list">
            {rows.map((row) => (
              <li key={row.id} className="task-item" data-testid={`review-${row.id}`}>
                <div className="task-item__head">
                  <span className="task-item__title">
                    {row.post_title?.trim() || `作品 #${row.id}`}
                  </span>
                  <span className="badge">{PLATFORM[row.platform] ?? row.platform}</span>
                </div>
                <p className="lede">
                  互动 {row.latest_snapshot?.engagement ?? '—'}
                  {' · '}
                  <a href={row.post_url} target="_blank" rel="noopener noreferrer">
                    打开链接
                  </a>
                </p>
                <div className="studio-actions">
                  <button
                    type="button"
                    className="btn btn--primary"
                    disabled={busyId === row.id}
                    data-testid={`review-approve-${row.id}`}
                    onClick={() => {
                      setConfirmFor(row.id)
                      setRejectFor(null)
                    }}
                  >
                    通过
                  </button>
                  <button
                    type="button"
                    className="btn btn--quiet"
                    data-testid={`review-reject-${row.id}`}
                    onClick={() => {
                      setRejectFor(row.id)
                      setReason('')
                    }}
                  >
                    驳回
                  </button>
                </div>
                {confirmFor === row.id ? (
                  <div className="stack" role="dialog" data-testid={`review-confirm-${row.id}`}>
                    <p>确定通过这篇作品？通过后按互动量峰值结算奖励，不能在这里撤销。</p>
                    <div className="studio-actions">
                      <button
                        type="button"
                        className="btn btn--primary"
                        disabled={busyId === row.id}
                        data-testid={`review-confirm-send-${row.id}`}
                        onClick={() => void approve(row.id)}
                      >
                        确定通过
                      </button>
                      <button
                        type="button"
                        className="btn btn--quiet"
                        data-testid={`review-confirm-cancel-${row.id}`}
                        onClick={() => setConfirmFor(null)}
                      >
                        取消
                      </button>
                    </div>
                  </div>
                ) : null}
                {rejectFor === row.id ? (
                  <div className="stack">
                    <label className="field__label" htmlFor={`reject-${row.id}`}>
                      驳回理由（至少 10 字）
                    </label>
                    <textarea
                      id={`reject-${row.id}`}
                      className="field__area"
                      value={reason}
                      data-testid={`review-reason-${row.id}`}
                      onChange={(event) => setReason(event.target.value)}
                    />
                    <button
                      type="button"
                      className="btn btn--primary"
                      disabled={busyId === row.id}
                      data-testid={`review-reject-send-${row.id}`}
                      onClick={() => void reject(row.id)}
                    >
                      确认驳回
                    </button>
                  </div>
                ) : null}
              </li>
            ))}
          </ul>
        ) : null}
      </main>
    </>
  )
}
