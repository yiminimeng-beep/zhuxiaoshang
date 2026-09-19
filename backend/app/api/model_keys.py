"""BYOK 密钥的用户侧端点（`/api/me/model-keys`）。

**请求体用裸 `dict` 收，不用 Pydantic 模型。** 这不是偷懒，是安全要求：
Pydantic 校验失败时，FastAPI 生成的 `422` 会把 `input`（整个请求体）原样
回给客户端——而这个请求体里带着**明文 API Key**。等于我们自己造了一条
「错误回显泄漏密钥」的通道（`K-16` 就盯着这里）。

手写校验的代价是错误信息要自己写，好处是**回什么由我们说了算**：
只报字段名，绝不带字段值。
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Body, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import AuthContext, get_current_auth
from app.db import get_session
from app.models.quota import KEY_STATUSES, PROVIDERS, UserModelKey
from app.services import model_key as model_key_service
from app.services.model_key import (
    CURRENT_KEY_VERSION,
    FORBIDDEN_BODY_KEYS,
    encrypt_key,
    mask_key,
)

router = APIRouter(prefix="/api/me/model-keys", tags=["model-keys"])

_ALLOWED_BODY_KEYS = ("provider", "api_key", "label")


def _reject_extra_keys(body: dict, allowed: tuple[str, ...]) -> None:
    """只报字段名，不回显值——请求体里有明文密钥。"""
    for key in body:
        if key in FORBIDDEN_BODY_KEYS:
            raise HTTPException(
                422, f"不接受字段 {key}：自定义地址等于把密钥打到任意地方"
            )
    unknown = [k for k in body if k not in allowed]
    if unknown:
        raise HTTPException(422, f"不认识的字段：{', '.join(sorted(unknown))}")


def _require_str(body: dict, key: str, *, max_len: int | None = None) -> str:
    value = body.get(key)
    if not isinstance(value, str) or not value.strip():
        raise HTTPException(422, f"{key} 必填且必须是非空字符串")
    if max_len is not None and len(value) > max_len:
        raise HTTPException(422, f"{key} 过长")
    return value


def key_public(key: UserModelKey) -> dict:
    return {
        "id": key.id,
        "provider": key.provider,
        "label": key.label,
        "key_masked": key.key_masked,
        "status": key.status,
        "last_verified_at": key.last_verified_at,
        "last_used_at": key.last_used_at,
    }


async def _mine(session: AsyncSession, user_id: int, key_id: int) -> UserModelKey:
    """取自己的密钥。别人的一律 403 而不是 404——404 会变成 id 枚举接口。"""
    key = await session.get(UserModelKey, key_id)
    if key is None:
        raise HTTPException(404, "密钥不存在")
    if key.user_id != user_id:
        raise HTTPException(403, "无权操作该密钥")
    return key


@router.get("")
async def list_keys(
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> dict:
    rows = (
        (
            await session.execute(
                select(UserModelKey)
                .where(UserModelKey.user_id == auth.user.id)
                .order_by(UserModelKey.id)
            )
        )
        .scalars()
        .all()
    )
    return {"items": [key_public(k) for k in rows]}


@router.post("", status_code=201)
async def create_key(
    body: dict = Body(...),
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> dict:
    _reject_extra_keys(body, _ALLOWED_BODY_KEYS)

    provider = _require_str(body, "provider")
    if provider not in PROVIDERS:
        raise HTTPException(422, f"不支持的 provider：{provider}")

    api_key = _require_str(body, "api_key", max_len=512)
    label = body.get("label")
    if label is not None and (not isinstance(label, str) or len(label) > 32):
        raise HTTPException(422, "label 必须是不超过 32 字的字符串")

    existing = (
        await session.execute(
            select(UserModelKey).where(
                UserModelKey.user_id == auth.user.id,
                UserModelKey.provider == provider,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(409, "该 provider 已有密钥，请改用 PATCH 更换")

    # 先验后用：没过校验的 Key 一行都不落，否则列表里会出现一把永远调不通的钥匙
    if not await model_key_service.verify_provider_key(provider, api_key):
        raise HTTPException(422, "密钥校验失败，请检查后重试")

    now = datetime.now(timezone.utc)
    key = UserModelKey(
        user_id=auth.user.id,
        provider=provider,
        label=label,
        key_cipher=encrypt_key(api_key),
        key_masked=mask_key(api_key),
        key_version=CURRENT_KEY_VERSION,
        status="active",
        fail_count=0,
        last_verified_at=now,
        created_at=now,
        updated_at=now,
    )
    session.add(key)
    await session.commit()
    await session.refresh(key)
    return {
        "id": key.id,
        "provider": key.provider,
        "key_masked": key.key_masked,
        "status": key.status,
    }


@router.patch("/{key_id}")
async def update_key(
    key_id: int,
    body: dict = Body(...),
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> dict:
    _reject_extra_keys(body, ("api_key", "label", "status"))
    key = await _mine(session, auth.user.id, key_id)

    if "api_key" in body:
        api_key = _require_str(body, "api_key", max_len=512)
        if not await model_key_service.verify_provider_key(key.provider, api_key):
            raise HTTPException(422, "密钥校验失败，请检查后重试")
        key.key_cipher = encrypt_key(api_key)
        key.key_masked = mask_key(api_key)
        key.key_version = CURRENT_KEY_VERSION
        key.last_verified_at = datetime.now(timezone.utc)
        # 换了 Key 就把失败计数清零：那是上一把钥匙的账
        key.fail_count = 0
        key.status = "active"

    if "label" in body:
        label = body["label"]
        if label is not None and (not isinstance(label, str) or len(label) > 32):
            raise HTTPException(422, "label 必须是不超过 32 字的字符串")
        key.label = label

    if "status" in body:
        if body["status"] not in KEY_STATUSES:
            raise HTTPException(422, "status 非法")
        key.status = body["status"]

    key.updated_at = datetime.now(timezone.utc)
    await session.commit()
    await session.refresh(key)
    return {
        "id": key.id,
        "provider": key.provider,
        "label": key.label,
        "key_masked": key.key_masked,
        "status": key.status,
    }


@router.delete("/{key_id}", status_code=204)
async def delete_key(
    key_id: int,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> Response:
    key = await _mine(session, auth.user.id, key_id)
    await session.delete(key)
    await session.commit()
    return Response(status_code=204)


@router.post("/{key_id}/verify")
async def verify_key(
    key_id: int,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> dict:
    from app.services.model_key import decrypt_key

    key = await _mine(session, auth.user.id, key_id)
    plaintext = decrypt_key(key.key_cipher, key.key_version)

    if not await model_key_service.verify_provider_key(key.provider, plaintext):
        key.fail_count += 1
        if key.fail_count >= 3:
            key.status = "disabled"
        key.updated_at = datetime.now(timezone.utc)
        await session.commit()
        raise HTTPException(422, "密钥校验失败")

    key.last_verified_at = datetime.now(timezone.utc)
    key.fail_count = 0
    if key.status == "invalid":
        key.status = "active"
    key.updated_at = key.last_verified_at
    await session.commit()
    await session.refresh(key)
    return {"status": key.status, "last_verified_at": key.last_verified_at}
