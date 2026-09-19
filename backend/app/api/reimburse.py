"""07-token · 报销的预览与台账（纯读）。

报销链路本身（`reimburse(post_id)`）要等 04 的 `social_post` 落地才跑得起来，
但**读端点不必等**：它们只依赖已建好的 `reimburse_claim` / `quota_ledger`。

三处口径：

1. **`est_points` 不含单次调用价**。预览发生在用户选模型之前，此刻还不知道
   这次要用哪个 op，硬塞一个「预计花费」等于用我猜的数冒充事实。故它 =
   `min(单用户可报剩余, 池子可用额)`——**上限**，不是预言。用户真正要知道的
   就是「最多能报回多少」，这也正是 spec 说的「不给他看就是让他盲赌垫付」。
2. **`my_consumed_points` 只算 `source='consume'`**。垫付的 debit 在结算时才落
   流水，其他来源（充值、报销入账）都不是他自己花掉的钱。
3. **`released` 的报销单不计入 `my_reimbursed_points`**：被驳回等于没报过，
   占住单用户额度会让他白白少报。
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.merchant_task import require_merchant_role
from app.core.deps import AuthContext, get_current_auth
from app.db import get_session
from app.models.quota import REIMBURSE_STATUSES, QuotaLedger, ReimburseClaim
from app.models.task import Task, TaskClaim

router = APIRouter(tags=["reimburse"])

MAX_PAGE_SIZE = 100


def claim_public(row: ReimburseClaim) -> dict:
    return {
        "id": row.id,
        "post_id": row.post_id,
        "task_id": row.task_id,
        "merchant_id": row.merchant_id,
        "user_id": row.user_id,
        "base_points": row.base_points,
        "covered_points": row.covered_points,
        "status": row.status,
        "reason": row.reason,
        "created_at": row.created_at,
        "settled_at": row.settled_at,
    }


def _validate_status(value: str | None) -> None:
    if value is not None and value not in REIMBURSE_STATUSES:
        raise HTTPException(422, "status 非法")


async def _paged(session: AsyncSession, filters: list, page: int, size: int) -> dict:
    total = await session.scalar(
        select(func.count()).select_from(ReimburseClaim).where(*filters)
    )
    rows = (
        (
            await session.execute(
                select(ReimburseClaim)
                .where(*filters)
                .order_by(ReimburseClaim.id.desc())
                .offset((page - 1) * size)
                .limit(size)
            )
        )
        .scalars()
        .all()
    )
    return {
        "items": [claim_public(r) for r in rows],
        "total": int(total or 0),
        "page": page,
        "size": size,
    }


# --------------------------------------------------------------------------- #
# 用户侧
# --------------------------------------------------------------------------- #
@router.get("/api/me/reimburse-preview")
async def reimburse_preview(
    task_id: int = Query(...),
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> dict:
    task = await session.get(Task, task_id)
    if task is None or task.deleted_at is not None:
        raise HTTPException(404, "任务不存在")

    claimed = await session.scalar(
        select(TaskClaim).where(
            TaskClaim.task_id == task_id,
            TaskClaim.user_id == auth.user.id,
            TaskClaim.status != "closed",
        )
    )
    if claimed is None:
        raise HTTPException(403, "请先领取该任务")

    if task.pay_mode != "user_pay_reimburse":
        # 商户直付时用户没有垫付也没有可报额度，全 0 是**准确**的答案
        return {
            "pay_mode": task.pay_mode,
            "per_user_limit": 0,
            "my_consumed_points": 0,
            "my_reimbursed_points": 0,
            "my_remaining": 0,
            "pool_remaining": 0,
            "est_points": 0,
        }

    consumed = await session.scalar(
        select(func.coalesce(func.sum(-QuotaLedger.change), 0)).where(
            QuotaLedger.user_id == auth.user.id,
            QuotaLedger.task_id == task_id,
            QuotaLedger.source == "consume",
        )
    )
    reimbursed = await session.scalar(
        select(func.coalesce(func.sum(ReimburseClaim.covered_points), 0)).where(
            ReimburseClaim.user_id == auth.user.id,
            ReimburseClaim.task_id == task_id,
            ReimburseClaim.status != "released",
        )
    )

    per_user = task.reimburse_per_user_limit or 0
    my_consumed = int(consumed or 0)
    my_reimbursed = int(reimbursed or 0)
    my_remaining = max(per_user - my_reimbursed, 0)
    pool_remaining = max(
        (task.reimburse_pool or 0)
        - (task.reimburse_pool_used or 0)
        - (task.reimburse_pool_reserved or 0),
        0,
    )

    return {
        "pay_mode": task.pay_mode,
        "per_user_limit": per_user,
        "my_consumed_points": my_consumed,
        "my_reimbursed_points": my_reimbursed,
        "my_remaining": my_remaining,
        "pool_remaining": pool_remaining,
        # 上限而非预言，见模块 docstring ①
        "est_points": min(my_remaining, pool_remaining),
    }


@router.get("/api/me/reimbursements")
async def my_reimbursements(
    task_id: int | None = Query(default=None),
    status_filter: str | None = Query(default=None, alias="status"),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=MAX_PAGE_SIZE),
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> dict:
    _validate_status(status_filter)
    filters = [ReimburseClaim.user_id == auth.user.id]
    if task_id is not None:
        filters.append(ReimburseClaim.task_id == task_id)
    if status_filter is not None:
        filters.append(ReimburseClaim.status == status_filter)
    return await _paged(session, filters, page, size)


# --------------------------------------------------------------------------- #
# 商户侧
# --------------------------------------------------------------------------- #
@router.get("/api/merchant/reimbursements")
async def merchant_reimbursements(
    task_id: int | None = Query(default=None),
    status_filter: str | None = Query(default=None, alias="status"),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=MAX_PAGE_SIZE),
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> dict:
    merchant = require_merchant_role(auth)
    _validate_status(status_filter)
    filters = [ReimburseClaim.merchant_id == merchant.id]
    if task_id is not None:
        filters.append(ReimburseClaim.task_id == task_id)
    if status_filter is not None:
        filters.append(ReimburseClaim.status == status_filter)
    return await _paged(session, filters, page, size)
