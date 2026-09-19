import { Link } from 'react-router-dom'

import type { CounterCell } from '../lib/counters'

const LOADING = '…'
/** 取不到就是取不到。**不编数字**，一个 `—` 比一个假的 0 诚实 */
const MISSING = '—'
const EMPTY = '还没有数据'

export function SummaryCard({
  testId,
  label,
  cell,
  to,
  featured = false,
  tone = 'panel',
}: {
  testId: string
  label: string
  cell: CounterCell
  /** 有去处就整卡可点。卡片**根节点不变**——只把标签换掉 */
  to?: string
  /** 商户轨主指标：加宽 + 强调边 */
  featured?: boolean
  /** panel = 经营卡；ledger = 客户账本行（更轻） */
  tone?: 'panel' | 'ledger'
}) {
  const className = [
    'card',
    tone === 'ledger' ? 'card--ledger' : '',
    featured ? 'card--featured' : '',
    to ? 'card--link' : '',
  ]
    .filter(Boolean)
    .join(' ')

  const body = (
    <>
      <p className="card__label">{label}</p>
      <Figure cell={cell} />
    </>
  )

  // 根节点挂着 `data-testid`：卡片是 `article` 还是 `a`，外层的取数测试都不该变
  if (to) {
    return (
      <Link className={className} to={to} data-testid={testId} data-state={cell.status}>
        {body}
      </Link>
    )
  }
  return (
    <article className={className} data-testid={testId} data-state={cell.status}>
      {body}
    </article>
  )
}

function Figure({ cell }: { cell: CounterCell }) {
  if (cell.status === 'error') {
    return (
      <p className="card__figure card__figure--missing" title="暂时取不到">
        {MISSING}
      </p>
    )
  }
  if (cell.status === 'empty') {
    return <p className="card__figure card__figure--missing">{EMPTY}</p>
  }
  // 加载中也占住数字的位置，避免数据到位时整页跳一下
  return <p className="card__figure">{cell.status === 'loading' ? LOADING : cell.value}</p>
}
