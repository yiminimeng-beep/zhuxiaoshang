/**
 * 商户任务列表 + 回收站。
 *
 * 页签存在 URL（`?tab=trash`）而不是组件状态里：刷新、分享链接、后退都还停在
 * 原来的页签上。`GET /api/merchant/tasks` **不分页也不收查询参数**，
 * 所以列表一次全取，前端不做「下一页」。
 *
 * 删除与恢复成功后**只摘掉这一行、不重取列表**：重取会让整页闪一下、
 * 滚动位置也丢。后端已经在同一个响应里告诉了我们结果，够用了。
 */

import { useCallback, useEffect, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'

import { apiFetch, failureMessage } from '../api/client'
import { AppBar } from '../components/AppBar'
import { FeedbackLauncher } from '../components/FeedbackLauncher'
import { useSession } from '../auth/useSession'
import { payLabel, quotaLabel, statusLabel, type TaskRow } from '../lib/tasks'
import { windowLabel } from '../lib/time'

const TASKS = '/api/merchant/tasks'
const TRASH = `${TASKS}/trash`

type LoadState = 'loading' | 'ok' | 'error'

export function MerchantTaskList() {
  const session = useSession()
  const [params, setParams] = useSearchParams()
  const trash = params.get('tab') === 'trash'
  const path = trash ? TRASH : TASKS

  const [rows, setRows] = useState<TaskRow[]>([])
  const [state, setState] = useState<LoadState>('loading')
  const [attempt, setAttempt] = useState(0)
  const [confirmId, setConfirmId] = useState<number | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)

  /**
   * 换页签 / 重试 = 换了一份数据，行与「确认删除」都得跟着清掉。
   *
   * 在**渲染期**做，不在 effect 里：effect 要等这一帧画完才跑，中间那一帧
   * 旧页签的行还挂在屏幕上——切到回收站会先闪一下进行中的任务。
   */
  const loadKey = `${path}#${attempt}`
  const [loadedKey, setLoadedKey] = useState(loadKey)
  if (loadedKey !== loadKey) {
    setLoadedKey(loadKey)
    setRows([])
    setState('loading')
    setConfirmId(null)
  }

  useEffect(() => {
    let alive = true
    void (async () => {
      try {
        const data = await apiFetch<{ items?: TaskRow[] }>(path)
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

  async function remove(id: number) {
    setActionError(null)
    try {
      await apiFetch(`${TASKS}/${id}`, { method: 'DELETE' })
      setRows((prev) => prev.filter((row) => row.id !== id))
    } catch (error) {
      setActionError(failureMessage(error))
    } finally {
      setConfirmId(null)
    }
  }

  async function restore(id: number) {
    setActionError(null)
    try {
      await apiFetch(`${TASKS}/${id}/restore`, { method: 'POST' })
      setRows((prev) => prev.filter((row) => row.id !== id))
    } catch (error) {
      setActionError(failureMessage(error))
    }
  }

  if (!session) return null

  return (
    <>
      <AppBar session={session} />
      <main className="shell page" data-testid="task-page">
        <p className="crumb">
          <Link className="link" to="/merchant">
            返回
          </Link>
        </p>
        <header className="stack">
          <h1 className="page__title">我的任务</h1>
          <p className="lede">草稿可以慢慢改，发布出去的改动有限。</p>
        </header>

        <div className="row">
          <Link className="btn btn--primary" to="/merchant/tasks/new">
            发布任务
          </Link>
        </div>

        <div className="row" role="tablist">
          <button
            type="button"
            role="tab"
            aria-selected={!trash}
            className={`btn ${trash ? 'btn--quiet' : 'btn--primary'}`}
            data-testid="task-tab-active"
            onClick={() => setParams({})}
          >
            进行中
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={trash}
            className={`btn ${trash ? 'btn--primary' : 'btn--quiet'}`}
            data-testid="task-tab-trash"
            onClick={() => setParams({ tab: 'trash' })}
          >
            回收站
          </button>
        </div>

        {actionError ? (
          <p className="note note--error" role="alert" data-testid="task-action-error">
            {actionError}
          </p>
        ) : null}

        {state === 'error' ? (
          <div className="panel stack" role="alert" data-testid="task-load-error">
            <p>任务列表暂时取不到，可能是网络或服务在打盹。</p>
            <div className="row">
              <button type="button" className="btn btn--quiet" data-testid="task-retry" onClick={retry}>
                重试
              </button>
            </div>
          </div>
        ) : state === 'loading' ? (
          <p className="note" data-testid="task-loading">
            正在取任务…
          </p>
        ) : rows.length === 0 ? (
          <p className="note" data-testid="task-empty">
            {trash ? '回收站是空的。' : '还没有任务，先发布一个吧。'}
          </p>
        ) : (
          <ul className="task-list" data-testid="task-list">
            {rows.map((task) => (
              <li className="task-item" data-testid={`task-row-${task.id}`} key={task.id}>
                <div className="task-item__head">
                  {trash ? (
                    <span className="task-item__title" data-testid="task-title">
                      {task.title}
                    </span>
                  ) : (
                    <Link
                      className="task-item__title link"
                      data-testid="task-title"
                      to={`/merchant/tasks/${task.id}`}
                    >
                      {task.title}
                    </Link>
                  )}
                  <span className="badge" data-testid="task-status">
                    {statusLabel(task.status)}
                  </span>
                </div>

                <dl className="task-item__facts">
                  <div className="fact">
                    <dt className="fact__key">时间窗</dt>
                    <dd className="fact__value" data-testid="task-window">
                      {windowLabel(task.start_at, task.end_at)}
                    </dd>
                  </div>
                  <div className="fact">
                    <dt className="fact__key">名额</dt>
                    <dd className="fact__value" data-testid="task-quota">
                      {quotaLabel(task)}
                    </dd>
                  </div>
                  <div className="fact">
                    <dt className="fact__key">付费</dt>
                    <dd className="fact__value" data-testid="task-pay">
                      {payLabel(task.pay_mode)}
                    </dd>
                  </div>
                </dl>

                <div className="row row--end">
                  {trash ? (
                    <button
                      type="button"
                      className="btn btn--quiet"
                      data-testid={`task-restore-${task.id}`}
                      onClick={() => void restore(task.id)}
                    >
                      恢复
                    </button>
                  ) : (
                    <button
                      type="button"
                      className="btn btn--text"
                      data-testid={`task-delete-${task.id}`}
                      onClick={() => {
                        if (confirmId === task.id) void remove(task.id)
                        else {
                          setActionError(null)
                          setConfirmId(task.id)
                        }
                      }}
                    >
                      {confirmId === task.id ? '确认删除' : '删除'}
                    </button>
                  )}
                </div>
              </li>
            ))}
          </ul>
        )}

        <FeedbackLauncher />
      </main>
    </>
  )
}

