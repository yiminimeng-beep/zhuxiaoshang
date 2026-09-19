"""04-tracking · 商户审核（待审列表 / 单条通过 / 单条驳回 / 批量通过）。

## 跃迁只有一份实现

`approve` / `reject` 端点自己不做状态机，全部交给 `services.review`。端点这里
只负责**三件周边的事**：认人（是不是这个商户）、窗口（72h 过没过）、把
`ReviewConflict` 翻成 `409`。把状态机抄一份到端点里，四个过审动作（单条 /
批量 / 超时 / 裁决受理）就会各自演化。

## 别人的作品回 `403` 而不是 `404`

`OW-03` 明确要求这一点。回 404 会把「这条不归你管」和「这条不存在」压成同一个
答案，而两者的排查方向完全相反——前者要找归属，后者要找数据。

## 批量通过为什么不是「一个事务包住循环」

spec 明写「不做全事务回滚」。最顺手的写法恰恰是一个事务包住整个循环，那样
末尾一条炸掉会把前面已成功的三条一起回滚——状态码仍是 `200`，只有查库才
看得见。故逐条独立提交，成功与失败分列。

## 作品归谁：`claim → task → merchant`

`social_post` 上没有 `task_id`（它挂的是 `claim_id`），所以「这条归哪个商户」
要跨两级。写成一处 `_merchant_of(...)` 而不是在每个端点里各 join 一次：
归属判错的话错的是**权限**，不是显示。
"""

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.post_serializers import post_detail, utcnow
from app.core.deps import AuthContext, get_current_auth
from app.db import get_session
from app.models.task import Task, TaskClaim
from app.models.tracking import (
    APPROVED_STATUSES,
    MAX_BATCH_APPROVE,
    MIN_REJECT_REASON_CHARS,
    POST_STATUSES,
    SocialPost,
)
from app.services import review as review_service
from app.services.review import ReviewConflict

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/merchant/reviews", tags=["review"])

# 批量通过的失败原因。摊给调用方看，前端据此决定该重试还是该提示
FAIL_FORBIDDEN = "forbidden"
FAIL_NOT_FOUND = "not_found"
FAIL_ALREADY_REVIEWED = "already_reviewed"
FAIL_INVALID_STATUS = "invalid_status"


class RejectIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str


class BatchApproveIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    post_ids: list[int]
    # 二次确认。**必填**而不是默认 true：默认 true 等于这道闸不存在
    confirm: bool


def _merchant_auth(
    auth: AuthContext = Depends(get_current_auth),
) -> AuthContext:
    """商户专属：客户来访一律 `403`。

    与 `merchant_quota` 同款，刻意不用 `core.deps.require_merchant`——它只回
    `User`，而这里处处要 `auth.user.id`。
    """
    if auth.user.role != "merchant":
        raise HTTPException(403, "仅限商户")
    return auth


async def _merchant_of(session: AsyncSession, post: SocialPost) -> int | None:
    """这条作品挂在哪个商户的任务下（`claim → task → merchant` 两级）。"""
    return await session.scalar(
        select(Task.merchant_id)
        .join(TaskClaim, TaskClaim.task_id == Task.id)
        .where(TaskClaim.id == post.claim_id)
    )


async def _own_post(
    session: AsyncSession, merchant_id: int, post_id: int
) -> SocialPost:
    """取一条**归本商户**的作品，否则 `403`（不是 `404`）。"""
    post = await session.get(SocialPost, post_id)
    if post is None:
        raise HTTPException(404, "作品不存在")
    if await _merchant_of(session, post) != merchant_id:
        raise HTTPException(403, "这不是你名下任务的作品")
    return post


async def _review_it(
    session: AsyncSession,
    post: SocialPost,
    *,
    operator_id: int,
    action: str,
    reason: str | None = None,
) -> None:
    """走一次审核跃迁，把 `ReviewConflict` 翻成 `409`。

    窗口闸（`ensure_reviewable`）排在跃迁之前：定时任务每 10 分钟才跑一次，
    两轮之间库里还是 `pending`，只看状态的实现会让商户抢在调度器前面通过
    一篇本该自动通过的作品（`TO-06` / `TO-06b`）。
    """
    try:
        review_service.ensure_reviewable(post)
        if reason is None:
            await review_service.approve(
                session, post, operator_id=operator_id, action=action
            )
        else:
            await review_service.reject(
                session,
                post,
                operator_id=operator_id,
                reason=reason,
                action=action,
            )
    except ReviewConflict as exc:
        raise HTTPException(409, str(exc)) from exc


