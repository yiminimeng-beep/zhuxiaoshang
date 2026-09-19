/**
 * 后端响应固定形状（照 06 spec 的 D 段抄，不照前端代码抄）。
 *
 * `GET /api/admin/feedback?role&category&status&page&size`
 *   → `{items, total, page, size}`
 *   item: `{id, user_id, account, nickname, role, category, content, contact,
 *           status, resolved_at, resolved_by, created_at}`
 */

export interface FeedbackItem {
  id: number
  user_id: number
  account: string
  nickname: string
  role: 'merchant' | 'customer'
  category: 'bug' | 'suggestion' | 'other'
  content: string
  contact: string | null
  status: 'open' | 'resolved'
  resolved_at: string | null
  resolved_by: number | null
  created_at: string
}

let nextId = 1

export function feedbackItem(over: Partial<FeedbackItem> = {}): FeedbackItem {
  const id = over.id ?? nextId++
  return {
    id,
    user_id: over.user_id ?? 100 + id,
    account: over.account ?? 'shop0001',
    nickname: over.nickname ?? '巷口咖啡',
    role: over.role ?? 'merchant',
    category: over.category ?? 'suggestion',
    content: over.content ?? '希望支持批量导出',
    contact: over.contact === undefined ? null : over.contact,
    status: over.status ?? 'open',
    resolved_at: over.resolved_at ?? null,
    resolved_by: over.resolved_by ?? null,
    // 固定时间：`formatCst` 的可断言性靠它（2026-09-16 02:00Z = 北京 10:00）
    created_at: over.created_at ?? '2026-09-16T02:00:00Z',
  }
}

export function feedbackPage(
  items: Partial<FeedbackItem>[],
  over: { total?: number; page?: number; size?: number } = {},
) {
  return {
    items: items.map(feedbackItem),
    total: over.total ?? items.length,
    page: over.page ?? 1,
    size: over.size ?? 100,
  }
}

/** 只要条数：`?status=open&size=1` */
export function countPage(total: number) {
  return { items: [], total, page: 1, size: 1 }
}

export function resolveOk(id: number) {
  return {
    id,
    status: 'resolved',
    resolved_at: '2026-09-16T03:00:00Z',
    resolved_by: 9,
  }
}

export function reopenOk(id: number) {
  return { id, status: 'open', resolved_at: null, resolved_by: null }
}

export function adminLoginOk() {
  return {
    access_token: 'admin-access-token',
    refresh_token: 'admin-refresh-token',
    user: { id: 9, role: 'admin', nickname: '运营小张' },
  }
}

export function merchantLoginOk() {
  return {
    access_token: 'merchant-access-token',
    refresh_token: 'merchant-refresh-token',
    user: { id: 7, role: 'merchant', nickname: '巷口咖啡' },
  }
}

export function refreshOk() {
  return {
    access_token: 'admin-access-token-2',
    refresh_token: 'admin-refresh-token-2',
  }
}

/* -------------------------------------------------------------------------- */
/* G 组：成本看板 / 用户管理 / 异常处理                                          */
/*                                                                            */
/* 形状照 06 的 A / B / C 三组端点抄（后端 `admin_cost.py` / `admin_user.py` /   */
/* `admin_exception.py`），不照前端代码抄——不然前端把键名写错时用例会跟着错。    */
/* -------------------------------------------------------------------------- */

/** `2026-09-16T02:00:00Z` = 北京 10:00，与上面那批同一个固定时刻 */
const AT = '2026-09-16T02:00:00Z'

/* ── 成本看板 ────────────────────────────────────────────────────────────── */

/** `GET /api/admin/cost/summary` */
export function costSummary(over: Record<string, number> = {}) {
  return {
    total_cents: over.total_cents ?? 123456,
    gen_count: over.gen_count ?? 42,
    job_count: over.job_count ?? 20,
    success_count: over.success_count ?? 40,
    fail_count: over.fail_count ?? 2,
    avg_cents: over.avg_cents ?? 2939,
  }
}

/** 空集：`avg_cents` 由后端整数除法给出 0，不是 `null` */
export function costSummaryEmpty() {
  return {
    total_cents: 0,
    gen_count: 0,
    job_count: 0,
    success_count: 0,
    fail_count: 0,
    avg_cents: 0,
  }
}

