/**
 * 直写 `localStorage` 模拟「已登录后台的浏览器」。**不写就是没登录。**
 *
 * 键名写死成字面量（与 8001 的 `zxs.auth` **不同**）：生产期两套前端同源，
 * `localStorage` 按源分而不按路径分，共用键名会让商家的登录态被后台读成
 * admin 登录态。这里写死，应用哪天换了键，`AD-01` 会红——那是**有用的红**。
 */

export const AUTH_KEY = 'zxs.admin.auth'
export const REDIRECT_KEY = 'zxs.admin.redirect'

export interface AuthState {
  access: string
  refresh: string
  role: 'admin'
  nickname: string
}

export function seedAdmin(state: Partial<AuthState> = {}): void {
  const full: AuthState = {
    access: state.access ?? 'seeded-admin-access',
    refresh: state.refresh ?? 'seeded-admin-refresh',
    role: 'admin',
    nickname: state.nickname ?? '运营小张',
  }
  localStorage.setItem(AUTH_KEY, JSON.stringify(full))
}

/** 塞一个**非 admin** 的登录态：用来验「后台读到非 admin 令牌 = 没登录」 */
export function seedForeignSession(): void {
  localStorage.setItem(
    AUTH_KEY,
    JSON.stringify({
      access: 'merchant-access',
      refresh: 'merchant-refresh',
      role: 'merchant',
      nickname: '巷口咖啡',
    }),
  )
}

export function readAuth(): AuthState | null {
  const raw = localStorage.getItem(AUTH_KEY)
  if (!raw) return null
  try {
    return JSON.parse(raw) as AuthState
  } catch {
    return null
  }
}

export function readRedirect(): string | null {
  return sessionStorage.getItem(REDIRECT_KEY)
}
