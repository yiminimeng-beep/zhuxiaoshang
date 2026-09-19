/**
 * 后台登录态的唯一真相：`localStorage`。与 8001 的商家/用户端**键名不同**。
 *
 * 为什么不能共用 `zxs.auth`：生产期两个前端都挂在 8002 同一个源下（`/` 与
 * `/admin`），`localStorage` 是**按源**分的、不是按路径。共用键名的话，
 * 商家在 8001 登录后打开 `/admin`，后台会把商家的令牌当成 admin 登录态读出来。
 * 分开存，两边互不干扰。
 *
 * 与 8001 一样：**不解码 JWT**，角色只认登录响应里的 `user.role`。
 */

export const AUTH_KEY = 'zxs.admin.auth'
export const REDIRECT_KEY = 'zxs.admin.redirect'

/** 后台只有这一个角色——`role` 字段仍保留，好让「令牌里带了谁」可断言 */
export type Role = 'admin'

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
 * 解析结果按**原始字符串**缓存：`useSyncExternalStore` 要求快照引用稳定，
 * 每次 `JSON.parse` 出新对象会死循环；而测试直写 `localStorage`，
 * 只认字符串才不会被陈旧的解析结果骗到。
 */
let cachedRaw: string | null = null
let cachedParsed: Session | null = null

function parseSession(raw: string): Session | null {
  try {
    const value = JSON.parse(raw) as Partial<Session> | null
    if (!value?.access || !value.refresh) return null
    // 写进 `zxs.admin.auth` 但不是 admin 的，一律当没登录——
    // 准入只认「是不是 admin」，不认「有没有令牌」
    if (value.role !== 'admin') return null
    return {
      access: value.access,
      refresh: value.refresh,
      role: 'admin',
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

/** 记住的目标只认后台真有的路径，免得登进来掉进 404 */
const KNOWN_TARGETS = ['/feedback', '/cost', '/users', '/exceptions', '/']

/**
 * `/users/12` 这种深链目标按**前缀**认——逐个列既列不全，也会跟着新页面漂。
 * 前缀只放 `/users/`（唯一的带参路由），不写成「凡 `/` 开头的都放行」。
 */
const KNOWN_PREFIXES = ['/users/']

export function landingPath(target: string | null): string {
  if (target && (KNOWN_TARGETS.includes(target) || KNOWN_PREFIXES.some((p) => target.startsWith(p)))) {
    return target
  }
  return '/feedback'
}
