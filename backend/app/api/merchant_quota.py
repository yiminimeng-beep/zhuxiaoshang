"""商户侧额度：`/api/merchant/quota*`。

限额字段是**商户的经营参数**，不是请求参数，所以：

- 改限额只认三个字段，别的（尤其 `balance`）一律 `400`。让它从请求体进来，
  等于给商户开了个「自己给自己充钱」的后门。
- 限额**不得低于今日已耗**。允许的话，商户把限额调到 100、今天已经花了 300，
  他名下所有任务会立刻全部 429——这是「改配置把自己锁死」，得拦。
- 传 `null` 是**清空**，不是设成 0。语义差一个数量级。
"""

from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import AuthContext, get_current_auth
from app.db import get_session
from app.models.quota import (
    ACCOUNT_STATUSES,
    LEDGER_SOURCES,
    ModelPrice,
    QuotaLedger,
    QuotaRecharge,
)
from app.services.quota import (
    account_public,
    ensure_account,
    load_account,
    today_consumed,
)

router = APIRouter(prefix="/api/merchant/quota", tags=["merchant-quota"])

MAX_PAGE_SIZE = 100
MAX_LEDGER_SPAN_DAYS = 365  # 含首尾算 366 天，与 spec 的「> 366 天」对齐

LIMIT_FIELDS = ("daily_limit", "per_user_daily_limit", "per_user_task_limit")


def _merchant_auth(
    auth: AuthContext = Depends(get_current_auth),
) -> AuthContext:
    """商户专属：客户来访一律 403。

    刻意不用 `core.deps.require_merchant`——它只回 `User`，而这里处处要
    `auth.user.id`。为了省一层解包把身份对象丢掉，后面会不断需要再查一次库。
    """
    if auth.user.role != "merchant":
        raise HTTPException(403, "仅限商户")
    return auth
GROUP_BY_FIELDS = ("task", "user", "provider", "day")


def _ledger_public(row) -> dict:
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


def _recharge_public(row) -> dict:
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


def _window(date_from: date | None, date_to: date | None) -> list:
    """把 `from` / `to` 翻成过滤条件，并拦下反向区间与过长跨度。

    跨度上限存在的意义不是省查询，是让「导出一年半流水」这种请求
    在接口层就被拒——否则它会变成一次全表扫描的入口。
    """
    if date_from is not None and date_to is not None:
        if date_from > date_to:
            raise HTTPException(422, "from 不得晚于 to")
        if (date_to - date_from).days > MAX_LEDGER_SPAN_DAYS:
            raise HTTPException(422, f"查询跨度不得超过 {MAX_LEDGER_SPAN_DAYS + 1} 天")

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


@router.get("")
async def my_quota(
    auth: AuthContext = Depends(_merchant_auth),
    session: AsyncSession = Depends(get_session),
) -> dict:
    account = await load_account(session, auth.user.id)
    body = account_public(account, auth.user.id)
    body["today_consumed"] = await today_consumed(session, auth.user.id)
    # 今日预占要等 `quota_reservation`（第二趟）才有口径，先按 0 报，
    # 而不是拿 `reserved` 冒充——后者是**全部未结算**的，不是「今日」的。
    body["today_reserved"] = 0
    return body


@router.get("/ledger")
async def merchant_ledger(
    date_from: date | None = Query(default=None, alias="from"),
    date_to: date | None = Query(default=None, alias="to"),
    source: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=MAX_PAGE_SIZE),
    auth: AuthContext = Depends(_merchant_auth),
    session: AsyncSession = Depends(get_session),
) -> dict:
    if source is not None and source not in LEDGER_SOURCES:
        raise HTTPException(422, "source 非法")

    # 账户主体恒为当前商户：流水里虽然有 spender_id，但「谁的账」只有一个答案
    filters = [QuotaLedger.user_id == auth.user.id, *_window(date_from, date_to)]
    if source is not None:
        filters.append(QuotaLedger.source == source)

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
        "items": [_ledger_public(r) for r in rows],
        "total": int(total or 0),
        "page": page,
        "size": size,
    }


@router.get("/usage")
async def usage_report(
    date_from: date | None = Query(default=None, alias="from"),
    date_to: date | None = Query(default=None, alias="to"),
    group_by: str = Query(default="day"),
    auth: AuthContext = Depends(_merchant_auth),
    session: AsyncSession = Depends(get_session),
) -> dict:
    if group_by not in GROUP_BY_FIELDS:
        raise HTTPException(422, "group_by 非法")

    filters = [
        QuotaLedger.user_id == auth.user.id,
        QuotaLedger.source == "consume",
        # 报表窗口与流水窗口同一套规则，避免两处口径漂移
        *_window(date_from, date_to),
    ]

    if group_by == "day":
        return await _usage_by_day(session, filters)

    column = {
        "task": QuotaLedger.task_id,
        # 商户付费时花钱的是**用户**，报表要定位到人就得按 spender_id 切
        "user": QuotaLedger.spender_id,
        "provider": QuotaLedger.provider,
    }[group_by]

    rows = (
        await session.execute(
            select(
                column.label("key"),
                func.coalesce(func.sum(-QuotaLedger.change), 0).label("points"),
                func.count().label("call_count"),
            )
            .where(*filters)
            .group_by(column)
            .order_by(func.sum(-QuotaLedger.change).desc())
        )
    ).all()
    return {
        "items": [
            {"key": r.key, "points": int(r.points), "call_count": r.call_count}
            for r in rows
        ]
    }


