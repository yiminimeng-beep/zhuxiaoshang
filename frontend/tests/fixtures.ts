/**
 * 后端响应的**固定形状**。形状照抄真实端点，不照抄 spec 的转述——
 * 两者不一致的地方在下面对应函数上写明了，别改成「spec 说的那样」。
 */

import { ROTATED, TOKENS } from './tokens'

export type Role = 'merchant' | 'customer' | 'admin'

const NICKNAMES: Record<Role, string> = {
  merchant: '巷口咖啡',
  customer: '小林',
  admin: '平台运营',
}

/** `POST /api/auth/login` → `{access_token, refresh_token, user}` */
export function loginOk(role: Role, nickname?: string) {
  return {
    access_token: TOKENS.access,
    refresh_token: TOKENS.refresh,
    user: {
      id: role === 'merchant' ? 11 : 22,
      account: role === 'merchant' ? 'shop0001' : 'user0001',
      email: null,
      phone: null,
      role,
      nickname: nickname ?? NICKNAMES[role],
      avatar_url: null,
      status: 'active',
      email_verified: false,
      created_at: '2026-09-01T00:00:00Z',
      last_login_at: null,
    },
  }
}

/** `POST /api/auth/refresh` → `{access_token, refresh_token}` */
export function refreshOk() {
  return { access_token: ROTATED.access, refresh_token: ROTATED.refresh }
}

/** `GET /api/merchant/tasks` → `{items, total}` */
export function merchantTasks({ total }: { total: number }) {
  return { items: Array.from({ length: total }, (_, i) => ({ id: i + 1 })), total }
}

/**
 * `GET /api/merchant/reviews` → **只有 `items`，没有 `total`**。
 *
 * 这是 04 的真实形状（`backend/app/api/review.py::list_reviews` 不分页，
 * 直接回列表）。`specs/08-frontend/spec.md` 的摘要表写的是取 `total`，
 * 与该端点不符——前端取 `items.length`，本 fixture 按**真实形状**给。
 */
export function merchantReviews({ count }: { count: number }) {
  return { items: Array.from({ length: count }, (_, i) => ({ id: i + 1 })) }
}

/** `GET /api/merchant/quota` → `{balance, reserved, ...}` */
export function merchantQuota({ balance }: { balance: number }) {
  return {
    user_id: 11,
    balance,
    reserved: 0,
    debt: 0,
    status: 'active',
    today_consumed: 0,
    today_reserved: 0,
  }
}

/** `GET /api/me/claims` → `{items, total}` */
export function meClaims({ total }: { total: number }) {
  return { items: Array.from({ length: total }, (_, i) => ({ id: i + 1 })), total }
}

/** `GET /api/me/points` → `{balance}` */
export function mePoints({ balance }: { balance: number }) {
  return { balance }
}

/** `GET /api/me/coupons` → `{items, total, page, size}` */
export function meCoupons({ total }: { total: number }) {
  return {
    items: Array.from({ length: total }, (_, i) => ({ id: i + 1 })),
    total,
    page: 1,
    size: 20,
  }
}

/** `POST /api/feedback` → 201，**键恰好六个**：不含 `user_id` / `status` */
export function feedbackOk() {
  return {
    id: 1,
    role: 'customer',
    category: 'suggestion',
    content: '希望支持批量导出',
    contact: null,
    created_at: '2026-09-16T02:00:00Z',
  }
}

/** FastAPI 422：字段级错误落在 `detail[]` 的 `loc` 上 */
export function fieldError(field: string, message: string) {
  return {
    detail: [{ type: 'value_error', loc: ['body', field], msg: message }],
  }
}

/**
 * 一行 `task_public`。
 * 形状照抄 `backend/app/api/task_serializers.py::task_public`，
 * 不照抄 spec 的转述——多一个少一个键会让「前端读错键」这类 bug 测不出来。
 */
export function taskRow(over: Record<string, unknown> = {}) {
  return {
    id: 1,
    merchant_id: 11,
    title: '新品奶茶试喝',
    description: '到店试喝新品奶茶，发布一条带图笔记',
    cover_url: null,
    category: '餐饮',
    requirement: null,
    tags: ['奶茶'],
    start_at: '2026-09-20T02:00:00+00:00',
    end_at: '2026-09-30T02:00:00+00:00',
    quota: 10,
    claimed_count: 3,
    pay_mode: 'merchant_pay',
    reimburse_pool: null,
    reimburse_pool_used: 0,
    reimburse_pool_reserved: 0,
    reimburse_pool_remaining: null,
    reimburse_per_user_limit: null,
    status: 'draft',
    created_at: '2026-09-16T01:00:00+00:00',
    updated_at: '2026-09-16T01:00:00+00:00',
    deleted_at: null,
    ...over,
  }
}

/** `reward_rule_public`：两档连续阶梯，末档 `max:null` */
export function defaultRule(over: Record<string, unknown> = {}) {
  return {
    id: 1,
    task_id: 1,
    metric: 'engagement',
    tiers: [
      { min: 0, max: 99, reward: { cash: 500 } },
      { min: 100, max: null, reward: { cash: 2000 } },
    ],
    max_reward_per_user: null,
    ...over,
  }
}

/**
 * `GET /api/tasks/{id}` → `{task, rule, merchant, claimed_by_me}`。
 *
 * `rule` **可为 `null`**（还没配阶梯的草稿），故用 `'rule' in over` 区分
 * 「没传 → 给一个合法规则」与「显式传 `null`」——`??` 会把显式的 null 吞掉。
 */
export function taskDetail(
  over: {
    task?: Record<string, unknown>
    rule?: unknown
    merchant?: unknown
    claimed_by_me?: boolean
  } = {},
) {
  const task = taskRow({ status: 'published', ...over.task })
  return {
    task,
    rule: 'rule' in over ? over.rule : defaultRule({ task_id: task.id }),
    merchant:
      'merchant' in over
        ? over.merchant
        : {
            user: {
              id: task.merchant_id,
              nickname: '巷口咖啡',
              role: 'merchant',
            },
            merchant_profile: {
              user_id: task.merchant_id,
              shop_name: '巷口咖啡',
              category: '餐饮',
              address: null,
              logo_url: null,
              description: null,
              contact: null,
            },
          },
    claimed_by_me: over.claimed_by_me ?? false,
  }
}

/** 客户列表一行：真实 `task_public` **没有** shop_name；缺省显示 `—` */
export function publishedTasks(rows: Record<string, unknown>[] = [taskRow({ status: 'published' })]) {
  return {
    items: rows.map((row, i) => taskRow({ id: i + 1, status: 'published', ...row })),
    total: rows.length,
    page: 1,
    size: 20,
  }
}

/** `GET /api/me/claims` 一项 = claim_public + 内嵌 task */
export function claimItem(over: {
  claim?: Record<string, unknown>
  task?: Record<string, unknown>
} = {}) {
  const task = taskRow({ status: 'published', title: '新品奶茶试喝', ...over.task })
  return {
    id: 101,
    task_id: task.id,
    user_id: 22,
    claimed_at: '2026-09-16T04:00:00+00:00',
    status: 'in_progress',
    ...over.claim,
    task,
  }
}

export function meClaimsList(items: ReturnType<typeof claimItem>[]) {
  return { items, total: items.length }
}
