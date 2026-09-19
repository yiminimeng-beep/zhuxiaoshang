/** 从详情里的 merchant 块抠店名；没有就诚实显示 — */
export function shopNameOf(merchant: unknown): string {
  if (!merchant || typeof merchant !== 'object') return '—'
  const block = merchant as {
    merchant_profile?: { shop_name?: string | null }
    user?: { nickname?: string | null }
  }
  const shop = block.merchant_profile?.shop_name?.trim()
  if (shop) return shop
  const nick = block.user?.nickname?.trim()
  if (nick) return nick
  return '—'
}

export const CLAIM_STATUS_LABEL: Record<string, string> = {
  in_progress: '进行中',
  submitted: '已提交',
  closed: '已关闭',
}

export function claimStatusLabel(status: string | null | undefined): string {
  if (!status) return '—'
  return CLAIM_STATUS_LABEL[status] ?? status
}

/** 券面额文案；缺字段就诚实显示 — */
export function couponFaceLabel(coupon: {
  type?: string | null
  value?: number | null
  min_amount?: number | null
  name?: string | null
}): string {
  const type = coupon.type ?? ''
  const value = coupon.value
  if (type === 'cash_off' && value != null) {
    const min = coupon.min_amount
    if (min != null) return `满 ${min / 100} 减 ${value / 100}`
    return `减 ${value / 100}`
  }
  if (type === 'discount' && value != null) {
    // 85 → 8.5 折
    const zhe = value % 10 === 0 ? String(value / 10) : (value / 10).toFixed(1)
    return `${zhe} 折`
  }
  if (type === 'gift') return '赠品'
  return coupon.name?.trim() || '—'
}

export function merchantDisplayName(name: string | null | undefined): string {
  const t = name?.trim()
  return t ? t : '—'
}