# --------------------------------------------------------------------------- #
# 待审列表
# --------------------------------------------------------------------------- #
@router.get("")
async def list_reviews(
    status: str = "pending",
    auth: AuthContext = Depends(_merchant_auth),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """待审列表：**只含本商户任务的作品**，按 `review_deadline` 升序。

    「最急的排最前」不是排序偏好而是功能需求：72h 一过就自动通过，商户得先
    处理快到期的那批。`appealed` 的作品天然不在 `pending` 里——已升级平台，
    不再压商户。
    """
    if status not in POST_STATUSES:
        raise HTTPException(422, f"status 只能是 {POST_STATUSES}")

    posts = (
        (
            await session.execute(
                select(SocialPost)
                .join(TaskClaim, TaskClaim.id == SocialPost.claim_id)
                .join(Task, Task.id == TaskClaim.task_id)
                .where(Task.merchant_id == auth.user.id, SocialPost.status == status)
                .order_by(SocialPost.review_deadline, SocialPost.id)
            )
        )
        .scalars()
        .all()
    )

    now = utcnow()
    return {"items": [await post_detail(session, p, now=now) for p in posts]}


# --------------------------------------------------------------------------- #
# 单条通过 / 驳回
# --------------------------------------------------------------------------- #
@router.post("/{post_id}/approve")
async def approve_post(
    post_id: int,
    auth: AuthContext = Depends(_merchant_auth),
    session: AsyncSession = Depends(get_session),
) -> dict:
    post = await _own_post(session, auth.user.id, post_id)
    await _review_it(
        session, post, operator_id=auth.user.id, action="approve"
    )
    await session.commit()
    return {"post": await post_detail(session, post, now=utcnow())}


@router.post("/{post_id}/reject")
async def reject_post(
    post_id: int,
    payload: RejectIn,
    auth: AuthContext = Depends(_merchant_auth),
    session: AsyncSession = Depends(get_session),
) -> dict:
    reason = payload.reason.strip()
    if len(reason) < MIN_REJECT_REASON_CHARS:
        # 理由要落进审计。空理由的驳回，事后谁都说不清为什么
        raise HTTPException(422, f"驳回理由不得少于 {MIN_REJECT_REASON_CHARS} 字")

    post = await _own_post(session, auth.user.id, post_id)
    await _review_it(
        session, post, operator_id=auth.user.id, action="reject", reason=reason
    )
    await session.commit()
    return {"post": await post_detail(session, post, now=utcnow())}


# --------------------------------------------------------------------------- #
# 批量通过
# --------------------------------------------------------------------------- #
@router.post("/batch-approve")
async def batch_approve(
    payload: BatchApproveIn,
    auth: AuthContext = Depends(_merchant_auth),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """一键全通过：逐条独立，部分失败照样 `200`。

    三条校验都在**动手之前**：空批不是「无事发生」，是调用方写错了；缺
    `confirm` 更不能先改库再校验——那样状态码虽仍是 `422`，作品却已经被批了。
    """
    if not payload.post_ids:
        raise HTTPException(422, "post_ids 不能为空")
    if len(payload.post_ids) > MAX_BATCH_APPROVE:
        raise HTTPException(422, f"单次最多 {MAX_BATCH_APPROVE} 条")
    if payload.confirm is not True:
        raise HTTPException(422, "批量通过需要二次确认")

    # 去重必须发生在处理之前：否则同一个 id 会落两条日志、结算两次
    unique_ids = list(dict.fromkeys(payload.post_ids))

    succeeded: list[int] = []
    failed: list[dict] = []

    for post_id in unique_ids:
        try:
            post = await session.get(SocialPost, post_id)
            if post is None:
                failed.append({"post_id": post_id, "reason": FAIL_NOT_FOUND})
                continue
            if await _merchant_of(session, post) != auth.user.id:
                failed.append({"post_id": post_id, "reason": FAIL_FORBIDDEN})
                continue
            if post.status in APPROVED_STATUSES:
                failed.append({"post_id": post_id, "reason": FAIL_ALREADY_REVIEWED})
                continue
            if post.status != "pending":
                failed.append({"post_id": post_id, "reason": FAIL_INVALID_STATUS})
                continue

            await review_service.approve(
                session, post, operator_id=auth.user.id, action="batch_approve"
            )
        except ReviewConflict:
            failed.append({"post_id": post_id, "reason": FAIL_INVALID_STATUS})
            continue
        except Exception:
            # 一条坏掉不该让整批停在原地：先把自己写下的几行回滚掉，再记一条失败。
            # 这里之所以**必须**逐条提交，就是为了让这次 rollback 只丢掉当前这条，
            # 而不是把前面已成功的几条一起抹掉。
            logger.exception("批量通过 post %s 失败", post_id)
            await session.rollback()
            failed.append({"post_id": post_id, "reason": FAIL_INVALID_STATUS})
            continue

        # **每条各自提交**：末尾一条失败不得把前面已成功的回滚掉
        await session.commit()
        succeeded.append(post_id)

    return {"succeeded": succeeded, "failed": failed}
