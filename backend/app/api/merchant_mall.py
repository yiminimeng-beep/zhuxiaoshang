"""05-reward · 商户侧：上架商品、改商品、确认/取消兑换、建券/改券。

## 两条反复出现的分寸

- **改别人家的东西 → `403`，不是 `404`**。「不归你管」与「不存在」是两个方向相反的
  排查起点，压成同一个答案等于把排查成本转嫁给调用方。
- **已发出去的量收不回来**。`stock` 不得改成小于 `sold`、`total` 不得改成小于
  `issued`——两条都不是格式错误，而是「账已经发生了」。所以它们是 `422`，
  并且**在改动前就拒**。

取消兑换要写两处：库存还原 + 积分原路返还。状态跃迁用条件 UPDATE
（`WHERE status = 'pending'`）而不是「先查再改」——后者在并发两次取消时双双通过，
钱就退了两遍。
"""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.merchant_task import require_merchant_role
from app.api.reward_serializers import (
    coupon_public,
    item_public,
    redemption_public,
)
from app.core.deps import AuthContext, get_current_auth
from app.db import get_session
from app.models.reward import (
    COUPON_STATUSES,
    COUPON_TYPES,
    Coupon,
    MallItem,
    Redemption,
)
from app.models.user import User
from app.services import reward as reward_service

router = APIRouter(tags=["merchant-mall"])


class ItemIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    cover_url: str | None = None
    description: str | None = None
    points_cost: int
    stock: int
    per_user_limit: int | None = None


class ItemPatchIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    cover_url: str | None = None
    description: str | None = None
    points_cost: int | None = None
    stock: int | None = None
    per_user_limit: int | None = None
    status: str | None = None


class CouponIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    type: str
    value: int
    min_amount: int | None = None
    total: int
    valid_from: datetime
    valid_to: datetime


class CouponPatchIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    value: int | None = None
    min_amount: int | None = None
    total: int | None = None
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    status: str | None = None


async def _owned_item(session: AsyncSession, item_id: int, merchant: User) -> MallItem:
    item = await session.get(MallItem, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="商品不存在")
    if item.merchant_id != merchant.id:
        raise HTTPException(status_code=403, detail="无权修改该商品")
    return item


async def _owned_coupon(session: AsyncSession, coupon_id: int, merchant: User) -> Coupon:
    coupon = await session.get(Coupon, coupon_id)
    if coupon is None:
        raise HTTPException(status_code=404, detail="券不存在")
    if coupon.merchant_id != merchant.id:
        raise HTTPException(status_code=403, detail="无权修改该券")
    return coupon


