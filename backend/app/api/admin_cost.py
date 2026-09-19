"""06-admin · AI 成本看板（`/api/admin/cost*`）。

## 这笔钱是谁花的

看板的 `total_cents` 是**平台真实成本**——付给 provider 的账单。07 的
`total_consumed` 是**向商户/用户的计费额**。两者允许不等（差值 = 毛利），
所以这里**只能**读 `gen_output.cost_cents`，绝不能改成读 `quota_ledger`：
混用之后看板显示的是收入，而它必须显示支出。

同理，金额**不因 07 的退款回退**。系统故障把商户的额度退了，平台的 API
账单照样产生——钱已经花出去了。

## 三处口径必须是同一个

`summary.total_cents`、`by-merchant` 的合计、`by-day` 的逐日和，三者算的是
同一批行、同一个时间窗。`CM-02` / `CD-02` 两条一致性用例盯的就是它们对不上
的场景——聚合写错不会报错，只会安静地给出一份错报表，而这种用例是整个模块
唯一能自动发现「口径整体漂移」的东西。所以三处的 WHERE 都由 `_filters()` 出。
"""

from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import AuthContext, require_admin
from app.db import get_session
from app.models.studio import BudgetAlert, ContentJob, GenOutput
from app.models.task import Task
from app.models.user import MerchantProfile, User

router = APIRouter(prefix="/api/admin/cost", tags=["admin-cost"])

MAX_PAGE_SIZE = 100
# 窗口跨度上界。spec 写「> 366 天 → 422」，而「跨度」按**含头尾的天数**算：
# `from=365 天前` 到 `to=今天` 是 366 天，合法；再往前一天就是 367，非法。
# 故比较写 `(to - from).days > 365`。
MAX_WINDOW_DAYS = 365
RECENT_JOBS_LIMIT = 20

# `content_job.status='failed'` 是唯一判「调用失败」的依据。
# 刻意**不看** `cost_cents`：BYOK 调用的成本恒为 0，那是「用户自己付了钱」，
# 不是「调用炸了」。拿金额判成败，看板会显示一条永远到不了 100% 的曲线，
# 而运营会去查一个根本不存在的故障（spec「成本看板 vs 07 计费」）。
FAILED_JOB_STATUS = "failed"


def _window(date_from: date | None, date_to: date | None) -> tuple[date, date]:
    """`from`/`to` 缺省都取今天。跨度按含头尾的天数卡在上界。"""
    today = datetime.now(timezone.utc).date()
    start = date_from or today
    end = date_to or today
    if start > end:
        raise HTTPException(422, "from 不得晚于 to")
    if (end - start).days > MAX_WINDOW_DAYS:
        raise HTTPException(422, "时间跨度不得超过 366 天")
    return start, end


def _lower(start: date) -> datetime:
    return datetime.combine(start, datetime.min.time(), timezone.utc)


def _upper(end: date) -> datetime:
    """上界取次日零点、且**开区间**（`<`）：用 `<= end 23:59:59` 会漏掉
    最后一天里 23:59:59 之后那半秒的行。"""
    return datetime.combine(end, datetime.min.time(), timezone.utc) + timedelta(days=1)


def _filters(start: date, end: date) -> list:
    """产出落在窗口内。三处聚合共用，口径不可能漂。"""
    return [
        GenOutput.created_at >= _lower(start),
        GenOutput.created_at < _upper(end),
    ]


def _is_success():
    """成功 = 所属 job 不是 `failed`。"""
    return ContentJob.status != FAILED_JOB_STATUS


async def _shop_names(session: AsyncSession, merchant_ids: set[int]) -> dict[int, str]:
    if not merchant_ids:
        return {}
    rows = (
        await session.execute(
            select(MerchantProfile.user_id, MerchantProfile.shop_name).where(
                MerchantProfile.user_id.in_(merchant_ids)
            )
        )
    ).all()
    return {r.user_id: r.shop_name for r in rows}


