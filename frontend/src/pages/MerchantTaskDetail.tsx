/**
 * 任务详情：编辑、奖励阶梯、状态机、自动保存。
 *
 * 三处「写错了屏幕上看着也对」的地方，都在这个文件里：
 *
 * 1. 详情走 **`GET /api/tasks/{id}`**，不是 `/api/merchant/tasks/{id}`。
 *    阶梯（`rule`）只在前者的响应里，换端点会静默变空——页面照常渲染，
 *    只是奖励区成了「未配置」。
 * 2. `PATCH` 体里**只放改过的字段**。后端读 `exclude_unset`，把整个 task
 *    原样发回去，连没动过的 `title` 也会被当成「要改 title」，published 下
 *    立刻 409。
 * 3. 状态**不做乐观更新**。`402` 之后先把状态改成「已发布」再回滚，用户
 *    会先看到一眼假的状态——状态只以服务端重取的为准。
 */

import { useCallback, useEffect, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'

import { ApiError, apiFetch, failureMessage } from '../api/client'
import { AppBar } from '../components/AppBar'
import { RewardTiers } from '../components/RewardTiers'
import { TaskFields } from '../components/TaskFields'
import { useSession } from '../auth/useSession'
import { formatCstClock } from '../lib/time'
import {
  DRAFT_EDITABLE,
  PUBLISHED_EDITABLE,
  buildPatchBody,
  formFromTask,
  parseTaskId,
  payLabel,
  rewardBody,
  statusLabel,
  tiersFromRule,
  validateTiers,
  type RewardRule,
  type RewardTier,
  type TaskDetailPayload,
  type TaskForm,
  type TaskRow,
} from '../lib/tasks'

const TASKS = '/api/merchant/tasks'
const RULES = '/api/merchant/reward-rules'
/** 停手 0.8 秒才落库：连着敲字不该每个键一个请求 */
const AUTOSAVE_DELAY = 800

type LoadState = 'loading' | 'ok' | 'missing' | 'error'

export function MerchantTaskDetail() {
  const session = useSession()
  const params = useParams()
  const taskId = parseTaskId(params.id)

  const [payload, setPayload] = useState<TaskDetailPayload | null>(null)
  const [state, setState] = useState<LoadState>('loading')
  /** 推一下就去重取一次 */
  const [version, setVersion] = useState(0)
  /**
   * 数据**落定**的次数，用作重建表单的 key。
   *
   * 不能用 `version`：它一变就重挂载，而那时新数据还在路上，表单会照着
   * 旧数据重建一次，等新数据到了 key 已经不变、再也不会重建——「重取详情
   * 后表单以服务端为准」这条就静默失效了。
   */
  const [revision, setRevision] = useState(0)
  // 动作类错误存在这一层：重取详情会重建表单，放在表单里会跟着一起没
  const [actionError, setActionError] = useState<string | null>(null)

  const reload = useCallback(() => setVersion((n) => n + 1), [])

  /**
   * 换了 id 或要重取，就是**新的一份数据**，先退回 loading。
   *
   * 在渲染期做而不是 effect 里：`taskId === null` 根本不该发请求，而「非法 id
   * → 任务不存在」是从 URL 直接推出来的事实，不是一次请求的结果。丢进 effect
   * 会让这一帧先按旧 payload 渲染一次，等于让用户看一眼错的任务。
   */
  const loadKey = taskId === null ? 'missing' : `${taskId}#${version}`
  const [loadedKey, setLoadedKey] = useState<string | null>(null)
  if (loadedKey !== loadKey) {
    setLoadedKey(loadKey)
    setState(taskId === null ? 'missing' : 'loading')
  }

  useEffect(() => {
    if (taskId === null) return
    let alive = true
    void (async () => {
      try {
        // 注意是 /api/tasks/{id}：阶梯只在这个响应里
        const data = await apiFetch<TaskDetailPayload>(`/api/tasks/${taskId}`)
        if (!alive) return
        setPayload(data)
        setRevision((n) => n + 1)
        setState('ok')
      } catch (error) {
        if (!alive) return
        setState(error instanceof ApiError && error.status === 404 ? 'missing' : 'error')
      }
    })()
    return () => {
      alive = false
    }
  }, [taskId, version])

  if (!session) return null

  return (
    <>
      <AppBar session={session} />
      <main className="shell page" data-testid="task-detail-page">
        <Link className="link" to="/merchant/tasks">
          返回列表
        </Link>

        {state === 'missing' ? (
          <div className="panel stack" data-testid="task-missing">
            <h1 className="page__title">任务不存在</h1>
            <p className="lede">它可能已经被删掉，或者从来就不是你的任务。</p>
          </div>
        ) : state === 'error' ? (
          <div className="panel stack" role="alert" data-testid="task-detail-error">
            <h1 className="page__title">任务详情暂时取不到</h1>
            <p className="lede">可能是网络或服务在打盹，重试一次通常就好了。</p>
            <div className="row">
              <button
                type="button"
                className="btn btn--quiet"
                data-testid="task-detail-retry"
                onClick={reload}
              >
                重试
              </button>
            </div>
          </div>
        ) : state === 'loading' || !payload ? (
          <p className="note" data-testid="task-detail-loading">
            正在取任务…
          </p>
        ) : (
          <DetailBody
            // 数据一落定就重建表单：服务端回来了，屏幕上就该是服务端的样子
            key={revision}
            task={payload.task}
            rule={payload.rule}
            actionError={actionError}
            onError={setActionError}
            onReload={reload}
          />
        )}
      </main>
    </>
  )
}

interface DetailBodyProps {
  task: TaskRow
  rule: RewardRule | null
  actionError: string | null
  onError: (message: string | null) => void
  onReload: () => void
}

function DetailBody({ task, rule, actionError, onError, onReload }: DetailBodyProps) {
  const navigate = useNavigate()

  const [form, setForm] = useState<TaskForm>(() => formFromTask(task))
  const [initial, setInitial] = useState<TaskForm>(() => formFromTask(task))
  const [tiers, setTiers] = useState<RewardTier[]>(() => tiersFromRule(rule))
  const metric = rule?.metric ?? 'engagement'
  const [maxReward, setMaxReward] = useState(() =>
    typeof rule?.max_reward_per_user === 'number' ? String(rule.max_reward_per_user) : '',
  )
  const [violations, setViolations] = useState<string[]>([])
  const [tierOk, setTierOk] = useState(false)
  const [autosave, setAutosave] = useState<{ state: 'idle' | 'saved' | 'failed'; at: string | null }>(
    { state: 'idle', at: null },
  )
  const [confirming, setConfirming] = useState(false)
  const [busy, setBusy] = useState(false)

  const draft = task.status === 'draft'
  const editable: readonly string[] =
    draft || task.status === 'paused'
      ? DRAFT_EDITABLE
      : task.status === 'published'
        ? PUBLISHED_EDITABLE
        : []

  /**
   * 自动保存：只在草稿上跑。
   *
   * `initial` 是基线——存成功后把它推到当前值，于是「没有差异」这件事
   * 由数据本身表达，不需要额外的 dirty 标志跟着状态走。
   */
  useEffect(() => {
    if (!draft) return
    const body = buildPatchBody(form, initial, DRAFT_EDITABLE)
    if (Object.keys(body).length === 0) return

    const timer = setTimeout(() => {
      void (async () => {
        try {
          const data = await apiFetch<{ saved_at?: string }>(`${TASKS}/${task.id}/draft`, {
            method: 'PUT',
            body,
          })
          setInitial(form)
          setAutosave({ state: 'saved', at: data?.saved_at ?? null })
        } catch {
          // 内容**不清空**：自动保存失败是后台的事，不该把用户正在敲的字收走
          setAutosave({ state: 'failed', at: null })
        }
      })()
    }, AUTOSAVE_DELAY)

    return () => clearTimeout(timer)
  }, [form, initial, draft, task.id])

  function patch(next: Partial<TaskForm>) {
    setForm((prev) => ({ ...prev, ...next }))
  }

  async function save() {
    if (busy) return
    const body = buildPatchBody(form, initial, editable)
    if (Object.keys(body).length === 0) {
      onError(null)
      return
    }
    setBusy(true)
    try {
      await apiFetch(`${TASKS}/${task.id}`, { method: 'PATCH', body })
      setInitial(form)
      onError(null)
    } catch (error) {
      onError(failureMessage(error))
      // 409 往往是「本地的形状已经过时了」（比如别处改过状态），重取一次对齐
      if (error instanceof ApiError && error.status === 409) onReload()
    } finally {
      setBusy(false)
    }
  }

  async function act(action: 'publish' | 'pause' | 'close') {
    if (busy) return
    setBusy(true)
    onError(null)
    try {
      await apiFetch(`${TASKS}/${task.id}/${action}`, { method: 'POST' })
      onReload()
    } catch (error) {
      onError(failureMessage(error))
    } finally {
      setBusy(false)
    }
  }

  async function remove() {
    if (busy) return
    setBusy(true)
    onError(null)
    try {
      await apiFetch(`${TASKS}/${task.id}`, { method: 'DELETE' })
      navigate('/merchant/tasks')
    } catch (error) {
      onError(failureMessage(error))
      setConfirming(false)
    } finally {
      setBusy(false)
    }
  }

  function explainRuleError(error: unknown) {
    const list = error instanceof ApiError ? error.violations : []
    if (list.length > 0) {
      setViolations(list)
      setTierOk(false)
      return
    }
    setTierOk(false)
    onError(failureMessage(error))
  }

  async function validateRule() {
    const local = validateTiers(tiers)
    if (local.length > 0) {
      setViolations(local)
      setTierOk(false)
      return
    }
    try {
      await apiFetch(`${RULES}/${task.id}/validate`, {
        method: 'POST',
        body: rewardBody(tiers, metric, maxReward),
      })
      setViolations([])
      setTierOk(true)
    } catch (error) {
      explainRuleError(error)
    }
  }

  async function saveRule() {
    const local = validateTiers(tiers)
    if (local.length > 0) {
      setViolations(local)
      setTierOk(false)
      return
    }
    setBusy(true)
    onError(null)
    try {
      await apiFetch(`${RULES}/${task.id}`, {
        method: 'PUT',
        body: rewardBody(tiers, metric, maxReward),
      })
      setViolations([])
      setTierOk(true)
    } catch (error) {
      explainRuleError(error)
    } finally {
      setBusy(false)
    }
  }

  const autosaveText =
    autosave.state === 'failed'
      ? '未保存（自动保存没成功，内容还在）'
      : autosave.state === 'saved'
        ? `已保存 ${formatCstClock(autosave.at)}`
        : '自动保存已开启'

  return (
    <div className="stack">
      <header className="stack">
        <div className="row">
          <h1 className="page__title" data-testid="task-detail-title">
            {task.title}
          </h1>
          <span className="badge" data-testid="task-detail-status">
            {statusLabel(task.status)}
          </span>
          <span className="badge" data-testid="task-detail-pay">
            {payLabel(task.pay_mode)}
          </span>
        </div>
        <p className="lede" data-testid="task-detail">
          {task.status === 'published'
            ? '已发布：标题、品类、标签和开始时间都锁住了，能改的只有描述、结束时间、要求和名额。'
            : '草稿阶段随便改。'}
        </p>
      </header>

      {actionError ? (
        <p className="note note--error" role="alert" data-testid="task-action-error">
          {actionError}
        </p>
      ) : null}

      {draft ? (
        <p className="field__helper" data-testid="task-autosave">
          {autosaveText}
        </p>
      ) : null}

      <TaskFields form={form} onChange={patch} errors={{}} editable={editable} showPayMode={false} />

      <RewardTiers
        tiers={tiers}
        onTiersChange={setTiers}
        metric={metric}
        maxRewardPerUser={maxReward}
        onMaxRewardChange={setMaxReward}
        notice={rule === null ? '未配置奖励阶梯，不能发布。先配好阶梯再发布。' : null}
        violations={violations}
        valid={tierOk}
        onValidate={() => void validateRule()}
        onSave={() => void saveRule()}
        busy={busy}
      />

      <div className="row row--end">
        {draft || task.status === 'paused' ? (
          <button
            type="button"
            className="btn btn--primary"
            data-testid="task-publish"
            onClick={() => void act('publish')}
            disabled={busy || rule === null}
          >
            {task.status === 'paused' ? '恢复发布' : '发布'}
          </button>
        ) : null}

        {task.status === 'published' ? (
          <>
            <button
              type="button"
              className="btn btn--quiet"
              data-testid="task-pause"
              onClick={() => void act('pause')}
              disabled={busy}
            >
              暂停
            </button>
            <button
              type="button"
              className="btn btn--quiet"
              data-testid="task-close"
              onClick={() => void act('close')}
              disabled={busy}
            >
              关闭
            </button>
          </>
        ) : null}

        {task.status === 'paused' ? (
          <button
            type="button"
            className="btn btn--quiet"
            data-testid="task-close"
            onClick={() => void act('close')}
            disabled={busy}
          >
            关闭
          </button>
        ) : null}

        {task.status !== 'published' && task.status !== 'closed' ? (
          <button
            type="button"
            className="btn btn--text"
            data-testid="task-detail-delete"
            onClick={() => {
              if (confirming) void remove()
              else {
                onError(null)
                setConfirming(true)
              }
            }}
            disabled={busy}
          >
            {confirming ? '确认删除' : '删除'}
          </button>
        ) : null}

        {task.status !== 'closed' ? (
          <button
            type="button"
            className="btn btn--primary"
            data-testid="task-save"
            onClick={() => void save()}
            disabled={busy}
          >
            保存
          </button>
        ) : null}
      </div>
    </div>
  )
}
