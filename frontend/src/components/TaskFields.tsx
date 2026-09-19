/**
 * 任务表单的字段区。新建页与详情页共用——两处各写一遍，
 * 「哪些字段 published 下只读」这条规则迟早只在一处生效。
 *
 * `editable` 是**白名单**：不在里面的字段一律 `disabled`。注意 disabled 的
 * 输入框依然在表单里、依然被读出值，所以「不下发」这件事由调用方按
 * `editable` 过滤请求体来完成，不是靠 DOM。
 */

import type { FieldErrors, TaskForm } from '../lib/tasks'

export interface TaskFieldsProps {
  form: TaskForm
  onChange: (patch: Partial<TaskForm>) => void
  errors: FieldErrors
  editable: readonly string[]
  /** 详情页不放付费模式（后端中途改 pay_mode 一律 409），新建页要放 */
  showPayMode: boolean
}

interface FieldDef {
  name: keyof TaskForm & string
  label: string
  kind: 'text' | 'area' | 'number' | 'datetime-local'
}

const FIELDS: FieldDef[] = [
  { name: 'title', label: '标题', kind: 'text' },
  { name: 'category', label: '品类', kind: 'text' },
  { name: 'description', label: '描述', kind: 'area' },
  { name: 'cover_url', label: '封面图', kind: 'text' },
  { name: 'requirement', label: '要求', kind: 'area' },
  { name: 'tags', label: '标签', kind: 'text' },
  { name: 'quota', label: '名额', kind: 'number' },
  { name: 'start_at', label: '开始时间', kind: 'datetime-local' },
  { name: 'end_at', label: '结束时间', kind: 'datetime-local' },
]

const HINT: Record<string, string> = {
  tags: '逗号分隔，最多 5 个，每个不超过 16 字',
  quota: '留空 = 不限量',
  cover_url: '可留空',
  requirement: '可留空。写清交付要求，能少一半来回',
}

export function TaskFields({ form, onChange, errors, editable, showPayMode }: TaskFieldsProps) {
  const reimburse = form.pay_mode === 'user_pay_reimburse'

  return (
    <div className="stack">
      {FIELDS.map((field) => {
        const id = `task-${field.name}`
        const error = errors[field.name]
        const disabled = !editable.includes(field.name)
        return (
          <div className={`field${error ? ' field--error' : ''}`} key={field.name}>
            <label className="field__label" htmlFor={id}>
              {field.label}
            </label>
            {field.kind === 'area' ? (
              <textarea
                id={id}
                name={field.name}
                className="field__area"
                value={form[field.name]}
                disabled={disabled}
                onChange={(event) => onChange({ [field.name]: event.target.value })}
              />
            ) : (
              <input
                id={id}
                name={field.name}
                className="field__input"
                type={field.kind === 'text' ? 'text' : field.kind}
                value={form[field.name]}
                disabled={disabled}
                onChange={(event) => onChange({ [field.name]: event.target.value })}
              />
            )}
            {error ? (
              <p className="field__helper field__helper--error" data-testid={`task-error-${field.name}`}>
                {error}
              </p>
            ) : HINT[field.name] ? (
              <p className="field__helper">{HINT[field.name]}</p>
            ) : null}
          </div>
        )
      })}

      {showPayMode ? (
        <fieldset className="field choice-group">
          <legend className="field__label">付费模式</legend>
          <label className="choice" htmlFor="task-pay-merchant">
            <input
              id="task-pay-merchant"
              name="pay_mode"
              type="radio"
              value="merchant_pay"
              checked={form.pay_mode === 'merchant_pay'}
              onChange={() => onChange({ pay_mode: 'merchant_pay' })}
            />
            商户付费
          </label>
          <label className="choice" htmlFor="task-pay-reimburse">
            <input
              id="task-pay-reimburse"
              name="pay_mode"
              type="radio"
              value="user_pay_reimburse"
              checked={reimburse}
              onChange={() => onChange({ pay_mode: 'user_pay_reimburse' })}
            />
            用户垫付
          </label>
        </fieldset>
      ) : null}

      {reimburse ? (
        <>
          <div className={`field${errors.reimburse ? ' field--error' : ''}`}>
            <label className="field__label" htmlFor="task-reimburse_pool">
              报销池额度
            </label>
            <input
              id="task-reimburse_pool"
              name="reimburse_pool"
              className="field__input"
              type="number"
              value={form.reimburse_pool}
              onChange={(event) => onChange({ reimburse_pool: event.target.value })}
            />
          </div>

          <div className={`field${errors.reimburse ? ' field--error' : ''}`}>
            <label className="field__label" htmlFor="task-reimburse_per_user_limit">
              单用户报销上限
            </label>
            <input
              id="task-reimburse_per_user_limit"
              name="reimburse_per_user_limit"
              className="field__input"
              type="number"
              value={form.reimburse_per_user_limit}
              onChange={(event) => onChange({ reimburse_per_user_limit: event.target.value })}
            />
          </div>

          {/* 池子与上限同一条规则，合成一个错误位——分开报会让人以为两处各错 */
          errors.reimburse ? (
            <p className="field__helper field__helper--error" data-testid="task-error-reimburse">
              {errors.reimburse}
            </p>
          ) : (
            <p className="field__helper">报销只退平台额度，不可提现</p>
          )}
        </>
      ) : null}
    </div>
  )
}
