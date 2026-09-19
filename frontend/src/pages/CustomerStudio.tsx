/**
 * 内容工坊：完全由 job.status 驱动（追加 C）。
 */

import { useEffect, useRef, useState } from 'react'
import { Link, useLocation, useNavigate, useParams } from 'react-router-dom'

import { ApiError, apiFetch, apiStream, failureMessage } from '../api/client'
import { AppBar } from '../components/AppBar'
import { KeyGuideDialog } from '../components/KeyGuideDialog'
import { LoadFailure } from '../components/LoadFailure'
import { useSession } from '../auth/useSession'
import { readSseResponse } from '../lib/sse'
import {
  canDelete,
  jobStatusLabel,
  rememberReserved,
  readReserved,
  stageLabel,
  type ChatMsg,
  type JobDetail,
} from '../lib/studio'

type PageState =
  | { kind: 'loading' }
  | { kind: 'bad-id' }
  | { kind: 'forbidden' }
  | { kind: 'missing' }
  | { kind: 'error' }
  | { kind: 'ok'; detail: JobDetail }

type LocState = {
  reserved_points?: number
  reimburse_reserved?: number
  task_id?: number
} | null

export function CustomerStudio() {
  const session = useSession()
  const navigate = useNavigate()
  const { jobId: raw } = useParams()
  const location = useLocation()
  const jobId = Number(raw)
  const badId = !Number.isInteger(jobId) || jobId < 1

  const [page, setPage] = useState<PageState>(badId ? { kind: 'bad-id' } : { kind: 'loading' })
  const [attempt, setAttempt] = useState(0)
  const [note, setNote] = useState<string | null>(null)
  const [guardReason, setGuardReason] = useState<string | null>(null)
  const [messages, setMessages] = useState<ChatMsg[]>([])
  const [chatDraft, setChatDraft] = useState('')
  const [streaming, setStreaming] = useState(false)
  const [chatLocked, setChatLocked] = useState(false)
  const [streamBubble, setStreamBubble] = useState<string | null>(null)
  const [streamError, setStreamError] = useState<string | null>(null)
  const [prompt, setPrompt] = useState('')
  const [prevScore, setPrevScore] = useState<number | null>(null)
  const [scoreLine, setScoreLine] = useState<string | null>(null)
  const [stageText, setStageText] = useState('处理中…')
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [keyGuideOpen, setKeyGuideOpen] = useState(false)
  const [reserved, setReserved] = useState<number | null>(null)
  const [reimburse, setReimburse] = useState<{
    prepaid: number
    claimed: number
    remain: number
  } | null>(null)

  const guardTimer = useRef<ReturnType<typeof setInterval> | null>(null)
  const eventsAlive = useRef(false)
  const chatLoaded = useRef(false)

  function stopGuard() {
    if (guardTimer.current) {
      clearInterval(guardTimer.current)
      guardTimer.current = null
    }
  }

  async function loadJob(): Promise<JobDetail | null> {
    const data = await apiFetch<JobDetail>(`/api/jobs/${jobId}`)
    setPage({ kind: 'ok', detail: data })
    const draft = data.prompt_draft
    if (draft?.optimized_prompt) setPrompt(draft.optimized_prompt)
    else if (draft?.raw_prompt) setPrompt(draft.raw_prompt)
    return data
  }

  useEffect(() => {
    if (badId) return
    const loc = location.state as LocState
    if (loc?.reserved_points != null && loc.task_id != null) {
      rememberReserved(jobId, loc.reserved_points, loc.task_id)
      setReserved(loc.reserved_points > 0 ? loc.reserved_points : null)
    } else {
      const saved = readReserved(jobId)
      if (saved && saved.reserved_points > 0) setReserved(saved.reserved_points)
    }
  }, [jobId, badId, location.state])

  useEffect(() => {
    if (badId) {
      setPage({ kind: 'bad-id' })
      return
    }
    let alive = true
    chatLoaded.current = false
    void (async () => {
      try {
        const data = await apiFetch<JobDetail>(`/api/jobs/${jobId}`)
        if (!alive) return
        setPage({ kind: 'ok', detail: data })
        const draft = data.prompt_draft
        if (draft?.optimized_prompt) setPrompt(draft.optimized_prompt)
        else if (draft?.raw_prompt) setPrompt(draft.raw_prompt)
        setPrevScore(draft?.quality_score ?? null)

        const saved = readReserved(jobId)
        const taskId = saved?.task_id ?? data.job.task_id
        try {
          const taskPack = await apiFetch<{
            task?: { pay_mode?: string }
          }>(`/api/tasks/${taskId}`)
          if (!alive) return
          if (taskPack.task?.pay_mode === 'user_pay_reimburse') {
            try {
              const prev = await apiFetch<{
                my_consumed_points?: number
                my_reimbursed_points?: number
                my_remaining?: number
              }>(`/api/me/reimburse-preview?task_id=${taskId}`)
              if (!alive) return
              setReimburse({
                prepaid: prev.my_consumed_points ?? 0,
                claimed: prev.my_reimbursed_points ?? 0,
                remain: prev.my_remaining ?? 0,
              })
            } catch (error) {
              if (error instanceof ApiError && error.status === 403) {
                setReimburse(null)
              } else throw error
            }
          }
        } catch {
          /* 任务信息失败不挡工坊 */
        }
      } catch (error) {
        if (!alive) return
        if (error instanceof ApiError && error.status === 403) setPage({ kind: 'forbidden' })
        else if (error instanceof ApiError && error.status === 404) setPage({ kind: 'missing' })
        else setPage({ kind: 'error' })
      }
    })()
    return () => {
      alive = false
      stopGuard()
      eventsAlive.current = false
    }
  }, [jobId, badId, attempt])

  const status = page.kind === 'ok' ? page.detail.job.status : null

  // 预检轮询
  useEffect(() => {
    stopGuard()
    if (status !== 'created' && status !== 'guarding') return
    let cancelled = false

    async function tick() {
      try {
        const g = await apiFetch<{ passed: boolean; reason: string | null }>(
          `/api/jobs/${jobId}/guard`,
        )
        if (cancelled) return
        stopGuard()
        if (g.passed) {
          setGuardReason(null)
          await loadJob()
        } else {
          setGuardReason(g.reason ?? '预检未通过')
          await loadJob()
        }
      } catch (error) {
        if (cancelled) return
        if (error instanceof ApiError && error.status === 409) return
        stopGuard()
        setNote(failureMessage(error))
      }
    }

    void tick()
    guardTimer.current = setInterval(() => void tick(), 2000)
    return () => {
      cancelled = true
      stopGuard()
    }
  }, [status, jobId])

  // chatting：拉历史
  useEffect(() => {
    if (status !== 'chatting' || chatLoaded.current) return
    chatLoaded.current = true
    void (async () => {
      try {
        const data = await apiFetch<{ messages?: ChatMsg[] }>(`/api/jobs/${jobId}/chat`)
        setMessages(data.messages ?? [])
      } catch {
        setNote('对话记录加载失败')
        chatLoaded.current = false
      }
    })()
  }, [status, jobId])

  // generating / judging：进度流
  useEffect(() => {
    if (status !== 'generating' && status !== 'judging') {
      eventsAlive.current = false
      return
    }
    let cancelled = false
    eventsAlive.current = true

    async function connect() {
      while (eventsAlive.current && !cancelled) {
        try {
          const response = await apiStream(`/api/jobs/${jobId}/events`)
          await readSseResponse(response, (event, data) => {
            if (event === 'stage') {
              const row = data as { stage?: string }
              setStageText(stageLabel(row.stage))
              void loadJob()
            }
          })
        } catch {
          if (cancelled) return
        }
        if (cancelled || !eventsAlive.current) return
        const fresh = await loadJob().catch(() => null)
        if (
          fresh &&
          fresh.job.status !== 'generating' &&
          fresh.job.status !== 'judging'
        ) {
          eventsAlive.current = false
          return
        }
        await new Promise((r) => setTimeout(r, 1000))
      }
    }

    void connect()
    return () => {
      cancelled = true
      eventsAlive.current = false
    }
  }, [status, jobId])

  async function sendChat() {
    const text = chatDraft.trim()
    if (!text || streaming || chatLocked) return
    setNote(null)
    setStreamError(null)
    setStreaming(true)
    setStreamBubble('')
    setChatDraft('')
    setMessages((prev) => [
      ...prev,
      { id: Date.now(), role: 'user', content: text },
    ])
    try {
      const response = await apiStream(`/api/jobs/${jobId}/chat`, {
        method: 'POST',
        body: { message: text },
      })
      let assembled = ''
      let errored = false
      await readSseResponse(response, (event, data) => {
        if (event === 'delta') {
          const piece = (data as { text?: string }).text ?? ''
          assembled += piece
          setStreamBubble(assembled)
        } else if (event === 'error') {
          errored = true
          setStreamError((data as { message?: string }).message ?? '对话失败')
          setStreamBubble(null)
        } else if (event === 'done') {
          setStreamBubble(null)
          if (!errored) {
            setMessages((prev) => [
              ...prev,
              { id: Date.now() + 1, role: 'assistant', content: assembled },
            ])
          }
        }
      })
    } catch (error) {
      if (error instanceof ApiError && error.status === 409) {
        setChatLocked(true)
        setNote(failureMessage(error))
      } else if (error instanceof ApiError && error.status === 402) {
        setNote(failureMessage(error))
      } else {
        setNote(failureMessage(error))
      }
      setStreamBubble(null)
    } finally {
      setStreaming(false)
    }
  }

  async function doRewrite() {
    const raw = prompt
    if (!raw || raw.length > 2000) return
    setNote(null)
    try {
      const data = await apiFetch<{
        optimized_prompt: string
        quality_score: number | null
        rewrite_failed: boolean
      }>(`/api/jobs/${jobId}/rewrite-prompt`, {
        method: 'POST',
        body: { raw_prompt: raw },
      })
      if (data.rewrite_failed) {
        setNote('这次没能优化，已用你原来的提示词')
        setPrompt(raw)
        setScoreLine(null)
      } else {
        setPrompt(data.optimized_prompt)
        const m = data.quality_score
        if (prevScore == null) {
          setScoreLine(m == null ? null : `${m} 分`)
        } else {
          setScoreLine(`上次 ${prevScore} 分 → 本次 ${m} 分`)
        }
        if (m != null) setPrevScore(m)
      }
    } catch (error) {
      if (error instanceof ApiError && error.status === 429) {
        setNote('改得太频繁了，稍等一下')
      } else {
        setNote(failureMessage(error))
      }
    }
  }

  async function doGenerate() {
    setNote(null)
    try {
      await apiFetch(`/api/jobs/${jobId}/generate`, {
        method: 'POST',
        body: { prompt },
      })
      await loadJob()
    } catch (error) {
      setNote(failureMessage(error))
    }
  }

  async function doRetry() {
    setNote(null)
    try {
      await apiFetch(`/api/jobs/${jobId}/retry`, { method: 'POST' })
      await loadJob()
    } catch (error) {
      setNote(failureMessage(error))
    }
  }

  async function doDelete() {
    if (!confirmDelete) {
      setConfirmDelete(true)
      return
    }
    setNote(null)
    try {
      await apiFetch(`/api/jobs/${jobId}`, { method: 'DELETE' })
      navigate('/customer/claims')
    } catch (error) {
      setNote(failureMessage(error))
    }
  }

  async function copyText(content: string) {
    try {
      await navigator.clipboard.writeText(content)
      setNote('已复制到剪贴板')
    } catch {
      setNote('复制失败')
    }
  }

  function downloadTxt(content: string, id: number) {
    const blob = new Blob([content], { type: 'text/plain;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `copy-${id}.txt`
    a.click()
    URL.revokeObjectURL(url)
  }

  if (!session) return null

  if (page.kind === 'bad-id' || page.kind === 'missing') {
    return (
      <>
        <AppBar session={session} />
        <main className="shell page page--app">
          <p className="note" data-testid="studio-missing">
            作品不存在
          </p>
        </main>
      </>
    )
  }

  if (page.kind === 'forbidden') {
    return (
      <>
        <AppBar session={session} />
        <main className="shell page page--app">
          <p className="note" data-testid="studio-forbidden">
            这个作品不属于你
          </p>
        </main>
      </>
    )
  }

  if (page.kind === 'error') {
    return (
      <>
        <AppBar session={session} />
        <main className="shell page page--app">
          <LoadFailure onRetry={() => setAttempt((n) => n + 1)} />
        </main>
      </>
    )
  }

  if (page.kind === 'loading') {
    return (
      <>
        <AppBar session={session} />
        <main className="shell page page--app">
          <p className="note">加载中…</p>
        </main>
      </>
    )
  }

  const { detail } = page
  const job = detail.job
  const active = detail.outputs.find((o) => o.is_active)
  const rewriteDisabled = !prompt.trim() || prompt.length > 2000

  return (
    <>
      <AppBar session={session} />
      <main className="shell page page--app page--studio">
        <p className="crumb">
          <Link className="link" to="/customer">
            返回
          </Link>
        </p>
        <header className="page-head studio-head">
          <div className="studio-head__titles">
            <h1 className="page__title">内容工坊</h1>
            <span className="badge" data-testid="studio-status">
              {jobStatusLabel(job.status)}
            </span>
          </div>
          <p className="lede" data-testid="studio-job">
            作品 #{job.id}
          </p>
          {reserved != null ? (
            <p className="studio-reserve" data-testid="studio-reserve">
              本次预扣 {reserved} 点
            </p>
          ) : null}
          {reimburse ? (
            <p className="studio-reimburse" data-testid="studio-reimburse">
              我垫付了 {reimburse.prepaid} / 已报 {reimburse.claimed} / 还能报{' '}
              {reimburse.remain}
            </p>
          ) : null}
        </header>

        {note ? (
          <p className="note note--error" role="alert" data-testid="studio-note">
            {note}
          </p>
        ) : null}

        {(job.status === 'created' || job.status === 'guarding') && !guardReason ? (
          <div className="studio-skeleton" data-testid="studio-guarding">
            正在检查素材…
          </div>
        ) : null}

        {guardReason || job.status === 'guard_failed' ? (
          <section className="studio-panel" data-testid="studio-guard-fail">
            <p>{guardReason ?? job.fail_reason ?? '预检未通过'}</p>
            <div className="studio-thumbs">
              {detail.inputs.map((a) => (
                <img key={a.id} src={a.url} alt="" className="studio-thumb" />
              ))}
            </div>
            <div className="studio-actions">
              <button type="button" className="btn btn--quiet" disabled data-testid="studio-retry">
                重试
              </button>
              {canDelete(job.status === 'guarding' ? 'guard_failed' : job.status) ||
              job.status === 'guard_failed' ||
              guardReason ? (
                <button
                  type="button"
                  className="btn btn--primary"
                  data-testid="studio-delete"
                  onClick={() => void doDelete()}
                >
                  {confirmDelete ? '确认删除' : '删除'}
                </button>
              ) : null}
            </div>
          </section>
        ) : null}

        {job.status === 'chatting' ? (
          <section className="studio-panel studio-panel--chat" data-testid="studio-chat">
            <h2 className="studio-panel__title">对话</h2>
            <ul className="chat-list">
              {messages.map((m) => (
                <li
                  key={m.id}
                  className={`chat-bubble chat-bubble--${m.role}`}
                  data-testid={`chat-${m.role}`}
                >
                  {m.content}
                </li>
              ))}
              {streamBubble != null ? (
                <li className="chat-bubble chat-bubble--assistant">{streamBubble}</li>
              ) : null}
              {streamError ? (
                <li className="chat-bubble chat-bubble--error" data-testid="chat-error">
                  {streamError}
                </li>
              ) : null}
            </ul>
            <div className="chat-compose">
              <input
                className="field"
                value={chatDraft}
                disabled={streaming || chatLocked}
                placeholder="跟 AI 聊聊怎么改"
                onChange={(e) => setChatDraft(e.target.value)}
                data-testid="chat-input"
              />
              <button
                type="button"
                className="btn btn--primary"
                disabled={streaming || chatLocked || !chatDraft.trim()}
                onClick={() => void sendChat()}
                data-testid="chat-send"
              >
                发送
              </button>
            </div>

            <div className="rewrite-block" data-testid="studio-rewrite">
              <h2 className="studio-panel__title">提示词</h2>
              <textarea
                className="field"
                rows={4}
                maxLength={2000}
                value={prompt}
                onChange={(e) => setPrompt(e.target.value)}
                data-testid="prompt-input"
              />
              {scoreLine ? (
                <p className="lede" data-testid="score-line">
                  {scoreLine}
                </p>
              ) : null}
              <div className="studio-actions">
                <button
                  type="button"
                  className="btn btn--quiet"
                  disabled={rewriteDisabled}
                  onClick={() => void doRewrite()}
                  data-testid="rewrite-btn"
                >
                  复写
                </button>
                <button
                  type="button"
                  className="btn btn--primary"
                  onClick={() => void doGenerate()}
                  data-testid="generate-btn"
                >
                  开始生成
                </button>
              </div>
            </div>
          </section>
        ) : null}

        {job.status === 'generating' || job.status === 'judging' ? (
          <section className="studio-panel studio-panel--progress" data-testid="studio-progress">
            <h2 className="studio-panel__title">生成中</h2>
            <p className="studio-stage">{stageText}</p>
            <div className="progress-bar" aria-hidden>
              <span className="progress-bar__fill" />
            </div>
          </section>
        ) : null}

        {job.status === 'ready' && active ? (
          <section className="studio-panel studio-panel--ready" data-testid="studio-ready">
            <h2 className="studio-panel__title">
              {active.type === 'video' ? '视频产物' : '文案产物'}
            </h2>
            {active.type === 'video' && active.url ? (
              <div data-testid="video-out">
                <video className="studio-video" src={active.url} controls playsInline />
                <p>
                  <a className="link" href={active.url} target="_blank" rel="noreferrer">
                    打开视频链接
                  </a>
                </p>
              </div>
            ) : (
              <pre className="copy-out" data-testid="copy-content">
                {active.content}
              </pre>
            )}
            {active.judge_score != null ? (
              <p data-testid="judge-score">质检 {active.judge_score} 分</p>
            ) : null}
            {active.judge_detail?.reasons?.map((r) => (
              <p key={r} className="lede">
                {r}
              </p>
            ))}
            <div className="studio-actions">
              <button
                type="button"
                className="btn btn--quiet"
                onClick={() => void copyText(active.content ?? '')}
                data-testid="copy-btn"
              >
                复制文案
              </button>
              <button
                type="button"
                className="btn btn--quiet"
                onClick={() => downloadTxt(active.content ?? '', active.id)}
                data-testid="download-txt"
              >
                下载 .txt
              </button>
              <Link
                className="btn btn--primary"
                to={`/customer/posts?job_id=${job.id}`}
                data-testid="goto-posts"
              >
                去回填
              </Link>
            </div>
          </section>
        ) : null}

        {job.status === 'need_review' ? (
          <section className="studio-panel" data-testid="studio-review">
            <p>已转人工复核</p>
            <p className="lede">质检未过线，平台会人工看一眼，请稍候。</p>
          </section>
        ) : null}

        {job.status === 'failed' ? (
          <section className="studio-panel" data-testid="studio-failed">
            <p>{job.fail_reason ?? '生成失败'}</p>
            {job.fail_reason === 'key_invalid' ||
            job.fail_reason === '视频模型尚未接入' ? (
              <button
                type="button"
                className="btn btn--primary"
                data-testid="open-key-guide"
                onClick={() => setKeyGuideOpen(true)}
              >
                查看如何添加密钥
              </button>
            ) : null}
            <div className="studio-actions">
              <button
                type="button"
                className="btn btn--primary"
                onClick={() => void doRetry()}
                data-testid="studio-retry"
              >
                重试
              </button>
              <button
                type="button"
                className="btn btn--quiet"
                onClick={() => void doDelete()}
                data-testid="studio-delete"
              >
                {confirmDelete ? '确认删除' : '删除'}
              </button>
            </div>
          </section>
        ) : null}

        {keyGuideOpen ? (
          <KeyGuideDialog
            title="密钥无效或未配置"
            detail={
              job.fail_reason === 'key_invalid'
                ? '当前密钥已失效，请更换后重试。'
                : '视频模型尚未接入平台 Key，请添加即梦或可灵密钥，或联系管理员配置。'
            }
            provider={job.kind === 'video' ? 'jimeng' : 'deepseek'}
            onClose={() => setKeyGuideOpen(false)}
          />
        ) : null}

        {canDelete(job.status) &&
        job.status !== 'failed' &&
        job.status !== 'guard_failed' &&
        !guardReason ? (
          <div className="studio-actions">
            <button
              type="button"
              className="btn btn--quiet"
              onClick={() => void doDelete()}
              data-testid="studio-delete"
            >
              {confirmDelete ? '确认删除' : '删除'}
            </button>
          </div>
        ) : null}
      </main>
    </>
  )
}