async def _usage_by_day(session: AsyncSession, filters: list) -> dict:
    """按天聚合，**跨度内每天都要有一行（含 0）**。

    跳空的日子会让折线图骗人：三天前花 100、今天花 50，中间那天不显示，
    看图的人会以为消耗是连着下来的。
    """
    day = func.date(QuotaLedger.created_at)
    rows = (
        await session.execute(
            select(
                day.label("day"),
                func.coalesce(func.sum(-QuotaLedger.change), 0).label("points"),
                func.count().label("call_count"),
            )
            .where(*filters)
            .group_by(day)
            .order_by(day)
        )
    ).all()

    if not rows:
        return {"items": []}

    buckets = {r.day: r for r in rows}
    first, last = min(buckets), max(buckets)
    items = []
    cursor = first
    while cursor <= last:
        found = buckets.get(cursor)
        items.append(
            {
                "key": cursor.isoformat(),
                "points": int(found.points) if found else 0,
                "call_count": found.call_count if found else 0,
            }
        )
        cursor += timedelta(days=1)
    return {"items": items}


@router.get("/price")
async def price_list(
    auth: AuthContext = Depends(_merchant_auth),
    session: AsyncSession = Depends(get_session),
) -> dict:
    rows = (
        (
            await session.execute(
                select(ModelPrice).order_by(
                    ModelPrice.provider,
                    ModelPrice.model,
                    ModelPrice.op,
                    ModelPrice.effective_from.desc(),
                )
            )
        )
        .scalars()
        .all()
    )

    # 只出每个 (provider, model, op) 的现行价：给出历史价会让商户按旧价算预算。
    # 商户能看到 `provider_visible=false` 的行——隐藏是**对客户端**隐藏。
    seen: set[tuple[str, str, str]] = set()
    items = []
    for row in rows:
        key = (row.provider, row.model, row.op)
        if key in seen:
            continue
        seen.add(key)
        items.append(
            {
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
        )
    return {"items": items}


@router.get("/debt")
async def debt_detail(
    auth: AuthContext = Depends(_merchant_auth),
    session: AsyncSession = Depends(get_session),
) -> dict:
    account = await load_account(session, auth.user.id)
    debt = account.debt if account is not None else 0
    return {
        "debt": debt,
        "items": await _debt_items(session, auth.user.id),
    }


async def _debt_items(session: AsyncSession, user_id: int) -> list:
    """欠款的构成明细。

    欠款只在「结算金额超出预扣」时产生，而结算（`settle`）属第二趟——
    因此本期这条路必然返回空表，**这不是没实现，是没有来源**。
    故意不拿别的流水凑数：凑出来的明细对不上 `debt` 总额更糟。
    """
    return []


@router.get("/recharges")
async def merchant_recharges(
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=MAX_PAGE_SIZE),
    auth: AuthContext = Depends(_merchant_auth),
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
        "items": [_recharge_public(r) for r in rows],
        "total": int(total or 0),
        "page": page,
        "size": size,
    }


@router.patch("/limits")
async def patch_limits(
    body: dict = Body(...),
    auth: AuthContext = Depends(_merchant_auth),
    session: AsyncSession = Depends(get_session),
) -> dict:
    # 用裸 dict 收：`balance` / `user_id` 这类字段要判成 **400**（不是 422），
    # 而且必须在任何写入之前判完——「先扣再退」在这里等于一次真实的越权。
    for field_name in body:
        if field_name not in LIMIT_FIELDS:
            raise HTTPException(400, f"不允许通过本接口修改 {field_name}")

    cleaned: dict[str, int | None] = {}
    for name in LIMIT_FIELDS:
        if name not in body:
            continue
        value = body[name]
        if value is None:
            cleaned[name] = None  # 传 null 是清空，不是 0
            continue
        if isinstance(value, bool) or not isinstance(value, int):
            raise HTTPException(422, f"{name} 必须是整数或 null")
        if value < 0:
            raise HTTPException(422, f"{name} 不得为负")
        cleaned[name] = value

    consumed = await today_consumed(session, auth.user.id)
    if cleaned.get("daily_limit") is not None and cleaned["daily_limit"] < consumed:
        raise HTTPException(
            422,
            f"日限额不得低于今日已消耗（{consumed}）——否则自己的任务会立刻全线 429",
        )

    account = await ensure_account(session, auth.user.id)
    for name, value in cleaned.items():
        setattr(account, name, value)
    account.updated_at = datetime.now(timezone.utc)
    await session.commit()
    await session.refresh(account)
    return account_public(account, auth.user.id)


@router.patch("/status")
async def patch_status(
    body: dict = Body(...),
    auth: AuthContext = Depends(_merchant_auth),
    session: AsyncSession = Depends(get_session),
) -> dict:
    status = body.get("status")
    if status not in ACCOUNT_STATUSES:
        raise HTTPException(422, "status 非法")

    # 幂等：冻结一个已冻结的账户返回 200。spec 只为 admin 的同类端点
    # 规定了「状态相同 → 409」，商户侧没规定，那就别给自己加戏。
    account = await ensure_account(session, auth.user.id)
    account.status = status
    account.updated_at = datetime.now(timezone.utc)
    await session.commit()
    await session.refresh(account)
    return account_public(account, auth.user.id)
