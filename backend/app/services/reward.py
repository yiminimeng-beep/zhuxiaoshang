"""05-reward 的结算与发放（**04 调用，05 实现**）。

04 的四个过审动作（单条 `approve` / `batch-approve` 的每条 / 72h `auto_approve` /
admin `appeal_accept`）都要触发结算，故这里只留**入口与守卫**——
`reward_grant` 表不在就返回 `None`，让人一眼看出「05 还没落地」。

## 返回值

`None` = 「这次没有结算发生」（表不在，或没配规则，或 post 不在）。
04 **不区分**这几种情况：它只负责把「这个 post 过审了、峰值是多少」交出去。

## 三条不可动摇的规则

1. **幂等靠 `post_id` 唯一约束**。重复投递时先查后返：应用层的 `if exists`
   是快路径，但真正兜底的是数据库——并发下两个请求会同时查到「不存在」。
2. **取峰值是调用方的事**。`engagement` 由 04 算好传进来；05 不查
   `metric_snapshot`，否则「取峰值」就有了两个实现。
3. **一笔里的一部分失败不拖垮其余**（spec「不得整体回滚」）。券发完了照样
   发积分、发现金——所以券的发放**只返回 None**，不抛异常。
"""

import secrets
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.reward import (
    CashPayout,
    Coupon,
    PointLedger,
    RewardGrant,
    UserCoupon,
)
from app.models.task import RewardRule
from app.models.tracking import SocialPost


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_code(prefix: str) -> str:
    """券号 / 兑换码。`varchar(32)` 装得下，碰撞概率忽略不计。"""
    return f"{prefix}{secrets.token_hex(6).upper()}"


def match_tier(tiers: list, engagement: int) -> tuple[int | None, dict]:
    """命中档位。区间**两端都含**（`min <= e <= max`），`max=None` 表示无上限。

    写成半开区间的话，商户按「100~199 发 50 分」配的规则会把 199 分的人
    放进 200 以上的档——多出来的钱没人对得上账。

    历史脏数据可能出现**缺口**（02 的 `validate_tiers` 不让建，但改规则能造出来），
    此时返回 `(None, {})`，由调用方落一条 `below_threshold`。
    """
    for index, tier in enumerate(tiers or []):
        low = tier.get("min", 0)
        high = tier.get("max")
        if engagement >= low and (high is None or engagement <= high):
            return index, dict(tier.get("reward") or {})
    return None, {}


async def grant_points(
    session: AsyncSession,
    *,
    user_id: int,
    change: int,
    source: str,
    ref_type: str,
    ref_id: int,
    remark: str | None = None,
) -> int:
    """写一条积分流水并返回**入账后**余额。

    `balance_after` 不是「查一下当前余额再加」——那在并发下会写出两个一模一样的
    值（两行单看都「对」，合起来少了一笔）。这里先 `FOR UPDATE` 锁住**用户行**
    再算，两笔并发入账被串行化。

    锁用户行而不是锁账本：账本可能一行都没有（新用户首笔），没得锁。
    """
    locked = await session.scalar(
        text('SELECT id FROM "user" WHERE id = :uid FOR UPDATE'), {"uid": user_id}
    )
    if locked is None:
        raise HTTPException(status_code=404, detail="用户不存在")

    current = await session.scalar(
        text("SELECT coalesce(sum(change), 0) FROM point_ledger WHERE user_id = :uid"),
        {"uid": user_id},
    )
    balance = int(current or 0) + int(change)
    if balance < 0:
        # 积分是「已发出去的债」，负余额等于平台倒欠用户。
        # `ck_point_ledger_balance_after` 是最后一道闸，但那时已经晚了一步：
        # 调用方拿到的是 500，不是「余额不够」。
        raise HTTPException(status_code=422, detail="积分不足")

    row = PointLedger(
        user_id=user_id,
        change=change,
        balance_after=balance,
        source=source,
        ref_type=ref_type,
        ref_id=ref_id,
        remark=remark,
    )
    session.add(row)
    await session.flush()
    return balance


async def issue_coupon(
    session: AsyncSession, *, user_id: int, coupon_id: int, now: datetime
) -> UserCoupon | None:
    """发一张券；发不出去（停用 / 发完）返回 `None`，**不抛异常**。

    `issued + 1` 与 `user_coupon` 的插入必须同事务：一个成功一个失败，
    对账时「还剩几张」就永远说不清了。占位用条件 UPDATE，
    并发下 `issued` 不会超过 `total`。
    """
    coupon = await session.get(Coupon, coupon_id)
    if coupon is None or coupon.status != "active":
        return None

    claimed = await session.scalar(
        text(
            """
            UPDATE coupon SET issued = issued + 1
            WHERE id = :cid AND status = 'active' AND issued < total
            RETURNING id
            """
        ),
        {"cid": coupon_id},
    )
    if claimed is None:
        return None

    row = UserCoupon(
        user_id=user_id,
        coupon_id=coupon_id,
        code=new_code("UC"),
        status="unused",
        obtained_at=now,
        # 券的有效期由模板定，发出去那一刻就冻结
        expire_at=coupon.valid_to,
    )
    session.add(row)
    await session.flush()
    return row


