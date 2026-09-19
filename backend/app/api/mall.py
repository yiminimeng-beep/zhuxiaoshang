"""05-reward · 积分商城（客户侧）：浏览 + 兑换。

## 兑换为什么必须用条件 UPDATE

`stock` 与积分是两个独立的共享量，校验与扣减之间不加锁就会被穿：
`stock=1` 的 100 个并发请求都会**先查到 1**，然后各扣一次，卖出 100 件。
所以扣减写成 `UPDATE ... WHERE stock >= :n`——把「判断」与「扣」压进同一条语句，
数据库的行锁替我们串行化；查不中行就是库存不足。

积分一侧同理：只查余额然后扣，两个并发请求会双双通过。扣减走
`reward.grant_points`（先锁用户行再算 `balance_after`），不够就 422。

## 判定顺序

`quantity` 合法性 → 商品在售 → 每人限兑 → 积分 → 库存。

顺序就是契约：`RD-09`（积分不足）要求「**积分不得被扣、库存不得变**」。
若先扣库存再查积分，那条就只能靠回滚补救——而失败请求的回滚未必干净。
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.reward_serializers import MAX_PAGE_SIZE, item_public, redemption_public
from app.core.deps import AuthContext, get_current_auth
from app.db import get_session
from app.models.reward import MallItem, Redemption
from app.models.user import User
from app.services import reward as reward_service

router = APIRouter(tags=["mall"])

MAX_REDEEM_QUANTITY = 99


class RedeemIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    quantity: int = 1
    address: dict | None = None


@router.get("/api/mall/items")
async def list_items(
    merchant_id: int = Query(...),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=MAX_PAGE_SIZE),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """商户不存在 → `404`（spec 明写），不是空列表。

    空列表会让「这家店没上架东西」和「这家店不存在」长得一模一样。
    """
    merchant = await session.get(User, merchant_id)
    if merchant is None or merchant.role != "merchant":
        raise HTTPException(status_code=404, detail="商户不存在")

    filters = [MallItem.merchant_id == merchant_id, MallItem.status == "on"]
    total = await session.scalar(
        select(func.count()).select_from(MallItem).where(*filters)
    )
    rows = (
        (
            await session.execute(
                select(MallItem)
                .where(*filters)
                .order_by(MallItem.id.desc())
                .offset((page - 1) * size)
                .limit(size)
            )
        )
        .scalars()
        .all()
    )
    return {
        "items": [item_public(r) for r in rows],
        "total": int(total or 0),
        "page": page,
        "size": size,
    }


@router.get("/api/mall/items/{item_id}")
async def item_detail(
    item_id: int, session: AsyncSession = Depends(get_session)
) -> dict:
    """已下架 → `404`（不是 403）：对客户而言它就是不存在。"""
    item = await session.get(MallItem, item_id)
    if item is None or item.status != "on":
        raise HTTPException(status_code=404, detail="商品不存在或已下架")
    return {"item": item_public(item)}


@router.post("/api/mall/items/{item_id}/redeem", status_code=201)
async def redeem_item(
    item_id: int,
    payload: RedeemIn,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> dict:
    quantity = int(payload.quantity)
    if quantity < 1 or quantity > MAX_REDEEM_QUANTITY:
        raise HTTPException(
            status_code=422,
            detail=f"quantity 必须在 1~{MAX_REDEEM_QUANTITY} 之间",
        )

    item = await session.get(MallItem, item_id)
    if item is None or item.status != "on":
        raise HTTPException(status_code=404, detail="商品不存在或已下架")

    user_id = auth.user.id
    cost = item.points_cost * quantity

    if item.per_user_limit is not None:
        bought = await session.scalar(
            text(
                """
                SELECT coalesce(sum(quantity), 0) FROM redemption
                WHERE user_id = :uid AND mall_item_id = :iid
                  AND status <> 'cancelled'
                """
            ),
            {"uid": user_id, "iid": item_id},
        )
        # 看**总量**不只是次数：「每用户限兑 2 件」若按次数算，
        # 兑两次 quantity=99 就能拿走 198 件。
        if int(bought or 0) + quantity > item.per_user_limit:
            raise HTTPException(status_code=409, detail="超出每人限兑数量")

    current = await session.scalar(
        text("SELECT coalesce(sum(change), 0) FROM point_ledger WHERE user_id = :uid"),
        {"uid": user_id},
    )
    if int(current or 0) < cost:
        raise HTTPException(status_code=422, detail="积分不足")

    claimed = await session.scalar(
        text(
            """
            UPDATE mall_item SET stock = stock - :q, sold = sold + :q
            WHERE id = :iid AND status = 'on' AND stock >= :q
            RETURNING id
            """
        ),
        {"q": quantity, "iid": item_id},
    )
    if claimed is None:
        raise HTTPException(status_code=409, detail="库存不足")

    redemption = Redemption(
        user_id=user_id,
        mall_item_id=item_id,
        points_spent=cost,
        quantity=quantity,
        status="pending",
        redeem_code=reward_service.new_code("RC"),
        address=payload.address,
    )
    session.add(redemption)
    # 先拿 id：积分流水的幂等键就是它
    await session.flush()

    new_balance = await reward_service.grant_points(
        session,
        user_id=user_id,
        change=-cost,
        source="redemption",
        ref_type="redemption",
        ref_id=redemption.id,
    )
    await session.commit()

    return {
        "redemption": redemption_public(redemption),
        "redeem_code": redemption.redeem_code,
        "points_balance": new_balance,
    }
