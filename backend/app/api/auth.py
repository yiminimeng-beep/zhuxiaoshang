"""注册端点。

校验口径全部来自 specs/01-auth/spec.md 的「注册与标识符」边界组。
`account` / `email` 一律归一化为小写后再比对与存储，保证大小写不敏感。
"""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.serializers import merchant_profile_public, user_public
from app.config import get_settings
from app.core.security import (
    create_access_token,
    hash_password,
    hash_refresh_token,
    new_refresh_token,
    verify_password,
)
from app.core.deps import AuthContext, get_current_auth
from app.db import get_session
from app.models.user import (
    ClosedIdentifier,
    LoginAttempt,
    MerchantProfile,
    RefreshToken,
    User,
    UserDevice,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])

ACCOUNT_PATTERN = r"^[A-Za-z0-9_]{6,20}$"
EMAIL_PATTERN = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"
PHONE_PATTERN = r"^1\d{10}$"

# 注册请求不带设备指纹，但 refresh_token 需要一个设备作为归属。
# 用与前端/测试默认一致的值，这样紧接着的同设备登录会复用同一行设备记录。
DEFAULT_DEVICE_FINGERPRINT = "fp-default"

# 连续错 5 次后第 6 次开始锁（spec「连续 5 次密码错 → 第 6 次 423」）
MAX_LOGIN_FAILURES = 5


class RegisterIn(BaseModel):
    account: str | None = Field(default=None, pattern=ACCOUNT_PATTERN)
    email: str | None = Field(default=None, max_length=254, pattern=EMAIL_PATTERN)
    password: str = Field(min_length=6, max_length=64)
    role: str
    nickname: str | None = Field(default=None, max_length=32)
    shop_name: str | None = Field(default=None, max_length=64)
    category: str | None = Field(default=None, max_length=32)
    phone: str | None = Field(default=None, pattern=PHONE_PATTERN)

    @field_validator("password")
    @classmethod
    def _password_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("密码不能全是空白")
        return v

    @model_validator(mode="after")
    def _need_identifier(self) -> "RegisterIn":
        if not self.account and not self.email:
            raise ValueError("account 与 email 至少要填一个")
        return self


def issue_refresh_token(
    session: AsyncSession, user_id: int, device_id: int
) -> str:
    """签发 refresh_token，库里只落 sha256。返回明文（仅此一次）。"""
    settings = get_settings()
    plain = new_refresh_token()
    session.add(
        RefreshToken(
            user_id=user_id,
            device_id=device_id,
            token_hash=hash_refresh_token(plain),
            expires_at=datetime.now(timezone.utc)
            + timedelta(seconds=settings.refresh_token_ttl_seconds),
            revoked=False,
        )
    )
    return plain


@router.post("/register", status_code=status.HTTP_201_CREATED)
async def register(
    payload: RegisterIn, session: AsyncSession = Depends(get_session)
) -> dict:
    # admin 只能由种子脚本写入，注册接口一律拒绝（400，不是 422）
    if payload.role == "admin":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "不允许注册管理员")
    if payload.role not in ("customer", "merchant"):
        raise HTTPException(422, "role 非法")
    if payload.role == "merchant" and not (payload.shop_name and payload.category):
        raise HTTPException(422, "商户必须提供 shop_name 与 category")

    account = payload.account.lower() if payload.account else None
    email = payload.email.lower() if payload.email else None

    if account and await session.scalar(
        select(User.id).where(User.account == account)
    ):
        raise HTTPException(status.HTTP_409_CONFLICT, "account 已被占用")
    if email and await session.scalar(select(User.id).where(User.email == email)):
        raise HTTPException(status.HTTP_409_CONFLICT, "email 已被占用")
    if payload.phone and await session.scalar(
        select(User.id).where(User.phone == payload.phone)
    ):
        raise HTTPException(status.HTTP_409_CONFLICT, "phone 已被占用")

    nickname = payload.nickname or account or (email or "").split("@")[0]
    user = User(
        account=account,
        email=email,
        password_hash=hash_password(payload.password),
        phone=payload.phone,
        role=payload.role,
        nickname=nickname[:32],
        status="active",
    )
    session.add(user)
    await session.flush()

    profile = None
    if payload.role == "merchant":
        profile = MerchantProfile(
            user_id=user.id,
            shop_name=payload.shop_name,
            category=payload.category,
        )
        session.add(profile)

    device = UserDevice(
        user_id=user.id,
        device_fingerprint=DEFAULT_DEVICE_FINGERPRINT,
        last_active_at=datetime.now(timezone.utc),
        revoked=False,
    )
    session.add(device)
    await session.flush()

    refresh_plain = issue_refresh_token(session, user.id, device.id)
    await session.commit()
    await session.refresh(user)

    return {
        "access_token": create_access_token(user.id, device_id=device.id),
        "refresh_token": refresh_plain,
        "user": user_public(user, profile),
    }


# --------------------------------------------------------------------------- #
# 登录
# --------------------------------------------------------------------------- #
class LoginIn(BaseModel):
    identifier: str = Field(min_length=1, max_length=254)
    password: str = Field(min_length=1)
    # 必填：缺失即 422（spec「登录必须带 device_fingerprint」）
    device_fingerprint: str = Field(min_length=1, max_length=64)
    device_name: str | None = Field(default=None, max_length=64)


async def _user_with_profile(
    session: AsyncSession, user: User
) -> tuple[dict, MerchantProfile | None]:
    profile = None
    if user.role == "merchant":
        profile = await session.get(MerchantProfile, user.id)
    return user_public(user, profile), profile


