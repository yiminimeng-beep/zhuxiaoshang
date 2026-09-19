"""03-studio · prompt 模板（商户预设 / 模板市场）。

## 可见性三条规则

| 模板状态 | 谁能看见 |
|---|---|
| `is_public=true` | 所有已登录用户（模板市场） |
| `is_public=false` | **创建者商户本人** + 该商户任务下的领取者 |
| `deleted_at` 非空 | 谁都不行——**但已经复制走的副本不受影响** |

最后那句是「复制」与「引用」的分水岭：复制是**拿走一份内容**（`source_template_id`
只作溯源），不是挂个指针。指针的话，源模板一删，别人手里的稿子就没了。

## 两个坑

- **`usage_count` 必须用 `UPDATE ... SET usage_count = usage_count + 1`**，
  不能「读出来 +1 写回去」。100 个并发套用时后者会丢更新（典型只到 30~70），
  而「模板热度」正是靠这个数排的（`TP-25`）。
- **套用不改 job 状态**。模板只是给用户的**草稿起点**，他还得自己编辑再复写；
  顺手把 job 推到 `generating` 等于替用户做了决定（`TP-28`）。
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.merchant_task import require_merchant_role
from app.core.deps import AuthContext, get_current_auth
from app.db import get_session
from app.models.studio import (
    MAX_TAG_CHARS,
    MAX_TEMPLATE_TAGS,
    ContentJob,
    PromptTemplate,
)
from app.models.task import Task, TaskClaim
from app.models.user import User

router = APIRouter(tags=["studio-template"])

Tag = Annotated[str, StringConstraints(max_length=MAX_TAG_CHARS)]

# 套用模板只发生在「商量提示词」这一步。生成中/已出结果都不该再套
APPLYABLE_STATUSES = ("chatting",)


# --------------------------------------------------------------------------- #
# 入参
# --------------------------------------------------------------------------- #
class TemplateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=2, max_length=64)
    content: str = Field(min_length=10, max_length=2000)
    category: str | None = None
    tags: list[Tag] | None = Field(default=None, max_length=MAX_TEMPLATE_TAGS)
    is_public: bool = False


class TemplatePatchIn(BaseModel):
    """PATCH 是**部分更新**：没传的字段保持原样，不是「清空」。"""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=2, max_length=64)
    content: str | None = Field(default=None, min_length=10, max_length=2000)
    category: str | None = None
    tags: list[Tag] | None = Field(default=None, max_length=MAX_TEMPLATE_TAGS)
    is_public: bool | None = None


class ApplyIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: int


# --------------------------------------------------------------------------- #
# 序列化
# --------------------------------------------------------------------------- #
def template_public(tpl: PromptTemplate) -> dict:
    return {
        "id": tpl.id,
        "merchant_id": tpl.merchant_id,
        "name": tpl.name,
        "content": tpl.content,
        "category": tpl.category,
        "tags": tpl.tags,
        "is_public": tpl.is_public,
        "source_template_id": tpl.source_template_id,
        "usage_count": tpl.usage_count,
        "created_at": tpl.created_at,
        "updated_at": tpl.updated_at,
    }


def _paged(items: list[PromptTemplate], total: int, page: int, size: int) -> dict:
    return {
        "items": [template_public(t) for t in items],
        "total": total,
        "page": page,
        "size": size,
    }


async def _count(session: AsyncSession, *conditions) -> int:
    return int(
        await session.scalar(
            select(func.count()).select_from(PromptTemplate).where(*conditions)
        )
        or 0
    )


# --------------------------------------------------------------------------- #
# 商户侧：建 / 改 / 删 / 我的
# --------------------------------------------------------------------------- #
@router.post(
    "/api/merchant/prompt-templates", status_code=status.HTTP_201_CREATED
)
async def create_template(
    payload: TemplateIn,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
):
    merchant = require_merchant_role(auth)
    tpl = PromptTemplate(
        merchant_id=merchant.id,
        name=payload.name,
        content=payload.content,
        category=payload.category,
        tags=payload.tags,
        is_public=payload.is_public,
        usage_count=0,
    )
    session.add(tpl)
    await session.commit()
    return {"template": template_public(tpl)}


async def _load_own_template(
    session: AsyncSession, template_id: int, merchant: User
) -> PromptTemplate:
    """取自己的模板。不存在 / 已软删 → 404；是别人的 → 403。"""
    tpl = await session.get(PromptTemplate, template_id)
    if tpl is None or tpl.deleted_at is not None:
        raise HTTPException(404, "模板不存在")
    if tpl.merchant_id != merchant.id:
        raise HTTPException(403, "无权操作该模板")
    return tpl


@router.patch("/api/merchant/prompt-templates/{template_id}")
async def patch_template(
    template_id: int,
    payload: TemplatePatchIn,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
):
    merchant = require_merchant_role(auth)
    tpl = await _load_own_template(session, template_id, merchant)

    for field in ("name", "content", "category", "tags", "is_public"):
        value = getattr(payload, field)
        if value is not None:
            setattr(tpl, field, value)
    await session.commit()
    return {"template": template_public(tpl)}


@router.delete(
    "/api/merchant/prompt-templates/{template_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_template(
    template_id: int,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
):
    merchant = require_merchant_role(auth)
    tpl = await _load_own_template(session, template_id, merchant)

    # 软删：`prompt_draft` 可能引用过它的内容，物理删会留下断链
    tpl.deleted_at = func.now()
    await session.commit()


@router.get("/api/merchant/prompt-templates")
async def my_templates(
    keyword: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
):
    merchant = require_merchant_role(auth)
    conditions = [
        PromptTemplate.merchant_id == merchant.id,
        PromptTemplate.deleted_at.is_(None),
    ]
    if keyword:
        conditions.append(PromptTemplate.name.ilike(f"%{keyword}%"))

    total = await _count(session, *conditions)
    rows = list(
        await session.scalars(
            select(PromptTemplate)
            .where(*conditions)
            .order_by(PromptTemplate.id.desc())
            .offset((page - 1) * size)
            .limit(size)
        )
    )
    return _paged(rows, total, page, size)


# --------------------------------------------------------------------------- #
# 模板市场 / 某任务可用模板
# --------------------------------------------------------------------------- #
@router.get("/api/prompt-templates")
async def template_market(
    category: str | None = Query(default=None),
    keyword: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
):
    conditions = [
        PromptTemplate.is_public.is_(True),
        PromptTemplate.deleted_at.is_(None),
    ]
    if category:
        conditions.append(PromptTemplate.category == category)
    if keyword:
        conditions.append(PromptTemplate.name.ilike(f"%{keyword}%"))

    total = await _count(session, *conditions)
    rows = list(
        await session.scalars(
            select(PromptTemplate)
            .where(*conditions)
            .order_by(PromptTemplate.usage_count.desc(), PromptTemplate.id.desc())
            .offset((page - 1) * size)
            .limit(size)
        )
    )
    return _paged(rows, total, page, size)


@router.get("/api/tasks/{task_id}/prompt-templates")
async def task_templates(
    task_id: int,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
):
    """该任务下可用的模板：**该商户的私有模板 + 全部公开模板**。

    没领取这个任务就看不到私有部分——私有模板是商户给自己的客户准备的，
    不该让任何路过的人翻到。
    """
    task = await session.get(Task, task_id)
    if task is None or task.deleted_at is not None:
        raise HTTPException(404, "任务不存在")

    claim = await session.scalar(
        select(TaskClaim).where(
            TaskClaim.task_id == task_id,
            TaskClaim.user_id == auth.user.id,
            TaskClaim.status != "closed",
        )
    )
    if claim is None:
        raise HTTPException(403, "请先领取该任务")

    conditions = [
        PromptTemplate.deleted_at.is_(None),
        (PromptTemplate.is_public.is_(True))
        | (PromptTemplate.merchant_id == task.merchant_id),
    ]
    rows = list(
        await session.scalars(
            select(PromptTemplate)
            .where(*conditions)
            .order_by(PromptTemplate.id.desc())
        )
    )
    return _paged(rows, len(rows), 1, len(rows) or 1)


# --------------------------------------------------------------------------- #
# 复制
# --------------------------------------------------------------------------- #
@router.post(
    "/api/merchant/prompt-templates/{template_id}/copy",
    status_code=status.HTTP_201_CREATED,
)
async def copy_template(
    template_id: int,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
):
    """从市场复制一份到自己的模板库。

    源**必须是公开且未删**的，否则 404——私有模板是别人的家底，
    「复制」这条路径不该成为绕过可见性的后门。
    """
    merchant = require_merchant_role(auth)
    src = await session.get(PromptTemplate, template_id)
    if src is None or src.deleted_at is not None or not src.is_public:
        raise HTTPException(404, "模板不存在或未公开")

    copy = PromptTemplate(
        merchant_id=merchant.id,
        name=src.name,
        content=src.content,
        category=src.category,
        tags=src.tags,
        # 副本默认私有：转手再公开是本人有意的决定，不该由复制行为代劳
        is_public=False,
        source_template_id=src.id,
        # 不继承使用数：那是**原模板**的热度，算到副本头上会让排行失真
        usage_count=0,
    )
    session.add(copy)
    await session.commit()
    return {"template": template_public(copy)}


# --------------------------------------------------------------------------- #
# 套用
# --------------------------------------------------------------------------- #
@router.post("/api/prompt-templates/{template_id}/apply")
async def apply_template(
    template_id: int,
    payload: ApplyIn,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
):
    tpl = await session.get(PromptTemplate, template_id)
    if tpl is None or tpl.deleted_at is not None:
        raise HTTPException(404, "模板不存在")

    job = await session.get(ContentJob, payload.job_id)
    if job is None or job.deleted_at is not None:
        raise HTTPException(404, "job 不存在")
    if job.user_id != auth.user.id:
        raise HTTPException(403, "无权操作该 job")

    task = await session.get(Task, job.task_id)
    # 有权使用 = 公开，或者**就是本 job 所属商户自己给的**私有模板
    if not tpl.is_public and tpl.merchant_id != task.merchant_id:
        raise HTTPException(403, "无权使用该模板")

    if job.status not in APPLYABLE_STATUSES:
        raise HTTPException(409, f"当前状态（{job.status}）不能套用模板")

    # 原子自增：读改写会在并发下丢更新（TP-25）
    await session.execute(
        PromptTemplate.__table__.update()
        .where(PromptTemplate.__table__.c.id == template_id)
        .values(usage_count=PromptTemplate.__table__.c.usage_count + 1)
    )
    await session.commit()
    return {"content": tpl.content, "name": tpl.name}
