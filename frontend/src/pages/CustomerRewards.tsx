/**
 * 我的奖励 · 按商家列表（05 追加 A）。
 */

import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'

import { apiFetch } from '../api/client'
import { AppBar } from '../components/AppBar'
import { LoadFailure } from '../components/LoadFailure'
import { useSession } from '../auth/useSession'
import { merchantDisplayName } from '../lib/customer'

export interface MerchantRewardRow {
  merchant_id: number
  merchant_name: string
  logo_url: string | null
  points_earned: number
  coupon_total: number
  coupon_unused: number
  cash_pending?: number
  benefits?: string[]
}

type LoadState = 'loading' | 'ok' | 'error'

export function CustomerRewards() {
  const session = useSession()
  const [items, setItems] = useState<MerchantRewardRow[]>([])
  const [state, setState] = useState<LoadState>('loading')
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    let alive = true
    void (async () => {
      try {
        const data = await apiFetch<{ items?: MerchantRewardRow[] }>(
          '/api/me/rewards/by-merchant',
        )
        if (!alive) return
        setItems(data.items ?? [])
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
          <h1 className="page__title">我的奖励</h1>
          <p className="lede">按商家查看积分与券。</p>
        </header>

        {state === 'error' ? (
          <LoadFailure onRetry={retry} />
        ) : state === 'loading' ? (
          <p className="note">加载中…</p>
        ) : items.length === 0 ? (
          <p className="note" data-testid="rewards-empty">
            还没有奖励
          </p>
        ) : (
          <ul className="reward-grid" data-testid="rewards-list">
            {items.map((row) => (
              <li key={row.merchant_id}>
                <Link
                  className="reward-card"
                  to={`/customer/rewards/${row.merchant_id}`}
                  data-testid={`reward-card-${row.merchant_id}`}
                >
                  <LogoMark name={row.merchant_name} url={row.logo_url} />
                  <div className="reward-card__body">
                    <p className="reward-card__name">
                      {merchantDisplayName(row.merchant_name)}
                    </p>
                    <p className="reward-card__meta">
                      {row.points_earned} 积分 · {row.coupon_unused}/{row.coupon_total}{' '}
                      张券
                      {row.cash_pending
                        ? ` · 现金 ${row.cash_pending} 分（待线下发放）`
                        : ''}
                      {row.benefits?.length ? ` · ${row.benefits.join(' · ')}` : ''}
                    </p>
                  </div>
                </Link>
              </li>
            ))}
          </ul>
        )}
      </main>
    </>
  )
}

export function LogoMark({ name, url }: { name: string; url: string | null }) {
  const label = merchantDisplayName(name)
  const initial = label === '—' ? '?' : label.slice(0, 1)
  if (url) {
    return <img className="reward-card__logo" src={url} alt="" />
  }
  return (
    <span className="reward-card__logo reward-card__logo--fallback" aria-hidden>
      {initial}
    </span>
  )
}
