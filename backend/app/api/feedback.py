"""06 追加 · 反馈提交（`POST /api/feedback`）——商家 / 用户侧的。

## `role` 由服务端推导，客户端说了不算

请求体只收 `category` / `content` / `contact`。带了 `role` 或 `user_id` 一律
`422`（`extra="forbid"`），**不是「忽略掉照样存」**——忽略就等于留了一条
「客户端以为自己能指定身份」的活路，而身份恰恰是列表分类的依据。
这是全局约定 #11（付费方服务端推导）的同款。

`role` 存的是**提交那一刻的快照**，不是外键实时推导：列表要按角色筛，
实时推导会让筛出来的结果随时间漂移。详见 `models/admin.py::UserFeedback`。

## 为什么不用 `require_merchant` / `require_customer`

两个身份走同一个端点，闸口只有一条：`role` 得在 `FEEDBACK_ROLES` 里。
`admin` 落到这里说明后台调错了端点，故 `403`；已注销 / 已封禁由
`get_current_auth` 在更前面就拦掉了。
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import AuthContext, get_current_auth
from app.db import get_session
from app.models.admin import (
    FEEDBACK_CATEGORIES,
    FEEDBACK_MAX_CONTACT,
    FEEDBACK_MAX_CONTENT,
    FEEDBACK_MIN_CONTENT,
    FEEDBACK_ROLES,
    UserFeedback,
)

router = APIRouter(prefix="/api/feedback", tags=["feedback"])


class FeedbackIn(BaseModel):
    # `forbid` 是「身份由服务端推导」这条规则的执行点：`role` / `user_id`
    # 进来就是一次 422，而不是被悄悄丢掉。
    model_config = ConfigDict(extra="forbid")

    category: str
    content: str
    contact: str | None = Field(default=None, max_length=FEEDBACK_MAX_CONTACT)

    @field_validator("category")
    @classmethod
    def _check_category(cls, value: str) -> str:
        if value not in FEEDBACK_CATEGORIES:
            raise ValueError(f"category 只能是 {FEEDBACK_CATEGORIES}")
        return value

    @field_validator("content")
    @classmethod
    def _check_content(cls, value: str) -> str:
        """按**去首尾空白后**的字数判——全空白因此自然落进下界之外。

        存的是 `strip()` 之后的值：留着首尾空白只会在列表里看起来像
        「这条怎么是空的」。
        """
        stripped = value.strip()
        if not FEEDBACK_MIN_CONTENT <= len(stripped) <= FEEDBACK_MAX_CONTENT:
            raise ValueError(
                f"content 需为 {FEEDBACK_MIN_CONTENT} ~ {FEEDBACK_MAX_CONTENT} 字"
            )
        return stripped


@router.post("", status_code=201)
async def submit_feedback(
    payload: FeedbackIn,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> dict:
    role = auth.user.role
    if role not in FEEDBACK_ROLES:
        raise HTTPException(403, "仅商家与用户可提交反馈")

    row = UserFeedback(
        user_id=auth.user.id,
        role=role,
        category=payload.category,
        content=payload.content,
        contact=payload.contact,
        status="open",
        # 显式给时间戳：`server_default` 要等一次回读，而异步会话里的
        # 隐式回读会抛 `MissingGreenlet`
        created_at=datetime.now(timezone.utc),
    )
    session.add(row)
    await session.commit()

    # 键**恰好**六个，不含 `user_id` / `status`——提交方是它自己，
    # 回一个 `user_id` 只会让前端误以为身份可以由客户端传。
    return {
        "id": row.id,
        "role": row.role,
        "category": row.category,
        "content": row.content,
        "contact": row.contact,
        "created_at": row.created_at,
    }
