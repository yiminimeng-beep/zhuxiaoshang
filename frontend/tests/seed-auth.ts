/**
 * 直写 `localStorage` 模拟「已登录的浏览器」。**不写就是没登录。**
 *
 * 键名在这里写死成字面量，不去 import 应用里的常量：真实的浏览器里
 * 只有字符串，没有共享常量。应用哪天换了键，TK-01 会红——那是**有用的红**
 * （已登录用户刷新后被踢下线），不是测试脆。
 */

import type { Role } from './fixtures'

export const AUTH_KEY = 'zxs.auth'
export const REDIRECT_KEY = 'zxs.redirect'

export interface AuthState {
  access: string
  refresh: string
  role: Role
  nickname: string
}

export function seedAuth(state: Partial<AuthState> & { role: Role }): void {
  const full: AuthState = {
    access: state.access ?? 'seeded-access',
    refresh: state.refresh ?? 'seeded-refresh',
    role: state.role,
    nickname: state.nickname ?? (state.role === 'merchant' ? '巷口咖啡' : '小林'),
  }
  localStorage.setItem(AUTH_KEY, JSON.stringify(full))
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

/** 被拦住时记下的原目标（RT-07/08/09 的回跳靠它） */
export function seedRedirect(path: string): void {
  sessionStorage.setItem(REDIRECT_KEY, path)
}

export function readRedirect(): string | null {
  return sessionStorage.getItem(REDIRECT_KEY)
}