/** `GET /api/admin/cost/by-provider` → `{items}`（无调用时是空数组，不造 0 行） */
export function providerCosts(rows: Record<string, unknown>[] = [{}]) {
  return {
    items: rows.map((over) => ({
      provider: (over.provider as string) ?? 'doubao',
      total_cents: (over.total_cents as number) ?? 100000,
      gen_count: (over.gen_count as number) ?? 30,
      avg_cents: (over.avg_cents as number) ?? 3333,
      success_rate: (over.success_rate as number) ?? 0.5,
    })),
  }
}

/** `GET /api/admin/cost/by-merchant` → 分页信封 */
export function merchantCosts(
  rows: Record<string, unknown>[] = [{}],
  over: { total?: number; page?: number; size?: number } = {},
) {
  return {
    items: rows.map((row) => ({
      merchant_id: (row.merchant_id as number) ?? 7,
      shop_name: row.shop_name === undefined ? '巷口咖啡' : (row.shop_name as string | null),
      total_cents: (row.total_cents as number) ?? 60000,
      gen_count: (row.gen_count as number) ?? 18,
      video_count: (row.video_count as number) ?? 6,
      copy_count: (row.copy_count as number) ?? 12,
    })),
    total: over.total ?? rows.length,
    page: over.page ?? 1,
    size: over.size ?? 100,
  }
}

/** `GET /api/admin/cost/by-day` → 后端**已补 0**，前端不许再插空日期 */
export function costDays(rows: Record<string, unknown>[] = [{}, {}, {}]) {
  return {
    items: rows.map((row, index) => ({
      date: (row.date as string) ?? `2026-09-1${4 + index}`,
      total_cents: (row.total_cents as number) ?? (index + 1) * 1000,
      gen_count: (row.gen_count as number) ?? index + 1,
    })),
  }
}

/** `GET /api/admin/cost/budget-alerts` → `{items}` */
export function budgetAlerts(rows: Record<string, unknown>[] = [{}]) {
  return {
    items: rows.map((row) => ({
      merchant_id: (row.merchant_id as number) ?? 7,
      shop_name: row.shop_name === undefined ? '巷口咖啡' : (row.shop_name as string | null),
      alert_date: (row.alert_date as string) ?? '2026-09-16',
      spend_cents: (row.spend_cents as number) ?? 320000,
      limit_cents: (row.limit_cents as number) ?? 300000,
    })),
  }
}

/** `GET /api/admin/cost/merchants/{id}/detail` */
export function merchantCostDetail(over: Record<string, unknown> = {}) {
  return {
    merchant: {
      id: (over.id as number) ?? 7,
      account: (over.account as string) ?? 'shop0001',
      nickname: (over.nickname as string) ?? '巷口咖啡',
      status: (over.status as string) ?? 'active',
    },
    by_day: costDays().items,
    by_provider: providerCosts().items,
    recent_jobs: [
      {
        job_id: 301,
        task_id: 12,
        user_id: 55,
        kind: 'copy',
        status: 'ready',
        provider: 'doubao',
        billing_source: 'platform',
        cost_cents: 1500,
        created_at: AT,
      },
    ],
  }
}

/* ── 用户管理 ────────────────────────────────────────────────────────────── */

export interface UserItem {
  id: number
  account: string | null
  email: string | null
  nickname: string | null
  role: 'merchant' | 'customer' | 'admin'
  status: 'active' | 'banned' | 'deleted'
  shop_name: string | null
  created_at: string
}

export function userItem(over: Partial<UserItem> = {}): UserItem {
  return {
    id: over.id ?? 7,
    account: over.account === undefined ? 'shop0001' : over.account,
    email: over.email === undefined ? 'shop0001@example.com' : over.email,
    nickname: over.nickname === undefined ? '巷口咖啡' : over.nickname,
    role: over.role ?? 'merchant',
    status: over.status ?? 'active',
    shop_name: over.shop_name === undefined ? '巷口咖啡' : over.shop_name,
    created_at: over.created_at ?? AT,
  }
}

