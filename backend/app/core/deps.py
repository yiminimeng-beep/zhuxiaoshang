"""鉴权依赖：解 token → 取用户 → 校验状态与角色。"""

from dataclasses import dataclass

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import decode_access_token
from app.db import get_session
from app.models.user import User


@dataclass(frozen=True)
class AuthContext:
    """当前请求的调用方。`device_id` 来自令牌，是设备级吊销的依据。"""

    user: User
    device_id: int | None

_bearer = HTTPBearer(auto_error=False)

_UNAUTHORIZED = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="未登录或令牌无效",
    headers={"WWW-Authenticate": "Bearer"},
)


async def get_current_auth(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    session: AsyncSession = Depends(get_session),
) -> "AuthContext":
    if creds is None or not creds.credentials:
        raise _UNAUTHORIZED

    claims = decode_access_token(creds.credentials)
    if claims is None:
        raise _UNAUTHORIZED

    user = await session.get(User, claims["user_id"])
    if user is None:
        raise _UNAUTHORIZED

    # 被封禁 / 已注销：即便 token 本身有效也不放行
    if user.status != "active":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="账号不可用",
        )
    return AuthContext(user=user, device_id=claims["device_id"])


async def get_optional_auth(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    session: AsyncSession = Depends(get_session),
) -> "AuthContext | None":
    """公开端点用：带了有效令牌就返回调用方，没带/无效就当匿名。

    任务列表与详情对游客开放，但登录后要能多返回 `claimed_by_me` 这类个性化字段。
    令牌无效**不报错**——游客不该因为一个过期令牌就看不了公开列表。
    """
    if creds is None or not creds.credentials:
        return None

    claims = decode_access_token(creds.credentials)
    if claims is None:
        return None

    user = await session.get(User, claims["user_id"])
    if user is None or user.status != "active":
        return None
    return AuthContext(user=user, device_id=claims["device_id"])


async def get_current_user(
    auth: "AuthContext" = Depends(get_current_auth),
) -> User:
    return auth.user


async def require_customer(user: User = Depends(get_current_user)) -> User:
    if user.role != "customer":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="仅限客户")
    return user


async def require_merchant(user: User = Depends(get_current_user)) -> User:
    if user.role != "merchant":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="仅限商户")
    return user


def require_admin(auth: "AuthContext" = Depends(get_current_auth)) -> "AuthContext":
    """平台后台闸。非 admin **一律 403，绝不用 404**。

    404 会把「这个 id 存不存在」透出去，等于送一个 id 枚举接口。返回
    `AuthContext` 而不是 `User`：审计日志要记 `auth.user.id`，回一个 ORM 对象
    只会让每个调用方再查一次库。
    """
    if auth.user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="仅限管理员")
    return auth
