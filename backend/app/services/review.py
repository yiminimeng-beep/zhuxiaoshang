"""04-tracking 的审核状态机：四个过审动作共用的**唯一**一条跃迁实现。

四个动作都只是「把 post 推到某个状态 + 写一条 `review_log` + 触发两笔钱」，
差别只在 `action` 与 `operator_id`：

| 动作 | action | operator_id | 允许的起始状态 |
|---|---|---|---|
| 单条通过 | `approve` | 商户 | `pending` |
| 批量通过里的每条 | `batch_approve` | 商户 | `pending` |
| 72h 超时 | `auto_approve` | `None`（系统） | `pending` |
| admin 裁决受理 | `appeal_accept` | admin | `appealed` |

把它们收在一处，是因为「审核通过要触发什么」这件事一旦分四份写，迟早在
某一份里漏掉报销或漏掉峰值口径——而那正是最容易出钱错的地方。

## 两笔钱互不牵连

过审同时触发 **05 的奖励结算** 与 **07 的报销**（spec「报销触发」）。两者
独立计算、独立封顶，**一个失败不影响另一个**。故各自包在
`begin_nested()` 里：SQLAlchemy 的 savepoint 让其中一个炸掉时只回滚它自己
写下的那几行，另一个与本次审核本身都不受影响。
（不这么做的话，PostgreSQL 里一条语句报错会让整个事务作废，「互不影响」
就成了空话。）

## 为什么跃迁不合法要抛异常而不是返回 False

调用方只有一种处理方式——回 409。返回布尔值会诱使调用方写成
`if not ok: pass`，那样一次不该发生的审核会静默消失。
"""

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.task import TaskClaim
from app.models.tracking import (
    REVIEW_WINDOW_HOURS,
    ReviewLog,
    SocialPost,
)
from app.services import quota as quota_service
from app.services import reward as reward_service

logger = logging.getLogger(__name__)


class ReviewConflict(Exception):
    """当前状态不允许这次跃迁。端点把它翻成 409。"""


def deadline_for(submitted_at: datetime) -> datetime:
    """`review_deadline = submitted_at + 72h`。

    写成一个函数，是因为这条规则**不因任何操作而延期**（spec 原话）。
    散在端点里各算一遍，就会有某一处「顺手」把审核时限往后挪一点。
    """
    return submitted_at + timedelta(hours=REVIEW_WINDOW_HOURS)


def ensure_reviewable(post: SocialPost, *, now: datetime | None = None) -> None:
    """窗口闸：已过 72h 的作品，**商户两侧都不许再动**。

    这道闸不能只靠定时任务。定时任务每 10 分钟才跑一次，两轮之间那段时间里
    库里还是 `pending`——只看状态的实现会在这里放行，商户就抢在调度器前面
    把一篇本该自动通过的作品驳回了。`TO-06` / `TO-06b` 盯的正是这个空档。

    边界与扫描器同一口径（`<=`）：恰好卡在 72 小时整点时，两边都判超时。
    用 `<` 的话，商户能在这个瞬间通过、而扫描器认为它已经超时——同一秒里
    两个不同的答案。
    """
    at = now or datetime.now(timezone.utc)
    if post.review_deadline <= at:
        raise ReviewConflict("审核窗口已过（72 小时），系统将自动通过")


def engagement_of(likes: int, collects: int, comments: int) -> int:
    """互动数 = 点赞 + 收藏 + 评论。

    **不含分享**：spec 明写三个加数。分享量在多数平台是「转发到自己主页」，
    与另外三个不是同一量级的用户动作，混进来会让奖励档位整体上浮一档。
    """
    return int(likes) + int(collects) + int(comments)


async def peak_engagement(
    session: AsyncSession, post_id: int, *, moment: datetime | None = None
) -> int:
    """取**峰值**：该 post 在 `moment` 之前所有快照里 `engagement` 的最大值。

    spec「结算取值规则」：`moment` 之后再产生的快照不参与——否则用户可以
    先报低位数据过审，过审后再慢慢涨，按涨完的量算钱。
    """
    at = moment or datetime.now(timezone.utc)
    peak = await session.scalar(
        text(
            "SELECT coalesce(max(engagement), 0) FROM metric_snapshot "
            "WHERE post_id = :pid AND captured_at <= :at"
        ),
        {"pid": post_id, "at": at},
    )
    return int(peak or 0)


async def latest_engagement(session: AsyncSession, post_id: int) -> int:
    """最新一条快照的 `engagement`。降级告警要比「新值 vs 旧值」时用。"""
    value = await session.scalar(
        text(
            "SELECT engagement FROM metric_snapshot WHERE post_id = :pid "
            "ORDER BY captured_at DESC, id DESC LIMIT 1"
        ),
        {"pid": post_id},
    )
    return int(value or 0)


def _log(
    session: AsyncSession,
    post: SocialPost,
    action: str,
    operator_id: int | None,
    reason: str | None = None,
) -> None:
    session.add(
        ReviewLog(
            post_id=post.id,
            action=action,
            operator_id=operator_id,
            reason=reason,
            created_at=datetime.now(timezone.utc),
        )
    )


