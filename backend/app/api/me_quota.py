"""用户侧额度：`/api/me/quota*`。

三条口径值得单独说：

1. **没有账户行不是错误**：全新用户 `GET /api/me/quota` 返回全 0 与
   `status=active`，不是 404。否则注册后的首屏就是一屏红字。
2. **冻结只拦写操作**：冻结的用户仍要能看见自己的额度与流水，
   否则他连「为什么不能用了」都查不到。
3. **`available` 由后端算**：前端做减法，算错了就会显示成「有钱」实则没有。
"""

from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import AuthContext, get_current_auth
from app.db import get_session
from app.models.quota import QuotaLedger, QuotaRecharge
from app.services.quota import account_public, load_account

router = APIRouter(prefix="/api/me", tags=["me-quota"])

MAX_PAGE_SIZE = 100


def ledger_public(row: QuotaLedger) -> dict:
    return {
        "id": row.id,
        "user_id": row.user_id,
        "change": row.change,
        "balance_after": row.balance_after,
        "source": row.source,
        "ref_type": row.ref_type,
        "ref_id": row.ref_id,
        "job_id": row.job_id,
        "task_id": row.task_id,
        "spender_id": row.spender_id,
        "counterparty_id": row.counterparty_id,
        "billing_source": row.billing_source,
        "provider": row.provider,
        "remark": row.remark,
        "created_at": row.created_at,
    }


def recharge_public(row: QuotaRecharge) -> dict:
    return {
        "id": row.id,
        "user_id": row.user_id,
        "amount_cents": row.amount_cents,
        "points": row.points,
        "channel": row.channel,
        "operator_id": row.operator_id,
        "remark": row.remark,
        "created_at": row.created_at,
    }


@router.get("/quota")
async def my_quota(
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> dict:
    account = await load_account(session, auth.user.id)
    return account_public(account, auth.user.id)


@router.get("/quota/ledger")
async def my_ledger(
    date_from: date | None = Query(default=None, alias="from"),
    date_to: date | None = Query(default=None, alias="to"),
    source: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=MAX_PAGE_SIZE),
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> dict:
    filters = [QuotaLedger.user_id == auth.user.id]
    if source is not None:
        filters.append(QuotaLedger.source == source)
    if date_from is not None:
        filters.append(
            QuotaLedger.created_at
            >= datetime.combine(date_from, datetime.min.time(), timezone.utc)
        )
    if date_to is not None:
        # 含当天：用次日零点做开区间上界，别用 <= 当天零点（那会把当天整天漏掉）
        filters.append(
            QuotaLedger.created_at
            < datetime.combine(date_to, datetime.min.time(), timezone.utc)
            + timedelta(days=1)
        )

    total = await session.scalar(
        select(func.count()).select_from(QuotaLedger).where(*filters)
    )
    rows = (
        (
            await session.execute(
                select(QuotaLedger)
                .where(*filters)
                .order_by(QuotaLedger.created_at.desc(), QuotaLedger.id.desc())
                .offset((page - 1) * size)
                .limit(size)
            )
        )
        .scalars()
        .all()
    )
    return {
        "items": [ledger_public(r) for r in rows],
        "total": int(total or 0),
        "page": page,
        "size": size,
    }


@router.get("/quota/recharges")
async def my_recharges(
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=MAX_PAGE_SIZE),
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> dict:
    filters = [QuotaRecharge.user_id == auth.user.id]
    total = await session.scalar(
        select(func.count()).select_from(QuotaRecharge).where(*filters)
    )
    rows = (
        (
            await session.execute(
                select(QuotaRecharge)
                .where(*filters)
                .order_by(QuotaRecharge.created_at.desc(), QuotaRecharge.id.desc())
                .offset((page - 1) * size)
                .limit(size)
            )
        )
        .scalars()
        .all()
    )
    return {
        "items": [recharge_public(r) for r in rows],
        "total": int(total or 0),
        "page": page,
        "size": size,
    }
