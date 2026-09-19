import { Navigate, Route, Routes } from 'react-router-dom'

import { AdminGuard } from './components/AdminGuard'
import { AdminLoginPage } from './pages/AdminLoginPage'
import { CostDashboard } from './pages/CostDashboard'
import { ExceptionsPage } from './pages/ExceptionsPage'
import { FeedbackInbox } from './pages/FeedbackInbox'
import { UserDetail } from './pages/UserDetail'
import { UserList } from './pages/UserList'

/**
 * `/login` 是**唯一**在守卫之外的路径；`*` 也走守卫——未登录时连
 * 「这个路径存不存在」都不该看到（准入先于 404）。
 *
 * 用户详情是**独立路由**（`/users/:id`）而不是列表里的一块：可直链、可刷新、
 * 可后退。与收件箱与成本看板的「行内展开」是两种不同的读法，有意保留两种。
 */
export function App() {
  return (
    <Routes>
      <Route path="/login" element={<AdminLoginPage />} />
      <Route
        path="/feedback"
        element={
          <AdminGuard>
            <FeedbackInbox />
          </AdminGuard>
        }
      />
      <Route
        path="/cost"
        element={
          <AdminGuard>
            <CostDashboard />
          </AdminGuard>
        }
      />
      <Route
        path="/users"
        element={
          <AdminGuard>
            <UserList />
          </AdminGuard>
        }
      />
      <Route
        path="/users/:id"
        element={
          <AdminGuard>
            <UserDetail />
          </AdminGuard>
        }
      />
      <Route
        path="/exceptions"
        element={
          <AdminGuard>
            <ExceptionsPage />
          </AdminGuard>
        }
      />
      <Route
        path="*"
        element={
          <AdminGuard>
            <Navigate to="/feedback" replace />
          </AdminGuard>
        }
      />
    </Routes>
  )
}