async def approve(
    session: AsyncSession,
    post: SocialPost,
    *,
    operator_id: int | None,
    action: str,
    allowed_from: tuple[str, ...] = ("pending",),
) -> None:
    """过审：置 `approved` + 写日志 + 触发奖励与报销。

    自动通过（`action="auto_approve"`, `operator_id=None`）走的就是这条，
    **不得**另写一条更简的路径——spec 明写「72h 自动通过触发的报销必须与
    人工通过完全一致」。

    `allowed_from` 只有 admin 裁决受理那一处不是 `pending`（它从 `appealed`
    过来）。做成参数而不是第二个函数：钱的那一段只有一份实现。
    """
    if post.status not in allowed_from:
        raise ReviewConflict(f"当前状态 {post.status} 不可通过审核")

    now = datetime.now(timezone.utc)
    # 系统超时通过要留一个**可区分**的终态：事后追查「这篇是谁批的」时，
    # `approved` 与 `auto_approved` 一个字母之差，决定了要不要找商户复盘
    post.status = "auto_approved" if action == "auto_approve" else "approved"
    post.reviewed_at = now
    post.reviewer_id = operator_id
    _log(session, post, action, operator_id)
    await session.flush()

    await _run_hooks(session, post, at=now)


async def reject(
    session: AsyncSession,
    post: SocialPost,
    *,
    operator_id: int | None,
    reason: str | None,
    action: str,
    allowed_from: tuple[str, ...] = ("pending",),
) -> None:
    """驳回：置 `rejected` + 写日志 + **释放报销预占**。

    驳回意味着商户不欠这笔钱了，池子上被这个 job 认领的份额要还回去，
    否则一个被驳回的 job 会永久占住池子（spec「报销触发」末条）。
    奖励侧不欠不发，故不触发结算。

    `allowed_from` 同上：只有 admin 裁决驳回（`appeal_reject`）从 `appealed` 来。
    """
    if post.status not in allowed_from:
        raise ReviewConflict(f"当前状态 {post.status} 不可驳回")

    now = datetime.now(timezone.utc)
    post.status = "rejected"
    post.reviewed_at = now
    post.reviewer_id = operator_id
    post.reject_reason = reason
    _log(session, post, action, operator_id, reason)
    await session.flush()

    await quota_service.release_reimburse(session, job_id=post.job_id)


async def _run_hooks(
    session: AsyncSession, post: SocialPost, *, at: datetime
) -> None:
    """依次跑两笔钱，各自独立成败。"""
    peak = await peak_engagement(session, post.id, moment=at)

    async def _reward() -> None:
        await reward_service.settle(
            session,
            post_id=post.id,
            user_id=post.user_id,
            task_id=await _task_id_of(session, post),
            engagement=peak,
        )

    async def _reimburse() -> None:
        await quota_service.reimburse(session, post_id=post.id)

    for name, hook in (("reward", _reward), ("reimburse", _reimburse)):
        try:
            async with session.begin_nested():
                await hook()
        except Exception:
            # 吞掉但绝不静默：一笔钱没发出去必须留痕，否则对账时找不到
            logger.exception("post %s 过审后的 %s 结算失败", post.id, name)


async def _task_id_of(session: AsyncSession, post: SocialPost) -> int:
    claim = await session.get(TaskClaim, post.claim_id)
    return claim.task_id if claim is not None else 0


async def auto_approve_expired(
    session: AsyncSession, *, now: datetime | None = None
) -> list[int]:
    """扫出已过 72h 的 `pending`，逐条自动通过，返回被处理的 post_id。

    - **边界用 `<=`**：spec 明写「`review_deadline` 恰好等于 now → 判定为超时」。
      用 `<` 会让恰好卡点的那条多等一轮，而下一轮（10 分钟后）它已经算超时，
      结果只是晚 10 分钟，但用例会红、口径也说不清。
    - **`SKIP LOCKED`**：定时任务重复跑（或上一轮还没提交完，下一轮就起来了）
      时，两条扫描不会抢到同一个 post。不这么写，同一个 post 可能落下两条
      `auto_approve` 日志——spec 明写「重复跑不得产生 2 条」。
    - 状态本身也是幂等的一道闸：处理完即离开 `pending`，下一轮扫不到它。

    本函数**不 commit**，交给调用方（定时任务或测试）决定事务边界。
    """
    at = now or datetime.now(timezone.utc)
    posts = (
        (
            await session.execute(
                select(SocialPost)
                .where(
                    SocialPost.status == "pending",
                    SocialPost.review_deadline <= at,
                )
                .order_by(SocialPost.review_deadline)
                .with_for_update(skip_locked=True)
            )
        )
        .scalars()
        .all()
    )

    done: list[int] = []
    for post in posts:
        # 逐条包 savepoint：一条坏掉不该让整批积压停在原地
        try:
            async with session.begin_nested():
                await approve(
                    session, post, operator_id=None, action="auto_approve"
                )
        except Exception:
            logger.exception("post %s 自动通过失败", post.id)
            continue
        done.append(post.id)
    return done