async def _evict_oldest_devices(
    session: AsyncSession, user_id: int, count: int
) -> None:
    """踢掉最老的若干台设备，并吊销它们名下的 refresh_token。"""
    if count <= 0:
        return
    victim_ids = (
        await session.scalars(
            select(UserDevice.id)
            .where(UserDevice.user_id == user_id)
            .order_by(UserDevice.last_active_at.asc(), UserDevice.id.asc())
            .limit(count)
        )
    ).all()
    if not victim_ids:
        return
    # 行要真的**删掉**：spec/测试认的是 user_device 的行数降回上限，
    # 只标 revoked 的话行还在，数量会一直涨。
    await session.execute(
        update(RefreshToken)
        .where(RefreshToken.device_id.in_(victim_ids))
        .values(revoked=True)
    )
    await session.execute(delete(UserDevice).where(UserDevice.id.in_(victim_ids)))


async def upsert_device(
    session: AsyncSession,
    user_id: int,
    fingerprint: str,
    device_name: str | None,
    now: datetime,
) -> UserDevice:
    """同一 (user, fingerprint) 复用一行；新增时保证总数不超过上限。"""
    settings = get_settings()
    device = await session.scalar(
        select(UserDevice).where(
            UserDevice.user_id == user_id,
            UserDevice.device_fingerprint == fingerprint,
        )
    )
    if device is not None:
        device.last_active_at = now
        if device_name:
            device.device_name = device_name
        return device

    existing = await session.scalar(
        select(func.count())
        .select_from(UserDevice)
        .where(UserDevice.user_id == user_id)
    )
    # 加上这一台后会变成 existing + 1，超出的部分要踢掉
    await _evict_oldest_devices(
        session, user_id, existing + 1 - settings.max_devices_per_user
    )

    device = UserDevice(
        user_id=user_id,
        device_fingerprint=fingerprint,
        device_name=device_name,
        last_active_at=now,
        revoked=False,
    )
    session.add(device)
    await session.flush()
    return device


async def _register_failure(
    session: AsyncSession, identifier: str, now: datetime
) -> None:
    settings = get_settings()
    attempt = await session.get(LoginAttempt, identifier)
    if attempt is None:
        attempt = LoginAttempt(identifier=identifier, fail_count=0)
        session.add(attempt)
    attempt.fail_count += 1
    if attempt.fail_count >= MAX_LOGIN_FAILURES:
        attempt.locked_until = now + timedelta(
            seconds=settings.login_lock_seconds
        )
    await session.commit()


@router.post("/login")
async def login(
    payload: LoginIn, session: AsyncSession = Depends(get_session)
) -> dict:
    identifier = payload.identifier.strip().lower()
    now = datetime.now(timezone.utc)

    attempt = await session.get(LoginAttempt, identifier)
    if attempt and attempt.locked_until and attempt.locked_until > now:
        raise HTTPException(423, "登录尝试过多，请 15 分钟后再试")

    user = await session.scalar(
        select(User).where(
            or_(User.account == identifier, User.email == identifier)
        )
    )
    if user is None:
        # 查不到人时还要区分「注销过」与「从没存在过」：前者 403，后者 404。
        # 存活用户优先，所以标识符被新用户重新注册后走上面的正常分支。
        closed = await session.get(ClosedIdentifier, identifier)
        if closed is not None:
            raise HTTPException(403, "账号已注销")
        raise HTTPException(404, "用户不存在")
    if user.status != "active":
        # banned 与 deleted 都是 403，不区分（避免泄露账号状态细节）
        raise HTTPException(403, "账号不可用")

    if not verify_password(payload.password, user.password_hash):
        await _register_failure(session, identifier, now)
        raise HTTPException(401, "密码错误")

    # 登录成功：清空该标识符的错误计数
    if attempt is not None:
        attempt.fail_count = 0
        attempt.locked_until = None

    user.last_login_at = now
    device = await upsert_device(
        session, user.id, payload.device_fingerprint, payload.device_name, now
    )
    refresh_plain = issue_refresh_token(session, user.id, device.id)
    await session.commit()
    await session.refresh(user)

    body, _ = await _user_with_profile(session, user)
    return {
        "access_token": create_access_token(user.id, device_id=device.id),
        "refresh_token": refresh_plain,
        "user": body,
    }


# --------------------------------------------------------------------------- #
# 刷新 / 登出
# --------------------------------------------------------------------------- #
class RefreshIn(BaseModel):
    refresh_token: str = Field(min_length=1)


@router.post("/refresh")
async def refresh(
    payload: RefreshIn, session: AsyncSession = Depends(get_session)
) -> dict:
    now = datetime.now(timezone.utc)
    row = await session.scalar(
        select(RefreshToken).where(
            RefreshToken.token_hash == hash_refresh_token(payload.refresh_token)
        )
    )
    if row is None or row.revoked or row.expires_at <= now:
        raise HTTPException(401, "刷新令牌无效")

    device = await session.get(UserDevice, row.device_id)
    if device is None or device.revoked:
        raise HTTPException(401, "设备已下线")

    user = await session.get(User, row.user_id)
    if user is None or user.status != "active":
        raise HTTPException(401, "账号不可用")

    # 轮换：旧的立刻作废，用旧的重放一律 401
    row.revoked = True
    new_plain = issue_refresh_token(session, user.id, device.id)
    await session.commit()

    return {
        "access_token": create_access_token(user.id, device_id=device.id),
        "refresh_token": new_plain,
    }


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> None:
    """只吊销**当前设备**名下的 refresh_token，其他设备不受影响。"""
    if auth.device_id is not None:
        await session.execute(
            update(RefreshToken)
            .where(RefreshToken.device_id == auth.device_id)
            .values(revoked=True)
        )
        await session.commit()
