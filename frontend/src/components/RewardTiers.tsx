/**
 * 奖励阶梯编辑器。详情页与新建页**共用同一份**——两处各写一遍，
 * 「校验」按钮的语义迟早会漂成两种。
 *
 * 这一块只负责收集与呈现；调不调后端由调用方决定（新建页要先建出任务
 * 才有 task_id 可 PUT）。
 */

import type { RewardTier } from '../lib/tasks'

const METRIC_LABEL: Record<string, string> = { engagement: '互动量' }

function metricLabel(metric: string): string {
  return METRIC_LABEL[metric] ?? metric
}

function numberOf(tier: RewardTier, key: 'cash' | 'points'): number | '' {
  const value = tier.reward?.[key]
  return typeof value === 'number' && !Number.isNaN(value) ? value : ''
}

function benefitOf(tier: RewardTier): string {
  const value = tier.reward?.benefit
  return typeof value === 'string' ? value : ''
}

export interface RewardTiersProps {
  tiers: RewardTier[]
  onTiersChange: (tiers: RewardTier[]) => void
  metric: string
  maxRewardPerUser: string
  onMaxRewardChange: (value: string) => void
  /** 未配置规则时的说明（`MR-08`）。`null` = 已配置 */
  notice: string | null
  violations: string[]
  valid: boolean
  onValidate: () => void
  onSave: () => void
  saveLabel?: string
  busy?: boolean
}

export function RewardTiers({
  tiers,
  onTiersChange,
  metric,
  maxRewardPerUser,
  onMaxRewardChange,
  notice,
  violations,
  valid,
  onValidate,
  onSave,
  saveLabel = '保存阶梯',
  busy = false,
}: RewardTiersProps) {
  function patchTier(index: number, next: Partial<RewardTier>): void {
    onTiersChange(tiers.map((tier, i) => (i === index ? { ...tier, ...next } : tier)))
  }

  function setNumber(index: number, key: 'cash' | 'points', raw: string): void {
    const tier = tiers[index]
    const reward = { ...tier.reward }
    if (raw.trim() === '') {
      delete reward[key]
    } else {
      const parsed = Number(raw)
      if (!Number.isNaN(parsed)) reward[key] = parsed
    }
    patchTier(index, { reward })
  }

  function setBenefit(index: number, raw: string): void {
    const tier = tiers[index]
    const reward = { ...tier.reward }
    if (raw.trim() === '') delete reward.benefit
    else reward.benefit = raw
    patchTier(index, { reward })
  }

  function addTier(): void {
    const last = tiers[tiers.length - 1]
    const min = last ? (last.max ?? 0) + 1 : 0
    onTiersChange([...tiers, { min, max: null, reward: {} }])
  }

  function removeTier(index: number): void {
    onTiersChange(tiers.filter((_, i) => i !== index))
  }

  return (
    <section className="panel stack" data-testid="tier-editor">
      <header className="row row--between">
        <h2 className="section-title">奖励阶梯</h2>
        <span className="badge" data-testid="tier-metric">
          {metricLabel(metric)}
        </span>
      </header>

      {notice ? (
        <p className="note" data-testid="tier-notice">
          {notice}
        </p>
      ) : null}

      <div className="stack" data-testid="tier-rows">
        {tiers.map((tier, i) => {
          const last = i === tiers.length - 1
          return (
            <div className="tier-row" data-testid={`tier-row-${i}`} key={i}>
              <div className="field">
                <label className="field__label" htmlFor={`tier-min-input-${i}`}>
                  下限（含）
                </label>
                <input
                  id={`tier-min-input-${i}`}
                  data-testid={`tier-min-${i}`}
                  className="field__input"
                  type="number"
                  // 第一档恒从 0 起：可编辑的第一档后端一律拒
                  value={tier.min}
                  disabled
                  readOnly
                />
              </div>

              <div className="field">
                <label className="field__label" htmlFor={`tier-max-input-${i}`}>
                  上限（含）
                </label>
                <input
                  id={`tier-max-input-${i}`}
                  data-testid={`tier-max-${i}`}
                  className="field__input"
                  type="number"
                  // 末档没有上限：留着不填就是「及以上全归这档」
                  value={tier.max ?? ''}
                  disabled={last}
                  onChange={(event) =>
                    patchTier(i, {
                      max: event.target.value.trim() === '' ? null : Number(event.target.value),
                    })
                  }
                />
              </div>

              <div className="field">
                <label className="field__label" htmlFor={`tier-cash-input-${i}`}>
                  现金（分）
                </label>
                <input
                  id={`tier-cash-input-${i}`}
                  data-testid={`tier-cash-${i}`}
                  className="field__input"
                  type="number"
                  value={numberOf(tier, 'cash')}
                  onChange={(event) => setNumber(i, 'cash', event.target.value)}
                />
              </div>

              <div className="field">
                <label className="field__label" htmlFor={`tier-points-input-${i}`}>
                  积分
                </label>
                <input
                  id={`tier-points-input-${i}`}
                  data-testid={`tier-points-${i}`}
                  className="field__input"
                  type="number"
                  value={numberOf(tier, 'points')}
                  onChange={(event) => setNumber(i, 'points', event.target.value)}
                />
              </div>

              <div className="field">
                <label className="field__label" htmlFor={`tier-benefit-input-${i}`}>
                  优惠券 / 代金券
                </label>
                <input
                  id={`tier-benefit-input-${i}`}
                  data-testid={`tier-benefit-${i}`}
                  className="field__input"
                  type="text"
                  placeholder="例如：满 30 减 10 代金券"
                  value={benefitOf(tier)}
                  onChange={(event) => setBenefit(i, event.target.value)}
                />
              </div>

              <button
                type="button"
                className="btn btn--text"
                data-testid={`tier-remove-${i}`}
                disabled={tiers.length <= 1}
                onClick={() => removeTier(i)}
              >
                删除
              </button>
            </div>
          )
        })}
      </div>

      <div className="field">
        <label className="field__label" htmlFor="tier-max-reward">
          单用户封顶（分，留空 = 不封顶）
        </label>
        <input
          id="tier-max-reward"
          data-testid="tier-max-reward"
          className="field__input"
          type="number"
          value={maxRewardPerUser}
          onChange={(event) => onMaxRewardChange(event.target.value)}
        />
      </div>

      <button type="button" className="btn btn--quiet" data-testid="tier-add" onClick={addTier}>
        加一档
      </button>

      {violations.map((violation, i) => (
        <p className="note note--error" data-testid={`tier-violation-${i}`} key={i}>
          {violation}
        </p>
      ))}

      {valid && violations.length === 0 ? (
        <p className="note note--ok" data-testid="tier-ok">
          阶梯合法
        </p>
      ) : null}

      <div className="row">
        <button type="button" className="btn btn--quiet" data-testid="tier-validate" onClick={onValidate}>
          校验
        </button>
        <button
          type="button"
          className="btn btn--primary"
          data-testid="tier-save"
          onClick={onSave}
          disabled={busy}
        >
          {saveLabel}
        </button>
      </div>
    </section>
  )
}
