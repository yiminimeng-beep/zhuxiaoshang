import { Link } from 'react-router-dom'

import { AppBar } from '../components/AppBar'
import { FeedbackLauncher } from '../components/FeedbackLauncher'
import { LoadFailure } from '../components/LoadFailure'
import { SummaryCard } from '../components/SummaryCard'
import { useSession } from '../auth/useSession'
import { balanceOf, totalOf, useCounters, type CounterSpec } from '../lib/counters'

/** 模块级常量：`useCounters` 依赖它的**引用稳定性** */
const MERCHANT_SPECS: Record<string, CounterSpec> = {
  tasks: { path: '/api/merchant/tasks', pick: totalOf },
  reviews: { path: '/api/merchant/reviews', pick: totalOf },
  quota: { path: '/api/merchant/quota', pick: balanceOf },
}

/** `to` = 这张卡点进去的落点。没有 `to` 的卡是纯读数，不假装可点 */
const MERCHANT_CARDS = [
  { key: 'tasks', testId: 'merchant-card-tasks', label: '我的任务', to: '/merchant/tasks', featured: true },
  { key: 'reviews', testId: 'merchant-card-reviews', label: '待我审核', to: '/merchant/reviews' },
  { key: 'quota', testId: 'merchant-card-quota', label: '额度余额' },
]

/** 商户端：横排的**经营摘要行**（主指标加宽）。客户端的卡片堆叠是另一套结构。 */
export function MerchantHome() {
  const session = useSession()
  const { cells, allFailed, retry } = useCounters('merchant', MERCHANT_SPECS)

  if (!session) return null

  return (
    <>
      <AppBar session={session} />
      <main className="shell page page--app">
        <header className="page-head">
          <h1 className="page__title">经营概览</h1>
          <p className="lede">今天要处理的事，一眼看完。</p>
        </header>

        {allFailed ? (
          <LoadFailure onRetry={retry} />
        ) : (
          <section className="summary-rail" data-testid="merchant-rail" aria-label="经营摘要">
            {MERCHANT_CARDS.map((card) => (
              <SummaryCard
                key={card.key}
                testId={card.testId}
                label={card.label}
                cell={cells[card.key]}
                to={card.to}
                featured={card.featured}
              />
            ))}
          </section>
        )}

        <section className="next-strip" aria-label="下一步">
          <h2 className="next-strip__title">下一步</h2>
          <div className="next-strip__actions">
            <Link className="btn btn--primary" to="/merchant/tasks/new">
              发布任务
            </Link>
            <Link className="btn btn--quiet" to="/merchant/tasks">
              管理任务
            </Link>
          </div>
        </section>

        <FeedbackLauncher />
      </main>
    </>
  )
}
