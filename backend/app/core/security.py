"""口令哈希与 JWT。

口径（见 specs/01-auth/spec.md）：
- 口令用 **argon2id**（argon2-cffi 的默认算法），与测试侧 `seed_accounts` 直插的哈希同源。
- access_token 是无状态 JWT；refresh_token 是随机串，**只存 sha256**，明文只在签发时返回一次。
"""

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

import jwt
from argon2 import PasswordHasher

from app.config import get_settings

_hasher = PasswordHasher()


def hash_password(plain: str) -> str:
    return _hasher.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return _hasher.verify(hashed, plain)
    except Exception:
        return False


def hash_needs_rehash(hashed: str) -> bool:
    try:
        return _hasher.check_needs_rehash(hashed)
    except Exception:
        return False


def create_access_token(
    user_id: int,
    expires_delta: timedelta | None = None,
    device_id: int | None = None,
) -> str:
    """`device_id` 进 payload 是必须的：登出、改密码都要按设备粒度吊销，
    而请求头里只有令牌，服务端只能从令牌里读当前设备。"""
    settings = get_settings()
    if expires_delta is None:
        expires_delta = timedelta(seconds=settings.access_token_ttl_seconds)
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "type": "access",
        "iat": now,
        "exp": now + expires_delta,
    }
    if device_id is not None:
        payload["did"] = int(device_id)
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> dict | None:
    """解出 `{"user_id", "device_id"}`；无效/过期/类型不对一律 None（调用方转 401）。"""
    settings = get_settings()
    try:
        payload = jwt.decode(
            token, settings.jwt_secret, algorithms=[settings.jwt_algorithm]
        )
    except jwt.PyJWTError:
        return None
    if payload.get("type") != "access":
        return None
    try:
        user_id = int(payload["sub"])
    except (KeyError, TypeError, ValueError):
        return None
    device_id = payload.get("did")
    return {
        "user_id": user_id,
        "device_id": int(device_id) if device_id is not None else None,
    }


def new_refresh_token() -> str:
    return secrets.token_urlsafe(48)


def hash_refresh_token(plain: str) -> str:
    return hashlib.sha256(plain.encode("utf-8")).hexdigest()


def unusable_password_hash() -> str:
    """注销时替换成一个哈希——随机的，谁也登不进去。"""
    return hash_password(secrets.token_urlsafe(32))
