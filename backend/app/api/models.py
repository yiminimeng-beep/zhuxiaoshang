"""模型选择：`GET /api/models`。

这是**用户决策前**看到的价目表，所以两件事必须较真：

1. `est_price_per_call` 直接取 `max_price_per_call`——它就是后端预扣的依据。
   前端展示一个数、后端扣另一个数，是最典型的账单纠纷来源。
2. `provider_visible=false` 的行不得出现。那些是内部/兜底模型，
   用户看得见却选不了，等于白解释一遍为什么不能用。
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import AuthContext, get_current_auth
from app.db import get_session
from app.models.quota import OPS, ModelPrice, UserModelKey
from app.models.user import User

router = APIRouter(prefix="/api", tags=["models"])


@router.get("/models")
async def list_models(
    op: str | None = Query(default=None),
    merchant_id: int | None = Query(default=None),
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> dict:
    if op is not None and op not in OPS:
        raise HTTPException(422, "op 非法")

    # `merchant_id` 只做存在性校验：spec 明确不做按商户的个性化定价，
    # 传与不传价格必须一样。留这个参数是为了让前端能提前发现「商户没了」。
    if merchant_id is not None:
        merchant = await session.get(User, merchant_id)
        if merchant is None:
            raise HTTPException(404, "商户不存在")

    stmt = select(ModelPrice).where(
        ModelPrice.provider_visible.is_(True),
        ModelPrice.effective_from <= datetime.now(timezone.utc),
    )
    if op is not None:
        stmt = stmt.where(ModelPrice.op == op)

    # 同一 (provider, model, op) 只出生效最新那一行；改价靠插新行，
    # 所以「最新」= 用户当下真正会被计费的价。
    stmt = stmt.order_by(
        ModelPrice.provider,
        ModelPrice.model,
        ModelPrice.op,
        ModelPrice.effective_from.desc(),
    )
    rows = (await session.execute(stmt)).scalars().all()

    seen: set[tuple[str, str, str]] = set()
    latest: list[ModelPrice] = []
    for row in rows:
        key = (row.provider, row.model, row.op)
        if key in seen:
            continue
        seen.add(key)
        latest.append(row)

    mine = set(
        (
            await session.execute(
                select(UserModelKey.provider).where(
                    UserModelKey.user_id == auth.user.id,
                    UserModelKey.status == "active",
                )
            )
        )
        .scalars()
        .all()
    )

    return {
        "items": [
            {
                "provider": row.provider,
                "model": row.model,
                "op": row.op,
                "unit": row.unit,
                "price_per_unit": row.price_per_unit,
                "cost_price_per_unit": row.cost_price_per_unit,
                "supports_byok": row.supports_byok,
                "has_my_key": row.provider in mine,
                "est_price_per_call": row.max_price_per_call,
            }
            for row in latest
        ]
    }
