import { useEffect, type ReactNode } from 'react'
import { Navigate, useLocation } from 'react-router-dom'

import { homePath, rememberTarget, type Role } from '../api/session'
import { useSession } from '../auth/useSession'

/**
 * 准入只有两条规则，且都**静默**：
 *
 * - 没有登录态 → 去 `/login`，顺手记下原目标（登录成功后回跳）。
 * - 角色不符 → 去自己的首页，**不报错**。商户误点客户页不是错误，
 *   弹一句「无权访问」只会让人以为自己点坏了。
 */
export function Guard({ role, children }: { role?: Role; children: ReactNode }) {
  const session = useSession()
  const location = useLocation()

  useEffect(() => {
    if (!session) rememberTarget(location.pathname)
  }, [session, location.pathname])

  if (!session) return <Navigate to="/login" replace />
  if (role && session.role !== role) return <Navigate to={homePath(session.role)} replace />
  return <>{children}</>
}
