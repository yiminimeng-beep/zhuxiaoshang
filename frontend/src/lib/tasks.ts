/**
 * 02-task 的**前端契约**：形状、标签、校验、请求体。
 *
 * 这里的每条校验都是后端那条规则的**复述**，不是新规则——本地拦一道只为
 * 「一次请求都不发」（`MC-01`~`MC-05`、`MC-12`），后端仍是唯一权威：
 * 它回的 409 / 422 一律**原样显示**，不翻译、不吞。
 */

import { toInputValue, toIso } from './time'

export interface TaskRow {
  id: number
  merchant_id: number
  title: string
  description: string
  cover_url: string | null
  category: string
  requirement: string | null
  tags: string[] | null
  start_at: string | null
  end_at: string | null
  quota: number | null
  claimed_count: number
  pay_mode: string | null
  reimburse_pool: number | null
  reimburse_per_user_limit: number | null
  reimburse_pool_remaining: number | null
  status: string
}

export interface TierReward {
  cash?: number
  points?: number
  coupon_id?: number
  benefit?: string
}

export interface RewardTier {
  min: number
  max: number | null
  reward: TierReward
}

export interface RewardRule {
  id?: number
  task_id?: number
  metric?: string
  tiers?: RewardTier[]
  max_reward_per_user?: number | null
}

export interface TaskDetailPayload {
  task: TaskRow
  rule: RewardRule | null
}

export type PayMode = 'merchant_pay' | 'user_pay_reimburse'

/** 后端 `task.status` 的四个取值 */
export const STATUS_LABEL: Record<string, string> = {
  draft: '草稿',
  published: '已发布',
  paused: '已暂停',
  closed: '已关闭',
}

export const PAY_LABEL: Record<string, string> = {
  merchant_pay: '商户付费',
  user_pay_reimburse: '用户垫付',
}

const UNKNOWN = '—'

export function statusLabel(status: string | null | undefined): string {
  return (status && STATUS_LABEL[status]) || UNKNOWN
}

export function payLabel(mode: string | null | undefined): string {
  return (mode && PAY_LABEL[mode]) || UNKNOWN
}

/**
 * `quota: null` 是**合法配置**（不限量），渲染成 `0 / 0` 会把它说成「一个都不收」，
 * 正好反过来。
 */
export function quotaLabel(task: Pick<TaskRow, 'quota' | 'claimed_count'>): string {
  if (task.quota === null) return '不限'
  return `${task.claimed_count} / ${task.quota}`
}

export function isActionable(status: string): boolean {
  return status === 'draft' || status === 'paused'
}

/**
 * `/merchant/tasks/abc` 不该打过去讨一个 404——非法 id 是**已知的错**，
 * 直接渲染「任务不存在」，一个请求都不发。
 */
export function parseTaskId(raw: string | undefined): number | null {
  if (!raw || !/^\d+$/.test(raw)) return null
  const id = Number(raw)
  return Number.isSafeInteger(id) && id > 0 ? id : null
}

// --------------------------------------------------------------------------- #
// 表单模型
// --------------------------------------------------------------------------- #
export interface TaskForm {
  title: string
  category: string
  description: string
  cover_url: string
  requirement: string
  /** 逗号分隔的原文。拆成数组只在提交时做 */
  tags: string
  quota: string
  /** `<input type="datetime-local">` 的值 = 北京墙上时钟 */
  start_at: string
  end_at: string
  pay_mode: PayMode
  reimburse_pool: string
  reimburse_per_user_limit: string
}

export function emptyForm(): TaskForm {
  const now = Date.now()
  const start = new Date(now + 60 * 60 * 1000).toISOString()
  const end = new Date(now + 7 * 24 * 60 * 60 * 1000).toISOString()
  return {
    title: '',
    category: '',
    description: '',
    cover_url: '',
    requirement: '',
    tags: '',
    quota: '',
    start_at: toInputValue(start),
    end_at: toInputValue(end),
    pay_mode: 'merchant_pay',
    reimburse_pool: '',
    reimburse_per_user_limit: '',
  }
}

/** 数字 → 输入框字符串。`null` 是空串，**不是 `'0'`** */
function num(value: number | null | undefined): string {
  return typeof value === 'number' ? String(value) : ''
}

export function formFromTask(task: TaskRow): TaskForm {
  return {
    ...emptyForm(),
    title: task.title ?? '',
    category: task.category ?? '',
    description: task.description ?? '',
    cover_url: task.cover_url ?? '',
    requirement: task.requirement ?? '',
    tags: (task.tags ?? []).join(','),
    quota: num(task.quota),
    start_at: toInputValue(task.start_at),
    end_at: toInputValue(task.end_at),
    pay_mode: task.pay_mode === 'user_pay_reimburse' ? 'user_pay_reimburse' : 'merchant_pay',
    reimburse_pool: num(task.reimburse_pool),
    reimburse_per_user_limit: num(task.reimburse_per_user_limit),
  }
}