/** `GET /api/admin/users?role&status&keyword&page&size` */
export function userPage(
  items: Partial<UserItem>[] = [{}],
  over: { total?: number; page?: number; size?: number } = {},
) {
  return {
    items: items.map(userItem),
    total: over.total ?? items.length,
    page: over.page ?? 1,
    size: over.size ?? 100,
  }
}

/** `GET /api/admin/users/{id}`；`merchant_profile` **只对商户出现** */
export function userDetail(
  over: { user?: Partial<UserItem>; role?: UserItem['role']; withProfile?: boolean } = {},
) {
  const role = over.role ?? 'merchant'
  const body: Record<string, unknown> = {
    user: userItem({ role, shop_name: role === 'merchant' ? '巷口咖啡' : null, ...over.user }),
    stats: { task_count: 3, claim_count: 5, job_count: 8, points_balance: 1200 },
  }
  if (over.withProfile ?? role === 'merchant') {
    body.merchant_profile = {
      shop_name: '巷口咖啡',
      category: '咖啡',
      address: '北京市朝阳区 1 号',
      logo_url: null,
      description: '一家小店',
      contact: '13800000000',
    }
  }
  return body
}

/** `GET /api/admin/action-logs` */
export function actionLogPage(
  items: Record<string, unknown>[] = [{}],
  over: { total?: number } = {},
) {
  return {
    items: items.map((row, index) => ({
      id: (row.id as number) ?? 100 - index,
      admin_id: (row.admin_id as number) ?? 9,
      action: (row.action as string) ?? 'ban_user',
      target_type: (row.target_type as string) ?? 'user',
      target_id: (row.target_id as number) ?? 7,
      detail: row.detail === undefined ? { reason: '刷单', before: 'active', after: 'banned' } : row.detail,
      created_at: (row.created_at as string) ?? AT,
    })),
    total: over.total ?? items.length,
    page: 1,
    size: 20,
  }
}

/** `POST /api/admin/users/{id}/ban` */
export function banOk(id: number) {
  return { user: userItem({ id, status: 'banned' }), reason: '刷单' }
}

/** `POST /api/admin/users/{id}/unban` */
export function unbanOk(id: number) {
  return { user: userItem({ id, status: 'active' }) }
}

/* ── 异常处理 ────────────────────────────────────────────────────────────── */

/** `GET /api/admin/exceptions?type=content_review` 的行 */
export function contentExcItem(over: Record<string, unknown> = {}) {
  return {
    job_id: (over.job_id as number) ?? 301,
    task_id: (over.task_id as number) ?? 12,
    user_id: (over.user_id as number) ?? 55,
    kind: (over.kind as string) ?? 'copy',
    status: (over.status as string) ?? 'need_review',
    fail_reason: (over.fail_reason as string | null) ?? null,
    created_at: (over.created_at as string) ?? AT,
  }
}

/** `?type=ocr_low_confidence` / `?type=ocr_mismatch` 的行 */
export function ocrExcItem(over: Record<string, unknown> = {}) {
  return {
    ocr_id: (over.ocr_id as number) ?? 801,
    post_id: (over.post_id as number) ?? 44,
    image_url: (over.image_url as string) ?? 'https://cdn.example.com/shot-1.png',
    confidence: (over.confidence as number) ?? 0.62,
    mismatch_flag: (over.mismatch_flag as boolean) ?? false,
    model: (over.model as string) ?? 'doubao-vision',
    created_at: (over.created_at as string) ?? AT,
  }
}

/** `?type=appeal` 的行 */
export function appealExcItem(over: Record<string, unknown> = {}) {
  return {
    appeal_id: (over.appeal_id as number) ?? 501,
    post_id: (over.post_id as number) ?? 44,
    user_id: (over.user_id as number) ?? 55,
    reason: (over.reason as string) ?? '截图确实是我的作品',
    status: (over.status as string) ?? 'pending',
    created_at: (over.created_at as string) ?? AT,
  }
}

export function excPage(
  items: unknown[] = [{}],
  over: { total?: number; page?: number; size?: number } = {},
) {
  return {
    items,
    total: over.total ?? items.length,
    page: over.page ?? 1,
    size: over.size ?? 100,
  }
}

/** 四段的空页，用来给「计数」那条 `?size=1` 请求打桩 */
export function excCount(total: number) {
  return { items: [], total, page: 1, size: 1 }
}
