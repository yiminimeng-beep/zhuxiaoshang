/** 内容工坊类型与展示帮手 */

export type JobStatus =
  | 'created'
  | 'guarding'
  | 'guard_failed'
  | 'chatting'
  | 'generating'
  | 'judging'
  | 'ready'
  | 'need_review'
  | 'failed'

export interface JobRow {
  id: number
  task_id: number
  claim_id: number
  status: JobStatus | string
  fail_reason: string | null
  retry_count: number
  kind: string
}

export interface JobDetail {
  job: JobRow
  inputs: { id: number; url: string; mime: string }[]
  prompt_draft: {
    raw_prompt: string | null
    optimized_prompt: string | null
    quality_score: number | null
    rewrite_failed: boolean
  } | null
  outputs: {
    id: number
    type: string
    content: string | null
    url: string | null
    judge_score: number | null
    judge_detail: { reasons?: string[] } | null
    is_active: boolean
  }[]
}

export interface ChatMsg {
  id: number
  role: 'user' | 'assistant' | string
  content: string
}

export const STAGE_LABEL: Record<string, string> = {
  guard: '预检',
  rewrite: '复写',
  generate: '生成',
  judge: '质检',
  settle: '结算',
}

export function stageLabel(stage: string | null | undefined): string {
  if (!stage) return '处理中…'
  return STAGE_LABEL[stage] ?? stage
}

const RESERVED_KEY = 'zxs.studio.reserved.'

export function rememberReserved(
  jobId: number,
  reserved: number,
  taskId: number,
): void {
  sessionStorage.setItem(
    RESERVED_KEY + jobId,
    JSON.stringify({ reserved_points: reserved, task_id: taskId }),
  )
}

export function readReserved(
  jobId: number,
): { reserved_points: number; task_id: number } | null {
  const raw = sessionStorage.getItem(RESERVED_KEY + jobId)
  if (!raw) return null
  try {
    return JSON.parse(raw) as { reserved_points: number; task_id: number }
  } catch {
    return null
  }
}

export function canDelete(status: string): boolean {
  return status === 'created' || status === 'guard_failed' || status === 'failed'
}

const STATUS_LABEL: Record<string, string> = {
  created: '已创建',
  guarding: '预检中',
  guard_failed: '预检未过',
  chatting: '对话中',
  generating: '生成中',
  judging: '质检中',
  ready: '已就绪',
  need_review: '人工复核',
  failed: '失败',
}

export function jobStatusLabel(status: string | null | undefined): string {
  if (!status) return '—'
  return STATUS_LABEL[status] ?? status
}
