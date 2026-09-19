"""关注（客户 → 商户）。

方向被硬约束：只有 `customer` 关注 `merchant` 合法，其余组合一律 `422`。
注意这几个端点挂在 `/api/merchant/*` 下，但**不适用**「客户不得访问 /api/merchant/*」
那条规则——粉丝数是公开数据，关注动作也允许任意已登录用户发起（非法组合由 422 拒绝）。
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import AuthContext, get_current_auth
from app.db import get_session
from app.models.user import User, UserFollow

router = APIRouter(prefix="/api/merchant", tags=["follow"])


async def _load_follow_target(
    session: AsyncSession, merchant_id: int, caller: AuthContext
) -> User:
    """按 spec 的顺序判：先自己(403) → 再不存在(404) → 再角色组合(422) → 再状态(422)。"""
    if merchant_id == caller.user.id:
        raise HTTPException(403, "不能关注自己")

    target = await session.get(User, merchant_id)
    if target is None:
        raise HTTPException(404, "商户不存在")

    if caller.user.role != "customer" or target.role != "merchant":
        raise HTTPException(422, "只允许客户关注商户")

    if target.status != "active":
        raise HTTPException(422, "该商户不可关注")

    return target


@router.post("/{merchant_id}/follow", status_code=status.HTTP_201_CREATED)
async def follow(
    merchant_id: int,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> dict:
    target = await _load_follow_target(session, merchant_id, auth)

    exists = await session.scalar(
        select(UserFollow.id).where(
            UserFollow.follower_id == auth.user.id,
            UserFollow.followee_id == target.id,
        )
    )
    if exists is not None:
        raise HTTPException(409, "已经关注过了")

    session.add(
        UserFollow(follower_id=auth.user.id, followee_id=target.id)
    )
    await session.commit()
    return {"follower_id": auth.user.id, "followee_id": target.id}


@router.delete("/{merchant_id}/follow", status_code=status.HTTP_204_NO_CONTENT)
async def unfollow(
    merchant_id: int,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> None:
    result = await session.execute(
        delete(UserFollow).where(
            UserFollow.follower_id == auth.user.id,
            UserFollow.followee_id == merchant_id,
        )
    )
    if result.rowcount == 0:
        raise HTTPException(404, "尚未关注")
    await session.commit()


@router.get("/{merchant_id}/followers/count")
async def followers_count(
    merchant_id: int, session: AsyncSession = Depends(get_session)
) -> dict:
    """公开端点，不校验商户状态——被封禁商户的粉丝数仍可查。"""
    count = await session.scalar(
        select(func.count())
        .select_from(UserFollow)
        .where(UserFollow.followee_id == merchant_id)
    )
    return {"count": count or 0}
