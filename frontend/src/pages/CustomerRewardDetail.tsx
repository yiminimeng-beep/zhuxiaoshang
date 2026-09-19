/**
 * 某商家的积分与券详情。
 */

import { useCallback, useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'

import { ApiError, apiFetch } from '../api/client'
import { AppBar } from '../components/AppBar'
import { LoadFailure } from '../components/LoadFailure'
import { useSession } from '../auth/useSession'
import { couponFaceLabel, merchantDisplayName } from '../lib/customer'
import { formatCst } from '../lib/time'
import { LogoMark } from './CustomerRewards'

interface CouponNest {
  id: number
  name: string
  type: string
  value: number
  min_amount: number | null
  status: string
}

interface UserCouponRow {
  id: number
  status: string
  expire_at: string | null
  coupon: CouponNest
}

interface DetailBody {
  merchant: {
    merchant_id: number
    merchant_name: string
    logo_url: string | null
  }
  points_earned: number
  cash_pending?: number
  benefits?: string[]
  coupons: UserCouponRow[]
  coupons_total: number
}

type LoadState = 'loading' | 'ok' | 'error' | 'missing' | 'bad-id'

export function CustomerRewardDetail() {
  const session = useSession()
  const { merchantId: raw } = useParams()
  const merchantId = Number(raw)
  const badId = !Number.isInteger(merchantId) || merchantId < 1

  const [body, setBody] = useState<DetailBody | null>(null)
  const [state, setState] = useState<LoadState>(badId ? 'bad-id' : 'loading')
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    if (badId) {
      setState('bad-id')
      return
    }
    let alive = true
    void (async () => {
      try {
        const data = await apiFetch<DetailBody>(
          `/api/me/rewards/by-merchant/${merchantId}`,
        )
        if (!alive) return
        setBody(data)
        setState('ok')
      } catch (error) {
        if (!alive) return
        if (error instanceof ApiError && error.status === 404) {
          setState('missing')
        } else {
          setState('error')
        }
      }
    })()
    return () => {
      alive = false
    }
  }, [merchantId, badId, attempt])

  const retry = useCallback(() => setAttempt((n) => n + 1), [])

  if (!session) return null

  return (
    <>
      <AppBar session={session} />
      <main className="shell page page--app">
        <p className="crumb">
          <Link className="link" to="/customer/rewards">
            返回
          </Link>
        </p>

        {state === 'bad-id' ? (
          <p className="note" data-testid="reward-bad-id">
            商家不存在
          </p>
        ) : state === 'missing' ? (
          <p className="note" data-testid="reward-missing">
            这个商家还没有你的记录
          </p>
        ) : state === 'error' ? (
          <LoadFailure onRetry={retry} />
        ) : state === 'loading' || !body ? (
          <p className="note">加载中…</p>
        ) : (
          <>
            <header className="page-head reward-detail-head">
              <LogoMark
                name={body.merchant.merchant_name}
                url={body.merchant.logo_url}
              />
              <div>
                <h1 className="page__title">
                  {merchantDisplayName(body.merchant.merchant_name)}
                </h1>
                <p className="reward-points" data-testid="reward-points">
                  {body.points_earned}
                </p>
                <p className="lede">在该商家挣到的积分</p>
                {body.cash_pending ? (
                  <p className="note" data-testid="reward-cash">
                    现金 {body.cash_pending} 分，待线下发放，不是账户余额
                  </p>
                ) : null}
                {body.benefits?.length ? (
                  <p className="note" data-testid="reward-benefits">
                    {body.benefits.join(' · ')}
                  </p>
                ) : null}
              </div>
            </header>

            {body.coupons.length === 0 ? (
              <p className="note" data-testid="reward-coupons-empty">
                还没有这个商家的券
              </p>
            ) : (
              <ul className="coupon-list" data-testid="reward-coupons">
                {body.coupons.map((row) => (
                  <li key={row.id} className="coupon-row">
                    <div>
                      <p className="coupon-row__name">{row.coupon.name}</p>
                      <p className="coupon-row__face">
                        {couponFaceLabel(row.coupon)}
                      </p>
                      <p className="coupon-row__expire">
                        有效至 {formatCst(row.expire_at)}
                      </p>
                    </div>
                    <span className="badge">{row.status}</span>
                  </li>
                ))}
              </ul>
            )}
          </>
        )}
      </main>
    </>
  )
}