async def expire_coupons(session: AsyncSession, *, now: datetime | None = None) -> int:
    """把已到期的 `unused` 券置为 `expired`，返回改动行数。

    只动 `unused`——已经用掉的券不该被改成「过期」，那会抹掉「他用过」这件事。
    也**不碰积分**：spec 明写「不影响已发积分」。
    """
    moment = now or utcnow()
    result = await session.execute(
        text(
            """
            UPDATE user_coupon SET status = 'expired'
            WHERE status = 'unused' AND expire_at <= :now
            """
        ),
        {"now": moment},
    )
    return int(result.rowcount or 0)


async def _cash_already_granted(session: AsyncSession, user_id: int, task_id: int) -> int:
    """该用户在该任务**累计已发**的现金。

    口径是 `(user, task)` 这个二元组：只测一条的话，写成「按 post 累计」
    （等于没截断）或「跨任务累计」（被别家的账牵连）都会全绿。
    """
    total = await session.scalar(
        text(
            """
            SELECT coalesce(sum(cp.amount), 0)
            FROM cash_payout cp
            JOIN reward_grant rg ON rg.id = cp.reward_grant_id
            WHERE cp.user_id = :uid AND rg.task_id = :tid
            """
        ),
        {"uid": user_id, "tid": task_id},
    )
    return int(total or 0)


async def settle(
    session: AsyncSession,
    *,
    post_id: int,
    user_id: int,
    task_id: int,
    engagement: int,
    rule_id: int | None = None,
) -> dict | None:
    """按 `reward_rule` 把 `engagement` 换算成奖励并落 `reward_grant`。

    `engagement` 是**峰值**（见 04 spec「结算取值规则」），由调用方算好传进来。

    返回落库那一行的 dict；无事发生（表不在 / 没配规则 / post 不存在）返回 `None`。
    """
    present = await session.scalar(
        text("SELECT to_regclass('public.reward_grant') IS NOT NULL")
    )
    if not present:
        return None

    # 幂等的第一道闸。第二道是 `uq_reward_grant_post`——并发下两个请求会
    # 同时走到这里，都查到「不存在」。
    existing = await session.scalar(
        select(RewardGrant).where(RewardGrant.post_id == post_id)
    )
    if existing is not None:
        return grant_dict(existing)

    post = await session.get(SocialPost, post_id)
    if post is None:
        return None

    rule = None
    if rule_id is not None:
        rule = await session.get(RewardRule, rule_id)
    if rule is None:
        rule = await session.scalar(
            select(RewardRule).where(RewardRule.task_id == task_id)
        )
    if rule is None:
        # 任务可以不配规则就发布，客户照样交作品、商户照样能过审。
        # 此时「没有奖励」是正确结果，不是错误。
        return None

    now = utcnow()
    tier_index, reward = match_tier(rule.tiers or [], engagement)

    if tier_index is None:
        row = RewardGrant(
            post_id=post_id,
            claim_id=post.claim_id,
            user_id=user_id,
            task_id=task_id,
            reward_rule_id=rule.id,
            engagement=engagement,
            tier_index=None,
            reward_detail={},
            status="below_threshold",
            granted_at=None,
        )
        session.add(row)
        await session.flush()
        return grant_dict(row)

    detail: dict = {}
    status = "granted"

    cash = int(reward.get("cash") or 0)
    if cash > 0:
        cap = rule.max_reward_per_user
        if cap is not None:
            remaining = max(int(cap) - await _cash_already_granted(session, user_id, task_id), 0)
            if cash > remaining:
                cash = remaining
                status = "capped"
        detail["cash"] = cash

    row = RewardGrant(
        post_id=post_id,
        claim_id=post.claim_id,
        user_id=user_id,
        task_id=task_id,
        reward_rule_id=rule.id,
        engagement=engagement,
        tier_index=tier_index,
        reward_detail={},
        status=status,
        granted_at=now,
    )
    session.add(row)
    # 先落行拿 id：积分与券的幂等键都要引用它
    await session.flush()

    points = int(reward.get("points") or 0)
    if points > 0:
        await grant_points(
            session,
            user_id=user_id,
            change=points,
            source="task_reward",
            ref_type="reward_grant",
            ref_id=row.id,
        )
        detail["points"] = points

    coupon_id = reward.get("coupon_id")
    if coupon_id:
        issued = await issue_coupon(
            session, user_id=user_id, coupon_id=int(coupon_id), now=now
        )
        if issued is not None:
            detail["coupon_ids"] = [issued.id]

    benefit = reward.get("benefit")
    if benefit:
        # spec「明确不做 benefit 核销」：只记文本，不产生任何一本账
        detail["benefit"] = benefit

    if cash > 0:
        session.add(
            CashPayout(
                user_id=user_id,
                reward_grant_id=row.id,
                # 台账写实发额而不是应发额：差的那 1000 分永远打不出去，
                # 对账时差额从哪来没人说得清。
                amount=cash,
                status="pending",
            )
        )

    row.reward_detail = dict(detail)
    row.status = status
    await session.flush()
    return grant_dict(row)


def grant_dict(row: RewardGrant) -> dict:
    return {
        "id": row.id,
        "post_id": row.post_id,
        "claim_id": row.claim_id,
        "user_id": row.user_id,
        "task_id": row.task_id,
        "reward_rule_id": row.reward_rule_id,
        "engagement": row.engagement,
        "tier_index": row.tier_index,
        "reward_detail": row.reward_detail,
        "status": row.status,
        "fail_reason": row.fail_reason,
        "granted_at": row.granted_at,
    }