export function parseTags(raw: string): string[] {
  return raw
    .split(/[,，]/)
    .map((tag) => tag.trim())
    .filter(Boolean)
}

// --------------------------------------------------------------------------- #
// 本地校验（后端规则的复述）
// --------------------------------------------------------------------------- #
export type FieldErrors = Record<string, string>

const MAX_TAGS = 5
const MAX_TAG_LEN = 16

export function validateForm(form: TaskForm): FieldErrors {
  const errors: FieldErrors = {}

  const title = form.title.trim()
  if (title.length < 2 || title.length > 64) errors.title = '标题 2~64 字'

  if (!form.category.trim()) errors.category = '请填品类'
  else if (form.category.trim().length > 32) errors.category = '品类最多 32 字'

  const description = form.description
  if (description.length < 10 || description.length > 2000) {
    errors.description = '描述 10~2000 字'
  }

  if (form.cover_url.trim().length > 512) errors.cover_url = '封面图链接最多 512 字'

  const tags = parseTags(form.tags)
  if (tags.length > MAX_TAGS) errors.tags = `标签最多 ${MAX_TAGS} 个`
  else if (tags.some((tag) => tag.length > MAX_TAG_LEN)) {
    errors.tags = `每个标签最多 ${MAX_TAG_LEN} 字`
  }

  if (!form.start_at) errors.start_at = '请选开始时间'
  if (!form.end_at) errors.end_at = '请选结束时间'
  // 相等也不行：`end_at <= start_at` 后端一样拒
  else if (form.start_at && form.end_at <= form.start_at) {
    errors.end_at = '结束时间必须晚于开始时间'
  }
  // 后端 start_at 不得早于 now（1 秒容差）；本地先拦，避免只看到「请求失败 422」
  if (form.start_at && !errors.start_at) {
    const iso = toIso(form.start_at)
    if (iso && new Date(iso).getTime() < Date.now() - 1000) {
      errors.start_at = '开始时间不能早于现在'
    }
  }

  const quota = form.quota.trim()
  if (quota !== '' && !(Number.isInteger(Number(quota)) && Number(quota) >= 1)) {
    errors.quota = '名额要么留空（不限），要么是不小于 1 的整数'
  }

  if (form.pay_mode === 'user_pay_reimburse') {
    const pool = form.reimburse_pool.trim()
    const limit = form.reimburse_per_user_limit.trim()
    const poolNum = Number(pool)
    const limitNum = Number(limit)

    if (pool === '' || !Number.isInteger(poolNum) || poolNum < 1) {
      errors.reimburse = '用户垫付必须填报销池额度（不小于 1 的整数）'
    } else if (limit === '' || !Number.isInteger(limitNum) || limitNum < 1) {
      errors.reimburse = '用户垫付必须填单用户报销上限（不小于 1 的整数）'
    } else if (limitNum > poolNum) {
      errors.reimburse = '单用户报销上限不得大于报销池额度'
    }
  }

  return errors
}

// --------------------------------------------------------------------------- #
// 请求体
// --------------------------------------------------------------------------- #
function quotaOf(form: TaskForm): number | null {
  const raw = form.quota.trim()
  return raw === '' ? null : Number(raw)
}

function reimburseOf(form: TaskForm): Record<string, number> {
  if (form.pay_mode !== 'user_pay_reimburse') return {}
  return {
    reimburse_pool: Number(form.reimburse_pool.trim()),
    reimburse_per_user_limit: Number(form.reimburse_per_user_limit.trim()),
  }
}

/** 建任务体。**不含** tiers / reward——奖励规则走自己的端点（`MC-08`） */
export function buildCreateBody(form: TaskForm): Record<string, unknown> {
  return {
    title: form.title.trim(),
    description: form.description,
    category: form.category.trim(),
    cover_url: form.cover_url.trim() || null,
    requirement: form.requirement.trim() || null,
    tags: parseTags(form.tags),
    start_at: toIso(form.start_at),
    end_at: toIso(form.end_at),
    quota: quotaOf(form),
    pay_mode: form.pay_mode,
    ...reimburseOf(form),
  }
}

/**
 * 已修改字段的子集。
 *
 * 后端读 `model_dump(exclude_unset=True)`——**没传才算没改**。把整个 task
 * 原样发回去，连一字未动的 `title` 也会被当成「要改 title」，published 下
 * 立刻撞 409。这是本模块最容易「屏幕上看着也对」的一处。
 */
function changedFields(
  form: TaskForm,
  initial: TaskForm,
  editable: readonly string[],
): string[] {
  const now = snapshot(form)
  const before = snapshot(initial)
  return editable.filter((field) => !same(now[field], before[field]))
}

function same(a: unknown, b: unknown): boolean {
  if (Array.isArray(a) || Array.isArray(b)) {
    return JSON.stringify(a) === JSON.stringify(b)
  }
  return a === b
}

