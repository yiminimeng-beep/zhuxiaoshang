/**
 * 客户任务详情：一次 `GET /api/tasks/{id}` 拿齐。
 * 拍 1 不做 reimburse-preview。
 */

import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'

import { apiFetch, ApiError, failureMessage } from '../api/client'
import { AppBar } from '../components/AppBar'
import { useSession } from '../auth/useSession'
import { shopNameOf } from '../lib/customer'
import {
  parseTaskId,
  payLabel,
  quotaLabel,
  type RewardRule,
  type TaskRow,
} from '../lib/tasks'
import { windowLabel } from '../lib/time'

type LoadState = 'loading' | 'ok' | 'missing' | 'error'

interface Detail {
  task: TaskRow
  rule: RewardRule | null
  merchant: unknown
  claimed_by_me: boolean
}

export function CustomerTaskDetail() {
  const session = useSession()
  const { id: raw } = useParams()
  const taskId = parseTaskId(raw)

  const [state, setState] = useState<LoadState>(taskId == null ? 'missing' : 'loading')
  const [detail, setDetail] = useState<Detail | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)
  const [claiming, setClaiming] = useState(false)

  useEffect(() => {
    if (taskId == null) return
    let alive = true
    void (async () => {
      try {
        const data = await apiFetch<Detail>(`/api/tasks/${taskId}`)
        if (!alive) return
        setDetail(data)
        setState('ok')
      } catch (error) {
        if (!alive) return
        setState(error instanceof ApiError && error.status === 404 ? 'missing' : 'error')
      }
    })()
    return () => {
      alive = false
    }
  }, [taskId])

  async function claim() {
    if (taskId == null || claiming) return
    setClaiming(true)
    setActionError(null)
    try {
      await apiFetch(`/api/tasks/${taskId}/claim`, { method: 'POST', body: {} })
      setDetail((prev) => (prev ? { ...prev, claimed_by_me: true } : prev))
    } catch (error) {
      setActionError(failureMessage(error))
    } finally {
      setClaiming(false)
    }
  }

  if (!session) return null

  return (
    <>
      <AppBar session={session} />
      <main className="shell page page--app">
        <p className="crumb">
          <Link className="link" to="/customer/tasks">
            返回
          </Link>
        </p>
        {state === 'missing' ? <p className="lede">任务不存在</p> : null}
        {state === 'error' ? (
          <p className="note note--error" role="alert">
            任务详情暂时取不到
          </p>
        ) : null}

        {state === 'ok' && detail ? (
          <>
            <header className="page-head">
              <h1 className="page__title">{detail.task.title}</h1>
              <p className="lede">
                {shopNameOf(detail.merchant)} · {detail.task.category || '—'}
              </p>
            </header>

            <dl className="task-item__facts">
              <div className="fact">
                <dt className="fact__key">时间窗</dt>
                <dd className="fact__value">
                  {windowLabel(detail.task.start_at, detail.task.end_at)}
                </dd>
              </div>
              <div className="fact">
                <dt className="fact__key">名额</dt>
                <dd className="fact__value">{quotaLabel(detail.task)}</dd>
              </div>
              <div className="fact">
                <dt className="fact__key">付费</dt>
                <dd className="fact__value">{payLabel(detail.task.pay_mode)}</dd>
              </div>
            </dl>

            {detail.task.pay_mode === 'user_pay_reimburse' ? (
              <p className="note">需垫付，过审后报销</p>
            ) : null}

            <p className="lede">{detail.task.description}</p>

            <section className="stack">
              <h2 className="section-title">奖励阶梯</h2>
              <p className="lede">互动量（点赞 + 收藏 + 评论）</p>
              {(detail.rule?.tiers ?? []).map((tier, index) => (
                <div key={index} className="tier-row" data-testid={`tier-${index}`}>
                  <div className="fact">
                    <dt className="fact__key">下限</dt>
                    <dd className="fact__value">{tier.min}</dd>
                  </div>
                  <div className="fact">
                    <dt className="fact__key">上限</dt>
                    <dd className="fact__value">{tier.max == null ? '以上' : tier.max}</dd>
                  </div>
                  <div className="fact">
                    <dt className="fact__key">奖励</dt>
                    <dd className="fact__value">{rewardText(tier.reward)}</dd>
                  </div>
                </div>
              ))}
            </section>

            {actionError ? (
              <p className="note note--error" role="alert">
                {actionError}
              </p>
            ) : null}

            <div className="row">
              {detail.claimed_by_me ? (
                <Link className="btn btn--primary" to={`/customer?task=${detail.task.id}`}>
                  去创作
                </Link>
              ) : (
                <button
                  type="button"
                  className="btn btn--primary"
                  disabled={claiming}
                  onClick={() => void claim()}
                >
                  领取
                </button>
              )}
              <Link className="btn btn--text" to="/customer/tasks">
                返回列表
              </Link>
            </div>
          </>
        ) : null}
      </main>
    </>
  )
}

function rewardText(reward: Record<string, unknown> | undefined): string {
  if (!reward) return '—'
  const parts: string[] = []
  if (typeof reward.cash === 'number') parts.push(`现金 ${reward.cash} 分（待线下发放）`)
  if (typeof reward.points === 'number') parts.push(`积分 ${reward.points}`)
  if (typeof reward.coupon_id === 'number') parts.push(`券 #${reward.coupon_id}`)
  if (typeof reward.benefit === 'string' && reward.benefit.trim()) {
    parts.push(reward.benefit.trim())
  }
  return parts.length ? parts.join(' · ') : '—'
}
