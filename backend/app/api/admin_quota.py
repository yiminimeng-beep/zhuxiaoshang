"""管理员侧额度：`/api/admin/quota*`。

三条贯穿全文件的规矩：

- **非 admin 一律 403，绝不用 404。** 404 会把「这个 id 存不存在」透出去，
  等于送一个 id 枚举接口（`AD-14`）。
- **人工调账必须留痕。** 充值 / 调整 / 冻结各自要写一条 `admin_action_log`
  （表在 06；未落地时优雅跳过，不假装写过）。
- **调账不得把余额调成负数。** 先校验后写入，被拒时库里必须一个字节都没动。
"""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.reimburse import claim_public
from app.core.deps import AuthContext, get_current_auth
from app.db import get_session
from app.models.quota import (
    ACCOUNT_STATUSES,
    REIMBURSE_STATUSES,
    ModelPrice,
    QuotaAccount,
    QuotaLedger,
    QuotaRecharge,
    ReimburseClaim,
    UserModelKey,
)
from app.models.user import User
from app.services import audit as audit_service
from app.services.quota import account_public, ensure_account, load_account

router = APIRouter(prefix="/api/admin/quota", tags=["admin-quota"])

MAX_PAGE_SIZE = 100
ROLES = ("merchant", "customer", "admin")
RECENT_LEDGER_LIMIT = 20
RECENT_DAYS = 14
# 备注下限。spec 的端点表写「< 5 字 → 422」，但 E-15 用 4 字的「线下补偿」
# 期望 200、AD-10 用 4 字的「重复冻结」期望 409——两处都要求 4 字必须放行。
# 取 4 是唯一同时满足 spec 意图（可追溯）与既有用例的下界，见 CLAUDE.md 工作记录。
MIN_REMARK_LEN = 4


class AdjustIn(BaseModel):
    points: int
    remark: str


class StatusIn(BaseModel):
    status: str
    reason: str


class PriceIn(BaseModel):
    provider: str = Field(max_length=32)
    model: str = Field(max_length=64)
    op: str
    unit: str
    cost_price_per_unit: Decimal
    price_per_unit: Decimal
    max_price_per_call: int = Field(ge=0)
    markup_rate: Decimal = Decimal("1.00")
    supports_byok: bool = False
    provider_visible: bool = True
    effective_from: datetime


def _admin_auth(auth: AuthContext = Depends(get_current_auth)) -> AuthContext:
    if auth.user.role != "admin":
        raise HTTPException(403, "仅限管理员")
    return auth


def _ledger_public(row: QuotaLedger) -> dict:
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
        "billing_source": row.billing_source,
        "provider": row.provider,
        "remark": row.remark,
        "created_at": row.created_at,
    }


async def _log_action(
    session: AsyncSession,
    *,
    admin_id: int,
    action: str,
    target_id: int,
    detail: dict,
) -> None:
    """07 记账操作的审计包装。

    target 一律是**额度账户**，故 `target_type='quota_account'`、`target_id`
    取账户 id（= 用户 id）。写入本体在 `services/audit`。
    """
    await audit_service.log_action(
        session,
        admin_id=admin_id,
        action=action,
        target_type="quota_account",
        target_id=target_id,
        detail=detail,
    )


def _check_range(date_from: date | None, date_to: date | None) -> None:
    if date_from is not None and date_to is not None and date_from > date_to:
        raise HTTPException(422, "from 不得晚于 to")


def _window(date_from: date | None, date_to: date | None) -> list:
    filters = []
    if date_from is not None:
        filters.append(
            QuotaLedger.created_at
            >= datetime.combine(date_from, datetime.min.time(), timezone.utc)
        )
    if date_to is not None:
        filters.append(
            QuotaLedger.created_at
            < datetime.combine(date_to, datetime.min.time(), timezone.utc)
            + timedelta(days=1)
        )
    return filters


def _require_remark(remark: str, field_name: str) -> str:
    if not isinstance(remark, str):
        raise HTTPException(422, f"{field_name} 必须是字符串")
    text_value = remark.strip()
    if len(text_value) < MIN_REMARK_LEN:
        raise HTTPException(
            422, f"{field_name}至少 {MIN_REMARK_LEN} 字——调账必须能追溯"
        )
    return text_value


async def _require_account(session: AsyncSession, user_id: int):
    """目标账户必须**已存在**才允许记账。

    这里不能 `ensure_account`：给一个不存在的 id 顺手建号，等于让管理员
    打错一位数字就凭空造出一个账户。
    """
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(404, "账户不存在")
    account = await load_account(session, user_id)
    if account is None:
        raise HTTPException(404, "账户不存在")
    return user, account