/** 归一化快照：比较「语义」而不是「输入框里的字符串」 */
function snapshot(form: TaskForm): Record<string, unknown> {
  return {
    title: form.title.trim(),
    category: form.category.trim(),
    description: form.description,
    cover_url: form.cover_url.trim() || null,
    requirement: form.requirement.trim() || null,
    tags: parseTags(form.tags),
    start_at: form.start_at,
    end_at: form.end_at,
    quota: quotaOf(form),
  }
}

const WIRE: Record<string, (form: TaskForm) => unknown> = {
  start_at: (form) => toIso(form.start_at),
  end_at: (form) => toIso(form.end_at),
}

/** draft 可改全部（`pay_mode` 除外：中途改计费口径后端一律 409） */
export const DRAFT_EDITABLE = [
  'title',
  'description',
  'category',
  'cover_url',
  'requirement',
  'tags',
  'start_at',
  'end_at',
  'quota',
] as const

/** published 只认这四个 + quota（后端 `PUBLISHED_EDITABLE` 的复述） */
export const PUBLISHED_EDITABLE = ['description', 'end_at', 'requirement', 'quota'] as const

export function buildPatchBody(
  form: TaskForm,
  initial: TaskForm,
  editable: readonly string[],
): Record<string, unknown> {
  const body: Record<string, unknown> = {}
  const now = snapshot(form)
  for (const field of changedFields(form, initial, editable)) {
    body[field] = WIRE[field] ? WIRE[field](form) : now[field]
  }
  return body
}

// --------------------------------------------------------------------------- #
// 奖励阶梯
// --------------------------------------------------------------------------- #
export const DEFAULT_TIERS: RewardTier[] = [{ min: 0, max: null, reward: {} }]

export function tiersFromRule(rule: RewardRule | null): RewardTier[] {
  const tiers = rule?.tiers
  if (!Array.isArray(tiers) || tiers.length === 0) return DEFAULT_TIERS.map(cloneTier)
  return tiers.map(cloneTier)
}

export function cloneTier(tier: RewardTier): RewardTier {
  return { min: tier.min, max: tier.max, reward: { ...tier.reward } }
}

/**
 * 后端 `validate_tiers` 的复述，用于「校验」按钮的本地前置检查。
 * 返回的是**逐条违规说明**，与后端 422 里的 `detail.violations` 同一个形状，
 * 于是两条来源共用同一段渲染。
 */
export function validateTiers(tiers: RewardTier[]): string[] {
  if (tiers.length === 0) return ['至少要有一档']

  const violations: string[] = []

  tiers.forEach((tier, i) => {
    const where = `第 ${i + 1} 档`
    const reward = tier.reward ?? {}
    const hasBenefit = typeof reward.benefit === 'string' && reward.benefit.trim().length > 0
    const hasAny =
      typeof reward.cash === 'number' ||
      typeof reward.points === 'number' ||
      typeof reward.coupon_id === 'number' ||
      hasBenefit
    if (!hasAny) {
      violations.push(`${where} 的 reward 不能为空（现金 / 积分 / 券 / 权益至少给一项）`)
      return
    }
    for (const key of ['cash', 'points'] as const) {
      const value = reward[key]
      if (value === undefined) continue
      if (!Number.isInteger(value)) {
        violations.push(`${where} 的 reward.${key} 必须是整数（不接受小数）`)
      } else if (value < 0) {
        violations.push(`${where} 的 reward.${key} 不得为负`)
      }
    }
  })

  if (tiers[0].min !== 0) violations.push(`第一档的 min 必须是 0，实际 ${tiers[0].min}`)

  for (let i = 0; i < tiers.length - 1; i += 1) {
    const hi = tiers[i].max
    const next = tiers[i + 1].min
    if (hi === null) {
      violations.push(`第 ${i + 1} 档的 max 为 null（无上限），后面不应再有档位`)
      break
    }
    if (next > hi + 1) {
      violations.push(`第 ${i + 1} 档与第 ${i + 2} 档之间有缺口：${hi + 1}~${next - 1} 无人覆盖`)
    } else if (next <= hi) {
      violations.push(`第 ${i + 1} 档与第 ${i + 2} 档重叠`)
    }
  }

  const last = tiers[tiers.length - 1]
  if (last.max !== null) {
    violations.push(`最后一档的 max 必须是空（否则超过 ${last.max} 的拿不到奖励）`)
  }

  return violations
}

export function rewardBody(
  tiers: RewardTier[],
  metric: string,
  maxRewardPerUser: string,
): Record<string, unknown> {
  const max = maxRewardPerUser.trim()
  return {
    metric,
    tiers,
    // 键必须在：后端用 `None` 表示不封顶，缺键与 null 在这里等价，
    // 但显式带上能让请求体自解释
    max_reward_per_user: max === '' ? null : Number(max),
  }
}
