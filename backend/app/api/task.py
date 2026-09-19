"""02-task · 客户侧：任务列表 / 详情 / 领取 / 放弃。

## 领取为什么必须是「条件更新」而不是「先查再写」

`quota` 是硬约束，且 spec 明确要求「`quota=1` 且 100 并发 → 恰好 1 个成功」。
应用层 `if claimed_count < quota: claimed_count += 1` 在并发下**必然超额**：
两个请求可以同时读到 `claimed_count=0`，然后各写一次 1。

这里改成一条带条件的原子 UPDATE：

```sql
UPDATE task SET claimed_count = claimed_count + 1
WHERE id = :id AND (quota IS NULL OR claimed_count < quota)
```

Postgres 会对该行加锁，并在**拿到锁之后**重新判定 `WHERE`（READ COMMITTED 的
语义），所以 100 个并发里只有一个能把 `affected rows` 变成 1，其余全是 0。
"""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.serializers import merchant_profile_public, user_public
from app.api.task_serializers import (
    claim_public,
    reward_rule_public,
    task_public,
    utcnow,
)
from app.core.deps import AuthContext, get_current_auth, get_optional_auth
from app.db import get_session
from app.models.task import RewardRule, Task, TaskClaim
from app.models.user import MerchantProfile, User

router = APIRouter(tags=["task"])

MAX_PAGE_SIZE = 100
DEFAULT_PAGE_SIZE = 10
MAX_TAGS = 5


def _escape_like(value: str) -> str:
    """把 `%` / `_` / `\\` 转义成字面量。

    不转义的话 `keyword=%` 会变成「匹配任意字符串」，用户搜一个百分号就能
    拖出全库；`_` 同理会变成单字符通配符。
    """
    return (
        value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    )


def _parse_tags(raw: str | None) -> list[str] | None:
    if raw is None:
        return None
    tags = [t.strip() for t in raw.split(",")]
    if not raw.strip() or any(not t for t in tags):
        raise HTTPException(422, "tags 不能为空串，多个标签用逗号分隔")
    if len(tags) > MAX_TAGS:
        raise HTTPException(422, f"tags 最多 {MAX_TAGS} 个")
    return tags


# --------------------------------------------------------------------------- #
# 列表 / 详情
# --------------------------------------------------------------------------- #
@router.get("/api/tasks")
async def list_tasks(
    keyword: str | None = None,
    tags: str | None = None,
    page: int = 1,
    size: int = DEFAULT_PAGE_SIZE,
    session: AsyncSession = Depends(get_session),
) -> dict:
    if size > MAX_PAGE_SIZE:
        raise HTTPException(422, f"size 不得超过 {MAX_PAGE_SIZE}")
    if size < 1 or page < 1:
        raise HTTPException(422, "page / size 必须为正整数")

    conditions = [Task.status == "published", Task.deleted_at.is_(None)]

    if keyword:
        pattern = f"%{_escape_like(keyword)}%"
        conditions.append(
            or_(
                Task.title.ilike(pattern, escape="\\"),
                Task.description.ilike(pattern, escape="\\"),
            )
        )

    wanted_tags = _parse_tags(tags)
    if wanted_tags:
        # JSONB 的 `@>` 是「包含」：同时含这几个标签才算命中（AND 语义）
        conditions.append(Task.tags.contains(wanted_tags))

    total = await session.scalar(
        select(func.count()).select_from(Task).where(*conditions)
    )

    rows = (
        await session.scalars(
            select(Task)
            .where(*conditions)
            # 搜索不改排序：永远按创建时间倒序
            .order_by(Task.created_at.desc(), Task.id.desc())
            .offset((page - 1) * size)
            .limit(size)
        )
    ).all()

    return {
        "items": [task_public(t) for t in rows],
        "total": total or 0,
        "page": page,
        "size": size,
    }


