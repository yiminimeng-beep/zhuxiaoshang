import { useEffect, useState, type FormEvent } from 'react'
import { Navigate } from 'react-router-dom'

import { ApiError, apiFetch } from '../api/client'
import { deviceFingerprint } from '../api/device'
import { forgetTarget, landingPath, readTarget, writeSession } from '../api/session'
import { useSession } from '../auth/useSession'

const PASSWORD_ERROR = '账号或密码错误'
const NOT_ADMIN = '该账号不是管理员账号'
const GENERIC_ERROR = '登录失败，请稍后再试'

interface LoginResponse {
  access_token: string
  refresh_token: string
  user: { role: string; nickname?: string | null }
}

/** 已有登录态（含刚登录成功）：落到被拦住时记下的目标，没有目标就回收件箱 */
function AlreadyIn() {
  // 冻结在首次渲染：forget 之后不能再让目的地跟着变
  const [destination] = useState(() => landingPath(readTarget()))

  useEffect(() => {
    forgetTarget()
  }, [])

  return <Navigate to={destination} replace />
}

export function AdminLoginPage() {
  const session = useSession()
  const [account, setAccount] = useState('')
  const [password, setPassword] = useState('')
  const [message, setMessage] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  if (session) return <AlreadyIn />

  async function submit(event: FormEvent) {
    event.preventDefault()
    if (submitting) return

    setMessage(null)
    setSubmitting(true)
    try {
      const data = await apiFetch<LoginResponse>('/api/auth/login', {
        method: 'POST',
        auth: false,
        body: {
          identifier: account.trim().toLowerCase(),
          password,
          device_fingerprint: deviceFingerprint(),
        },
      })

      // 后台只认 admin。商家/客户账号在这里登录 → **不写入登录态**，
      // 否则商户的令牌会以「后台已登录」的形式留在本地。
      if (data.user.role !== 'admin') {
        setMessage(NOT_ADMIN)
        return
      }

      writeSession({
        access: data.access_token,
        refresh: data.refresh_token,
        role: 'admin',
        nickname: data.user.nickname ?? '',
      })
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) setMessage(PASSWORD_ERROR)
      // 403 用后端那句话（账号已注销 / 账号不可用 / 已封禁）
      else if (error instanceof ApiError && error.detail) setMessage(error.detail)
      else setMessage(GENERIC_ERROR)
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <main className="shell page">
      <header className="stack">
        <h1 className="page__title">助小商 · 平台后台</h1>
        <p className="lede">管理员登录。商家与用户请用主站。</p>
      </header>

      <form className="stack panel login-panel" onSubmit={submit} noValidate>
        <div className="field">
          <label className="field__label" htmlFor="admin-account">
            账号
          </label>
          <input
            id="admin-account"
            name="account"
            type="text"
            className="field__input"
            autoComplete="username"
            value={account}
            onChange={(event) => setAccount(event.target.value)}
          />
        </div>

        <div className="field">
          <label className="field__label" htmlFor="admin-password">
            密码
          </label>
          <input
            id="admin-password"
            name="password"
            type="password"
            className="field__input"
            autoComplete="current-password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
          />
        </div>

        <button
          type="submit"
          className="btn btn--primary btn--lg btn--block"
          disabled={submitting}
        >
          {submitting ? '登录中…' : '登录'}
        </button>

        {message ? (
          <p className="note note--error" role="alert">
            {message}
          </p>
        ) : null}
      </form>
    </main>
  )
}
