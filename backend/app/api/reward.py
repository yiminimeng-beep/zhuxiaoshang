"""05-reward · 用户侧读端点 + 商户查奖励规则。

积分余额的口径只有一处：`point_ledger` 的 `sum(change)`。
不另存一列「当前余额」——那会变成第二个真相，与流水对不上时没人知道该信哪个。
"""

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.reward_serializers import (
    MAX_PAGE_SIZE,
    grant_public,
    ledger_public,
    paged,
    rule_public,
    redemption_public,
    user_coupon_public,
)
from app.core.deps import AuthContext, get_current_auth
from app.db import get_session
from app.models.reward import (
    USER_COUPON_STATUSES,
    CashPayout,
    Coupon,
    PointLedger,
    Redemption,
    RewardGrant,
    UserCoupon,
)
from app.models.task import RewardRule, Task
from app.models.user import MerchantProfile, User

router = APIRouter(tags=["reward"])


async def balance_of(session: AsyncSession, user_id: int) -> int:
    total = await session.scalar(
        select(func.coalesce(func.sum(PointLedger.change), 0)).where(
            PointLedger.user_id == user_id
        )
    )
    return int(total or 0)


def _merchant_label(shop_name: str | None, nickname: str | None) -> str:
    if shop_name:
        return shop_name
    if nickname:
        return nickname
    return ""


async def _merchant_card(session: AsyncSession, merchant_id: int) -> dict:
    row = (
        await session.execute(
            select(User, MerchantProfile)
            .outerjoin(MerchantProfile, MerchantProfile.user_id == User.id)
            .where(User.id == merchant_id)
        )
    ).one_or_none()
    if row is None:
        return {
            "merchant_id": merchant_id,
            "merchant_name": "",
            "logo_url": None,
        }
    user, profile = row
    return {
        "merchant_id": merchant_id,
        "merchant_name": _merchant_label(
            profile.shop_name if profile else None, user.nickname
        ),
        "logo_url": profile.logo_url if profile else None,
    }


async def _points_by_merchant(session: AsyncSession, user_id: int) -> dict[int, int]:
    rows = (
        await session.execute(
            select(Task.merchant_id, func.sum(PointLedger.change))
            .select_from(PointLedger)
            .join(RewardGrant, RewardGrant.id == PointLedger.ref_id)
            .join(Task, Task.id == RewardGrant.task_id)
            .where(
                PointLedger.user_id == user_id,
                PointLedger.ref_type == "reward_grant",
                PointLedger.change > 0,
            )
            .group_by(Task.merchant_id)
        )
    ).all()
    return {int(mid): int(total) for mid, total in rows}


async def _cash_pending_by_merchant(
    session: AsyncSession, user_id: int
) -> dict[int, int]:
    """待线下发放的现金（分）。`paid` 不进这列——那笔已经打过，不是待发。"""
    rows = (
        await session.execute(
            select(Task.merchant_id, func.coalesce(func.sum(CashPayout.amount), 0))
            .select_from(CashPayout)
            .join(RewardGrant, RewardGrant.id == CashPayout.reward_grant_id)
            .join(Task, Task.id == RewardGrant.task_id)
            .where(CashPayout.user_id == user_id, CashPayout.status == "pending")
            .group_by(Task.merchant_id)
        )
    ).all()
    return {int(mid): int(total) for mid, total in rows}


async def _benefits_by_merchant(
    session: AsyncSession, user_id: int
) -> dict[int, list[str]]:
    """过审后记下的优惠券 / 代金券说明。只记文字，不发可核销的券。"""
    rows = (
        await session.execute(
            select(Task.merchant_id, RewardGrant.reward_detail)
            .join(Task, Task.id == RewardGrant.task_id)
            .where(
                RewardGrant.user_id == user_id,
                RewardGrant.status.in_(("granted", "capped")),
            )
        )
    ).all()
    out: dict[int, list[str]] = {}
    for mid, detail in rows:
        text = detail.get("benefit") if isinstance(detail, dict) else None
        if isinstance(text, str) and text.strip():
            out.setdefault(int(mid), []).append(text.strip())
    return out


async def _coupons_by_merchant(
    session: AsyncSession, user_id: int
) -> dict[int, tuple[int, int]]:
    rows = (
        await session.execute(
            select(
                Coupon.merchant_id,
                func.count(),
                func.count().filter(UserCoupon.status == "unused"),
            )
            .select_from(UserCoupon)
            .join(Coupon, Coupon.id == UserCoupon.coupon_id)
            .where(UserCoupon.user_id == user_id)
            .group_by(Coupon.merchant_id)
        )
    ).all()
    return {int(mid): (int(total), int(unused)) for mid, total, unused in rows}


def _nested_user_coupon(uc: UserCoupon, coupon: Coupon) -> dict:
    return {
        "id": uc.id,
        "coupon_id": uc.coupon_id,
        "code": uc.code,
        "status": uc.status,
        "obtained_at": uc.obtained_at,
        "expire_at": uc.expire_at,
        "used_at": uc.used_at,
        "coupon": {
            "id": coupon.id,
            "name": coupon.name,
            "type": coupon.type,
            "value": coupon.value,
            "min_amount": coupon.min_amount,
            "valid_from": coupon.valid_from,
            "valid_to": coupon.valid_to,
            "status": coupon.status,
        },
    }