# --------------------------------------------------------------------------- #
# 账户列表与详情
# --------------------------------------------------------------------------- #
@router.get("/accounts")
async def list_accounts(
    role: str | None = Query(default=None),
    status_filter: str | None = Query(default=None, alias="status"),
    keyword: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=MAX_PAGE_SIZE),
    auth: AuthContext = Depends(_admin_auth),
    session: AsyncSession = Depends(get_session),
) -> dict:
    if role is not None and role not in ROLES:
        raise HTTPException(422, "role 非法")
    if status_filter is not None and status_filter not in ACCOUNT_STATUSES:
        raise HTTPException(422, "status 非法")

    # 从 `user` 出发左连账户：还没开过户的人也要能被管理员找到并充值，
    # 否则「给新商户充值」这条最常走的路第一步就是死路。
    stmt = select(User, QuotaAccount).outerjoin(
        QuotaAccount, QuotaAccount.user_id == User.id
    )
    if role is not None:
        stmt = stmt.where(User.role == role)
    if status_filter is not None:
        # 显式筛状态时只可能筛出有账户行的人：没行就没状态
        stmt = stmt.where(QuotaAccount.status == status_filter)
    if keyword:
        like = f"%{keyword}%"
        stmt = stmt.where(
            User.account.ilike(like)
            | User.email.ilike(like)
            | User.nickname.ilike(like)
        )

    total = await session.scalar(
        select(func.count()).select_from(stmt.subquery())
    )
    rows = (
        await session.execute(
            stmt.order_by(User.id).offset((page - 1) * size).limit(size)
        )
    ).all()

    items = []
    for user, account in rows:
        item = account_public(account, user.id)
        item.update(
            {
                "user_id": user.id,
                "account": user.account,
                "email": user.email,
                "nickname": user.nickname,
                "role": user.role,
            }
        )
        items.append(item)

    return {
        "items": items,
        "total": int(total or 0),
        "page": page,
        "size": size,
    }


@router.get("/accounts/{account_id}")
async def account_detail(
    account_id: int,
    auth: AuthContext = Depends(_admin_auth),
    session: AsyncSession = Depends(get_session),
) -> dict:
    user = await session.get(User, account_id)
    if user is None:
        raise HTTPException(404, "账户不存在")

    account = await load_account(session, account_id)

    recent = (
        (
            await session.execute(
                select(QuotaLedger)
                .where(QuotaLedger.user_id == account_id)
                .order_by(QuotaLedger.created_at.desc(), QuotaLedger.id.desc())
                .limit(RECENT_LEDGER_LIMIT)
            )
        )
        .scalars()
        .all()
    )

    byok_calls = await session.scalar(
        select(func.count())
        .select_from(QuotaLedger)
        .where(
            QuotaLedger.user_id == account_id,
            QuotaLedger.billing_source == "byok",
        )
    )

    return {
        "account": account_public(account, account_id),
        "user": {"id": user.id, "account": user.account, "role": user.role},
        "recent_ledger": [_ledger_public(r) for r in recent],
        "by_day": await _account_by_day(session, account_id),
        "byok_call_count": int(byok_calls or 0),
    }


