/**
 * 新建任务。
 *
 * 「保存草稿」与「直接发布」是**两条不同的动作**，不是同一动作的两个开关：
 * 建任务（`POST /api/merchant/tasks`）与配奖励规则（`PUT .../reward-rules/{id}`）
 * 是两个端点，发布要靠后者的结果才成立。所以「直接发布」必然是三跳，
 * 且顺序固定——先有 task_id 才谈得上给它配规则。
 */

import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'

import { ApiError, apiFetch, failureMessage } from '../api/client'
import { AppBar } from '../components/AppBar'
import { RewardTiers } from '../components/RewardTiers'
import { TaskFields } from '../components/TaskFields'
import { useSession } from '../auth/useSession'
import {
  buildCreateBody,
  emptyForm,
  rewardBody,
  DEFAULT_TIERS,
  validateForm,
  validateTiers,
  type FieldErrors,
  type RewardTier,
  type TaskForm,
  type TaskRow,
} from '../lib/tasks'

const TASKS = '/api/merchant/tasks'
const RULES = '/api/merchant/reward-rules'

const METRIC = 'engagement'

/** 新建页所有字段都可改 */
const ALL_EDITABLE = [
  'title',
  'category',
  'description',
  'cover_url',
  'requirement',
  'tags',
  'quota',
  'start_at',
  'end_at',
] as const

export function MerchantTaskNew() {
  const session = useSession()
  const navigate = useNavigate()

  const [form, setForm] = useState<TaskForm>(emptyForm)
  const [errors, setErrors] = useState<FieldErrors>({})
  const [formError, setFormError] = useState<string | null>(null)
  const [tiers, setTiers] = useState<RewardTier[]>(() => DEFAULT_TIERS.map((t) => ({ ...t })))
  const [maxReward, setMaxReward] = useState('')
  const [violations, setViolations] = useState<string[]>([])
  const [tierOk, setTierOk] = useState(false)
  const [busy, setBusy] = useState(false)

  function patch(next: Partial<TaskForm>) {
    setForm((prev) => ({ ...prev, ...next }))
  }

  /** 本地校验。返回 true = 可以发请求 */
  function check(): boolean {
    const found = validateForm(form)
    setErrors(found)
    setFormError(null)
    return Object.keys(found).length === 0
  }

  function explain(error: unknown) {
    // 422 的字段级错误落在 `detail[].loc` 上，逐项贴回对应字段下面
    if (error instanceof ApiError) {
      const fields = { ...error.fieldErrors }
      const formMsg = fields._form
      delete fields._form
      if (Object.keys(fields).length > 0) {
        setErrors(fields)
        if (formMsg) setFormError(formMsg)
        else if (fields.start_at || fields.end_at) setFormError(null)
        return
      }
      if (formMsg) {
        setFormError(formMsg)
        return
      }
    }
    setFormError(failureMessage(error))
  }

  async function create(): Promise<TaskRow | null> {
    const data = await apiFetch<{ task: TaskRow }>(TASKS, {
      method: 'POST',
      // 注意**不含** tiers / reward：奖励规则走自己的端点
      body: buildCreateBody(form),
    })
    return data.task ?? null
  }

  async function saveDraft() {
    if (busy || !check()) return
    setBusy(true)
    try {
      const task = await create()
      if (task) navigate(`/merchant/tasks/${task.id}`)
    } catch (error) {
      explain(error)
    } finally {
      setBusy(false)
    }
  }

  async function publishNow() {
    if (busy || !check()) return

    const local = validateTiers(tiers)
    setViolations(local)
    if (local.length > 0) {
      setTierOk(false)
      return
    }

    setBusy(true)
    try {
      const task = await create()
      if (!task) return
      await apiFetch(`${RULES}/${task.id}`, {
        method: 'PUT',
        body: rewardBody(tiers, METRIC, maxReward),
      })
      await apiFetch(`${TASKS}/${task.id}/publish`, { method: 'POST' })
      navigate(`/merchant/tasks/${task.id}`)
    } catch (error) {
      explain(error)
    } finally {
      setBusy(false)
    }
  }

  /** 新建时还没有 task_id，服务端校验无从谈起——只做本地那一遍，并说明白 */
  function validateLocally() {
    const local = validateTiers(tiers)
    setViolations(local)
    setTierOk(local.length === 0)
  }

  if (!session) return null

  return (
    <>
      <AppBar session={session} />
      <main className="shell page" data-testid="task-new">
        <header className="stack">
          <h1 className="page__title">发布任务</h1>
          <p className="lede">先存草稿也行——草稿能慢慢改，发布出去的改动有限。</p>
        </header>

        <Link className="link" to="/merchant/tasks">
          返回列表
        </Link>

        {formError ? (
          <p className="note note--error" role="alert" data-testid="task-form-error">
            {formError}
          </p>
        ) : null}

        <TaskFields form={form} onChange={patch} errors={errors} editable={ALL_EDITABLE} showPayMode />

        <p className="note">
          奖励阶梯每档至少填一项：现金（单位：分，过审后待线下发放）、积分，或优惠券 / 代金券说明。直接发布前请确认开始时间不早于现在。
        </p>

        <RewardTiers
          tiers={tiers}
          onTiersChange={setTiers}
          metric={METRIC}
          maxRewardPerUser={maxReward}
          onMaxRewardChange={setMaxReward}
          notice="任务还没建出来，这儿的「校验」只跑本地那一遍；发布时服务端还会再校验一次。"
          violations={violations}
          valid={tierOk}
          onValidate={validateLocally}
          onSave={validateLocally}
          saveLabel="检查一下"
          busy={busy}
        />

        <div className="row row--end">
          <button
            type="button"
            className="btn btn--quiet"
            data-testid="task-save-draft"
            onClick={() => void saveDraft()}
            disabled={busy}
          >
            保存草稿
          </button>
          <button
            type="button"
            className="btn btn--primary"
            data-testid="task-publish"
            onClick={() => void publishNow()}
            disabled={busy}
          >
            直接发布
          </button>
        </div>
      </main>
    </>
  )
}
