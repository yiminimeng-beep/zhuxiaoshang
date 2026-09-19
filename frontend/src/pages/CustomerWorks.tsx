/**
 * 我的创作：内容工坊记录列表，点一行回到工坊。
 */

import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'

import { apiFetch } from '../api/client'
import { AppBar } from '../components/AppBar'
import { useSession } from '../auth/useSession'
import { formatCst } from '../lib/time'

interface WorkRow {
  id: number
  kind: string
  status: string
  fail_reason: string | null
  created_at: string | null
}

const STATUS_LABEL: Record<string, string> = {
  created: '预检中',
  guarding: '预检中',
  guard_failed: '预检未通过',
  chatting: '对话中',
  generating: '生成中',
  judging: '把关中',
  ready: '已完成',
  need_review: '待人工',
  failed: '失败',
}

function statusLabel(status: string): string {
  return STATUS_LABEL[status] ?? status
}

function kindLabel(kind: string): string {
  if (kind === 'copy') return '文案'
  if (kind === 'video') return '视频'
  return kind
}

type LoadState = 'loading' | 'ok' | 'error'

export function CustomerWorks() {
  const session = useSession()
  const [rows, setRows] = useState<WorkRow[]>([])
  const [state, setState] = useState<LoadState>('loading')
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    let alive = true
    void (async () => {
      try {
        const data = await apiFetch<{ items?: WorkRow[] }>('/api/me/jobs?page=1&size=100')
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
          <h1 className="page__title">我的创作</h1>
          <p className="lede">点开一条，回到当时的内容工坊。</p>
        </header>

        {state === 'error' ? (
          <div className="panel stack" role="alert">
            <p>创作列表暂时取不到。</p>
            <button type="button" className="btn btn--quiet" onClick={retry}>
              重试
            </button>
          </div>
        ) : null}

        {state === 'ok' && rows.length === 0 ? (
          <p className="lede">
            还没有创作{' '}
            <Link className="link" to="/customer">
              去创作
            </Link>
          </p>
        ) : null}

        {state === 'ok' && rows.length > 0 ? (
          <ul className="task-list" data-testid="works-list">
            {rows.map((row) => (
              <li key={row.id}>
                <Link
                  className="task-item task-item--link"
                  to={`/customer/studio/${row.id}`}
                  data-testid={`work-row-${row.id}`}
                >
                  <div className="task-item__head">
                    <span className="task-item__title">作品 #{row.id}</span>
                    <span className="badge">{statusLabel(row.status)}</span>
                  </div>
                  <dl className="task-item__facts">
                    <div className="fact">
                      <dt className="fact__key">类型</dt>
                      <dd className="fact__value">{kindLabel(row.kind)}</dd>
                    </div>
                    <div className="fact">
                      <dt className="fact__key">创建时间</dt>
                      <dd className="fact__value">{formatCst(row.created_at)}</dd>
                    </div>
                  </dl>
                  {row.fail_reason ? <p className="note">{row.fail_reason}</p> : null}
                </Link>
              </li>
            ))}
          </ul>
        ) : null}
      </main>
    </>
  )
}
