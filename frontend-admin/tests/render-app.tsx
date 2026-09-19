import { render, screen } from '@testing-library/react'
import { MemoryRouter, useLocation } from 'react-router-dom'

import { App } from '../src/App'

/**
 * 探针：把当前路径渲进 DOM。
 *
 * 「跳没跳 /login」不能靠文案猜——登录页和收件箱都可能出现同一个词。
 * 路径是路由器的事实，探针把它变成可断言的东西。
 *
 * 带查询串（`pathname + search`）：用户管理页的筛选就存在 URL 里，
 * 「返回后筛选还在」只能靠它断。既有用例断言的都是无查询串的路径，不受影响。
 *
 * 测试里不带 basename：`/admin` 那段前缀由 `main.tsx` 的 `BrowserRouter`
 * 加，`App` 里的路由是相对路径。这里直接给相对路径更省事，也少一层噪音。
 */
function LocationProbe() {
  const location = useLocation()
  return <span data-testid="location">{location.pathname + location.search}</span>
}

export function renderApp({ route = '/' }: { route?: string } = {}) {
  return render(
    <MemoryRouter initialEntries={[route]}>
      <App />
      <LocationProbe />
    </MemoryRouter>,
  )
}

export function currentPath(): string {
  return screen.getByTestId('location').textContent ?? ''
}
