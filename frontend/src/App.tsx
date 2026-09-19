import { Navigate, Route, Routes } from 'react-router-dom'

import { homePath } from './api/session'
import { useSession } from './auth/useSession'
import { Guard } from './components/Guard'
import { CustomerClaims } from './pages/CustomerClaims'
import { CustomerKeys } from './pages/CustomerKeys'
import { CustomerHome } from './pages/CustomerHome'
import { CustomerPosts } from './pages/CustomerPosts'
import { CustomerRewardDetail } from './pages/CustomerRewardDetail'
import { CustomerRewards } from './pages/CustomerRewards'
import { CustomerStudio } from './pages/CustomerStudio'
import { CustomerWorks } from './pages/CustomerWorks'
import { CustomerTaskDetail } from './pages/CustomerTaskDetail'
import { CustomerTaskList } from './pages/CustomerTaskList'
import { LoginPage } from './pages/LoginPage'
import { MerchantHome } from './pages/MerchantHome'
import { MerchantReviews } from './pages/MerchantReviews'
import { MerchantTaskDetail } from './pages/MerchantTaskDetail'
import { MerchantTaskList } from './pages/MerchantTaskList'
import { MerchantTaskNew } from './pages/MerchantTaskNew'
import { NotFound } from './pages/NotFound'

function HomeRedirect() {
  const session = useSession()
  if (!session) return null
  return <Navigate to={homePath(session.role)} replace />
}

/**
 * `/login` 是**唯一**在守卫之外的路径。
 *
 * 其余全部走 `Guard`——包括 404：未登录时连「这个路径存不存在」都不该看到
 * （准入先于 404）。角色准入也由同一个组件管，所以「未登录」与「角色不符」
 * 的处理只有一处，不会两边漂。
 */
export function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route
        path="/"
        element={
          <Guard>
            <HomeRedirect />
          </Guard>
        }
      />
      <Route
        path="/merchant"
        element={
          <Guard role="merchant">
            <MerchantHome />
          </Guard>
        }
      />
      <Route
        path="/merchant/reviews"
        element={
          <Guard role="merchant">
            <MerchantReviews />
          </Guard>
        }
      />
      {/* 静态段在前：`new` 不是 id，不能落进详情页的 `:id` */}
      <Route
        path="/merchant/tasks"
        element={
          <Guard role="merchant">
            <MerchantTaskList />
          </Guard>
        }
      />
      <Route
        path="/merchant/tasks/new"
        element={
          <Guard role="merchant">
            <MerchantTaskNew />
          </Guard>
        }
      />
      <Route
        path="/merchant/tasks/:id"
        element={
          <Guard role="merchant">
            <MerchantTaskDetail />
          </Guard>
        }
      />
      <Route
        path="/customer"
        element={
          <Guard role="customer">
            <CustomerHome />
          </Guard>
        }
      />
      <Route
        path="/customer/tasks"
        element={
          <Guard role="customer">
            <CustomerTaskList />
          </Guard>
        }
      />
      <Route
        path="/customer/tasks/:id"
        element={
          <Guard role="customer">
            <CustomerTaskDetail />
          </Guard>
        }
      />
      <Route
        path="/customer/claims"
        element={
          <Guard role="customer">
            <CustomerClaims />
          </Guard>
        }
      />
      <Route
        path="/customer/rewards"
        element={
          <Guard role="customer">
            <CustomerRewards />
          </Guard>
        }
      />
      <Route
        path="/customer/rewards/:merchantId"
        element={
          <Guard role="customer">
            <CustomerRewardDetail />
          </Guard>
        }
      />
      <Route
        path="/customer/works"
        element={
          <Guard role="customer">
            <CustomerWorks />
          </Guard>
        }
      />
      <Route
        path="/customer/keys"
        element={
          <Guard role="customer">
            <CustomerKeys />
          </Guard>
        }
      />
      <Route
        path="/customer/studio/:jobId"
        element={
          <Guard role="customer">
            <CustomerStudio />
          </Guard>
        }
      />
      <Route
        path="/customer/posts"
        element={
          <Guard role="customer">
            <CustomerPosts />
          </Guard>
        }
      />
      <Route
        path="*"
        element={
          <Guard>
            <NotFound />
          </Guard>
        }
      />
    </Routes>
  )
}
