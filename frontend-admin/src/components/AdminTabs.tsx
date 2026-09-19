import { NavLink } from 'react-router-dom'

/**
 * 页签轨。四个目的地**不用侧栏**：四个条目白吃 1/4 屏宽，
 * 而这一层只需要一个横向的、当前项带 accent 下划线的条。
 *
 * 用 `NavLink` 而不是手写 `active`：它自己会挂 `aria-current="page"`，
 * 屏幕阅读器与我们的用例读的是同一个事实。
 */
const TABS = [
  { to: '/feedback', label: '反馈' },
  { to: '/cost', label: '成本' },
  { to: '/users', label: '用户' },
  { to: '/exceptions', label: '异常' },
] as const

export function AdminTabs() {
  return (
    <nav className="app-tabs" aria-label="后台导航" data-testid="app-tabs">
      {TABS.map((tab) => (
        <NavLink
          key={tab.to}
          to={tab.to}
          className={({ isActive }) => `app-tabs__item${isActive ? ' app-tabs__item--on' : ''}`}
        >
          {tab.label}
        </NavLink>
      ))}
    </nav>
  )
}
