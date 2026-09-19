/**
 * 登录态的唯一真相：`localStorage`。
 *
 * 前端**不解码 JWT**——角色一律以登录响应里的 `user.role` 为准，令牌只当
 * 不透明字符串透传。真去解析令牌会引入「令牌里的 role 与后端不一致时听谁的」
 * 这种本不该存在的分支。
 *
 * ⚠️ 已知取舍：`localStorage` 对 XSS 不设防。本轮不做 httpOnly Cookie——
 * 8001/8002 跨源且本地是 http，`SameSite=None; Secure` 的 Cookie 发不出去。
 * **生产上线（HTTPS + 同源）前必须改。**
 */

export const AUTH_KEY = 'zxs.auth'
export const REDIRECT_KEY = 'zxs.redirect'

export type Role = 'merchant' | 'customer'

export interface Session {
  access: string
  refresh: string
  role: Role
  nickname: string
}

type Listener = () => void

const listeners = new Set<Listener>()

function emit(): void {
  for (const listener of [...listeners]) listener()
}

export function subscribeSession(listener: Listener): () => void {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}

/**
 * 解析结果按**原始字符串**缓存。
 *
 * 两个原因，缺一不可：
 * 1. `useSyncExternalStore` 要求快照引用稳定，每次 `JSON.parse` 出新对象会死循环；
 * 2. 测试会直接往 `localStorage` 里塞登录态，只认字符串才不会被陈旧的解析结果骗到。
 */
let cachedRaw: string | null = null
let cachedParsed: Session | null = null

function parseSession(raw: string): Session | null {
  try {
    const value = JSON.parse(raw) as Partial<Session> | null
    if (!value?.access || !value.refresh) return null
    if (value.role !== 'merchant' && value.role !== 'customer') return null
    return {
      access: value.access,
      refresh: value.refresh,
      role: value.role,
      nickname: value.nickname ?? '',
    }
  } catch {
    return null
  }
}

export function readSession(): Session | null {
  const raw = localStorage.getItem(AUTH_KEY)
  if (raw !== cachedRaw) {
    cachedRaw = raw
    cachedParsed = raw === null ? null : parseSession(raw)
  }
  return cachedParsed
}

export function writeSession(session: Session): void {
  localStorage.setItem(AUTH_KEY, JSON.stringify(session))
  emit()
}

export function clearSession(): void {
  localStorage.removeItem(AUTH_KEY)
  emit()
}

export function homePath(role: Role): string {
  return role === 'merchant' ? '/merchant' : '/customer'
}

/** 被拦住时记下原目标，登录成功后回跳（sessionStorage：关掉标签页就忘） */
export function rememberTarget(path: string): void {
  sessionStorage.setItem(REDIRECT_KEY, path)
}

export function readTarget(): string | null {
  return sessionStorage.getItem(REDIRECT_KEY)
}

export function forgetTarget(): void {
  sessionStorage.removeItem(REDIRECT_KEY)
}

/**
 * 记住的目标只认**本轮存在的受保护路径**。
 *
 * `/admin` 这类路径记了也没用——登进来还是会掉进 404。落回自己的首页
 * 比落进空白页强（RT-11）。
 *
 * 带 `:id` 的页面按**前缀**认：`/merchant/tasks/7` 这种带参路径列不全，
 * 但它确实存在。前缀表必须窄——放宽到 `/merchant` 会让 `/admin` 那种
 * 判断失效，等于把 RT-11 拆了。
 */
const KNOWN_TARGETS = [
  '/merchant',
  '/merchant/tasks',
  '/merchant/tasks/new',
  '/merchant/reviews',
  '/customer',
  '/customer/tasks',
  '/customer/claims',
  '/customer/rewards',
  '/customer/posts',
  '/customer/works',
  '/',
]
const KNOWN_PREFIXES = [
  '/merchant/tasks/',
  '/customer/tasks/',
  '/customer/rewards/',
  '/customer/studio/',
]

export function landingPath(session: Session, target: string | null): string {
  if (target) {
    if (KNOWN_TARGETS.includes(target)) return target
    if (KNOWN_PREFIXES.some((prefix) => target.startsWith(prefix))) return target
  }
  return homePath(session.role)
}
