import { useState } from 'react'

import { FeedbackSheet } from './FeedbackSheet'

/** 反馈入口：悬浮右下（真 FAB），两端首页常驻 */
export function FeedbackLauncher() {
  const [open, setOpen] = useState(false)
  const [sent, setSent] = useState(false)

  return (
    <>
      {sent ? (
        <p className="note note--ok feedback-toast" role="status">
          已收到，感谢你的建议
        </p>
      ) : null}

      <button
        type="button"
        className="btn btn--primary feedback-fab"
        onClick={() => {
          setSent(false)
          setOpen(true)
        }}
      >
        问题反馈
      </button>

      {open ? (
        <FeedbackSheet
          onClose={() => setOpen(false)}
          onSent={() => {
            setOpen(false)
            setSent(true)
          }}
        />
      ) : null}
    </>
  )
}
