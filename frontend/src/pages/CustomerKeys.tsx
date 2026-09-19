/**
 * 客户 · 模型与密钥（08 追加 F）。对接既有 `/api/me/model-keys` + `/api/models`。
 */

import { useEffect, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'

import { ApiError, apiFetch, failureMessage } from '../api/client'
import { AppBar } from '../components/AppBar'
import { useSession } from '../auth/useSession'

const PROVIDERS = [
  { id: 'deepseek', label: 'DeepSeek（文案）' },
  { id: 'jimeng', label: '即梦（视频）' },
  { id: 'kling', label: '可灵（视频）' },
] as const

interface KeyRow {
  id: number
  provider: string
  label: string | null
  key_masked: string
  status: string
}

export function CustomerKeys() {
  const session = useSession()
  const [params] = useSearchParams()
  const preselect = params.get('provider') ?? 'deepseek'

  const [items, setItems] = useState<KeyRow[] | null>(null)
  const [note, setNote] = useState<string | null>(null)
  const [provider, setProvider] = useState(preselect)
  const [apiKey, setApiKey] = useState('')
  const [label, setLabel] = useState('')
  const [busy, setBusy] = useState(false)

  async function reload() {
    const [keys] = await Promise.all([
      apiFetch<{ items?: KeyRow[] }>('/api/me/model-keys'),
      apiFetch('/api/models'),
    ])
    setItems(keys.items ?? [])
  }

  useEffect(() => {
    let alive = true
    void (async () => {
      try {
        await reload()
      } catch (error) {
        if (!alive) return
        setNote(failureMessage(error))
        setItems([])
      }
    })()
    return () => {
      alive = false
    }
  }, [])

  async function onSave() {
    setBusy(true)
    setNote(null)
    try {
      const existing = (items ?? []).find((k) => k.provider === provider)
      if (existing) {
        await apiFetch(`/api/me/model-keys/${existing.id}`, {
          method: 'PATCH',
          body: { api_key: apiKey, ...(label.trim() ? { label: label.trim() } : {}) },
        })
      } else {
        await apiFetch('/api/me/model-keys', {
          method: 'POST',
          body: {
            provider,
            api_key: apiKey,
            ...(label.trim() ? { label: label.trim() } : {}),
          },
        })
      }
      setApiKey('')
      await reload()
    } catch (error) {
      setNote(failureMessage(error))
    } finally {
      setBusy(false)
    }
  }

  async function onDelete(id: number) {
    setBusy(true)
    setNote(null)
    try {
      await apiFetch(`/api/me/model-keys/${id}`, { method: 'DELETE' })
      await reload()
    } catch (error) {
      setNote(failureMessage(error))
    } finally {
      setBusy(false)
    }
  }

  async function onVerify(id: number) {
    setBusy(true)
    setNote(null)
    try {
      await apiFetch(`/api/me/model-keys/${id}/verify`, { method: 'POST' })
      await reload()
    } catch (error) {
      setNote(failureMessage(error))
    } finally {
      setBusy(false)
    }
  }

  if (!session) return null

  return (
    <>
      <AppBar session={session} />
      <main className="shell page page--app page--keys">
        <p className="crumb">
          <Link className="link" to="/customer">
            返回
          </Link>
        </p>
        <header className="page-head">
          <h1 className="page__title">模型与密钥</h1>
          <p className="lede">
            Key 只保存在服务端（加密），列表只显示掩码。文案用 DeepSeek，视频用即梦或可灵。
          </p>
        </header>

        {note ? (
          <p className="note note--error" role="alert">
            {note}
          </p>
        ) : null}

        <section className="studio-panel" data-testid="keys-form">
          <h2 className="studio-panel__title">添加或更换</h2>
          <label className="field-label">
            厂商
            <select
              className="field"
              value={provider}
              onChange={(e) => setProvider(e.target.value)}
              data-testid="keys-provider"
            >
              {PROVIDERS.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.label}
                </option>
              ))}
            </select>
          </label>
          <label className="field-label">
            API Key
            <input
              className="field"
              type="password"
              autoComplete="off"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              placeholder="粘贴你的 Key"
              data-testid="keys-api-key"
            />
          </label>
          <label className="field-label">
            备注（可选）
            <input
              className="field"
              value={label}
              onChange={(e) => setLabel(e.target.value)}
              data-testid="keys-label"
            />
          </label>
          <button
            type="button"
            className="btn btn--primary"
            disabled={busy || !apiKey.trim()}
            onClick={() => void onSave()}
            data-testid="keys-save"
          >
            保存
          </button>
        </section>

        <section className="studio-panel" data-testid="keys-list-panel">
          <h2 className="studio-panel__title">已保存</h2>
          {items === null ? (
            <p className="note">加载中…</p>
          ) : items.length === 0 ? (
            <p className="note" data-testid="keys-empty">
              还没有密钥。在上方添加后即可用自己的额度创作。
            </p>
          ) : (
            <ul className="post-list" data-testid="keys-list">
              {items.map((row) => (
                <li key={row.id} className="post-row" data-testid={`key-${row.provider}`}>
                  <div>
                    <p className="post-row__title">{labelOf(row.provider)}</p>
                    <p className="post-row__meta">
                      {row.key_masked} · {row.status}
                      {row.label ? ` · ${row.label}` : ''}
                    </p>
                  </div>
                  <div className="studio-actions">
                    <button
                      type="button"
                      className="btn btn--quiet"
                      disabled={busy}
                      onClick={() => void onVerify(row.id)}
                    >
                      校验
                    </button>
                    <button
                      type="button"
                      className="btn btn--quiet"
                      disabled={busy}
                      onClick={() => void onDelete(row.id)}
                    >
                      删除
                    </button>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </section>
      </main>
    </>
  )
}

function labelOf(provider: string): string {
  return PROVIDERS.find((p) => p.id === provider)?.label ?? provider
}

/** 供测试：确保错误路径不把 ApiError 明文 key 写进 DOM。 */
export function formatKeyError(error: unknown): string {
  if (error instanceof ApiError) return error.detail ?? `请求失败（${error.status}）`
  return failureMessage(error)
}
