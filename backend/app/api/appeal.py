"""04-tracking · 申诉与复议（用户提起 → 平台裁决）。

## 为什么「仅一次」不能从 post 状态反推

最自然的写法是「post 处于 `rejected` 就可以申诉」。它错在 `AP-17`：
申诉被 admin 驳回后，post 会**回到 `rejected`**——那个状态天然满足「可以申诉」，
于是用户能无限次提。规则必须以 `appeal.post_id` 唯一为准，post 状态只是入口
合法性的一道前闸，不是次数的记账。

## 受理 = 过审

`admin accept` 走的就是 `services.review.approve`（`allowed_from=("appealed",)`）。
奖励与报销两笔钱由同一段 `_run_hooks` 发出去——另写一条「裁决专用」的结算路径，
迟早在其中一条上漏掉报销。
"""

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import AuthContext, get_current_auth
from app.db import get_session
from app.models.tracking import (
    APPEAL_STATUSES,
    MAX_APPEAL_REASON_CHARS,
    MIN_APPEAL_REASON_CHARS,
    Appeal,
    SocialPost,
)
from app.services import audit as audit_service
from app.services import review as review_service
from app.services.review import ReviewConflict

logger = logging.getLogger(__name__)

router = APIRouter(tags=["appeal"])

DECIDE_ACTIONS = ("accept", "reject")


class AppealIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str


class DecideIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: str
    admin_note: str | None = None


def _admin_auth(auth: AuthContext = Depends(get_current_auth)) -> AuthContext:
    if auth.user.role != "admin":
        raise HTTPException(403, "仅限管理员")
    return auth


def appeal_public(row: Appeal) -> dict:
    return {
        "id": row.id,
        "post_id": row.post_id,
        "user_id": row.user_id,
        "reason": row.reason,
        "status": row.status,
        "admin_id": row.admin_id,
        "admin_note": row.admin_note,
        "created_at": row.created_at,
        "decided_at": row.decided_at,
    }


# --------------------------------------------------------------------------- #
# 用户侧：提起申诉 / 我的申诉
# --------------------------------------------------------------------------- #
@router.post("/api/posts/{post_id}/appeal", status_code=201)
async def create_appeal(
    post_id: int,
    payload: AppealIn,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> dict:
    post = await session.get(SocialPost, post_id)
    if post is None:
        raise HTTPException(404, "作品不存在")
    if post.user_id != auth.user.id:
        raise HTTPException(403, "这不是你的作品")

    reason = payload.reason.strip()
    if len(reason) < MIN_APPEAL_REASON_CHARS:
        raise HTTPException(422, f"申诉理由不得少于 {MIN_APPEAL_REASON_CHARS} 字")
    if len(reason) > MAX_APPEAL_REASON_CHARS:
        raise HTTPException(422, f"申诉理由不得超过 {MAX_APPEAL_REASON_CHARS} 字")

    # 只有被驳回的才有申诉标的：pending 还没判、approved 无需申诉、
    # appealed 是同一次申诉进行中
    if post.status != "rejected":
        raise HTTPException(409, "只有被驳回的作品才能申诉")

    # 「仅一次」的真闸：以 `appeal.post_id` 唯一为准。被驳回的申诉会把 post
    # 放回 `rejected`，只看状态的实现会在这里放行第二次（AP-17）
    exists = await session.scalar(
        select(Appeal.id).where(Appeal.post_id == post.id).limit(1)
    )
    if exists is not None:
        raise HTTPException(409, "该作品已经申诉过，申诉机会仅有一次")

    now = datetime.now(timezone.utc)
    appeal = Appeal(
        post_id=post.id,
        user_id=auth.user.id,
        reason=reason,
        status="pending",
        created_at=now,
    )
    session.add(appeal)
    post.status = "appealed"
    await session.flush()
    await session.commit()

    return {"appeal": appeal_public(appeal)}


@router.get("/api/me/appeals")
async def my_appeals(
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> dict:
    rows = (
        (
            await session.execute(
                select(Appeal)
                .where(Appeal.user_id == auth.user.id)
                .order_by(Appeal.id.desc())
            )
        )
        .scalars()
        .all()
    )
    return {"items": [appeal_public(r) for r in rows]}


# --------------------------------------------------------------------------- #
# 平台侧：待裁决 / 裁决
# --------------------------------------------------------------------------- #
@router.get("/api/admin/appeals")
async def list_appeals(
    status: str = "pending",
    auth: AuthContext = Depends(_admin_auth),
    session: AsyncSession = Depends(get_session),
) -> dict:
    if status not in APPEAL_STATUSES:
        raise HTTPException(422, f"status 只能是 {APPEAL_STATUSES}")

    rows = (
        (
            await session.execute(
                select(Appeal).where(Appeal.status == status).order_by(Appeal.id)
            )
        )
        .scalars()
        .all()
    )
    return {"items": [appeal_public(r) for r in rows]}


@router.post("/api/admin/appeals/{appeal_id}/decide")
async def decide_appeal(
    appeal_id: int,
    payload: DecideIn,
    auth: AuthContext = Depends(_admin_auth),
    session: AsyncSession = Depends(get_session),
) -> dict:
    if payload.action not in DECIDE_ACTIONS:
        raise HTTPException(422, f"action 只能是 {DECIDE_ACTIONS}")

    # 锁住再判状态：两个 admin 同时裁决（`AP-05`）时，后到的那次会阻塞到
    # 前一次提交，读到的已不是 `pending`，于是 409。不加锁的话两次都读到
    # `pending`，会走两遍跃迁——`review_log` / `admin_action_log` 各多出一条
    appeal = await session.scalar(
        select(Appeal).where(Appeal.id == appeal_id).with_for_update()
    )
    if appeal is None:
        raise HTTPException(404, "申诉不存在")
    # 裁决即终局：第二次裁决不得翻掉第一次的结果
    if appeal.status != "pending":
        raise HTTPException(409, "该申诉已经裁决过了")

    post = await session.get(SocialPost, appeal.post_id)
    if post is None:
        raise HTTPException(404, "作品不存在")

    accept = payload.action == "accept"
    try:
        if accept:
            await review_service.approve(
                session,
                post,
                operator_id=auth.user.id,
                action="appeal_accept",
                allowed_from=("appealed",),
            )
        else:
            await review_service.reject(
                session,
                post,
                operator_id=auth.user.id,
                # 记 admin_note：裁决驳回时「为什么维持原判」是这条日志唯一
                # 能说清的东西，落成用户的原文会让审计以为那是平台的理由
                reason=payload.admin_note or appeal.reason,
                action="appeal_reject",
                allowed_from=("appealed",),
            )
    except ReviewConflict as exc:
        raise HTTPException(409, str(exc)) from exc

    appeal.status = "accepted" if accept else "rejected"
    appeal.admin_id = auth.user.id
    appeal.admin_note = payload.admin_note
    appeal.decided_at = datetime.now(timezone.utc)

    # 两张表各记一笔，不是重复：`review_log` 记的是**作品的状态跃迁**，
    # `admin_action_log` 记的是**管理员的动作**。「这条作品怎么了」与
    # 「这个管理员干了什么」是两个问题，一张表回答不了两个。
    await audit_service.log_action(
        session,
        admin_id=auth.user.id,
        action="appeal_accept" if accept else "appeal_reject",
        target_type="appeal",
        target_id=appeal.id,
        detail={
            "post_id": appeal.post_id,
            "user_id": appeal.user_id,
            "note": payload.admin_note,
        },
    )

    await session.commit()

    return {"appeal": appeal_public(appeal)}
