/**
 * 客户首页：创作台（主体）+ 我的信息入口。
 * 拍 2：开始创作 → 选领取 → 串行 uploads → POST /api/jobs → /customer/studio/{id}
 */

import { useEffect, useRef, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'

import { apiFetch, apiUpload, failureMessage, ApiError } from '../api/client'
import { AppBar } from '../components/AppBar'
import { FeedbackLauncher } from '../components/FeedbackLauncher'
import { KeyGuideDialog } from '../components/KeyGuideDialog'
import { LoadFailure } from '../components/LoadFailure'
import { useSession } from '../auth/useSession'
import { shopNameOf } from '../lib/customer'
import { balanceOf, totalOf, useCounters, type CounterSpec } from '../lib/counters'
import { useWideViewport } from '../lib/media'
import { parseTaskId, type TaskRow } from '../lib/tasks'

const CUSTOMER_SPECS: Record<string, CounterSpec> = {
  claims: { path: '/api/me/claims', pick: totalOf },
  points: { path: '/api/me/points', pick: balanceOf },
  coupons: { path: '/api/me/coupons', pick: totalOf },
  works: { path: '/api/me/jobs', pick: totalOf },
  posts: { path: '/api/me/posts', pick: totalOf },
  tasks: { path: '/api/tasks', pick: totalOf },
}

const MAX_ASSETS = 9
const MAX_BYTES = 20 * 1024 * 1024

interface ClaimPick {
  id: number
  task_id: number
  task: TaskRow | null
  merchant?: unknown
}

interface Selected {
  taskId: number
  claimId: number
  title: string
  shop: string
}

type GateMessage =
  | { kind: 'goto-tasks' }
  | { kind: 'need-images' }
  | { kind: 'error'; text: string }
  | null

export function CustomerHome() {
  const session = useSession()
  const navigate = useNavigate()
  const wide = useWideViewport()
  const [params] = useSearchParams()
  const preselectId = parseTaskId(params.get('task') ?? undefined)
  const fileRef = useRef<HTMLInputElement>(null)

  const { cells, allFailed, retry } = useCounters('customer', CUSTOMER_SPECS)
  const [prompt, setPrompt] = useState('')
  const [gate, setGate] = useState<GateMessage>(null)
  const [selected, setSelected] = useState<Selected | null>(null)
  const [files, setFiles] = useState<File[]>([])
  const [previews, setPreviews] = useState<string[]>([])
  const [pickerOpen, setPickerOpen] = useState(false)
  const [pickerRows, setPickerRows] = useState<ClaimPick[]>([])
  const [busy, setBusy] = useState(false)
  const [kind, setKind] = useState<'copy' | 'video'>('copy')
  const [useByok, setUseByok] = useState(true)
  const [keyGuide, setKeyGuide] = useState<{
    title: string
    detail: string
    provider: string
  } | null>(null)

  function providerFor(k: 'copy' | 'video'): string {
    return k === 'video' ? 'jimeng' : 'deepseek'
  }

  // ?task= 预选：拉详情拿标题；claim_id 在开始创作时再对齐
  useEffect(() => {
    if (preselectId == null) return
    let alive = true
    void (async () => {
      try {
        const data = await apiFetch<{
          task?: TaskRow
          merchant?: unknown
        }>(`/api/tasks/${preselectId}`)
        if (!alive) return
        setSelected((prev) => ({
          taskId: preselectId,
          claimId: prev?.taskId === preselectId ? prev.claimId : 0,
          title: data.task?.title?.trim() || '—',
          shop: shopNameOf(data.merchant),
        }))
      } catch {
        if (!alive) return
        setSelected(null)
      }
    })()
    return () => {
      alive = false
    }
  }, [preselectId])

  function rememberFiles(next: File[]) {
    setFiles(next)
    setPreviews((old) => {
      for (const url of old) URL.revokeObjectURL(url)
      return next.map((file) => URL.createObjectURL(file))
    })
  }

  function onPickFiles(list: FileList | null) {
    if (!list || list.length === 0) return
    setGate(null)
    const next = [...files]
    for (const file of Array.from(list)) {
      if (next.length >= MAX_ASSETS) break
      if (file.size > MAX_BYTES) {
        setGate({ kind: 'error', text: '单张图片不得超过 20MB' })
        continue
      }
      const type = file.type.toLowerCase()
      if (type && !['image/jpeg', 'image/png', 'image/webp'].includes(type)) {
        setGate({ kind: 'error', text: '只接受 jpg、png、webp' })
        continue
      }
      next.push(file)
    }
    rememberFiles(next.slice(0, MAX_ASSETS))
    if (fileRef.current) fileRef.current.value = ''
  }

  function removeFile(index: number) {
    rememberFiles(files.filter((_, i) => i !== index))
  }

  async function openPicker() {
    const data = await apiFetch<{ items?: ClaimPick[] }>('/api/me/claims')
    const items = data.items ?? []
    if (items.length === 0) {
      setGate({ kind: 'goto-tasks' })
      setPickerOpen(false)
      return
    }
    setPickerRows(items)
    setPickerOpen(true)
  }

  async function runCreate(sel: Selected) {
    if (files.length === 0) {
      setGate({ kind: 'need-images' })
      return
    }
    const provider = providerFor(kind)
    if (useByok) {
      try {
        const models = await apiFetch<{
          items?: { provider: string; has_my_key?: boolean }[]
        }>('/api/models')
        const hit = (models.items ?? []).find((m) => m.provider === provider)
        const keys = await apiFetch<{ items?: { provider: string; status: string }[] }>(
          '/api/me/model-keys',
        )
        const hasKey =
          hit?.has_my_key === true ||
          (keys.items ?? []).some((k) => k.provider === provider && k.status === 'active')
        if (!hasKey) {
          setKeyGuide({
            title: kind === 'video' ? '还不能做视频' : '还不能写文案',
            detail: `还没有可用的 ${provider} 密钥。请先添加 API Key，再回来创作。`,
            provider,
          })
          return
        }
      } catch (error) {
        setGate({ kind: 'error', text: failureMessage(error) })
        return
      }
    }

    setBusy(true)
    setGate(null)
    try {
      const assets: { url: string; mime: string; size_bytes: number }[] = []
      for (const file of files) {
        const up = await apiUpload<{
          url: string
          mime: string
          size_bytes: number
        }>(file)
        assets.push({
          url: up.url,
          mime: up.mime,
          size_bytes: up.size_bytes,
        })
      }
      const created = await apiFetch<{
        job_id: number
        reserved_points?: number
        reimburse_reserved?: number
      }>('/api/jobs', {
        method: 'POST',
        body: {
          task_id: sel.taskId,
          kind,
          claim_id: sel.claimId,
          assets,
          provider,
          billing_source: useByok ? 'byok' : 'platform',
        },
      })
      navigate(`/customer/studio/${created.job_id}`, {
        state: {
          reserved_points: created.reserved_points ?? 0,
          reimburse_reserved: created.reimburse_reserved ?? 0,
          task_id: sel.taskId,
        },
      })
    } catch (error) {
      if (error instanceof ApiError && error.status === 422) {
        const detail = error.detail ?? ''
        if (/Key|密钥|BYOK|未配置/i.test(detail)) {
          setKeyGuide({
            title: '需要配置 API Key',
            detail: detail || '当前无法使用所选模型，请先添加密钥。',
            provider: providerFor(kind),
          })
          return
        }
      }
      setGate({ kind: 'error', text: failureMessage(error) })
    } finally {
      setBusy(false)
    }
  }

  async function startCreate() {
    setGate(null)
    if (
      cells.claims.status === 'empty' ||
      (cells.claims.status === 'ok' && cells.claims.value === 0)
    ) {
      setGate({ kind: 'goto-tasks' })
      return
    }

    if (!selected || !selected.taskId) {
      try {
        await openPicker()
      } catch (error) {
        setGate({ kind: 'error', text: failureMessage(error) })
      }
      return
    }

    // 预选了任务但还没有 claim_id：从领取列表对齐
    if (!selected.claimId) {
      try {
        const data = await apiFetch<{ items?: ClaimPick[] }>('/api/me/claims')
        const hit = (data.items ?? []).find((c) => c.task_id === selected.taskId)
        if (!hit) {
          await openPicker()
          return
        }
        const next = { ...selected, claimId: hit.id }
        setSelected(next)
        await runCreate(next)
      } catch (error) {
        setGate({ kind: 'error', text: failureMessage(error) })
      }
      return
    }

    await runCreate(selected)
  }

  async function chooseClaim(row: ClaimPick) {
    setPickerOpen(false)
    let title = row.task?.title?.trim() || '—'
    let shopName = '—'
    try {
      const data = await apiFetch<{ task?: TaskRow; merchant?: unknown }>(
        `/api/tasks/${row.task_id}`,
      )
      title = data.task?.title?.trim() || title
      shopName = shopNameOf(data.merchant)
    } catch {
      /* 用已有字段 */
    }
    const next: Selected = {
      taskId: row.task_id,
      claimId: row.id,
      title,
      shop: shopName,
    }
    setSelected(next)
    await runCreate(next)
  }

  if (!session) return null

  const infoNav = (
    <InfoNav
      claims={cells.claims}
      points={cells.points}
      coupons={cells.coupons}
      works={cells.works}
      posts={cells.posts}
      tasks={cells.tasks}
      placement={wide ? 'aside' : 'footer'}
    />
  )

  return (
    <>
      <AppBar session={session} />
      <main className="shell page page--app page--customer">
        {allFailed ? (
          <LoadFailure onRetry={retry} />
        ) : (
          <div className={`customer-shell${wide ? ' customer-shell--wide' : ''}`}>
            <div className="customer-shell__main">
              <p className="customer-hero">让每个小店都有好内容</p>

              <section
                className="composer"
                data-testid="customer-composer"
                aria-label="创作台"
              >
                {selected ? (
                  <p className="composer__task" data-testid="composer-task">
                    <span className="composer__shop">{selected.shop}</span>
                    <span className="composer__title">{selected.title}</span>
                  </p>
                ) : null}

                <textarea
                  className="composer__input"
                  rows={2}
                  maxLength={2000}
                  placeholder="说说你想做什么内容"
                  value={prompt}
                  onChange={(event) => setPrompt(event.target.value)}
                />

                <div className="composer__bar">
                  <button
                    type="button"
                    className="btn btn--quiet"
                    disabled={busy}
                    onClick={() => fileRef.current?.click()}
                  >
                    图片 {files.length}/{MAX_ASSETS}
                  </button>
                  <input
                    ref={fileRef}
                    className="file-input"
                    type="file"
                    accept="image/jpeg,image/png,image/webp,.jpg,.jpeg,.png,.webp"
                    multiple
                    data-testid="composer-file"
                    onChange={(e) => onPickFiles(e.target.files)}
                  />
                  <button
                    type="button"
                    className="btn btn--primary"
                    disabled={busy}
                    onClick={() => void startCreate()}
                  >
                    开始创作
                  </button>
                </div>

                {previews.length > 0 ? (
                  <ul className="composer__thumbs" data-testid="composer-previews">
                    {previews.map((url, index) => (
                      <li key={url}>
                        <img src={url} alt="" />
                        <button type="button" onClick={() => removeFile(index)}>
                          移除
                        </button>
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="composer__hint">先选图片，再点「开始创作」才会上传到服务器</p>
                )}

                <div className="composer__kinds">
                  <button
                    type="button"
                    className={`btn btn--quiet${kind === 'copy' ? ' composer__kind--active' : ''}`}
                    aria-pressed={kind === 'copy'}
                    data-testid="kind-copy"
                    onClick={() => setKind('copy')}
                  >
                    写文案
                  </button>
                  <button
                    type="button"
                    className={`btn btn--quiet${kind === 'video' ? ' composer__kind--active' : ''}`}
                    aria-pressed={kind === 'video'}
                    data-testid="kind-video"
                    onClick={() => setKind('video')}
                  >
                    做视频
                  </button>
                  <label className="composer__hint">
                    <input
                      type="checkbox"
                      checked={useByok}
                      onChange={(e) => setUseByok(e.target.checked)}
                      data-testid="use-byok"
                    />{' '}
                    使用我的 API Key
                  </label>
                  <Link className="link" to="/customer/keys" data-testid="composer-keys-link">
                    管理密钥
                  </Link>
                </div>
              </section>

              {gate?.kind === 'goto-tasks' ? (
                <div className="note" role="status">
                  <span>先领一个任务，再来创作。</span>
                  <Link className="link" to="/customer/tasks">
                    去找任务
                  </Link>
                </div>
              ) : null}

              {gate?.kind === 'need-images' ? (
                <p className="note" role="status">
                  请先选择至少一张图片
                </p>
              ) : null}

              {gate?.kind === 'error' ? (
                <p className="note note--error" role="alert">
                  {gate.text}
                </p>
              ) : null}

              {!wide ? infoNav : null}
            </div>

            {wide ? infoNav : null}
          </div>
        )}

        {pickerOpen ? (
          <div className="picker" role="dialog" aria-label="选择任务" data-testid="claim-picker">
            <div className="picker__panel">
              <header className="picker__head">
                <h2 className="picker__title">选择要创作的任务</h2>
                <button
                  type="button"
                  className="btn btn--quiet"
                  onClick={() => setPickerOpen(false)}
                >
                  关闭
                </button>
              </header>
              <ul className="picker__list">
                {pickerRows.map((row) => (
                  <li key={row.id}>
                    <button
                      type="button"
                      className="picker__item"
                      onClick={() => void chooseClaim(row)}
                    >
                      {row.task?.title?.trim() || `任务 #${row.task_id}`}
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          </div>
        ) : null}

        {keyGuide ? (
          <KeyGuideDialog
            title={keyGuide.title}
            detail={keyGuide.detail}
            provider={keyGuide.provider}
            onClose={() => setKeyGuide(null)}
          />
        ) : null}

        <FeedbackLauncher />
      </main>
    </>
  )
}

function InfoNav({
  claims,
  points,
  coupons,
  works,
  posts,
  tasks,
  placement,
}: {
  claims: { status: string; value: number }
  points: { status: string; value: number }
  coupons: { status: string; value: number }
  works: { status: string; value: number }
  posts: { status: string; value: number }
  tasks: { status: string; value: number }
  placement: 'aside' | 'footer'
}) {
  const testId = placement === 'aside' ? 'customer-aside' : 'customer-footer-nav'
  const claimsFigure = figureOf(claims)
  const rewardFigure = rewardFigureOf(points, coupons)
  const worksFigure = countFigure(works)
  const postsFigure = countFigure(posts)
  const tasksFigure = countFigure(tasks)

  return (
    <nav
      className={`customer-info customer-info--${placement}`}
      data-testid={testId}
      aria-label="我的信息"
    >
      <Link
        className="customer-info__item"
        to="/customer/tasks"
        data-testid="customer-entry-tasks"
      >
        <span className="customer-info__label">找任务</span>
        <span className="customer-info__figure">{tasksFigure}</span>
      </Link>
      <Link
        className="customer-info__item"
        to="/customer/claims"
        data-testid="customer-entry-claims"
      >
        <span className="customer-info__label">我领取的任务</span>
        <span className="customer-info__figure">{claimsFigure}</span>
      </Link>
      <Link
        className="customer-info__item"
        to="/customer/rewards"
        data-testid="customer-entry-rewards"
      >
        <span className="customer-info__label">我的奖励</span>
        <span className="customer-info__figure">{rewardFigure}</span>
      </Link>
      <Link
        className="customer-info__item"
        to="/customer/works"
        data-testid="customer-entry-works"
      >
        <span className="customer-info__label">我的创作</span>
        <span className="customer-info__figure">{worksFigure}</span>
      </Link>
      <Link
        className="customer-info__item"
        to="/customer/posts"
        data-testid="customer-entry-posts"
      >
        <span className="customer-info__label">我的作品</span>
        <span className="customer-info__figure">{postsFigure}</span>
      </Link>
      <Link
        className="customer-info__item"
        to="/customer/keys"
        data-testid="customer-entry-keys"
      >
        <span className="customer-info__label">模型与密钥</span>
        <span className="customer-info__figure">管理</span>
      </Link>
    </nav>
  )
}

function countFigure(cell: { status: string; value: number }): string {
  if (cell.status === 'error') return '—'
  if (cell.status === 'loading') return '…'
  return String(cell.value)
}

function figureOf(cell: { status: string; value: number }): string {
  if (cell.status === 'error') return '—'
  if (cell.status === 'empty') return '还没有数据'
  if (cell.status === 'loading') return '…'
  return String(cell.value)
}

function rewardFigureOf(
  points: { status: string; value: number },
  coupons: { status: string; value: number },
): string {
  const p = figureOf(points)
  const c = figureOf(coupons)
  if (p === '—' && c === '—') return '—'
  if (p === '还没有数据' && c === '还没有数据') return '还没有数据'
  if (p === '…' || c === '…') return '…'
  const pointsText = p === '—' || p === '还没有数据' ? '—' : `${p} 积分`
  const couponsText = c === '—' || c === '还没有数据' ? '—' : `${c} 张券`
  return `${pointsText} · ${couponsText}`
}