@router.get("/api/tasks/{task_id}")
async def task_detail(
    task_id: int,
    auth: AuthContext | None = Depends(get_optional_auth),
    session: AsyncSession = Depends(get_session),
) -> dict:
    task = await session.get(Task, task_id)
    if task is None:
        raise HTTPException(404, "任务不存在")

    is_owner = auth is not None and auth.user.id == task.merchant_id
    if (
        task.deleted_at is not None
        # 非 published 只有本人看得到；对别人一律 404，不泄露「这里有个草稿」
        or (task.status != "published" and not is_owner)
    ):
        raise HTTPException(404, "任务不存在")

    rule = await session.scalar(
        select(RewardRule).where(RewardRule.task_id == task.id)
    )

    merchant = await session.get(User, task.merchant_id)
    profile = await session.get(MerchantProfile, task.merchant_id)

    claimed_by_me = False
    if auth is not None:
        claimed_by_me = (
            await session.scalar(
                select(TaskClaim.id).where(
                    TaskClaim.task_id == task.id,
                    TaskClaim.user_id == auth.user.id,
                    TaskClaim.status != "closed",
                )
            )
        ) is not None

    return {
        "task": task_public(task),
        "rule": reward_rule_public(rule) if rule else None,
        "merchant": {
            "user": user_public(merchant),
            "merchant_profile": (
                merchant_profile_public(profile) if profile else None
            ),
        }
        if merchant
        else None,
        "claimed_by_me": claimed_by_me,
    }


# --------------------------------------------------------------------------- #
# 领取
# --------------------------------------------------------------------------- #
@router.post("/api/tasks/{task_id}/claim", status_code=status.HTTP_201_CREATED)
async def claim_task(
    task_id: int,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> dict:
    user = auth.user
    now = utcnow()

    task = await session.get(Task, task_id)
    if task is None or task.deleted_at is not None:
        raise HTTPException(404, "任务不存在")

    if task.merchant_id == user.id:
        raise HTTPException(403, "不能领取自己发布的任务")

    if task.status != "published":
        raise HTTPException(422, "该任务当前不可领取")

    if now < task.start_at:
        raise HTTPException(422, "任务尚未开始")
    # 闭区间：end_at 那一瞬就算已结束
    if now >= task.end_at:
        raise HTTPException(422, "任务已结束")

    already = await session.scalar(
        select(TaskClaim.id).where(
            TaskClaim.task_id == task.id,
            TaskClaim.user_id == user.id,
            TaskClaim.status != "closed",
        )
    )
    if already is not None:
        raise HTTPException(409, "已经领取过了")

    # 名额：一条原子条件更新，并发下也只可能有一个请求把它更新成功
    quota_ok = await session.execute(
        update(Task)
        .where(
            Task.id == task.id,
            or_(Task.quota.is_(None), Task.claimed_count < Task.quota),
        )
        .values(claimed_count=Task.claimed_count + 1)
        .returning(Task.id)
    )
    if quota_ok.first() is None:
        await session.rollback()
        raise HTTPException(409, "名额已满")

    claim = TaskClaim(task_id=task.id, user_id=user.id, status="in_progress")
    session.add(claim)

    try:
        await session.commit()
    except IntegrityError:
        # 同一用户并发重复领取时才会走到这里：部分唯一索引兜底
        await session.rollback()
        raise HTTPException(409, "已经领取过了")

    await session.refresh(claim)
    return {"claim": claim_public(claim)}


# --------------------------------------------------------------------------- #
# 我的领取 / 放弃
# --------------------------------------------------------------------------- #
@router.get("/api/me/claims")
async def my_claims(
    # 参数按 spec 叫 `status`，但 `status` 已被 fastapi 的模块占用，故用别名
    status_filter: str | None = Query(default=None, alias="status"),
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> dict:
    conditions = [TaskClaim.user_id == auth.user.id]
    if status_filter:
        conditions.append(TaskClaim.status == status_filter)

    rows = (
        await session.scalars(
            select(TaskClaim)
            .where(*conditions)
            .order_by(TaskClaim.claimed_at.desc(), TaskClaim.id.desc())
        )
    ).all()

    items = []
    for claim in rows:
        task = await session.get(Task, claim.task_id)
        item = claim_public(claim)
        item["task"] = task_public(task) if task else None
        items.append(item)
    return {"items": items, "total": len(items)}


@router.delete("/api/me/claims/{claim_id}", status_code=status.HTTP_204_NO_CONTENT)
async def abandon_claim(
    claim_id: int,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> None:
    claim = await session.get(TaskClaim, claim_id)
    if claim is None:
        raise HTTPException(404, "领取记录不存在")
    if claim.user_id != auth.user.id:
        raise HTTPException(403, "无权放弃他人的领取")

    if claim.status != "in_progress":
        raise HTTPException(409, "已提交的领取不可放弃")

    # 标 closed 而不是删行：领取历史要留（审计），部分唯一索引让位给重新领取
    claim.status = "closed"

    await session.execute(
        update(Task)
        .where(Task.id == claim.task_id)
        .values(claimed_count=func.greatest(Task.claimed_count - 1, 0))
    )
    await session.commit()
