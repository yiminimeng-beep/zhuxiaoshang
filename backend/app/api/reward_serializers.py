"""05-reward 的出参形状。测试直接读这些键，所以这里是契约。"""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.reward import (
    Coupon,
    MallItem,
    PointLedger,
    Redemption,
    RewardGrant,
    UserCoupon,
)

MAX_PAGE_SIZE = 100


def grant_public(row: RewardGrant) -> dict:
    return {
        "id": row.id,
        "post_id": row.post_id,
        "task_id": row.task_id,
        "reward_rule_id": row.reward_rule_id,
        "engagement": row.engagement,
        "tier_index": row.tier_index,
        "reward_detail": row.reward_detail,
        "status": row.status,
        "fail_reason": row.fail_reason,
        "granted_at": row.granted_at,
    }


def rule_public(row) -> dict:
    return {
        "id": row.id,
        "task_id": row.task_id,
        "metric": row.metric,
        "tiers": row.tiers,
        "max_reward_per_user": row.max_reward_per_user,
    }


def ledger_public(row: PointLedger) -> dict:
    return {
        "id": row.id,
        "change": row.change,
        "balance_after": row.balance_after,
        "source": row.source,
        "ref_type": row.ref_type,
        "ref_id": row.ref_id,
        "remark": row.remark,
        "created_at": row.created_at,
    }


def coupon_public(row: Coupon) -> dict:
    return {
        "id": row.id,
        "merchant_id": row.merchant_id,
        "name": row.name,
        "type": row.type,
        "value": row.value,
        "min_amount": row.min_amount,
        "total": row.total,
        "issued": row.issued,
        "valid_from": row.valid_from,
        "valid_to": row.valid_to,
        "status": row.status,
    }


def user_coupon_public(row: UserCoupon) -> dict:
    return {
        "id": row.id,
        "coupon_id": row.coupon_id,
        "code": row.code,
        "status": row.status,
        "obtained_at": row.obtained_at,
        "expire_at": row.expire_at,
        "used_at": row.used_at,
    }


def item_public(row: MallItem) -> dict:
    return {
        "id": row.id,
        "merchant_id": row.merchant_id,
        "name": row.name,
        "cover_url": row.cover_url,
        "description": row.description,
        "points_cost": row.points_cost,
        "stock": row.stock,
        "sold": row.sold,
        "per_user_limit": row.per_user_limit,
        "status": row.status,
    }


def redemption_public(row: Redemption) -> dict:
    return {
        "id": row.id,
        "user_id": row.user_id,
        "mall_item_id": row.mall_item_id,
        "points_spent": row.points_spent,
        "quantity": row.quantity,
        "status": row.status,
        "redeem_code": row.redeem_code,
        "address": row.address,
        "created_at": row.created_at,
    }


async def paged(session: AsyncSession, model, filters: list, page: int, size: int,
                serialize) -> dict:
    """统一的 `{items,total,page,size}`。全局约定 #4：`size` 上限 100。"""
    total = await session.scalar(
        select(func.count()).select_from(model).where(*filters)
    )
    rows = (
        (
            await session.execute(
                select(model)
                .where(*filters)
                .order_by(model.id.desc())
                .offset((page - 1) * size)
                .limit(size)
            )
        )
        .scalars()
        .all()
    )
    return {
        "items": [serialize(r) for r in rows],
        "total": int(total or 0),
        "page": page,
        "size": size,
    }
