"""02-task · 商户侧：建任务、配奖励规则、发布、回收站。

## 状态机

```
draft ──publish──► published ──pause──► paused ──publish──► published
                                            └──close──► closed（终态）
```

`closed` 不可回到任何状态；`paused` 允许恢复成 `published`。

## 软删

`DELETE` 只写 `deleted_at`，**不动 `status`**——恢复后任务回到删除前的状态。
子表（`reward_rule` / `task_claim`）一律保留，回收站只能恢复、不能销毁。
"""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.task_serializers import (
    reward_rule_public,
    task_public,
    utcnow,
    validate_tiers,
)
from app.core.deps import AuthContext, get_current_auth
from app.db import get_session
from app.models.task import PAY_MODES, RewardRule, Task, TaskClaim
from app.models.user import User

router = APIRouter(prefix="/api/merchant", tags=["merchant-task"])

MAX_TAGS = 5
MAX_TAG_LEN = 16
MAX_PAGE_SIZE = 100

# 发布后允许修改的字段（spec「published 任务改 title → 409」）
PUBLISHED_EDITABLE = ("description", "end_at", "requirement")


# --------------------------------------------------------------------------- #
# 入参
# --------------------------------------------------------------------------- #
def _check_tags(tags: list[str] | None) -> list[str] | None:
    if tags is None:
        return None
    if len(tags) > MAX_TAGS:
        raise ValueError(f"tags 最多 {MAX_TAGS} 个")
    for tag in tags:
        if not isinstance(tag, str) or not (1 <= len(tag) <= MAX_TAG_LEN):
            raise ValueError(f"每个 tag 必须 1~{MAX_TAG_LEN} 字符")
    return tags


class TaskCreateIn(BaseModel):
    model_config = ConfigDict(extra="allow")

    title: str = Field(min_length=2, max_length=64)
    description: str = Field(min_length=10, max_length=2000)
    category: str = Field(min_length=1, max_length=32)
    cover_url: str | None = Field(default=None, max_length=512)
    requirement: str | None = None
    tags: list[str] | None = None
    start_at: datetime
    end_at: datetime
    quota: int | None = None

    pay_mode: str | None = None
    reimburse_pool: int | None = None
    reimburse_per_user_limit: int | None = None

    @field_validator("tags")
    @classmethod
    def _tags(cls, v):
        return _check_tags(v)

    @model_validator(mode="after")
    def _time_window(self) -> "TaskCreateIn":
        if self.end_at <= self.start_at:
            raise ValueError("end_at 必须晚于 start_at")
        # spec 给 1 秒容差：请求体里的 start_at 常是客户端刚取的时间，
        # 到服务端比对自己 now() 时已经过去了几十毫秒，不该因此被拒。
        if self.start_at < datetime.now(timezone.utc) - timedelta(seconds=1):
            raise ValueError("start_at 不得早于当前时间")
        if self.quota is not None and self.quota < 1:
            raise ValueError("quota 必须 >= 1 或为 null")
        return self


class TaskPatchIn(BaseModel):
    model_config = ConfigDict(extra="allow")

    title: str | None = Field(default=None, min_length=2, max_length=64)
    description: str | None = Field(default=None, min_length=10, max_length=2000)
    category: str | None = Field(default=None, min_length=1, max_length=32)
    cover_url: str | None = Field(default=None, max_length=512)
    requirement: str | None = None
    tags: list[str] | None = None
    start_at: datetime | None = None
    end_at: datetime | None = None
    quota: int | None = None
    pay_mode: str | None = None
    reimburse_pool: int | None = None
    reimburse_per_user_limit: int | None = None

    @field_validator("tags")
    @classmethod
    def _tags(cls, v):
        return _check_tags(v)

    @model_validator(mode="after")
    def _quota(self) -> "TaskPatchIn":
        if self.quota is not None and self.quota < 1:
            raise ValueError("quota 必须 >= 1 或为 null")
        return self


class RewardRuleIn(BaseModel):
    metric: str = "engagement"
    tiers: list = Field(default_factory=list)
    max_reward_per_user: int | None = None


# --------------------------------------------------------------------------- #
# 通用守卫
# --------------------------------------------------------------------------- #
def require_merchant_role(auth: AuthContext) -> User:
    if auth.user.role != "merchant":
        raise HTTPException(403, "仅限商户")
    return auth.user


