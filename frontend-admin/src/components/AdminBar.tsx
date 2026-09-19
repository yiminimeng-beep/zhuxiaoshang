import { useState } from 'react'

import { apiFetch } from '../api/client'
import { clearSession, type Session } from '../api/session'
import { AdminTabs } from './AdminTabs'

/**
 * 顶栏 + 页签轨。与 8001 同款 N9 边缘对齐极简：左=身份，右=唯一的退出动作。
 *
 * 角色徽章这里固定是「管理员」——后台不存在第二种身份。文案仍走一处常量，
 * 免得将来说要分运营/超管时散在 JSX 里。
 *
 * 页签轨从这里一起出：它是**后台外框**的一部分，不是某一页的东西。
 * 四个页面各自写一遍的话，迟早有一页忘加，而那一页看上去「就是少个导航」。
 */
const ROLE_LABEL = '管理员'

export function AdminBar({ session }: { session: Session }) {
  const [leaving, setLeaving] = useState(false)

  async function signOut() {
    if (leaving) return
    setLeaving(true)
    try {
      await apiFetch('/api/auth/logout', { method: 'POST' })
    } catch {
      // 退出接口炸了也照样清。退出失败却把用户留在原地，比退出失败更糟。
    } finally {
      clearSession()
    }
  }

  return (
    <>
      <header className="app-bar" data-testid="app-bar">
        <span className="app-bar__identity">
          <span className="app-bar__name">{session.nickname}</span>
          <span className="badge" data-testid="role-badge">
            {ROLE_LABEL}
          </span>
        </span>
        <button
          type="button"
          className="btn btn--text"
          onClick={signOut}
          disabled={leaving}
        >
          退出登录
        </button>
      </header>
      <AdminTabs />
    </>
  )
}