# --------------------------------------------------------------------------- #
# 商户侧：查奖励规则
# --------------------------------------------------------------------------- #
@router.get("/api/merchant/reward-rules/{task_id}")
async def get_reward_rule(
    task_id: int,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """客户访商户侧 → `403`；查**别人任务**的规则 → `403`（不是 404）。

    回 404 会把「不归你管」和「不存在」压成同一个答案，排查方向完全相反。
    """
    from app.api.merchant_task import require_merchant_role

    merchant = require_merchant_role(auth)

    task = await session.get(Task, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    if task.merchant_id != merchant.id:
        raise HTTPException(status_code=403, detail="无权查看该任务")

    rule = await session.scalar(
        select(RewardRule).where(RewardRule.task_id == task_id)
    )
    if rule is None:
        raise HTTPException(status_code=404, detail="该任务未配置奖励规则")
    return {"rule": rule_public(rule)}


# --------------------------------------------------------------------------- #
# 用户侧：奖励 / 积分 / 券 / 兑换
# --------------------------------------------------------------------------- #
@router.get("/api/me/rewards")
async def my_rewards(
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=MAX_PAGE_SIZE),
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> dict:
    return await paged(
        session,
        RewardGrant,
        [RewardGrant.user_id == auth.user.id],
        page,
        size,
        grant_public,
    )


@router.get("/api/me/rewards/by-merchant")
async def my_rewards_by_merchant(
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=MAX_PAGE_SIZE),
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> dict:
    points = await _points_by_merchant(session, auth.user.id)
    coupons = await _coupons_by_merchant(session, auth.user.id)
    cash = await _cash_pending_by_merchant(session, auth.user.id)
    benefits = await _benefits_by_merchant(session, auth.user.id)
    merchant_ids = sorted(set(points) | set(coupons) | set(cash) | set(benefits))
    total = len(merchant_ids)
    page_ids = merchant_ids[(page - 1) * size : page * size]

    items = []
    for mid in page_ids:
        card = await _merchant_card(session, mid)
        c_total, c_unused = coupons.get(mid, (0, 0))
        items.append(
            {
                **card,
                "points_earned": points.get(mid, 0),
                "coupon_total": c_total,
                "coupon_unused": c_unused,
                "cash_pending": cash.get(mid, 0),
                "benefits": benefits.get(mid, []),
            }
        )
    return {"items": items, "total": total, "page": page, "size": size}


@router.get("/api/me/rewards/by-merchant/{merchant_id}")
async def my_rewards_at_merchant(
    merchant_id: int = Path(..., ge=1),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=MAX_PAGE_SIZE),
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> dict:
    points = await _points_by_merchant(session, auth.user.id)
    coupons = await _coupons_by_merchant(session, auth.user.id)
    cash = await _cash_pending_by_merchant(session, auth.user.id)
    benefits = await _benefits_by_merchant(session, auth.user.id)
    if (
        merchant_id not in points
        and merchant_id not in coupons
        and merchant_id not in cash
        and merchant_id not in benefits
    ):
        raise HTTPException(status_code=404, detail="与该商家无往来")

    total = await session.scalar(
        select(func.count())
        .select_from(UserCoupon)
        .join(Coupon, Coupon.id == UserCoupon.coupon_id)
        .where(UserCoupon.user_id == auth.user.id, Coupon.merchant_id == merchant_id)
    )
    rows = (
        await session.execute(
            select(UserCoupon, Coupon)
            .join(Coupon, Coupon.id == UserCoupon.coupon_id)
            .where(UserCoupon.user_id == auth.user.id, Coupon.merchant_id == merchant_id)
            .order_by(UserCoupon.id.asc())
            .offset((page - 1) * size)
            .limit(size)
        )
    ).all()

    return {
        "merchant": await _merchant_card(session, merchant_id),
        "points_earned": points.get(merchant_id, 0),
        "cash_pending": cash.get(merchant_id, 0),
        "benefits": benefits.get(merchant_id, []),
        "coupons": [_nested_user_coupon(uc, c) for uc, c in rows],
        "coupons_total": int(total or 0),
        "page": page,
        "size": size,
    }


@router.get("/api/me/points")
async def my_points(
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> dict:
    return {"balance": await balance_of(session, auth.user.id)}


@router.get("/api/me/points/ledger")
async def my_point_ledger(
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=MAX_PAGE_SIZE),
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> dict:
    return await paged(
        session,
        PointLedger,
        [PointLedger.user_id == auth.user.id],
        page,
        size,
        ledger_public,
    )


@router.get("/api/me/coupons")
async def my_coupons(
    status_filter: str | None = Query(default=None, alias="status"),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=MAX_PAGE_SIZE),
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> dict:
    if status_filter is not None and status_filter not in USER_COUPON_STATUSES:
        raise HTTPException(status_code=422, detail="status 非法")

    filters = [UserCoupon.user_id == auth.user.id]
    if status_filter is not None:
        filters.append(UserCoupon.status == status_filter)
    return await paged(session, UserCoupon, filters, page, size, user_coupon_public)


@router.get("/api/me/redemptions")
async def my_redemptions(
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=MAX_PAGE_SIZE),
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> dict:
    return await paged(
        session,
        Redemption,
        [Redemption.user_id == auth.user.id],
        page,
        size,
        redemption_public,
    )
