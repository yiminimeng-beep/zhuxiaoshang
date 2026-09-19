import { render, screen } from '@testing-library/react'
import { MemoryRouter, useLocation } from 'react-router-dom'

import { App } from '../src/App'

/**
 * 探针：把当前路径（含查询串）渲进 DOM。
 *
 * 「跳没跳 /login」不能靠文案猜——登录页和首页都可能出现同一个词。
 * 路径是路由器的事实，探针把它变成可断言的东西。
 *
 * **带查询串**：任务列表的页签存在 URL 里（`?tab=trash`），
 * `ML-05` 要断言的正是「刷新后仍停在回收站」。既有用例断的都是无查询串的
 * 路径，拼上 `search` 对它们没有影响。
 */
function LocationProbe() {
  const location = useLocation()
  return (
    <span data-testid="location">{location.pathname + location.search}</span>
  )
}

export function renderApp({ route = '/' }: { route?: string } = {}) {
  return render(
    <MemoryRouter initialEntries={[route]}>
      <App />
      <LocationProbe />
    </MemoryRouter>,
  )
}

/** 当前落点（探针读回来）。`/login` 就说明被拦了。 */
export function currentPath(): string {
  return screen.getByTestId('location').textContent ?? ''
}