# --------------------------------------------------------------------------- #
# 商品
# --------------------------------------------------------------------------- #
@router.post("/api/merchant/mall/items", status_code=201)
async def create_item(
    payload: ItemIn,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> dict:
    merchant = require_merchant_role(auth)

    if payload.points_cost < 1:
        raise HTTPException(status_code=422, detail="points_cost 必须 >= 1")
    if payload.stock < 0:
        raise HTTPException(status_code=422, detail="stock 必须 >= 0")
    if payload.per_user_limit is not None and payload.per_user_limit < 1:
        raise HTTPException(status_code=422, detail="per_user_limit 必须 >= 1 或留空")

    item = MallItem(
        merchant_id=merchant.id,
        name=payload.name,
        cover_url=payload.cover_url,
        description=payload.description,
        points_cost=payload.points_cost,
        stock=payload.stock,
        sold=0,
        per_user_limit=payload.per_user_limit,
        status="on",
    )
    session.add(item)
    await session.commit()
    return {"item": item_public(item)}


@router.patch("/api/merchant/mall/items/{item_id}")
async def patch_item(
    item_id: int,
    payload: ItemPatchIn,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> dict:
    merchant = require_merchant_role(auth)
    item = await _owned_item(session, item_id, merchant)

    if payload.points_cost is not None and payload.points_cost < 1:
        raise HTTPException(status_code=422, detail="points_cost 必须 >= 1")
    if payload.stock is not None:
        if payload.stock < 0:
            raise HTTPException(status_code=422, detail="stock 必须 >= 0")
        if payload.stock < item.sold:
            # 已经卖出去的收不回来：把库存改成比已售还少是自相矛盾
            raise HTTPException(status_code=422, detail="stock 不得小于 sold")
    if payload.per_user_limit is not None and payload.per_user_limit < 1:
        raise HTTPException(status_code=422, detail="per_user_limit 必须 >= 1")
    if payload.status is not None and payload.status not in ("on", "off"):
        raise HTTPException(status_code=422, detail="status 非法")

    for field in (
        "name",
        "cover_url",
        "description",
        "points_cost",
        "stock",
        "per_user_limit",
        "status",
    ):
        value = getattr(payload, field)
        if value is not None:
            setattr(item, field, value)

    await session.commit()
    return {"item": item_public(item)}


# --------------------------------------------------------------------------- #
# 兑换的履约
# --------------------------------------------------------------------------- #
async def _owned_redemption(
    session: AsyncSession, redemption_id: int, merchant: User
) -> Redemption:
    """兑换单 → 商品 → 归属。别人家的兑换回 `403`（不是 404）。"""
    redemption = await session.get(Redemption, redemption_id)
    if redemption is None:
        raise HTTPException(status_code=404, detail="兑换记录不存在")

    item = await session.get(MallItem, redemption.mall_item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="商品不存在")
    if item.merchant_id != merchant.id:
        raise HTTPException(status_code=403, detail="无权操作该兑换")
    return redemption


@router.post("/api/merchant/redemptions/{redemption_id}/confirm")
async def confirm_redemption(
    redemption_id: int,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> dict:
    merchant = require_merchant_role(auth)
    redemption = await _owned_redemption(session, redemption_id, merchant)

    moved = await session.scalar(
        text(
            """
            UPDATE redemption SET status = 'confirmed'
            WHERE id = :rid AND status = 'pending'
            RETURNING id
            """
        ),
        {"rid": redemption.id},
    )
    if moved is None:
        raise HTTPException(status_code=409, detail="该兑换不在待履约状态")

    await session.commit()
    await session.refresh(redemption)
    return {"redemption": redemption_public(redemption)}


@router.post("/api/merchant/redemptions/{redemption_id}/cancel")
async def cancel_redemption(
    redemption_id: int,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """取消 `pending` 兑换：**原路返还积分**，库存与已售对称还原。

    状态跃迁是**先**做的条件 UPDATE——先退款再判状态的话，第二次取消时钱
    已经出去了。而且退款本身还有 `(source, ref_type, ref_id)` 唯一约束兜底。
    """
    merchant = require_merchant_role(auth)
    redemption = await _owned_redemption(session, redemption_id, merchant)

    moved = await session.scalar(
        text(
            """
            UPDATE redemption SET status = 'cancelled'
            WHERE id = :rid AND status = 'pending'
            RETURNING id
            """
        ),
        {"rid": redemption.id},
    )
    if moved is None:
        raise HTTPException(status_code=409, detail="该兑换已不可取消")

    await session.execute(
        text(
            """
            UPDATE mall_item SET stock = stock + :q, sold = sold - :q
            WHERE id = :iid
            """
        ),
        {"q": redemption.quantity, "iid": redemption.mall_item_id},
    )
    await reward_service.grant_points(
        session,
        user_id=redemption.user_id,
        change=redemption.points_spent,
        source="refund",
        ref_type="redemption",
        ref_id=redemption.id,
    )

    await session.commit()
    await session.refresh(redemption)
    return {"redemption": redemption_public(redemption)}


# --------------------------------------------------------------------------- #
# 券模板
# --------------------------------------------------------------------------- #
@router.post("/api/merchant/coupons", status_code=201)
async def create_coupon(
    payload: CouponIn,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> dict:
    merchant = require_merchant_role(auth)

    if payload.type not in COUPON_TYPES:
        raise HTTPException(status_code=422, detail="券类型非法")
    if payload.total <= 0:
        raise HTTPException(status_code=422, detail="total 必须 > 0")
    if payload.valid_to < payload.valid_from:
        raise HTTPException(status_code=422, detail="valid_to 不得早于 valid_from")

    coupon = Coupon(
        merchant_id=merchant.id,
        name=payload.name,
        type=payload.type,
        value=payload.value,
        min_amount=payload.min_amount,
        total=payload.total,
        issued=0,
        valid_from=payload.valid_from,
        valid_to=payload.valid_to,
        status="active",
    )
    session.add(coupon)
    await session.commit()
    return {"coupon": coupon_public(coupon)}


@router.patch("/api/merchant/coupons/{coupon_id}")
async def patch_coupon(
    coupon_id: int,
    payload: CouponPatchIn,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> dict:
    merchant = require_merchant_role(auth)
    coupon = await _owned_coupon(session, coupon_id, merchant)

    if payload.total is not None and payload.total < coupon.issued:
        # 发出去的券追不回来，改小等于自相矛盾
        raise HTTPException(status_code=422, detail="total 不得小于已发放数")
    if payload.status is not None and payload.status not in COUPON_STATUSES:
        raise HTTPException(status_code=422, detail="status 非法")

    new_from = payload.valid_from or coupon.valid_from
    new_to = payload.valid_to or coupon.valid_to
    if new_to < new_from:
        raise HTTPException(status_code=422, detail="valid_to 不得早于 valid_from")

    for field in ("name", "value", "min_amount", "total", "status"):
        value = getattr(payload, field)
        if value is not None:
            setattr(coupon, field, value)
    coupon.valid_from = new_from
    coupon.valid_to = new_to

    await session.commit()
    return {"coupon": coupon_public(coupon)}
