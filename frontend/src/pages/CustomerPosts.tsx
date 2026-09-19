/**
 * 我的作品与数据回填（追加 C）。
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'

import { ApiError, apiFetch, apiUpload, failureMessage } from '../api/client'
import { AppBar } from '../components/AppBar'
import { LoadFailure } from '../components/LoadFailure'
import { useSession } from '../auth/useSession'
import type { JobRow } from '../lib/studio'

const PLATFORMS = [
  { id: 'xhs', label: '小红书' },
  { id: 'douyin', label: '抖音' },
  { id: 'kuaishou', label: '快手' },
  { id: 'bilibili', label: 'B 站' },
  { id: 'shipinhao', label: '视频号' },
] as const

const STATUS_LABEL: Record<string, string> = {
  pending: '待审核',
  approved: '已通过',
  auto_approved: '已自动通过',
  rejected: '已驳回',
  appealed: '申诉中',
}

interface PostRow {
  id: number
  claim_id: number
  job_id: number
  platform: string
  post_url: string
  status: string
  countdown_seconds: number
  latest_snapshot: {
    likes: number
    collects: number
    comments: number
    shares: number
    engagement: number
  } | null
}

type LoadState = 'loading' | 'ok' | 'error'

function countdownLabel(seconds: number, nowTick: number): string {
  void nowTick
  if (seconds < 0) return '已超时，系统将自动通过'
  const h = Math.floor(seconds / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  return `还有 ${h} 小时 ${m} 分`
}

export function CustomerPosts() {
  const session = useSession()
  const [params] = useSearchParams()
  const preJob = Number(params.get('job_id'))
  const hasPreJob = Number.isInteger(preJob) && preJob > 0

  const [rows, setRows] = useState<PostRow[]>([])
  const [titles, setTitles] = useState<Record<number, string>>({})
  const [state, setState] = useState<LoadState>('loading')
  const [attempt, setAttempt] = useState(0)
  const [tick, setTick] = useState(0)
  const [note, setNote] = useState<string | null>(null)
  const [baseCountdown, setBaseCountdown] = useState<Record<number, { at: number; sec: number }>>(
    {},
  )

  // 回填弹层
  const [fillOpen, setFillOpen] = useState(false)
  const [readyJobs, setReadyJobs] = useState<JobRow[]>([])
  const [pickedJob, setPickedJob] = useState<JobRow | null>(null)
  const [platform, setPlatform] = useState('xhs')
  const [postUrl, setPostUrl] = useState('')
  const [postTitle, setPostTitle] = useState('')
  const [postBody, setPostBody] = useState('')
  const [platformErr, setPlatformErr] = useState<string | null>(null)

  // 追加速据
  const [metricsFor, setMetricsFor] = useState<number | null>(null)
  const [mLikes, setMLikes] = useState('0')
  const [mCollects, setMCollects] = useState('0')
  const [mComments, setMComments] = useState('0')
  const [mShares, setMShares] = useState('0')

  // 截图：选文件后立刻标出是否传完，不把「打开文件」当成已上传
  const [shotProgress, setShotProgress] = useState<{
    postId: number
    name: string
    phase: 'uploading' | 'uploaded' | 'recognizing' | 'done' | 'failed'
    preview: string
  } | null>(null)
  const previewUrl = useRef<string | null>(null)

  // 截图 OCR
  const [shotFor, setShotFor] = useState<number | null>(null)
  const [ocrView, setOcrView] = useState<{
    likes: string
    collects: string
    comments: string
    shares: string
    confidence: number | null
    low: boolean
    mismatch: boolean
    unrecognized: boolean
  } | null>(null)
  const ocrTimer = useRef<ReturnType<typeof setInterval> | null>(null)

  // 申诉
  const [appealFor, setAppealFor] = useState<number | null>(null)
  const [appealReason, setAppealReason] = useState('')
  const [appealReady, setAppealReady] = useState(false)
  const appealDelay = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(() => {
    const id = setInterval(() => setTick((n) => n + 1), 1000)
    return () => clearInterval(id)
  }, [])

  useEffect(() => {
    let alive = true
    void (async () => {
      try {
        const [posts, claims] = await Promise.all([
          apiFetch<{ items?: PostRow[] }>('/api/me/posts'),
          apiFetch<{ items?: { id: number; task?: { title?: string } | null }[] }>(
            '/api/me/claims',
          ),
        ])
        if (!alive) return
        const map: Record<number, string> = {}
        for (const c of claims.items ?? []) {
          map[c.id] = c.task?.title?.trim() || '—'
        }
        setTitles(map)
        const items = posts.items ?? []
        setRows(items)
        const now = Date.now()
        const base: Record<number, { at: number; sec: number }> = {}
        for (const p of items) {
          base[p.id] = { at: now, sec: p.countdown_seconds }
        }
        setBaseCountdown(base)
        setState('ok')
      } catch {
        if (!alive) return
        setState('error')
      }
    })()
    return () => {
      alive = false
      if (ocrTimer.current) clearInterval(ocrTimer.current)
      if (appealDelay.current) clearTimeout(appealDelay.current)
    }
  }, [attempt])

  const retry = useCallback(() => setAttempt((n) => n + 1), [])

  const liveSeconds = useMemo(() => {
    const now = Date.now()
    const out: Record<number, number> = {}
    for (const [id, b] of Object.entries(baseCountdown)) {
      const elapsed = Math.floor((now - b.at) / 1000)
      out[Number(id)] = b.sec - elapsed
    }
    return out
  }, [baseCountdown, tick])

  async function openFill() {
    setNote(null)
    setPlatformErr(null)
    const data = await apiFetch<{ items?: JobRow[] }>('/api/me/jobs?page=1&size=100')
    const ready = (data.items ?? []).filter((j) => j.status === 'ready')
    if (ready.length === 0) {
      setNote('先去创作')
      setFillOpen(false)
      return
    }
    setReadyJobs(ready)
    const pre = hasPreJob ? ready.find((j) => j.id === preJob) : null
    setPickedJob(pre ?? ready[0])
    setFillOpen(true)
  }

  useEffect(() => {
    if (hasPreJob && state === 'ok') {
      void openFill().catch((e) => setNote(failureMessage(e)))
    }
    // 仅进页时预开一次
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hasPreJob, state])

  function validUrl(url: string): boolean {
    return /^https?:\/\//i.test(url.trim())
  }

  async function submitPost() {
    setPlatformErr(null)
    setNote(null)
    if (!pickedJob) return
    if (!postUrl.trim() || !validUrl(postUrl)) {
      setNote('请填写以 http/https 开头的作品链接')
      return
    }
    try {
      const body: Record<string, unknown> = {
        claim_id: pickedJob.claim_id,
        job_id: pickedJob.id,
        platform,
        post_url: postUrl.trim(),
        source: 'manual',
      }
      if (postTitle.trim()) body.post_title = postTitle.trim()
      if (postBody.trim()) body.post_body = postBody.trim()
      const data = await apiFetch<{ post: PostRow }>('/api/posts', {
        method: 'POST',
        body,
      })
      setRows((prev) => [data.post, ...prev])
      setBaseCountdown((prev) => ({
        ...prev,
        [data.post.id]: { at: Date.now(), sec: data.post.countdown_seconds },
      }))
      setFillOpen(false)
      setPostUrl('')
      setPostTitle('')
      setPostBody('')
    } catch (error) {
      const msg = failureMessage(error)
      if (
        error instanceof ApiError &&
        error.status === 422 &&
        /平台|域名/.test(msg)
      ) {
        setPlatformErr(msg)
      } else {
        setNote(msg)
      }
    }
  }

  async function submitMetrics() {
    if (metricsFor == null) return
    const likes = Number(mLikes)
    const collects = Number(mCollects)
    const comments = Number(mComments)
    const shares = Number(mShares)
    if (
      ![likes, collects, comments, shares].every(
        (n) => Number.isInteger(n) && n >= 0,
      )
    ) {
      setNote('互动量须为非负整数')
      return
    }
    try {
      const data = await apiFetch<{ snapshot: { engagement: number } }>(
        `/api/posts/${metricsFor}/metrics`,
        {
          method: 'POST',
          body: { likes, collects, comments, shares, source: 'manual' },
        },
      )
      setRows((prev) =>
        prev.map((r) =>
          r.id === metricsFor
            ? {
                ...r,
                latest_snapshot: {
                  likes,
                  collects,
                  comments,
                  shares,
                  engagement: data.snapshot.engagement,
                },
              }
            : r,
        ),
      )
      setMetricsFor(null)
    } catch (error) {
      setNote(failureMessage(error))
    }
  }

  async function uploadShot(file: File, postId: number) {
    setNote(null)
    setOcrView(null)
    if (file.size > 20 * 1024 * 1024) {
      setNote('截图不得超过 20MB')
      return
    }
    const okType =
      file.type === 'image/jpeg' ||
      file.type === 'image/png' ||
      file.type === 'image/webp' ||
      /\.(jpe?g|png|webp)$/i.test(file.name)
    if (!okType || /\.txt$/i.test(file.name)) {
      setNote('只接受 jpeg / png / webp')
      return
    }
    if (previewUrl.current) URL.revokeObjectURL(previewUrl.current)
    const preview = URL.createObjectURL(file)
    previewUrl.current = preview
    setShotProgress({ postId, name: file.name, phase: 'uploading', preview })
    try {
      const up = await apiUpload<{ url: string; mime: string; size_bytes: number }>(file)
      setShotProgress({ postId, name: file.name, phase: 'uploaded', preview })
      const shot = await apiFetch<{ ocr_result_id: number | null }>(
        `/api/posts/${postId}/screenshot`,
        {
          method: 'POST',
          body: {
            image_url: up.url,
            mime: up.mime,
            size_bytes: up.size_bytes,
          },
        },
      )
      if (shot.ocr_result_id == null) {
        setShotProgress({ postId, name: file.name, phase: 'done', preview })
        setNote('截图已上传，但没有识别出互动数。请点「更新数据」手填点赞、收藏和评论。')
        return
      }
      setShotProgress({ postId, name: file.name, phase: 'recognizing', preview })
      setShotFor(postId)
      let elapsed = 0
      if (ocrTimer.current) clearInterval(ocrTimer.current)
      ocrTimer.current = setInterval(() => {
        elapsed += 2000
        void (async () => {
          try {
            const ocr = await apiFetch<{
              parsed: {
                likes?: number
                collects?: number
                comments?: number
                shares?: number
              } | null
              confidence: number | null
              mismatch_flag: boolean
            }>(`/api/ocr/${postId}`)
            if (ocrTimer.current) clearInterval(ocrTimer.current)
            ocrTimer.current = null
            if (ocr.parsed == null) {
              setShotProgress((prev) =>
                prev && prev.postId === postId ? { ...prev, phase: 'done' } : prev,
              )
              setNote('没识别出互动数。请点「更新数据」手填。')
              setOcrView({
                likes: '—',
                collects: '—',
                comments: '—',
                shares: '—',
                confidence: ocr.confidence,
                low: false,
                mismatch: ocr.mismatch_flag,
                unrecognized: true,
              })
            } else {
              const conf = ocr.confidence ?? 1
              setOcrView({
                likes: String(ocr.parsed.likes ?? '—'),
                collects: String(ocr.parsed.collects ?? '—'),
                comments: String(ocr.parsed.comments ?? '—'),
                shares: String(ocr.parsed.shares ?? '—'),
                confidence: conf,
                low: conf < 0.7,
                mismatch: ocr.mismatch_flag,
                unrecognized: false,
              })
              setShotProgress((prev) =>
                prev && prev.postId === postId ? { ...prev, phase: 'done' } : prev,
              )
              const likes = ocr.parsed.likes
              const collects = ocr.parsed.collects
              const comments = ocr.parsed.comments
              const shares = ocr.parsed.shares
              const sure =
                !ocr.mismatch_flag &&
                conf >= 0.7 &&
                [likes, collects, comments, shares].every((n) => typeof n === 'number')
              if (sure) {
                const data = await apiFetch<{ snapshot: { engagement: number } }>(
                  `/api/posts/${postId}/metrics`,
                  {
                    method: 'POST',
                    body: {
                      likes,
                      collects,
                      comments,
                      shares,
                      source: 'ocr',
                    },
                  },
                )
                setRows((prev) =>
                  prev.map((row) =>
                    row.id === postId
                      ? {
                          ...row,
                          latest_snapshot: {
                            likes: likes as number,
                            collects: collects as number,
                            comments: comments as number,
                            shares: shares as number,
                            engagement: data.snapshot.engagement,
                          },
                        }
                      : row,
                  ),
                )
                setNote('已按识别结果写入互动数')
              } else {
                setNote('识别不确定，请点「更新数据」核对后再提交')
              }
            }
          } catch (error) {
            if (error instanceof ApiError && error.status === 409) {
              if (elapsed >= 30000) {
                if (ocrTimer.current) clearInterval(ocrTimer.current)
                ocrTimer.current = null
                setShotProgress((prev) =>
                  prev && prev.postId === postId ? { ...prev, phase: 'failed' } : prev,
                )
                setNote('识别没有完成。请点「更新数据」手填点赞、收藏和评论。')
              }
              return
            }
            if (ocrTimer.current) clearInterval(ocrTimer.current)
            setNote(failureMessage(error))
          }
        })()
      }, 2000)
    } catch (error) {
      setShotProgress((prev) =>
        prev && prev.postId === postId ? { ...prev, phase: 'failed' } : prev,
      )
      setNote(failureMessage(error))
    }
  }

  function openAppeal(postId: number) {
    setAppealFor(postId)
    setAppealReason('')
    setAppealReady(false)
    if (appealDelay.current) clearTimeout(appealDelay.current)
    appealDelay.current = setTimeout(() => setAppealReady(true), 2000)
  }

  async function confirmAppeal() {
    if (!appealReady || appealFor == null) return
    const reason = appealReason.trim()
    if (reason.length < 10 || reason.length > 500) {
      setNote('申诉理由须 10~500 字')
      return
    }
    try {
      await apiFetch(`/api/posts/${appealFor}/appeal`, {
        method: 'POST',
        body: { reason },
      })
      setRows((prev) =>
        prev.map((r) => (r.id === appealFor ? { ...r, status: 'appealed' } : r)),
      )
      setAppealFor(null)
    } catch (error) {
      if (error instanceof ApiError && error.status === 409) {
        setNote('已申诉过')
        setRows((prev) =>
          prev.map((r) => (r.id === appealFor ? { ...r, status: 'appealed' } : r)),
        )
        setAppealFor(null)
      } else {
        setNote(failureMessage(error))
      }
    }
  }

  if (!session) return null

  return (
    <>
      <AppBar session={session} />
      <main className="shell page page--app page--posts">
        <p className="crumb">
          <Link className="link" to="/customer">
            返回
          </Link>
        </p>
        <header className="page-head">
          <h1 className="page__title">我的作品</h1>
          <p className="lede" data-testid="peak-hint">
            奖励按<strong>互动量峰值</strong>结算
          </p>
        </header>

        <div className="studio-actions">
          <button
            type="button"
            className="btn btn--primary"
            onClick={() => void openFill().catch((e) => setNote(failureMessage(e)))}
            data-testid="fill-open"
          >
            回填作品
          </button>
        </div>

        {note ? (
          <div className="note" role="status" data-testid="posts-note">
            <span>{note}</span>
            {note.includes('创作') ? (
              <Link className="link" to="/customer">
                去创作
              </Link>
            ) : null}
          </div>
        ) : null}

        {state === 'error' ? (
          <LoadFailure onRetry={retry} />
        ) : state === 'loading' ? (
          <p className="note">加载中…</p>
        ) : rows.length === 0 ? (
          <p className="note">还没有作品</p>
        ) : (
          <ul className="post-list" data-testid="posts-list">
            {rows.map((row) => {
              const title = titles[row.claim_id] ?? '—'
              const eng = row.latest_snapshot?.engagement
              const sec = liveSeconds[row.id] ?? row.countdown_seconds
              return (
                <li key={row.id} className="post-row" data-testid={`post-${row.id}`}>
                  <div>
                    <p className="post-row__title">{title}</p>
                    <p className="post-row__meta">
                      {PLATFORMS.find((p) => p.id === row.platform)?.label ?? row.platform}
                      {' · '}
                      <a
                        href={row.post_url}
                        target="_blank"
                        rel="noopener noreferrer"
                      >
                        链接
                      </a>
                      {' · '}
                      <span className="badge">{STATUS_LABEL[row.status] ?? row.status}</span>
                      {' · '}
                      互动 {eng ?? '—'}
                      {' · '}
                      {countdownLabel(sec, tick)}
                    </p>
                  </div>
                  <div className="studio-actions">
                    {row.status === 'pending' || row.status === 'appealed' ? (
                      <>
                        <button
                          type="button"
                          className="btn btn--quiet"
                          onClick={() => {
                            setMetricsFor(row.id)
                            setMLikes('0')
                            setMCollects('0')
                            setMComments('0')
                            setMShares('0')
                          }}
                        >
                          更新数据
                        </button>
                        <label className="btn btn--quiet">
                          传截图
                          <input
                            type="file"
                            accept="image/jpeg,image/png,image/webp,.jpg,.jpeg,.png,.webp"
                            hidden
                            data-testid={`shot-${row.id}`}
                            onChange={(e) => {
                              const f = e.target.files?.[0]
                              if (f) void uploadShot(f, row.id)
                            }}
                          />
                        </label>
                      </>
                    ) : null}
                    {row.status === 'rejected' ? (
                      <button
                        type="button"
                        className="btn btn--quiet"
                        onClick={() => openAppeal(row.id)}
                        data-testid={`appeal-${row.id}`}
                      >
                        申诉
                      </button>
                    ) : null}
                    {row.status === 'appealed' ? (
                      <button type="button" className="btn btn--quiet" disabled>
                        申诉
                      </button>
                    ) : null}
                  </div>
                  {shotProgress?.postId === row.id ? (
                    <p className="note shot-status" data-testid={`shot-status-${row.id}`}>
                      <span>
                        {shotProgress.phase === 'uploading'
                          ? `正在上传「${shotProgress.name}」…`
                          : null}
                        {shotProgress.phase === 'uploaded'
                          ? `「${shotProgress.name}」已上传`
                          : null}
                        {shotProgress.phase === 'recognizing'
                          ? `「${shotProgress.name}」已上传，正在识别`
                          : null}
                        {shotProgress.phase === 'done'
                          ? `「${shotProgress.name}」上传完成`
                          : null}
                        {shotProgress.phase === 'failed'
                          ? `「${shotProgress.name}」上传失败`
                          : null}
                      </span>
                      {shotProgress.preview ? (
                        <img src={shotProgress.preview} alt={shotProgress.name} />
                      ) : null}
                    </p>
                  ) : null}
                </li>
              )
            })}
          </ul>
        )}

        {shotFor != null && ocrView ? (
          <section className="studio-panel" data-testid="ocr-result">
            {ocrView.unrecognized ? (
              <p>没认出来，请重新传一张清晰的截图</p>
            ) : null}
            <p>
              赞 {ocrView.likes} · 藏 {ocrView.collects} · 评 {ocrView.comments} · 转{' '}
              {ocrView.shares}
              {ocrView.confidence != null && !ocrView.unrecognized
                ? ` · 置信度 ${(ocrView.confidence * 100).toFixed(0)}%`
                : null}
            </p>
            {ocrView.low ? <p>识别置信度低，需要商家人工核对</p> : null}
            {ocrView.mismatch ? <p>与链接侧数据不一致，已转人工核对</p> : null}
          </section>
        ) : null}

        {fillOpen ? (
          <div className="picker" role="dialog" data-testid="fill-picker">
            <div className="picker__panel">
              <header className="picker__head">
                <h2 className="picker__title">回填作品</h2>
                <button type="button" className="btn btn--quiet" onClick={() => setFillOpen(false)}>
                  关闭
                </button>
              </header>
              <label className="field-label">
                作品
                <select
                  className="field"
                  value={pickedJob?.id ?? ''}
                  onChange={(e) =>
                    setPickedJob(
                      readyJobs.find((j) => j.id === Number(e.target.value)) ?? null,
                    )
                  }
                  data-testid="fill-job"
                >
                  {readyJobs.map((j) => (
                    <option key={j.id} value={j.id}>
                      job #{j.id}
                    </option>
                  ))}
                </select>
              </label>
              <label className="field-label">
                平台
                <select
                  className="field"
                  value={platform}
                  onChange={(e) => setPlatform(e.target.value)}
                  data-testid="fill-platform"
                >
                  {PLATFORMS.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.label}
                    </option>
                  ))}
                </select>
              </label>
              {platformErr ? (
                <p className="note note--error" data-testid="platform-error">
                  {platformErr}
                </p>
              ) : null}
              <label className="field-label">
                链接
                <input
                  className="field"
                  value={postUrl}
                  onChange={(e) => setPostUrl(e.target.value)}
                  data-testid="fill-url"
                />
              </label>
              <label className="field-label">
                标题（选填）
                <input
                  className="field"
                  value={postTitle}
                  onChange={(e) => setPostTitle(e.target.value)}
                />
              </label>
              <label className="field-label">
                正文（选填）
                <textarea
                  className="field"
                  rows={3}
                  value={postBody}
                  onChange={(e) => setPostBody(e.target.value)}
                />
              </label>
              <button
                type="button"
                className="btn btn--primary"
                onClick={() => void submitPost()}
                data-testid="fill-submit"
              >
                提交
              </button>
            </div>
          </div>
        ) : null}

        {metricsFor != null ? (
          <div className="picker" role="dialog" data-testid="metrics-picker">
            <div className="picker__panel">
              <h2 className="picker__title">更新数据</h2>
              {(['likes', 'collects', 'comments', 'shares'] as const).map((key) => {
                const map = {
                  likes: [mLikes, setMLikes],
                  collects: [mCollects, setMCollects],
                  comments: [mComments, setMComments],
                  shares: [mShares, setMShares],
                } as const
                const [val, setVal] = map[key]
                return (
                  <label key={key} className="field-label">
                    {key}
                    <input
                      className="field"
                      value={val}
                      onChange={(e) => setVal(e.target.value)}
                      data-testid={`metric-${key}`}
                    />
                  </label>
                )
              })}
              <div className="studio-actions">
                <button type="button" className="btn btn--quiet" onClick={() => setMetricsFor(null)}>
                  取消
                </button>
                <button
                  type="button"
                  className="btn btn--primary"
                  onClick={() => void submitMetrics()}
                  data-testid="metrics-submit"
                >
                  提交
                </button>
              </div>
            </div>
          </div>
        ) : null}

        {appealFor != null ? (
          <div className="picker" role="dialog" data-testid="appeal-dialog">
            <div className="picker__panel">
              <p>申诉机会仅有一次，提交后不可撤销</p>
              <textarea
                className="field"
                rows={4}
                value={appealReason}
                onChange={(e) => setAppealReason(e.target.value)}
                data-testid="appeal-reason"
              />
              <div className="studio-actions">
                <button
                  type="button"
                  className="btn btn--quiet"
                  disabled={!appealReady}
                  onClick={() => setAppealFor(null)}
                  data-testid="appeal-cancel"
                >
                  取消申诉
                </button>
                <button
                  type="button"
                  className="btn btn--primary"
                  disabled={!appealReady}
                  onClick={() => void confirmAppeal()}
                  data-testid="appeal-confirm"
                >
                  确定申诉
                </button>
              </div>
            </div>
          </div>
        ) : null}
      </main>
    </>
  )
}
