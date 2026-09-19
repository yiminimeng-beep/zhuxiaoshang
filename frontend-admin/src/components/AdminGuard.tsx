import { useEffect, type ReactNode } from 'react'
import { Navigate, useLocation } from 'react-router-dom'

import { rememberTarget } from '../api/session'
import { useSession } from '../auth/useSession'

/**
 * 后台准入只有一条规则：**没有 admin 登录态就去 `/login`**，顺手记下原目标。
 *
 * 与 8001 的 `Guard` 不同，这里没有「角色不符 → 静默弹回自己首页」那一支：
 * 后台只有 admin 一种身份，「登录了但不是 admin」在 `readSession()` 里
 * 已经被当成没登录（非 admin 的令牌根本读不出来）。少一个分支，
 * 就少一处两边漂移的地方。
 */
export function AdminGuard({ children }: { children: ReactNode }) {
  const session = useSession()
  const location = useLocation()

  useEffect(() => {
    if (!session) rememberTarget(location.pathname)
  }, [session, location.pathname])

  if (!session) return <Navigate to="/login" replace />
  return <>{children}</>
}
