"""06 追加 · 反馈收件箱（`/api/admin/feedback*`）——平台运营侧。

## 处理状态是**两态机**，不是布尔开关

`open` ⇄ `resolved`，两个方向各一个端点，各有各的 `409`：
`resolve` 已处理的 → `409`；`reopen` 未处理的 → `409`。
做成一个 `toggle` 的话，「谁把它标成已处理的」这条痕就没了方向，
而 `admin_action_log` 里记的正是方向。

## 并发靠行锁，不靠「先查再判」

`with_for_update()` 与 06 的内容异常处理同款：两个 admin 同时点「标记已处理」，
后到的那次会阻塞到前一次提交，读到的已经是 `resolved`，于是 `409`——
**审计也就只有一条**。不加锁时两次都能读到 `open`，日志会写两条。

## 行本身不可改、不可删

`content` / `role` / `user_id` 三列没有写入路径，表上也没有删除端点。
`FR-11` 扫源码盯着这条。
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import AuthContext, require_admin
from app.db import get_session
from app.models.admin import (
    FEEDBACK_CATEGORIES,
    FEEDBACK_ROLES,
    FEEDBACK_STATUSES,
    UserFeedback,
)
from app.models.user import User
from app.services import audit as audit_service

router = APIRouter(prefix="/api/admin/feedback", tags=["admin-feedback"])

MAX_PAGE_SIZE = 100


def _paged(items: list, total: int, page: int, size: int) -> dict:
    return {"items": items, "total": total, "page": page, "size": size}


def _item(row: UserFeedback, submitter: User | None) -> dict:
    """列表行。`account` / `nickname` 回表取——取不到（人已物理消失）就为 `null`。

    外键没有 CASCADE，所以注销**不会**带走行；这里刻意用左连接，
    让「提交人已注销」表现为两个 null 而不是整行消失。
    """
    return {
        "id": row.id,
        "user_id": row.user_id,
        "account": submitter.account if submitter else None,
        "nickname": submitter.nickname if submitter else None,
        "role": row.role,
        "category": row.category,
        "content": row.content,
        "contact": row.contact,
        "status": row.status,
        "resolved_at": row.resolved_at,
        "resolved_by": row.resolved_by,
        "created_at": row.created_at,
    }


@router.get("")
async def list_feedback(
    role: str | None = Query(default=None),
    category: str | None = Query(default=None),
    status: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=MAX_PAGE_SIZE),
    auth: AuthContext = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> dict:
    # 筛选项非法一律 422，**不退化成空列表**：空列表与「筛错了词」在
    # 界面上长得一样，运营会以为自己看的就是全部
    for value, allowed, name in (
        (role, FEEDBACK_ROLES, "role"),
        (category, FEEDBACK_CATEGORIES, "category"),
        (status, FEEDBACK_STATUSES, "status"),
    ):
        if value is not None and value not in allowed:
            raise HTTPException(422, f"{name} 只能是 {allowed}")

    filters = []
    if role is not None:
        filters.append(UserFeedback.role == role)
    if category is not None:
        filters.append(UserFeedback.category == category)
    if status is not None:
        filters.append(UserFeedback.status == status)

    total = await session.scalar(
        select(func.count()).select_from(UserFeedback).where(*filters)
    )
    rows = (
        await session.execute(
            select(UserFeedback, User)
            .outerjoin(User, UserFeedback.user_id == User.id)
            .where(*filters)
            .order_by(UserFeedback.created_at.desc(), UserFeedback.id.desc())
            .offset((page - 1) * size)
            .limit(size)
        )
    ).all()

    return _paged(
        [_item(row, submitter) for row, submitter in rows],
        int(total or 0),
        page,
        size,
    )


async def _lock(session: AsyncSession, feedback_id: int) -> UserFeedback:
    row = await session.scalar(
        select(UserFeedback).where(UserFeedback.id == feedback_id).with_for_update()
    )
    if row is None:
        raise HTTPException(404, "反馈不存在")
    return row


@router.post("/{feedback_id}/resolve")
async def resolve_feedback(
    feedback_id: int,
    auth: AuthContext = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> dict:
    row = await _lock(session, feedback_id)
    if row.status == "resolved":
        # 查无此人与重复处理是两件事：前者 404、后者 409。
        # 都并成 404 的话，运营会以为这条不见了而去别处找
        raise HTTPException(409, "该反馈已被处理")

    row.status = "resolved"
    row.resolved_at = datetime.now(timezone.utc)
    row.resolved_by = auth.user.id
    await audit_service.log_action(
        session,
        admin_id=auth.user.id,
        action="resolve_feedback",
        target_type="feedback",
        target_id=row.id,
        detail={"before": "open", "after": "resolved"},
    )
    await session.commit()
    return {
        "id": row.id,
        "status": row.status,
        "resolved_at": row.resolved_at,
        "resolved_by": row.resolved_by,
    }


@router.post("/{feedback_id}/reopen")
async def reopen_feedback(
    feedback_id: int,
    auth: AuthContext = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> dict:
    row = await _lock(session, feedback_id)
    if row.status != "resolved":
        raise HTTPException(409, "该反馈本就未处理")

    row.status = "open"
    # 两个字段一起清空。只改 `status` 会留下「未处理但记录了处理人和时间」
    # 的脏行，下次筛「已处理」筛不到它，翻审计却看见它被处理过
    row.resolved_at = None
    row.resolved_by = None
    await audit_service.log_action(
        session,
        admin_id=auth.user.id,
        action="reopen_feedback",
        target_type="feedback",
        target_id=row.id,
        detail={"before": "resolved", "after": "open"},
    )
    await session.commit()
    return {
        "id": row.id,
        "status": row.status,
        "resolved_at": row.resolved_at,
        "resolved_by": row.resolved_by,
    }
