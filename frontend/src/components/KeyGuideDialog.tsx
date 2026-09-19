/**
 * 缺 Key / Key 失效指引弹窗（08 追加 F）。
 */

import { Link } from 'react-router-dom'

export function KeyGuideDialog({
  title,
  detail,
  provider,
  onClose,
}: {
  title: string
  detail: string
  provider: string
  onClose: () => void
}) {
  const href = `/customer/keys?provider=${encodeURIComponent(provider)}`
  return (
    <div className="picker" role="dialog" aria-label="密钥指引" data-testid="key-guide-dialog">
      <div className="picker__panel">
        <header className="picker__head">
          <h2 className="picker__title">{title}</h2>
          <button type="button" className="btn btn--quiet" onClick={onClose}>
            关闭
          </button>
        </header>
        <p className="lede" data-testid="key-guide-detail">
          {detail}
        </p>
        <ol className="lede" data-testid="key-guide-steps">
          <li>打开「模型与密钥」页</li>
          <li>选择对应厂商（{provider}）</li>
          <li>粘贴 API Key 并保存；需要时可点「校验」</li>
          <li>回到创作台再试一次</li>
        </ol>
        <p className="note">Key 在对应厂商控制台创建；本站只保存加密后的密钥，列表只显示掩码。</p>
        <div className="studio-actions">
          <Link className="btn btn--primary" to={href} data-testid="key-guide-go">
            去添加密钥
          </Link>
          <button type="button" className="btn btn--quiet" onClick={onClose}>
            关闭
          </button>
        </div>
      </div>
    </div>
  )
}
