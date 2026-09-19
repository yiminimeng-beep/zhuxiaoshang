"""06 追加 · 今日使用量（`GET /api/admin/stats/daily`）。

## 切日按**北京时间**，尽管库里存的是 UTC

全局约定 #2 管**存**（一律 UTC 落库），本端点管**统计区间怎么切**。
两件事不冲突：运营嘴里的「今天」是北京时间的今天。

于是区间 = `[date 00:00 CST, date+1 00:00 CST)`，**再换算成 UTC 去查库**。
北京 `09-16 00:00` 就是 UTC `09-15 16:00`——用 UTC 整天去切，
运营看到的「今天」会从早上 8 点才开始，前一天的 16:00 ~ 24:00 被算进昨天。

`date` 省略 → 北京时间的今天。UTC+8 没有夏令时，故「加一天」直接
`timedelta(days=1)` 即可，不需要按日历重算。

## 空窗口必须返回 0，不是 null

聚合写法一律用 `func.count()`：它在零行时返回 `0`。写成
`func.sum(case(...))` 的话，空表会返回 `NULL`，透到前端就是渲染出一个
「—」或干脆崩掉——而这恰恰是新平台上线第一天的样子（表里什么都没有）。

## `role='admin'` 不计入任何一类

使用量说的是商家和用户。过滤条件里直接 `role IN ('merchant','customer')`，
比事后减掉一类更不容易漏。

## 与成本看板是两套口径

spec 明写不得混用同一张表或同一个字段：成本看的是**钱**（`quota_ledger` /
`model_price`），使用量看的是**人次**。两边的数不要求能对上。
"""

from datetime import date as date_type
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import AuthContext, require_admin
from app.db import get_session
from app.models.studio import ContentJob
from app.models.task import Task, TaskClaim
from app.models.tracking import SocialPost
from app.models.user import User

router = APIRouter(prefix="/api/admin/stats", tags=["admin-stats"])

BEIJING = timezone(timedelta(hours=8))

#: 使用量只统计这两种角色，`admin` 天然被排除
COUNTED_ROLES = ("merchant", "customer")


def beijing_day_window(day: date_type) -> tuple[datetime, datetime]:
    """北京某日的 `[00:00, 次日 00:00)` 对应的 **UTC** 区间（左闭右开）。"""
    start = datetime(day.year, day.month, day.day, tzinfo=BEIJING).astimezone(
        timezone.utc
    )
    return start, start + timedelta(days=1)


async def _count_users(
    session: AsyncSession, column, start: datetime, end: datetime
) -> dict:
    """按 `role` 分组数人数，未出现的角色补零。

    补零不能省：`.group_by` 只返回**有行**的角色，某一天没有商家登录时
    这个键会整个消失，前端 `active.merchant` 就成了 `undefined`。
    """
    rows = await session.execute(
        select(User.role, func.count())
        .where(
            User.status != "deleted",
            User.role.in_(COUNTED_ROLES),
            column >= start,
            column < end,
        )
        .group_by(User.role)
    )
    counted = {role: int(total) for role, total in rows}
    return {role: counted.get(role, 0) for role in COUNTED_ROLES}


async def _count_rows(
    session: AsyncSession, model, column, start: datetime, end: datetime
) -> int:
    return int(
        await session.scalar(
            select(func.count())
            .select_from(model)
            .where(column >= start, column < end)
        )
        or 0
    )


@router.get("/daily")
async def daily_stats(
    day: date_type | None = Query(default=None, alias="date"),
    auth: AuthContext = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> dict:
    # `date` 的格式校验交给 FastAPI 的 `date` 类型：`2026-13-45`、`notadate`、
    # `2026-02-30`（格式合法但日期不存在）三者都会在这里变成 422
    target = day or datetime.now(BEIJING).date()
    start, end = beijing_day_window(target)

    return {
        "date": target.isoformat(),
        "active": await _count_users(session, User.last_login_at, start, end),
        "new": await _count_users(session, User.created_at, start, end),
        "activity": {
            # 已软删的任务照算——建了就建了，删除动作改的是 `deleted_at`
            "tasks_created": await _count_rows(
                session, Task, Task.created_at, start, end
            ),
            "claims": await _count_rows(
                session, TaskClaim, TaskClaim.claimed_at, start, end
            ),
            "posts_submitted": await _count_rows(
                session, SocialPost, SocialPost.submitted_at, start, end
            ),
            "jobs_created": await _count_rows(
                session, ContentJob, ContentJob.created_at, start, end
            ),
        },
    }
