/**
 * 三个摘要接口全挂时才会出现。**不白屏**：一句能读懂的话 + 一个重试按钮。
 * 单个接口挂掉走的是卡片上的 `—`，不是这里。
 */
export function LoadFailure({ onRetry }: { onRetry: () => void }) {
  return (
    <div className="panel stack" data-testid="home-error" role="alert">
      <p>摘要数据暂时取不到，可能是网络或服务在打盹。</p>
      <div className="row">
        <button type="button" className="btn btn--quiet" onClick={onRetry}>
          重试
        </button>
      </div>
    </div>
  )
}
