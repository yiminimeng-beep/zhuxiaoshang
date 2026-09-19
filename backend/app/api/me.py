"""「我的」相关端点：资料、密码、设备。"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.serializers import merchant_profile_public, user_public
from app.core.deps import AuthContext, get_current_auth
from app.core.security import hash_password, unusable_password_hash, verify_password
from app.db import get_session
from app.models.quota import UserModelKey
from app.models.user import (
    ClosedIdentifier,
    MerchantProfile,
    RefreshToken,
    UserDevice,
)

router = APIRouter(prefix="/api/me", tags=["me"])

AVATAR_SCHEMES = ("http://", "https://")

# 不允许通过 PATCH /api/me 修改的字段
PROTECTED_FIELDS = ("account", "email", "role", "status", "password")


class PatchMeIn(BaseModel):
    # 额外字段要**收下来再判**，不能直接忽略：spec 要求改 account/email/role/status
    # 返回 400（而不是静默成功，也不是 422）。
    model_config = ConfigDict(extra="allow")

    nickname: str | None = Field(default=None, max_length=32)
    avatar_url: str | None = Field(default=None, max_length=512)


class ChangePasswordIn(BaseModel):
    old_password: str = Field(min_length=1)
    new_password: str = Field(min_length=1)


def device_public(device: UserDevice) -> dict:
    return {
        "id": device.id,
        "device_fingerprint": device.device_fingerprint,
        "device_name": device.device_name,
        "last_ip": device.last_ip,
        "last_active_at": (
            device.last_active_at.isoformat() if device.last_active_at else None
        ),
        "revoked": device.revoked,
        "created_at": device.created_at.isoformat() if device.created_at else None,
    }


@router.get("")
async def get_me(
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> dict:
    user = auth.user
    body: dict = {"user": user_public(user)}
    if user.role == "merchant":
        profile = await session.get(MerchantProfile, user.id)
        # spec：`user` 与 `merchant_profile` 是**同级**键，不是套在 user 里面
        body["merchant_profile"] = (
            merchant_profile_public(profile) if profile else None
        )
    return body


@router.patch("")
async def patch_me(
    payload: PatchMeIn,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> dict:
    user = auth.user

    for forbidden in PROTECTED_FIELDS:
        if forbidden in (payload.model_extra or {}):
            raise HTTPException(400, f"字段 {forbidden} 不允许通过本接口修改")

    if payload.nickname is not None:
        user.nickname = payload.nickname
    if payload.avatar_url is not None:
        if not payload.avatar_url.startswith(AVATAR_SCHEMES):
            raise HTTPException(422, "avatar_url 必须是 http/https")
        user.avatar_url = payload.avatar_url

    await session.commit()
    await session.refresh(user)

    profile = None
    if user.role == "merchant":
        profile = await session.get(MerchantProfile, user.id)
    return {"user": user_public(user, profile)}


@router.post("/password", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(
    payload: ChangePasswordIn,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> None:
    if len(payload.new_password) < 6:
        raise HTTPException(422, "新密码至少 6 位")
    if payload.new_password == payload.old_password:
        raise HTTPException(422, "新密码不能与旧密码相同")
    if not verify_password(payload.old_password, auth.user.password_hash):
        raise HTTPException(401, "旧密码错误")

    auth.user.password_hash = hash_password(payload.new_password)

    # spec：只吊销「其他」设备，当前设备保留
    stmt = update(RefreshToken).where(RefreshToken.user_id == auth.user.id)
    if auth.device_id is not None:
        stmt = stmt.where(RefreshToken.device_id != auth.device_id)
    await session.execute(stmt.values(revoked=True))
    await session.commit()


@router.get("/devices")
async def list_devices(
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> dict:
    devices = (
        await session.scalars(
            select(UserDevice)
            .where(UserDevice.user_id == auth.user.id)
            .order_by(UserDevice.last_active_at.desc(), UserDevice.id.desc())
        )
    ).all()
    return {
        "items": [device_public(d) for d in devices],
        "total": len(devices),
    }


@router.delete("/devices/{device_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_device(
    device_id: int,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> None:
    device = await session.get(UserDevice, device_id)
    if device is None:
        raise HTTPException(404, "设备不存在")
    # 别人的设备一律 403，不泄露它是否存在
    if device.user_id != auth.user.id:
        raise HTTPException(403, "无权操作该设备")

    device.revoked = True
    await session.execute(
        update(RefreshToken)
        .where(RefreshToken.device_id == device.id)
        .values(revoked=True)
    )
    await session.commit()


@router.post("/devices/revoke-others", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_other_devices(
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> None:
    """下线除当前设备外的全部设备。"""
    current_id = auth.device_id

    stmt = select(UserDevice).where(UserDevice.user_id == auth.user.id)
    if current_id is not None:
        stmt = stmt.where(UserDevice.id != current_id)
    others = (await session.scalars(stmt)).all()

    for device in others:
        device.revoked = True

    token_stmt = update(RefreshToken).where(RefreshToken.user_id == auth.user.id)
    if current_id is not None:
        token_stmt = token_stmt.where(RefreshToken.device_id != current_id)
    await session.execute(token_stmt.values(revoked=True))
    await session.commit()


# --------------------------------------------------------------------------- #
# 注销（软删 + 匿名化）
# --------------------------------------------------------------------------- #
CLOSED_NICKNAME = "已注销用户"


class CloseAccountIn(BaseModel):
    password: str = Field(min_length=1)
    confirm: bool


async def _merchant_has_published_task(
    session: AsyncSession, merchant_id: int
) -> bool:
    """商户还有进行中的任务时不允许注销。

    `task` 表归 02-task；该模块未落地前表还不存在，这里**优雅跳过**
    （`to_regclass` 判空），等 02 建表后本检查自动生效。
    """
    from sqlalchemy import text

    present = await session.scalar(
        text("SELECT to_regclass('public.task') IS NOT NULL")
    )
    if not present:
        return False
    count = await session.scalar(
        text(
            "SELECT count(*) FROM task "
            "WHERE merchant_id = :mid AND status = 'published'"
        ),
        {"mid": merchant_id},
    )
    return bool(count)


@router.post("/close", status_code=status.HTTP_202_ACCEPTED)
async def close_account(
    payload: CloseAccountIn,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> dict:
    if not payload.confirm:
        raise HTTPException(422, "需要 confirm: true 才可注销")
    if not verify_password(payload.password, auth.user.password_hash):
        raise HTTPException(401, "密码错误")

    user = auth.user

    if user.role == "merchant" and await _merchant_has_published_task(
        session, user.id
    ):
        raise HTTPException(409, "尚有进行中的任务，无法注销")

    # 标识符马上就要置 null，先把它们记进墓碑，否则登录侧就无法区分
    # 「已注销」与「从未存在」，只能一律 404。
    for identifier in (user.account, user.email, user.phone):
        if identifier is None:
            continue
        tombstone = await session.get(ClosedIdentifier, identifier)
        if tombstone is None:
            session.add(
                ClosedIdentifier(
                    identifier=identifier,
                    user_id=user.id,
                    closed_at=datetime.now(timezone.utc),
                )
            )
        else:
            # 同一标识符可能被后来的用户用过并再次注销，覆盖成最新的归属
            tombstone.user_id = user.id
            tombstone.closed_at = datetime.now(timezone.utc)

    # 软删 + 匿名化：释放标识符占用，但行保留（审计表靠 user_id 仍能查到）
    user.status = "deleted"
    user.deleted_at = datetime.now(timezone.utc)
    user.account = None
    user.email = None
    user.phone = None
    user.nickname = CLOSED_NICKNAME
    user.avatar_url = None
    user.password_hash = unusable_password_hash()

    await session.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user.id)
        .values(revoked=True)
    )

    # 凭据**物理删除**，与「流水 / 报销单不可删」不冲突：后者是账，前者是钥匙。
    # 留着既是负担（没人知道那串密文还有没有用）又是风险（拖库即失守）。
    await session.execute(
        delete(UserModelKey).where(UserModelKey.user_id == user.id)
    )

    await session.commit()
    return {"status": "deleted"}


# --------------------------------------------------------------------------- #
# 关注列表（follow 端点本体在 follow.py）
# --------------------------------------------------------------------------- #
@router.get("/following")
async def my_following(
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> dict:
    from app.models.user import User, UserFollow

    rows = (
        await session.execute(
            select(User)
            .join(UserFollow, UserFollow.followee_id == User.id)
            .where(UserFollow.follower_id == auth.user.id)
            .order_by(UserFollow.created_at.desc())
        )
    ).scalars().all()

    items = []
    for user in rows:
        profile = await session.get(MerchantProfile, user.id)
        items.append(
            {
                "id": user.id,
                "nickname": user.nickname,
                "avatar_url": user.avatar_url,
                "merchant_profile": (
                    merchant_profile_public(profile) if profile else None
                ),
            }
        )
    return {"items": items, "total": len(items)}
