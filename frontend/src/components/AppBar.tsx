import { useState } from 'react'
import { Link } from 'react-router-dom'

import { apiFetch } from '../api/client'
import { clearSession, homePath, type Role, type Session } from '../api/session'

const ROLE_LABEL: Record<Role, string> = { merchant: '商户', customer: '客户' }

/** N9 边缘对齐极简：左=字标+身份，右=唯一的退出动作 */
export function AppBar({ session }: { session: Session }) {
  const [leaving, setLeaving] = useState(false)

  async function signOut() {
    if (leaving) return
    setLeaving(true)
    try {
      await apiFetch('/api/auth/logout', { method: 'POST' })
    } catch {
      // 退出接口炸了也照样清（spec：无论成败都清空本地并跳 /login）。
      // 退出失败却把用户留在原地，比退出失败更糟。
    } finally {
      clearSession()
    }
  }

  return (
    <header className="app-bar" data-testid="app-bar">
      <div className="app-bar__cluster">
        <Link className="app-bar__mark" to={homePath(session.role)}>
          助小商
        </Link>
        <span className="app-bar__identity">
          <span className="app-bar__name">{session.nickname}</span>
          <span
            className={`badge${session.role === 'merchant' ? ' badge--merchant' : ''}`}
            data-testid="role-badge"
          >
            {ROLE_LABEL[session.role]}
          </span>
        </span>
      </div>
      <button
        type="button"
        className="btn btn--text"
        onClick={signOut}
        disabled={leaving}
      >
        退出登录
      </button>
    </header>
  )
}