async def _load_owned_task(
    session: AsyncSession, task_id: int, merchant: User
) -> Task:
    """取任务并校验归属。不存在 → 404；不是本人的 → 403（不伪装成 404）。"""
    task = await session.get(Task, task_id)
    if task is None:
        raise HTTPException(404, "任务不存在")
    if task.merchant_id != merchant.id:
        raise HTTPException(403, "无权操作该任务")
    return task


def _apply_pay_mode(payload, target: Task) -> None:
    """付费模式与报销池的校验与落值（spec「付费模式」整组）。"""
    if payload.pay_mode is None:
        mode = target.pay_mode or "merchant_pay"
    else:
        mode = payload.pay_mode
        if mode not in PAY_MODES:
            raise HTTPException(422, f"pay_mode 非法，只接受 {PAY_MODES}")

    pool = (
        payload.reimburse_pool
        if payload.reimburse_pool is not None
        else target.reimburse_pool
    )
    limit = (
        payload.reimburse_per_user_limit
        if payload.reimburse_per_user_limit is not None
        else target.reimburse_per_user_limit
    )

    if mode == "merchant_pay":
        if payload.reimburse_pool is not None:
            raise HTTPException(422, "merchant_pay 任务不接受 reimburse_pool")
        if payload.reimburse_per_user_limit is not None:
            raise HTTPException(422, "merchant_pay 任务不接受 reimburse_per_user_limit")
    else:
        if pool is None:
            raise HTTPException(422, "user_pay_reimburse 必须提供 reimburse_pool")
        if limit is None:
            raise HTTPException(422, "user_pay_reimburse 必须提供 reimburse_per_user_limit")
        if limit > pool:
            raise HTTPException(422, "reimburse_per_user_limit 不得大于 reimburse_pool")

    target.pay_mode = mode
    target.reimburse_pool = pool
    target.reimburse_per_user_limit = limit