async def _account_by_day(session: AsyncSession, user_id: int) -> list:
    """近 `RECENT_DAYS` 天逐日消耗，空格补 0。

    管理员看的是「这个人最近有没有异常」，折线跳空比数字偏一点更容易误判。
    """
    start = (datetime.now(timezone.utc) - timedelta(days=RECENT_DAYS - 1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    day = func.date(QuotaLedger.created_at)
    rows = (
        await session.execute(
            select(
                day.label("day"),
                func.coalesce(func.sum(-QuotaLedger.change), 0).label("points"),
                func.count().label("call_count"),
            )
            .where(
                QuotaLedger.user_id == user_id,
                QuotaLedger.source == "consume",
                QuotaLedger.created_at >= start,
            )
            .group_by(day)
        )
    ).all()
    buckets = {r.day: r for r in rows}

    items = []
    cursor = start.date()
    today = datetime.now(timezone.utc).date()
    while cursor <= today:
        found = buckets.get(cursor)
        items.append(
            {
                "day": cursor.isoformat(),
                "points": int(found.points) if found else 0,
                "call_count": found.call_count if found else 0,
            }
        )
        cursor += timedelta(days=1)
    return items


# --------------------------------------------------------------------------- #
# 记账充值
# --------------------------------------------------------------------------- #
@router.post("/accounts/{account_id}/recharge", status_code=201)
async def recharge(
    account_id: int,
    body: dict = Body(...),
    auth: AuthContext = Depends(_admin_auth),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """线下转账 + 管理员记账。

    `channel` 本期**只有 `manual`**：在线支付会让平台持有 C 端预付款，
    与总览「不做真实资金池」冲突，开通前必须重新评审。
    """
    channel = body.get("channel", "manual")
    if channel != "manual":
        raise HTTPException(422, "本期只有 manual 一条通道，在线支付涉二清")

    amount_cents = body.get("amount_cents")
    points = body.get("points")
    for name, value in (("amount_cents", amount_cents), ("points", points)):
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise HTTPException(422, f"{name} 必须是 >= 1 的整数")

    remark = body.get("remark")
    if remark is not None and not isinstance(remark, str):
        raise HTTPException(422, "remark 必须是字符串")

    user, account = await _require_account(session, account_id)

    now = datetime.now(timezone.utc)
    recharge_row = QuotaRecharge(
        user_id=account_id,
        amount_cents=amount_cents,
        points=points,
        channel="manual",
        operator_id=auth.user.id,
        remark=remark,
        created_at=now,
    )
    session.add(recharge_row)
    await session.flush()

    account.balance += points
    account.total_recharged += points
    account.updated_at = now

    # 流水与余额在同一事务里：`balance_after` 必须等于充值后的真实余额，
    # 两笔分开提交就会出现「账对不上」的中间态。
    ledger = QuotaLedger(
        user_id=account_id,
        change=points,
        balance_after=account.balance,
        source="recharge",
        ref_type="recharge",
        ref_id=recharge_row.id,
        remark=remark,
        created_at=now,
    )
    session.add(ledger)

    await _log_action(
        session,
        admin_id=auth.user.id,
        action="quota_recharge",
        target_id=account_id,
        detail={
            "points": points,
            "amount_cents": amount_cents,
            "before": account.balance - points,
            "after": account.balance,
            "remark": remark,
        },
    )

    await session.commit()
    return {
        "recharge": {
            "id": recharge_row.id,
            "user_id": recharge_row.user_id,
            "amount_cents": recharge_row.amount_cents,
            "points": recharge_row.points,
            "channel": recharge_row.channel,
            "created_at": recharge_row.created_at,
        },
        "balance": account.balance,
    }


# --------------------------------------------------------------------------- #
# 人工调整
# --------------------------------------------------------------------------- #
@router.post("/accounts/{account_id}/adjust")
async def adjust(
    account_id: int,
    payload: AdjustIn,
    auth: AuthContext = Depends(_admin_auth),
    session: AsyncSession = Depends(get_session),
) -> dict:
    if payload.points == 0:
        raise HTTPException(422, "points 不得为 0——没有变化就不是一次调整")

    remark = _require_remark(payload.remark, "remark")
    user, account = await _require_account(session, account_id)

    after = account.balance + payload.points
    if after < 0:
        raise HTTPException(422, "调整后余额不得为负——负数会撞上账户的硬约束")

    now = datetime.now(timezone.utc)
    account.balance = after
    account.updated_at = now
    session.add(
        QuotaLedger(
            user_id=account_id,
            change=payload.points,
            balance_after=after,
            source="adjust",
            ref_type="adjust",
            ref_id=auth.user.id,
            remark=remark,
            created_at=now,
        )
    )
    await _log_action(
        session,
        admin_id=auth.user.id,
        action="quota_adjust",
        target_id=account_id,
        detail={
            "change": payload.points,
            "before": account.balance - payload.points,
            "after": account.balance,
            "reason": remark,
        },
    )

    await session.commit()
    return {"balance": after}


# --------------------------------------------------------------------------- #
# 冻结 / 解冻
# --------------------------------------------------------------------------- #
@router.post("/accounts/{account_id}/status")
async def set_status(
    account_id: int,
    payload: StatusIn,
    auth: AuthContext = Depends(_admin_auth),
    session: AsyncSession = Depends(get_session),
) -> dict:
    if payload.status not in ACCOUNT_STATUSES:
        raise HTTPException(422, "status 非法")

    reason = _require_remark(payload.reason, "reason")
    user, account = await _require_account(session, account_id)

    # 与商户侧相反：admin 这里 spec 明确要求「状态相同 → 409」。
    # 管理员的操作是要留痕的，一次没生效的「冻结」被记成成功会更糟。
    if account.status == payload.status:
        raise HTTPException(409, "状态未发生变化")

    before_status = account.status
    account.status = payload.status
    account.updated_at = datetime.now(timezone.utc)
    await _log_action(
        session,
        admin_id=auth.user.id,
        action="quota_status",
        target_id=account_id,
        detail={
            "before": before_status,
            "after": account.status,
            "reason": reason,
        },
    )

    await session.commit()
    return account_public(account, account_id)


# --------------------------------------------------------------------------- #
# BYOK 使用情况
# --------------------------------------------------------------------------- #
@router.get("/byok")
async def byok_stats(
    date_from: date | None = Query(default=None, alias="from"),
    date_to: date | None = Query(default=None, alias="to"),
    auth: AuthContext = Depends(_admin_auth),
    session: AsyncSession = Depends(get_session),
) -> dict:
    _check_range(date_from, date_to)

    rows = (
        await session.execute(
            select(
                QuotaLedger.provider.label("provider"),
                func.count().label("call_count"),
                # BYOK 的 `change` 恒为 0（平台零成本），单独计数。
                # 不分开数的话，看板会把「没花钱」和「调用失败」混成一堆。
                func.count()
                .filter(QuotaLedger.change == 0)
                .label("zero_cost_count"),
            )
            .where(
                QuotaLedger.billing_source == "byok",
                *_window(date_from, date_to),
            )
            .group_by(QuotaLedger.provider)
            .order_by(func.count().desc())
        )
    ).all()

    invalid_rows = (
        await session.execute(
            select(UserModelKey.provider, func.count().label("n"))
            .where(UserModelKey.status.in_(("invalid", "disabled")))
            .group_by(UserModelKey.provider)
        )
    ).all()
    invalid = {r.provider: r.n for r in invalid_rows}

    return {
        "items": [
            {
                "provider": r.provider,
                "call_count": r.call_count,
                "zero_cost_count": r.zero_cost_count,
                # 失效 Key 数来自 `user_model_key` 而不是流水：Key 失效本身
                # 不会产生一行流水，硬从流水里推只能推出一堆 0。
                "invalid_key_count": int(invalid.get(r.provider, 0)),
            }
            for r in rows
        ]
    }


# --------------------------------------------------------------------------- #
# 计价表维护
# --------------------------------------------------------------------------- #
@router.post("/prices", status_code=201)
async def create_price(
    payload: PriceIn,
    auth: AuthContext = Depends(_admin_auth),
    session: AsyncSession = Depends(get_session),
) -> dict:
    existing = (
        await session.execute(
            select(ModelPrice).where(
                ModelPrice.provider == payload.provider,
                ModelPrice.model == payload.model,
                ModelPrice.op == payload.op,
                ModelPrice.effective_from == payload.effective_from,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(409, "同 (provider, model, op, effective_from) 已存在")

    row = ModelPrice(**payload.model_dump())
    session.add(row)
    try:
        await session.commit()
    except IntegrityError:
        # 并发下两个请求同时通过了上面的检查：让唯一约束兜住，翻译成 409
        await session.rollback()
        raise HTTPException(409, "同 (provider, model, op, effective_from) 已存在")
    await session.refresh(row)
    return {
        "id": row.id,
        "provider": row.provider,
        "model": row.model,
        "op": row.op,
        "unit": row.unit,
        "cost_price_per_unit": row.cost_price_per_unit,
        "price_per_unit": row.price_per_unit,
        "max_price_per_call": row.max_price_per_call,
        "markup_rate": row.markup_rate,
        "supports_byok": row.supports_byok,
        "provider_visible": row.provider_visible,
        "effective_from": row.effective_from,
    }


@router.get("/reimbursements")
async def list_reimbursements(
    merchant_id: int | None = Query(default=None),
    user_id: int | None = Query(default=None),
    task_id: int | None = Query(default=None),
    status_filter: str | None = Query(default=None, alias="status"),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=MAX_PAGE_SIZE),
    auth: AuthContext = Depends(_admin_auth),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """全平台报销台账。**争议处理用**——用户说「报少了」、商户说「报多了」时，
    唯一能对上账的就是这张表，故四个维度都可筛。"""
    if status_filter is not None and status_filter not in REIMBURSE_STATUSES:
        raise HTTPException(422, "status 非法")

    filters = []
    if merchant_id is not None:
        filters.append(ReimburseClaim.merchant_id == merchant_id)
    if user_id is not None:
        filters.append(ReimburseClaim.user_id == user_id)
    if task_id is not None:
        filters.append(ReimburseClaim.task_id == task_id)
    if status_filter is not None:
        filters.append(ReimburseClaim.status == status_filter)

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
