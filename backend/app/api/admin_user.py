"""06-admin · 用户与商户管理（`/api/admin/users*` / `action-logs`）。

## 封禁是本模块唯一能改变**别的模块**行为的动作

被封的人下一次请求就进不来，所以这一组要防两件事：**下界被卡死**（4 字拒 /
5 字放行）与**每一笔都留痕**。两条都是「写松了不会报错」的类型——比较写成
`<= 5` 只是偶尔多拒一条，日志漏写则完全没有症状，直到需要翻账。

## 关键词里的 `%` 与 `_`

自然写法 `ilike(f"%{kw}%")` 会把用户输入当通配符，搜 `a_b` 会命中 `axb`。
在功能上它像是「模糊搜索更聪明」，在安全上它是逃逸的种子。故一律转义后配
`ESCAPE`——`_escape_like()` 是这条规则唯一的落点。
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import AuthContext, require_admin
from app.db import get_session
from app.models.admin import AdminActionLog
from app.models.reward import PointLedger
from app.models.studio import ContentJob
from app.models.task import Task, TaskClaim
from app.models.user import ROLES, STATUSES, MerchantProfile, User
from app.services import audit as audit_service

router = APIRouter(prefix="/api/admin", tags=["admin-user"])

MAX_PAGE_SIZE = 100
# spec 端点表写「reason 空或 < 5 字 → 422」。5 是下界那一条的**唯一**取值：
# 写成 `<= 5` 会把 5 字也拒掉（AB-03 红），写成 `< 10` 会把 4 字放行（AB-02 红）。
MIN_BAN_REASON_CHARS = 5

#: 审计表里 `target_type` 的合法取值（权威定义在 `app/models/admin.py`）。
ACTION_LOG_TARGET_TYPES = ("user", "content_job", "ocr_result", "quota_account", "appeal")


class BanIn(BaseModel):
    reason: str


def _escape_like(keyword: str) -> str:
    """把 `LIKE` 的通配符转成字面量。

    反斜杠必须先转——否则 `\\%` 会被二次解释成「转义符 + 通配符」。
    """
    return keyword.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _user_public(user: User, shop_name: str | None) -> dict:
    return {
        "id": user.id,
        "account": user.account,
        "email": user.email,
        "nickname": user.nickname,
        "role": user.role,
        "status": user.status,
        "shop_name": shop_name,
        "created_at": user.created_at,
    }


# --------------------------------------------------------------------------- #
# 列表与详情
# --------------------------------------------------------------------------- #
@router.get("/users")
async def list_users(
    role: str | None = Query(default=None),
    status_filter: str | None = Query(default=None, alias="status"),
    keyword: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=MAX_PAGE_SIZE),
    auth: AuthContext = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> dict:
    if role is not None and role not in ROLES:
        raise HTTPException(422, "role 非法")
    if status_filter is not None and status_filter not in STATUSES:
        raise HTTPException(422, "status 非法")

    stmt = select(User, MerchantProfile.shop_name).outerjoin(
        MerchantProfile, MerchantProfile.user_id == User.id
    )
    if role is not None:
        stmt = stmt.where(User.role == role)
    if status_filter is not None:
        # 已注销的人**照常能筛出来**：注销不是从平台消失，是这个人不能用了
        stmt = stmt.where(User.status == status_filter)
    if keyword:
        like = f"%{_escape_like(keyword)}%"
        # `shop_name` 在 merchant_profile 上——漏了 join，按店名搜就是搜不到。
        # `ESCAPE '\'` 不是可选项：没有它，上一行的转义只是往模式里塞了反斜杠。
        stmt = stmt.where(
            or_(
                User.account.ilike(like, escape="\\"),
                User.email.ilike(like, escape="\\"),
                User.nickname.ilike(like, escape="\\"),
                MerchantProfile.shop_name.ilike(like, escape="\\"),
            )
        )

    total = await session.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = (
        await session.execute(
            stmt.order_by(User.id).offset((page - 1) * size).limit(size)
        )
    ).all()

    return {
        "items": [_user_public(user, shop_name) for user, shop_name in rows],
        "total": int(total or 0),
        "page": page,
        "size": size,
    }


@router.get("/users/{user_id}")
async def user_detail(
    user_id: int,
    auth: AuthContext = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> dict:
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(404, "用户不存在")

    profile = await session.get(MerchantProfile, user_id)

    task_count = await session.scalar(
        select(func.count()).select_from(Task).where(Task.merchant_id == user_id)
    )
    claim_count = await session.scalar(
        select(func.count()).select_from(TaskClaim).where(TaskClaim.user_id == user_id)
    )
    job_count = await session.scalar(
        select(func.count()).select_from(ContentJob).where(ContentJob.user_id == user_id)
    )
    # 余额**只认账本**：从别的表（比如 `reward_grant`）另推一遍，
    # 和 `point_ledger` 迟早会漂，而用户看到的余额是账本那一本。
    points_balance = await session.scalar(
        select(func.coalesce(func.sum(PointLedger.change), 0)).where(
            PointLedger.user_id == user_id
        )
    )

    body = {
        "user": _user_public(user, profile.shop_name if profile else None),
        "stats": {
            "task_count": int(task_count or 0),
            "claim_count": int(claim_count or 0),
            "job_count": int(job_count or 0),
            "points_balance": int(points_balance or 0),
        },
    }
    if user.role == "merchant":
        # 客户详情**不带这个键**：一个恒为 null 的 `merchant_profile`
        # 只会让前端多写一个分支
        body["merchant_profile"] = (
            {
                "shop_name": profile.shop_name,
                "category": profile.category,
                "address": profile.address,
                "logo_url": profile.logo_url,
                "description": profile.description,
                "contact": profile.contact,
            }
            if profile is not None
            else None
        )
    return body


# --------------------------------------------------------------------------- #
# 封禁 / 解封
# --------------------------------------------------------------------------- #
async def _lock_user(session: AsyncSession, user_id: int) -> User:
    """取用户行并**锁住**，直到本次事务结束。

    并发封禁（`CC-02`）靠它串行化：两个请求同时进来，第二个会阻塞到第一个
    提交，此时读到的已是 `banned`，于是 409——日志也就只会有一条。
    不加锁的话两个请求都读到 `active`，`admin_action_log` 会多出一条
    「其实没生效」的执法记录。
    """
    user = await session.scalar(
        select(User).where(User.id == user_id).with_for_update()
    )
    if user is None:
        raise HTTPException(404, "用户不存在")
    return user


def _reject_admin_target(user: User, verb: str) -> None:
    """`admin` 只由种子脚本写入，后台没有新增管理员的入口。

    所以一旦允许封禁 admin，任何一个管理员都能锁死整个后台，**且没有恢复
    入口**——没有第二个 admin 能来解封他。
    """
    if user.role == "admin":
        raise HTTPException(409, f"不能{verb}管理员账号")


@router.post("/users/{user_id}/ban")
async def ban_user(
    user_id: int,
    payload: BanIn,
    auth: AuthContext = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> dict:
    reason = payload.reason.strip()
    if len(reason) < MIN_BAN_REASON_CHARS:
        raise HTTPException(
            422, f"封禁理由至少 {MIN_BAN_REASON_CHARS} 字——执法必须能追溯"
        )

    user = await _lock_user(session, user_id)
    _reject_admin_target(user, "封禁")

    if user.status == "banned":
        raise HTTPException(409, "该账号已被封禁")
    if user.status == "deleted":
        # 注销是不可逆的（标识符已置 null 并写了墓碑表），封一个已经不可用的
        # 账号不产生任何效果，只会往审计里塞一条假的「执法记录」
        raise HTTPException(409, "该账号已注销，封禁没有意义")

    before = user.status
    user.status = "banned"
    await audit_service.log_action(
        session,
        admin_id=auth.user.id,
        action="ban_user",
        target_type="user",
        target_id=user.id,
        detail={"reason": reason, "before": before, "after": "banned"},
    )
    await session.commit()
    return {"user": _user_public(user, None), "reason": reason}


@router.post("/users/{user_id}/unban")
async def unban_user(
    user_id: int,
    auth: AuthContext = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> dict:
    user = await _lock_user(session, user_id)
    _reject_admin_target(user, "解封")

    if user.status != "banned":
        raise HTTPException(409, "该账号当前不是封禁状态")

    user.status = "active"
    await audit_service.log_action(
        session,
        admin_id=auth.user.id,
        action="unban_user",
        target_type="user",
        target_id=user.id,
        detail={"before": "banned", "after": "active"},
    )
    await session.commit()
    return {"user": _user_public(user, None)}


# --------------------------------------------------------------------------- #
# 操作日志
# --------------------------------------------------------------------------- #
@router.get("/action-logs")
async def list_action_logs(
    target_type: str | None = Query(default=None),
    target_id: int | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=MAX_PAGE_SIZE),
    auth: AuthContext = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> dict:
    if target_type is not None and target_type not in ACTION_LOG_TARGET_TYPES:
        raise HTTPException(422, "target_type 非法")

    filters = []
    if target_type is not None:
        filters.append(AdminActionLog.target_type == target_type)
    if target_id is not None:
        filters.append(AdminActionLog.target_id == target_id)

    total = await session.scalar(
        select(func.count()).select_from(AdminActionLog).where(*filters)
    )
    rows = (
        (
            await session.execute(
                select(AdminActionLog)
                .where(*filters)
                # 倒序：翻账的人要看的是「最近发生了什么」
                .order_by(AdminActionLog.id.desc())
                .offset((page - 1) * size)
                .limit(size)
            )
        )
        .scalars()
        .all()
    )

    return {
        "items": [
            {
                "id": r.id,
                "admin_id": r.admin_id,
                "action": r.action,
                "target_type": r.target_type,
                "target_id": r.target_id,
                # `detail` 是 jsonb → 直接是 dict。列类型若写成 text，
                # 前端拿到的是 `'{"reason": ...}'`，得自己 parse
                "detail": r.detail,
                "created_at": r.created_at,
            }
            for r in rows
        ],
        "total": int(total or 0),
        "page": page,
        "size": size,
    }