# --------------------------------------------------------------------------- #
# 建 / 改 / 自动保存
# --------------------------------------------------------------------------- #
@router.post("/tasks", status_code=status.HTTP_201_CREATED)
async def create_task(
    payload: TaskCreateIn,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> dict:
    merchant = require_merchant_role(auth)

    task = Task(
        merchant_id=merchant.id,
        title=payload.title,
        description=payload.description,
        cover_url=payload.cover_url,
        category=payload.category,
        requirement=payload.requirement,
        tags=payload.tags,
        start_at=payload.start_at,
        end_at=payload.end_at,
        quota=payload.quota,
        claimed_count=0,
        status="draft",
    )
    _apply_pay_mode(payload, task)

    session.add(task)
    await session.commit()
    await session.refresh(task)
    return {"task": task_public(task)}


@router.get("/tasks")
async def my_tasks(
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> dict:
    merchant = require_merchant_role(auth)
    rows = (
        await session.scalars(
            select(Task)
            .where(Task.merchant_id == merchant.id, Task.deleted_at.is_(None))
            .order_by(Task.created_at.desc(), Task.id.desc())
        )
    ).all()
    return {"items": [task_public(t) for t in rows], "total": len(rows)}


@router.get("/tasks/trash")
async def trash(
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> dict:
    merchant = require_merchant_role(auth)
    rows = (
        await session.scalars(
            select(Task)
            .where(Task.merchant_id == merchant.id, Task.deleted_at.is_not(None))
            .order_by(Task.deleted_at.desc(), Task.id.desc())
        )
    ).all()
    return {"items": [task_public(t) for t in rows], "total": len(rows)}


@router.get("/tasks/{task_id}")
async def merchant_task_detail(
    task_id: int,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> dict:
    merchant = require_merchant_role(auth)
    task = await _load_owned_task(session, task_id, merchant)
    return {"task": task_public(task)}


def _guard_published_edit(task: Task, payload) -> None:
    """已发布任务的字段白名单 + 值约束。"""
    if task.status != "published":
        return

    for field in payload.model_dump(exclude_unset=True):
        if field not in PUBLISHED_EDITABLE and field != "quota":
            raise HTTPException(
                409,
                f"任务已发布，不允许修改 {field}"
                f"（仅可改 {PUBLISHED_EDITABLE} 与 quota）",
            )

    if payload.end_at is not None and payload.end_at <= utcnow():
        raise HTTPException(422, "已发布任务的 end_at 不得早于当前时间")
    if payload.start_at is not None:
        raise HTTPException(409, "已发布任务的 start_at 不可修改")
    if payload.quota is not None and payload.quota < task.claimed_count:
        raise HTTPException(422, "quota 不得小于已领取人数")


def _assign(task: Task, payload) -> None:
    for field in (
        "title",
        "description",
        "category",
        "cover_url",
        "requirement",
        "tags",
        "start_at",
        "end_at",
        "quota",
    ):
        value = getattr(payload, field, None)
        if value is not None:
            setattr(task, field, value)


@router.patch("/tasks/{task_id}")
async def patch_task(
    task_id: int,
    payload: TaskPatchIn,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> dict:
    merchant = require_merchant_role(auth)
    task = await _load_owned_task(session, task_id, merchant)

    _guard_published_edit(task, payload)

    if payload.pay_mode is not None and task.status != "draft":
        raise HTTPException(409, "已发布任务不可中途改 pay_mode（计费口径会错乱）")

    if task.status == "draft":
        _apply_pay_mode(payload, task)
    _assign(task, payload)
    task.updated_at = utcnow()

    await session.commit()
    await session.refresh(task)
    return {"task": task_public(task)}


@router.put("/tasks/{task_id}/draft")
async def autosave_draft(
    task_id: int,
    payload: TaskPatchIn,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """草稿自动保存：**部分更新**，没传的字段保持原值（不能当置空）。

    校验全部在落值**之前**做完——一旦中途 raise，本请求的 session 不会 commit，
    已有内容自然保持原样。
    """
    merchant = require_merchant_role(auth)
    task = await _load_owned_task(session, task_id, merchant)

    if task.status != "draft":
        raise HTTPException(409, "只有草稿可以自动保存")

    if payload.pay_mode is not None:
        _apply_pay_mode(payload, task)

    _assign(task, payload)

    if task.end_at <= task.start_at:
        raise HTTPException(422, "end_at 必须晚于 start_at")

    task.updated_at = utcnow()
    await session.commit()
    await session.refresh(task)
    return {"task": task_public(task), "saved_at": task.updated_at.isoformat()}


# --------------------------------------------------------------------------- #
# 奖励规则
# --------------------------------------------------------------------------- #
async def _rule_owner_task(
    session: AsyncSession, task_id: int, merchant: User
) -> Task:
    return await _load_owned_task(session, task_id, merchant)


@router.post("/reward-rules/{task_id}/validate")
async def validate_reward_rule(
    task_id: int,
    payload: RewardRuleIn,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> dict:
    merchant = require_merchant_role(auth)
    await _rule_owner_task(session, task_id, merchant)

    violations = validate_tiers(payload.tiers)
    if violations:
        raise HTTPException(422, {"violations": violations})
    return {"valid": True}


@router.put("/reward-rules/{task_id}")
async def put_reward_rule(
    task_id: int,
    payload: RewardRuleIn,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> dict:
    merchant = require_merchant_role(auth)
    await _rule_owner_task(session, task_id, merchant)

    violations = validate_tiers(payload.tiers)
    if violations:
        raise HTTPException(422, {"violations": violations})

    rule = await session.scalar(
        select(RewardRule).where(RewardRule.task_id == task_id)
    )
    if rule is None:
        rule = RewardRule(task_id=task_id)
        session.add(rule)

    rule.metric = payload.metric
    rule.tiers = payload.tiers
    rule.max_reward_per_user = payload.max_reward_per_user

    await session.commit()
    await session.refresh(rule)
    return {"rule": reward_rule_public(rule)}


# --------------------------------------------------------------------------- #
# 发布 / 暂停 / 关闭
# --------------------------------------------------------------------------- #
async def _quota_account_exists(session: AsyncSession) -> bool:
    """07-token 的表还没落地时，报销池的额度校验只能优雅跳过。"""
    return bool(await session.scalar(text("SELECT to_regclass('public.quota_account') IS NOT NULL")))


async def _reserve_pool(session: AsyncSession, task: Task) -> None:
    """发布 user_pay_reimburse 任务：把报销池从商户额度里**真实锁住**。

    池子是真金白银，不是空头承诺——锁不住就不许发布（402）。

    **锁的是整个池子，但 `reimburse_pool_reserved` 留在 0**。两者不是一回事：
    前者是「商户该掏的上限」，后者是「已经被某些 job 认领的份额」。
    发布时锁全额，之后每建一个 job 才往 `reimburse_pool_reserved` 里累加
    （`JB-25` 断言的正是这条），于是剩余的池子 = 池子 − 已用 − 已认领。
    若发布时就把 `reimburse_pool_reserved` 填成全额，每建一个 job 就再占一份，
    池子会被重复扣。
    """
    if task.pay_mode != "user_pay_reimburse":
        return
    if not await _quota_account_exists(session):
        return

    row = await session.execute(
        text(
            "SELECT balance, reserved FROM quota_account "
            "WHERE user_id = :uid FOR UPDATE"
        ),
        {"uid": task.merchant_id},
    )
    account = row.first()
    pool = task.reimburse_pool or 0

    if account is None or (account.balance - account.reserved) < pool:
        raise HTTPException(402, "商户可用额度不足以锁定报销池")

    await session.execute(
        text(
            "UPDATE quota_account SET reserved = reserved + :pool WHERE user_id = :uid"
        ),
        {"pool": pool, "uid": task.merchant_id},
    )


async def _release_pool(session: AsyncSession, task: Task) -> None:
    """暂停 / 关闭：把**尚未被花掉**的那部分池子还回去。

    还的是 `池子 − 已用`，不是 `reimburse_pool_reserved`：后者只记「已认领
    但还没结算」的份额，把它当总额会漏掉从未被任何 job 认领的那一大块。
    """
    if task.pay_mode != "user_pay_reimburse":
        return
    if not await _quota_account_exists(session):
        return
    held = (task.reimburse_pool or 0) - (task.reimburse_pool_used or 0)
    held = max(held, 0)
    if held <= 0:
        return

    await session.execute(
        text(
            "UPDATE quota_account SET reserved = GREATEST(reserved - :pool, 0) "
            "WHERE user_id = :uid"
        ),
        {"pool": held, "uid": task.merchant_id},
    )
    task.reimburse_pool_reserved = 0


async def _publish(session: AsyncSession, task: Task) -> None:
    if task.status == "published":
        raise HTTPException(409, "任务已发布")
    if task.status == "closed":
        raise HTTPException(409, "已关闭的任务不可发布")

    rule = await session.scalar(
        select(RewardRule).where(RewardRule.task_id == task.id)
    )
    if rule is None:
        raise HTTPException(409, "尚未配置奖励规则，不能发布")

    if task.end_at <= utcnow():
        raise HTTPException(422, "任务已过期，不能发布")

    await _reserve_pool(session, task)
    task.status = "published"
    task.updated_at = utcnow()


@router.post("/tasks/{task_id}/publish")
async def publish_task(
    task_id: int,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> dict:
    merchant = require_merchant_role(auth)
    task = await _load_owned_task(session, task_id, merchant)

    await _publish(session, task)
    await session.commit()
    return {"status": task.status}


@router.post("/tasks/{task_id}/pause")
async def pause_task(
    task_id: int,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> dict:
    merchant = require_merchant_role(auth)
    task = await _load_owned_task(session, task_id, merchant)

    if task.status != "published":
        raise HTTPException(409, "只有已发布的任务可以暂停")

    await _release_pool(session, task)
    task.status = "paused"
    task.updated_at = utcnow()
    await session.commit()
    return {"status": task.status}


@router.post("/tasks/{task_id}/close")
async def close_task(
    task_id: int,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> dict:
    merchant = require_merchant_role(auth)
    task = await _load_owned_task(session, task_id, merchant)

    if task.status not in ("published", "paused"):
        raise HTTPException(409, "只有已发布或已暂停的任务可以关闭")

    await _release_pool(session, task)
    task.status = "closed"
    task.updated_at = utcnow()
    await session.commit()
    return {"status": task.status}


# --------------------------------------------------------------------------- #
# 删除（进回收站）与恢复
# --------------------------------------------------------------------------- #
@router.delete("/tasks/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_task(
    task_id: int,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> None:
    merchant = require_merchant_role(auth)
    task = await _load_owned_task(session, task_id, merchant)

    if task.deleted_at is not None:
        raise HTTPException(404, "任务不在回收站之外")

    if task.status == "published":
        raise HTTPException(409, "已发布的任务需先关闭才能删除")

    active = await session.scalar(
        select(func.count())
        .select_from(TaskClaim)
        .where(TaskClaim.task_id == task.id, TaskClaim.status != "closed")
    )
    if active:
        raise HTTPException(409, "任务已有有效领取，不能删除")

    task.deleted_at = utcnow()
    await session.commit()


@router.post("/tasks/{task_id}/restore")
async def restore_task(
    task_id: int,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> dict:
    merchant = require_merchant_role(auth)
    task = await _load_owned_task(session, task_id, merchant)

    if task.deleted_at is None:
        raise HTTPException(404, "任务不在回收站里")

    # 只把 deleted_at 放回 null：status 保持删除前的样子
    task.deleted_at = None
    task.updated_at = utcnow()
    await session.commit()
    await session.refresh(task)
    return {"task": task_public(task)}