# --------------------------------------------------------------------------- #
# 总览
# --------------------------------------------------------------------------- #
@router.get("/summary")
async def summary(
    date_from: date | None = Query(default=None, alias="from"),
    date_to: date | None = Query(default=None, alias="to"),
    auth: AuthContext = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> dict:
    start, end = _window(date_from, date_to)

    row = (
        await session.execute(
            select(
                func.coalesce(func.sum(GenOutput.cost_cents), 0).label("total_cents"),
                func.count().label("gen_count"),
                func.count(func.distinct(GenOutput.job_id)).label("job_count"),
                func.count().filter(_is_success()).label("success_count"),
                func.count().filter(~_is_success()).label("fail_count"),
            )
            .select_from(GenOutput)
            .join(ContentJob, ContentJob.id == GenOutput.job_id)
            .where(*_filters(start, end))
        )
    ).one()

    total_cents = int(row.total_cents or 0)
    gen_count = int(row.gen_count or 0)
    return {
        "total_cents": total_cents,
        "gen_count": gen_count,
        "job_count": int(row.job_count or 0),
        "success_count": int(row.success_count or 0),
        "fail_count": int(row.fail_count or 0),
        # 空集上 `AVG()` 返回 NULL，直接透传会让前端渲染出 `null` / `NaN`。
        # 整数除法还有一层用意：金额不允许出现小数点后半分钱。
        "avg_cents": total_cents // gen_count if gen_count else 0,
    }


# --------------------------------------------------------------------------- #
# 按商户
# --------------------------------------------------------------------------- #
@router.get("/by-merchant")
async def by_merchant(
    date_from: date | None = Query(default=None, alias="from"),
    date_to: date | None = Query(default=None, alias="to"),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=MAX_PAGE_SIZE),
    auth: AuthContext = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> dict:
    start, end = _window(date_from, date_to)

    # `content_job → task.merchant_id` 这条 join 就是「谁花的钱」。
    # 漏掉它直接按 `gen_output` 分组，会得到一个没有商户维度的「合计」，
    # 而它看上去依然合理——`CM-02` 存在的理由。
    grouped = (
        select(
            Task.merchant_id.label("merchant_id"),
            func.coalesce(func.sum(GenOutput.cost_cents), 0).label("total_cents"),
            func.count().label("gen_count"),
            func.count().filter(GenOutput.type == "video").label("video_count"),
            func.count().filter(GenOutput.type == "copy").label("copy_count"),
        )
        .select_from(GenOutput)
        .join(ContentJob, ContentJob.id == GenOutput.job_id)
        .join(Task, Task.id == ContentJob.task_id)
        .where(*_filters(start, end))
        .group_by(Task.merchant_id)
        .subquery()
    )

    total = await session.scalar(select(func.count()).select_from(grouped))
    rows = (
        await session.execute(
            select(grouped)
            .order_by(grouped.c.total_cents.desc(), grouped.c.merchant_id)
            .offset((page - 1) * size)
            .limit(size)
        )
    ).all()

    names = await _shop_names(session, {r.merchant_id for r in rows})
    return {
        "items": [
            {
                "merchant_id": r.merchant_id,
                "shop_name": names.get(r.merchant_id),
                "total_cents": int(r.total_cents or 0),
                "gen_count": int(r.gen_count or 0),
                "video_count": int(r.video_count or 0),
                "copy_count": int(r.copy_count or 0),
            }
            for r in rows
        ],
        "total": int(total or 0),
        "page": page,
        "size": size,
    }


# --------------------------------------------------------------------------- #
# 按日趋势
# --------------------------------------------------------------------------- #
@router.get("/by-day")
async def by_day(
    date_from: date | None = Query(default=None, alias="from"),
    date_to: date | None = Query(default=None, alias="to"),
    auth: AuthContext = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> dict:
    start, end = _window(date_from, date_to)

    day = func.date(GenOutput.created_at)
    rows = (
        await session.execute(
            select(
                day.label("day"),
                func.coalesce(func.sum(GenOutput.cost_cents), 0).label("total_cents"),
                func.count().label("gen_count"),
            )
            .where(*_filters(start, end))
            .group_by(day)
        )
    ).all()
    buckets = {r.day: r for r in rows}

    # **补 0**而不是跳过：「没有数据」与「花费为 0」在这个页面上必须是同一个点，
    # 否则前端的折线会在中间断开，看起来像服务挂了。
    items = []
    cursor = start
    while cursor <= end:
        found = buckets.get(cursor)
        items.append(
            {
                "date": cursor.isoformat(),
                "total_cents": int(found.total_cents) if found else 0,
                "gen_count": int(found.gen_count) if found else 0,
            }
        )
        cursor += timedelta(days=1)
    return {"items": items}


# --------------------------------------------------------------------------- #
# 按模型
# --------------------------------------------------------------------------- #
@router.get("/by-provider")
async def by_provider(
    date_from: date | None = Query(default=None, alias="from"),
    date_to: date | None = Query(default=None, alias="to"),
    auth: AuthContext = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> dict:
    start, end = _window(date_from, date_to)

    rows = (
        await session.execute(
            select(
                GenOutput.provider.label("provider"),
                func.coalesce(func.sum(GenOutput.cost_cents), 0).label("total_cents"),
                func.count().label("gen_count"),
                func.count().filter(_is_success()).label("success_count"),
            )
            .select_from(GenOutput)
            .join(ContentJob, ContentJob.id == GenOutput.job_id)
            .where(*_filters(start, end))
            .group_by(GenOutput.provider)
            .order_by(func.count().desc(), GenOutput.provider)
        )
    ).all()

    items = []
    for r in rows:
        gen_count = int(r.gen_count or 0)
        total_cents = int(r.total_cents or 0)
        items.append(
            {
                "provider": r.provider,
                "total_cents": total_cents,
                # BYOK 的 `cost_cents` 恒为 0：平台没花钱，但**确实调了一次**。
                # 只看金额会以为这个 provider 今天没被使用。
                "gen_count": gen_count,
                "avg_cents": total_cents // gen_count if gen_count else 0,
                # 分子分母都含 BYOK 调用：它成功就是成功，只是不花钱。
                # 无数据时是 0 而不是 NaN——NaN 会渲染成「NaN%」。
                "success_rate": (int(r.success_count or 0) / gen_count) if gen_count else 0,
            }
        )
    # 无调用时**没有 provider 行**，不凭空造一批 0 行
    return {"items": items}


# --------------------------------------------------------------------------- #
# 熔断告警
# --------------------------------------------------------------------------- #
@router.get("/budget-alerts")
async def budget_alerts(
    date_from: date | None = Query(default=None, alias="from"),
    date_to: date | None = Query(default=None, alias="to"),
    auth: AuthContext = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> dict:
    start, end = _window(date_from, date_to)

    rows = (
        await session.execute(
            select(BudgetAlert, MerchantProfile.shop_name)
            .outerjoin(MerchantProfile, MerchantProfile.user_id == BudgetAlert.merchant_id)
            .where(BudgetAlert.alert_date >= start, BudgetAlert.alert_date <= end)
            .order_by(BudgetAlert.alert_date.desc(), BudgetAlert.id.desc())
        )
    ).all()

    return {
        "items": [
            {
                "merchant_id": alert.merchant_id,
                # 管理员看 id 认不出是谁——清单不带店名等于白给
                "shop_name": shop_name,
                "alert_date": alert.alert_date.isoformat(),
                "spend_cents": alert.spend_cents,
                "limit_cents": alert.limit_cents,
            }
            for alert, shop_name in rows
        ]
    }


# --------------------------------------------------------------------------- #
# 单商户明细
# --------------------------------------------------------------------------- #
@router.get("/merchants/{merchant_id}/detail")
async def merchant_detail(
    merchant_id: int,
    date_from: date | None = Query(default=None, alias="from"),
    date_to: date | None = Query(default=None, alias="to"),
    auth: AuthContext = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> dict:
    start, end = _window(date_from, date_to)

    # 传一个客户的 id 必须 404，**不得返回空报表**：否则「这家店没数据」
    # 与「这个 id 根本不是店」长得一模一样，运营会去查一个不存在的问题。
    merchant = await session.scalar(
        select(User).where(User.id == merchant_id, User.role == "merchant")
    )
    if merchant is None:
        raise HTTPException(404, "商户不存在")

    day = func.date(GenOutput.created_at)
    day_rows = (
        await session.execute(
            select(
                day.label("day"),
                func.coalesce(func.sum(GenOutput.cost_cents), 0).label("total_cents"),
                func.count().label("gen_count"),
            )
            .select_from(GenOutput)
            .join(ContentJob, ContentJob.id == GenOutput.job_id)
            .join(Task, Task.id == ContentJob.task_id)
            .where(Task.merchant_id == merchant_id, *_filters(start, end))
            .group_by(day)
        )
    ).all()
    day_buckets = {r.day: r for r in day_rows}
    by_day_items = []
    cursor = start
    while cursor <= end:
        found = day_buckets.get(cursor)
        by_day_items.append(
            {
                "date": cursor.isoformat(),
                "total_cents": int(found.total_cents) if found else 0,
                "gen_count": int(found.gen_count) if found else 0,
            }
        )
        cursor += timedelta(days=1)

    provider_rows = (
        await session.execute(
            select(
                GenOutput.provider.label("provider"),
                func.coalesce(func.sum(GenOutput.cost_cents), 0).label("total_cents"),
                func.count().label("gen_count"),
                func.count().filter(_is_success()).label("success_count"),
            )
            .select_from(GenOutput)
            .join(ContentJob, ContentJob.id == GenOutput.job_id)
            .join(Task, Task.id == ContentJob.task_id)
            .where(Task.merchant_id == merchant_id, *_filters(start, end))
            .group_by(GenOutput.provider)
            .order_by(func.count().desc(), GenOutput.provider)
        )
    ).all()
    by_provider_items = []
    for r in provider_rows:
        gen_count = int(r.gen_count or 0)
        total_cents = int(r.total_cents or 0)
        by_provider_items.append(
            {
                "provider": r.provider,
                "total_cents": total_cents,
                "gen_count": gen_count,
                "avg_cents": total_cents // gen_count if gen_count else 0,
                "success_rate": (int(r.success_count or 0) / gen_count) if gen_count else 0,
            }
        )

    job_rows = (
        await session.execute(
            select(
                ContentJob,
                func.coalesce(func.sum(GenOutput.cost_cents), 0).label("cost_cents"),
            )
            .outerjoin(GenOutput, GenOutput.job_id == ContentJob.id)
            .join(Task, Task.id == ContentJob.task_id)
            .where(
                Task.merchant_id == merchant_id,
                ContentJob.created_at >= _lower(start),
                ContentJob.created_at < _upper(end),
            )
            .group_by(ContentJob.id)
            .order_by(ContentJob.created_at.desc(), ContentJob.id.desc())
            .limit(RECENT_JOBS_LIMIT)
        )
    ).all()

    return {
        "merchant": {
            "id": merchant.id,
            "account": merchant.account,
            "nickname": merchant.nickname,
            "status": merchant.status,
        },
        "by_day": by_day_items,
        "by_provider": by_provider_items,
        "recent_jobs": [
            {
                "job_id": job.id,
                "task_id": job.task_id,
                "user_id": job.user_id,
                "kind": job.kind,
                "status": job.status,
                "provider": job.provider,
                "billing_source": job.billing_source,
                "cost_cents": int(cost_cents or 0),
                "created_at": job.created_at,
            }
            for job, cost_cents in job_rows
        ],
    }
