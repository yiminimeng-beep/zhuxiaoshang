import { useEffect, useState, type FormEvent } from 'react'
import { Navigate } from 'react-router-dom'

import { ApiError, apiFetch } from '../api/client'
import { deviceFingerprint } from '../api/device'
import {
  forgetTarget,
  landingPath,
  readTarget,
  writeSession,
  type Role,
  type Session,
} from '../api/session'
import { useSession } from '../auth/useSession'

const IDENTITY_LABEL: Record<Role, string> = { merchant: '商家登录', customer: '用户登录' }
const IDENTITY_NOUN: Record<Role, string> = { merchant: '商户', customer: '客户' }

const PASSWORD_ERROR = '账号或密码错误'
const ADMIN_NOTICE = '管理员请使用平台后台'
const GENERIC_ERROR = '登录失败，请稍后再试'

interface LoginResponse {
  access_token: string
  refresh_token: string
  user: { role: string; nickname?: string | null }
}

/**
 * 已有登录态（含**刚登录成功**）：落到被拦住时记下的目标，没有目标才回首页。
 *
 * 目标只认本轮真存在的路径——`/admin` 这种记了也只会掉进 404，
 * 落回自己的首页比落进空白页强（RT-11）。
 */
function AlreadyIn({ session }: { session: Session }) {
  // 冻结在首次渲染：forget 之后不能再让目的地跟着变
  const [destination] = useState(() => landingPath(session, readTarget()))

  useEffect(() => {
    forgetTarget()
  }, [])

  return <Navigate to={destination} replace />
}

export function LoginPage() {
  const session = useSession()
  const [identity, setIdentity] = useState<Role | null>(null)
  const [account, setAccount] = useState('')
  const [password, setPassword] = useState('')
  const [message, setMessage] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  if (session) return <AlreadyIn session={session} />

  function choose(next: Role) {
    setIdentity(next)
    setMessage(null)
  }

  async function submit(event: FormEvent) {
    event.preventDefault()
    if (submitting) return

    // 没选身份就提交：提示，且**一个请求都不发**
    if (!identity) {
      setMessage('请先选择登录身份')
      return
    }

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

      const role = data.user.role as Role
      // admin 不走这个前台
      if (data.user.role === 'admin') {
        setMessage(ADMIN_NOTICE)
        return
      }
      // 商户账号走用户登录：不暴露身份，只当密码错。仍不写入登录态。
      if (identity === 'customer' && data.user.role === 'merchant') {
        setMessage('密码错误')
        return
      }
      // 其余身份不符：**不写入登录态**。两个按钮不是 UI 摆设。
      if (data.user.role !== identity) {
        const noun = IDENTITY_NOUN[role] ?? '其他'
        setMessage(`该账号是${noun}账号，请用「${IDENTITY_LABEL[role] ?? '正确的入口'}」`)
        return
      }

      writeSession({
        access: data.access_token,
        refresh: data.refresh_token,
        role: identity,
        nickname: data.user.nickname ?? '',
      })
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) setMessage(PASSWORD_ERROR)
      // 403 用后端那句话（账号已注销 / 账号不可用），不用通用文案
      else if (error instanceof ApiError && error.detail) setMessage(error.detail)
      else setMessage(GENERIC_ERROR)
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <main className="login">
      <div className="login__wash" aria-hidden="true" />
      <div className="login__frame shell">
        <header className="login__brand">
          <p className="login__wordmark">助小商</p>
          <p className="lede login__lede">先选身份，再填账号密码。</p>
        </header>

        <form className="login__panel stack" onSubmit={submit} noValidate>
          {identity === null ? (
            <div className="identity-grid" role="group" aria-label="选择登录身份">
              <button
                type="button"
                className="identity identity--merchant"
                onClick={() => choose('merchant')}
              >
                商家登录
              </button>
              <button
                type="button"
                className="identity identity--customer"
                onClick={() => choose('customer')}
              >
                用户登录
              </button>
            </div>
          ) : (
            <div className="stack login__form-block">
              <div className="row row--between">
                <span
                  className={`badge${identity === 'merchant' ? ' badge--merchant' : ''}`}
                  data-testid="identity-badge"
                >
                  {IDENTITY_LABEL[identity]}
                </span>
                <button
                  type="button"
                  className="btn btn--text"
                  onClick={() => {
                    setIdentity(null)
                    setMessage(null)
                  }}
                >
                  重新选择身份
                </button>
              </div>

              <div className="field">
                <label className="field__label" htmlFor="login-account">
                  账号
                </label>
                <input
                  id="login-account"
                  name="account"
                  type="text"
                  className="field__input"
                  autoComplete="username"
                  value={account}
                  onChange={(event) => setAccount(event.target.value)}
                />
              </div>

              <div className="field">
                <label className="field__label" htmlFor="login-password">
                  密码
                </label>
                <input
                  id="login-password"
                  name="password"
                  type="password"
                  className="field__input"
                  autoComplete="current-password"
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                />
              </div>
            </div>
          )}

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
      </div>
    </main>
  )
}
