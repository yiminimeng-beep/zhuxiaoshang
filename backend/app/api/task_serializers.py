"""02-task 的对外形状与阶梯校验。

阶梯校验（`validate_tiers`）是这一块最容易写错的地方：spec 要求阶梯
**有序、连续、不重叠、首档从 0 起、末档无上限**，且每条违规都要能单独报出来，
好让前端在配置页上高亮到具体哪一档。
"""

from datetime import datetime, timezone

from app.models.task import RewardRule, Task

# reward 里允许出现的键。至少要有其中一个（spec：`reward` 为空对象 `{}` → 422）
REWARD_KEYS = ("cash", "coupon_id", "points", "benefit")
_INT_KEYS = ("cash", "coupon_id", "points")


def validate_tiers(tiers) -> list[str]:
    """返回违规说明列表；空列表 = 合法。

    不做 raise：`validate` 端点要把 violations 原样回给前端，
    `PUT /reward-rules` 则把它变成 422 的 detail。
    """
    violations: list[str] = []

    if not isinstance(tiers, list) or not tiers:
        return ["tiers 必须是非空数组"]

    parsed: list[tuple[int, int | None]] = []

    for i, tier in enumerate(tiers):
        where = f"第 {i + 1} 档"

        if not isinstance(tier, dict):
            violations.append(f"{where} 不是对象")
            continue

        lo, hi = tier.get("min"), tier.get("max")

        if not isinstance(lo, int) or isinstance(lo, bool):
            violations.append(f"{where} 的 min 必须是整数")
            continue
        if hi is not None and (not isinstance(hi, int) or isinstance(hi, bool)):
            violations.append(f"{where} 的 max 必须是整数或 null")
            continue

        if lo < 0:
            violations.append(f"{where} 的 min 不得为负（{lo}）")
        if hi is not None and lo > hi:
            violations.append(f"{where} 的 min({lo}) 大于 max({hi})")

        reward = tier.get("reward")
        if not isinstance(reward, dict) or not reward:
            violations.append(f"{where} 的 reward 不能为空")
        else:
            if not any(k in reward for k in REWARD_KEYS):
                violations.append(f"{where} 的 reward 至少要含 {REWARD_KEYS} 之一")
            for key in _INT_KEYS:
                if key in reward:
                    val = reward[key]
                    # bool 是 int 的子类，得单独挡掉
                    if not isinstance(val, int) or isinstance(val, bool):
                        violations.append(f"{where} 的 reward.{key} 必须是整数（不接受小数）")
                    elif val < 0:
                        violations.append(f"{where} 的 reward.{key} 不得为负（{val}）")

        parsed.append((lo, hi))

    if violations:
        return violations

    # 顺序与连续性：首档必须从 0 起，其余每档必须紧接上一档，末档必须开放上限
    first_lo, _ = parsed[0]
    if first_lo != 0:
        violations.append(f"第一档的 min 必须是 0，实际 {first_lo}")

    for i in range(len(parsed) - 1):
        _, cur_hi = parsed[i]
        next_lo, _ = parsed[i + 1]

        if cur_hi is None:
            violations.append(f"第 {i + 1} 档的 max 为 null（无上限），后面不应再有档位")
            break
        if next_lo > cur_hi + 1:
            violations.append(
                f"第 {i + 1} 档与第 {i + 2} 档之间有缺口："
                f"{cur_hi + 1}~{next_lo - 1} 无人覆盖"
            )
        elif next_lo <= cur_hi:
            violations.append(
                f"第 {i + 1} 档与第 {i + 2} 档重叠："
                f"第 {i + 1} 档到 {cur_hi}，第 {i + 2} 档却从 {next_lo} 起"
            )

    _, last_hi = parsed[-1]
    if last_hi is not None:
        violations.append(
            f"最后一档的 max 必须是 null（否则互动量超过 {last_hi} 的用户拿不到奖励）"
        )

    return violations


def task_public(task: Task) -> dict:
    remaining = None
    if task.pay_mode == "user_pay_reimburse":
        pool = task.reimburse_pool or 0
        remaining = max(pool - task.reimburse_pool_used - task.reimburse_pool_reserved, 0)

    return {
        "id": task.id,
        "merchant_id": task.merchant_id,
        "title": task.title,
        "description": task.description,
        "cover_url": task.cover_url,
        "category": task.category,
        "requirement": task.requirement,
        "tags": task.tags,
        "start_at": task.start_at.isoformat() if task.start_at else None,
        "end_at": task.end_at.isoformat() if task.end_at else None,
        "quota": task.quota,
        "claimed_count": task.claimed_count,
        "pay_mode": task.pay_mode,
        "reimburse_pool": task.reimburse_pool,
        "reimburse_pool_used": task.reimburse_pool_used,
        "reimburse_pool_reserved": task.reimburse_pool_reserved,
        # 客户可见：不把剩余可报销额摆出来，就是在让他盲赌垫付
        "reimburse_pool_remaining": remaining,
        "reimburse_per_user_limit": task.reimburse_per_user_limit,
        "status": task.status,
        "created_at": task.created_at.isoformat() if task.created_at else None,
        "updated_at": task.updated_at.isoformat() if task.updated_at else None,
        "deleted_at": task.deleted_at.isoformat() if task.deleted_at else None,
    }


def reward_rule_public(rule: RewardRule) -> dict:
    return {
        "id": rule.id,
        "task_id": rule.task_id,
        "metric": rule.metric,
        "tiers": rule.tiers,
        "max_reward_per_user": rule.max_reward_per_user,
    }


def claim_public(claim) -> dict:
    return {
        "id": claim.id,
        "task_id": claim.task_id,
        "user_id": claim.user_id,
        "claimed_at": claim.claimed_at.isoformat() if claim.claimed_at else None,
        "status": claim.status,
    }


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
