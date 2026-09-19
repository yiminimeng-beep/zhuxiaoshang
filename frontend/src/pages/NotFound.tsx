import { Link } from 'react-router-dom'

import { useSession } from '../auth/useSession'
import { homePath } from '../api/session'

/** 准入先于 404：未登录连「这个路径存不存在」都不该看到（RT-12） */
export function NotFound() {
  const session = useSession()

  return (
    <main className="shell page">
      <h1 className="page__title">页面不存在</h1>
      <p className="lede">这个地址可能已经失效，或者从来就没有过。</p>
      {session ? <Link className="link" to={homePath(session.role)}>回到首页</Link> : null}
    </main>
  )
}
