import { useEffect, useState, type FormEvent } from 'react'

import { ApiError, apiFetch } from '../api/client'

const CATEGORIES = [
  { value: 'bug', label: '功能异常' },
  { value: 'suggestion', label: '功能建议' },
  { value: 'other', label: '其他' },
] as const

const MIN_LENGTH = 5
const MAX_LENGTH = 500

const CONTENT_FIELD = 'content'
const FORM_ERROR = '_form'

export function FeedbackSheet({
  onClose,
  onSent,
}: {
  onClose: () => void
  onSent: () => void
}) {
  const [category, setCategory] = useState<string>('suggestion')
  const [content, setContent] = useState('')
  const [contact, setContact] = useState('')
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [submitting, setSubmitting] = useState(false)

  // 与后端同口径：按**去掉首尾空白后**的字数算，全空白自然落进下界之外
  const length = content.trim().length
  const tooShort = length < MIN_LENGTH
  const tooLong = length > MAX_LENGTH
  const blocked = tooShort || tooLong

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', onKeyDown)
    return () => document.removeEventListener('keydown', onKeyDown)
  }, [onClose])

  async function submit(event: FormEvent) {
    event.preventDefault()
    if (submitting || blocked) return
    setSubmitting(true)
    setErrors({})
    try {
      await apiFetch('/api/feedback', {
        method: 'POST',
        // 绝不带 role / user_id：身份由服务端按登录态推导。带上就是一次 422。
        body: {
          category,
          content: content.trim(),
          contact: contact.trim() || null,
        },
      })
      onSent()
    } catch (error) {
      // 401 时令牌已被清空，守卫会把用户送到 /login——这里不要再写任何状态
      if (error instanceof ApiError && error.status === 401) return
      if (error instanceof ApiError && error.status === 422) {
        setErrors(error.fieldErrors)
      } else if (error instanceof ApiError && error.detail) {
        setErrors({ [FORM_ERROR]: error.detail })
      } else {
        setErrors({ [FORM_ERROR]: '提交失败，请稍后再试' })
      }
    } finally {
      setSubmitting(false)
    }
  }

  const hint = tooShort ? `至少 ${MIN_LENGTH} 字` : tooLong ? `最多 ${MAX_LENGTH} 字` : ''

  return (
    <div className="overlay">
      <div className="sheet" role="dialog" aria-modal="true" aria-label="问题反馈">
        <header className="stack">
          <h2 className="page__title">问题反馈</h2>
          <p className="lede">说说哪里不好用、哪里缺东西。我们会读。</p>
        </header>

        <form className="stack" onSubmit={submit} noValidate>
          <fieldset className="choice-group">
            <legend className="field__label">类型</legend>
            {CATEGORIES.map((option) => (
              <label key={option.value} className="choice">
                <input
                  type="radio"
                  name="category"
                  value={option.value}
                  checked={category === option.value}
                  onChange={() => setCategory(option.value)}
                />
                <span>{option.label}</span>
              </label>
            ))}
          </fieldset>

          <div className={`field${errors[CONTENT_FIELD] ? ' field--error' : ''}`}>
            <label className="field__label" htmlFor="feedback-content">
              反馈内容
            </label>
            <textarea
              id="feedback-content"
              name="content"
              className="field__area"
              value={content}
              onChange={(event) => setContent(event.target.value)}
              placeholder="例如：希望任务列表能按截止时间排序"
            />
            <div className="field__meta">
              <span className="field__helper">{errors[CONTENT_FIELD] ?? hint}</span>
              <span className="field__helper field__helper--count">
                {`${length} / ${MAX_LENGTH}`}
              </span>
            </div>
          </div>

          <div className={`field${errors.contact ? ' field--error' : ''}`}>
            <label className="field__label" htmlFor="feedback-contact">
              联系方式（选填）
            </label>
            <input
              id="feedback-contact"
              name="contact"
              type="text"
              className="field__input"
              value={contact}
              onChange={(event) => setContact(event.target.value)}
            />
            <span className="field__helper">{errors.contact ?? ''}</span>
          </div>

          {errors[FORM_ERROR] ? (
            <p className="note note--error" role="alert">
              {errors[FORM_ERROR]}
            </p>
          ) : null}

          <div className="row row--end">
            <button type="button" className="btn btn--quiet" onClick={onClose}>
              取消
            </button>
            <button type="submit" className="btn btn--primary" disabled={submitting || blocked}>
              {submitting ? '提交中…' : '提交反馈'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}
