"""06-admin · 异常处理列表（`/api/admin/exceptions*`）。

## 这是 06 里唯一会改**别的模块状态**的地方

处理内容异常会把 job 推到 `ready` / `failed`，处理截图异常会往 04 的
`metric_snapshot` 里补一条。所以这一组的重心在**准入闸**：状态不对就 409、
动作非法就 422、被拒的动作**不留痕**。

闸门松了的后果不是「多改了一次」，而是**事后翻案**：奖励可能已经按当时的
快照峰值结算完了，再补一条快照不会重算（结算不回溯），却会让台账与看板对不上。

## `engagement` 必须由后端算

截图里识别出的 `parsed` 是**外部输入**。把 `parsed["engagement"]` 直接抄进快照
等于让截图自己挑奖励档位——而它恰恰是用户能控制的那一份文件。04 的
`engagement_of()` 是这条公式唯一的落点（`EO-01` 盯的就是这里）。
"""

from datetime import datetime, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import AuthContext, require_admin
from app.db import get_session
from app.models.studio import ContentJob
from app.models.tracking import (
    APPROVED_STATUSES,
    MISMATCH_CONFIDENCE,
    Appeal,
    MetricSnapshot,
    OcrResult,
    SocialPost,
)
from app.services import audit as audit_service
from app.services.review import engagement_of

router = APIRouter(prefix="/api/admin/exceptions", tags=["admin-exception"])

MAX_PAGE_SIZE = 100

#: spec 的 type 表。四个值各有各的数据源与动作集，端点按它分支。
EXCEPTION_TYPES = (
    "content_review",
    "ocr_low_confidence",
    "ocr_mismatch",
    "appeal",
)

CONTENT_ACTIONS = ("approve", "discard")
OCR_ACTIONS = ("accept", "reject")

JOB_NEEDS_REVIEW = "need_review"
JOB_READY = "ready"
JOB_FAILED = "failed"


class ResolveIn(BaseModel):
    action: str
    # 选填。**为空也要写日志**——把写日志挂在「有 note」的分支里，
    # 绝大多数正常的 approve 就都不留痕了，而 approve 恰恰最需要留痕。
    note: str | None = None


def _paged(items: list, total: int, page: int, size: int) -> dict:
    return {"items": items, "total": total, "page": page, "size": size}


# --------------------------------------------------------------------------- #
# 列表
# --------------------------------------------------------------------------- #
@router.get("")
async def list_exceptions(
    type: str = Query(default="content_review"),  # noqa: A002 - spec 的查询串就叫 type
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=MAX_PAGE_SIZE),
    auth: AuthContext = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> dict:
    if type not in EXCEPTION_TYPES:
        raise HTTPException(422, f"type 只能是 {EXCEPTION_TYPES}")

    if type == "content_review":
        return await _list_content(session, page, size)
    if type == "appeal":
        return await _list_appeals(session, page, size)
    return await _list_ocr(session, type, page, size)


async def _list_content(session: AsyncSession, page: int, size: int) -> dict:
    where = ContentJob.status == JOB_NEEDS_REVIEW
    total = await session.scalar(
        select(func.count()).select_from(ContentJob).where(where)
    )
    rows = (
        (
            await session.execute(
                select(ContentJob)
                .where(where)
                .order_by(ContentJob.created_at.desc(), ContentJob.id.desc())
                .offset((page - 1) * size)
                .limit(size)
            )
        )
        .scalars()
        .all()
    )
    return _paged(
        [
            {
                "job_id": r.id,
                "task_id": r.task_id,
                "user_id": r.user_id,
                "kind": r.kind,
                "status": r.status,
                "fail_reason": r.fail_reason,
                "created_at": r.created_at,
            }
            for r in rows
        ],
        int(total or 0),
        page,
        size,
    )


async def _list_ocr(
    session: AsyncSession, type: str, page: int, size: int  # noqa: A002
) -> dict:
    filters = [OcrResult.is_active.is_(True)]
    if type == "ocr_low_confidence":
        # 严格小于：`0.70` 恰好等于阈值的不进名单。写成 `<=` 会把刚及格的
        # 识别结果也推给运营，待办里会堆一堆不需要看的单子。
        filters.append(OcrResult.confidence < Decimal(str(MISMATCH_CONFIDENCE)))
    else:
        filters.append(OcrResult.mismatch_flag.is_(True))

    total = await session.scalar(
        select(func.count()).select_from(OcrResult).where(*filters)
    )
    rows = (
        (
            await session.execute(
                select(OcrResult)
                .where(*filters)
                .order_by(OcrResult.created_at.desc(), OcrResult.id.desc())
                .offset((page - 1) * size)
                .limit(size)
            )
        )
        .scalars()
        .all()
    )
    return _paged(
        [
            {
                "ocr_id": r.id,
                "post_id": r.post_id,
                "image_url": r.image_url,
                "confidence": float(r.confidence),
                "mismatch_flag": r.mismatch_flag,
                "model": r.model,
                "created_at": r.created_at,
            }
            for r in rows
        ],
        int(total or 0),
        page,
        size,
    )


async def _list_appeals(session: AsyncSession, page: int, size: int) -> dict:
    where = Appeal.status == "pending"
    total = await session.scalar(select(func.count()).select_from(Appeal).where(where))
    rows = (
        (
            await session.execute(
                select(Appeal)
                .where(where)
                .order_by(Appeal.id.desc())
                .offset((page - 1) * size)
                .limit(size)
            )
        )
        .scalars()
        .all()
    )
    return _paged(
        [
            {
                "appeal_id": r.id,
                "post_id": r.post_id,
                "user_id": r.user_id,
                "reason": r.reason,
                "status": r.status,
                "created_at": r.created_at,
            }
            for r in rows
        ],
        int(total or 0),
        page,
        size,
    )


# --------------------------------------------------------------------------- #
# 处理内容异常
# --------------------------------------------------------------------------- #
@router.post("/content/{job_id}/resolve")
async def resolve_content(
    job_id: int,
    payload: ResolveIn,
    auth: AuthContext = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> dict:
    if payload.action not in CONTENT_ACTIONS:
        raise HTTPException(422, f"action 只能是 {CONTENT_ACTIONS}")

    # 锁住再判状态：并发两次 approve（`CC-01`）里，后到的那次会阻塞到
    # 前一次提交，读到的已经是 `ready`，于是 409——日志也就只有一条。
    job = await session.scalar(
        select(ContentJob).where(ContentJob.id == job_id).with_for_update()
    )
    if job is None:
        raise HTTPException(404, "任务不存在")
    if job.status != JOB_NEEDS_REVIEW:
        # 一旦放行就能事后改判，而用户可能已经下载过 / 已经结算过
        raise HTTPException(409, f"当前状态 {job.status} 不在待处理队列里")

    before = job.status
    if payload.action == "approve":
        job.status = JOB_READY
    else:
        job.status = JOB_FAILED
        # **等于** note，不拼前缀：这是用户下次打开 job 时唯一看到的原因，
        # 拼上「人工作废：」会让他看到一段自己没要求过的文字
        job.fail_reason = payload.note

    await audit_service.log_action(
        session,
        admin_id=auth.user.id,
        action="resolve_content",
        target_type="content_job",
        target_id=job.id,
        detail={
            "action": payload.action,
            "note": payload.note,
            "before": before,
            "after": job.status,
        },
    )
    await session.commit()
    return {
        "job": {
            "job_id": job.id,
            "status": job.status,
            "fail_reason": job.fail_reason,
        }
    }


# --------------------------------------------------------------------------- #
# 处理截图异常
# --------------------------------------------------------------------------- #
@router.post("/ocr/{ocr_id}/resolve")
async def resolve_ocr(
    ocr_id: int,
    payload: ResolveIn,
    auth: AuthContext = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> dict:
    if payload.action not in OCR_ACTIONS:
        raise HTTPException(422, f"action 只能是 {OCR_ACTIONS}")

    ocr = await session.scalar(
        select(OcrResult).where(OcrResult.id == ocr_id).with_for_update()
    )
    if ocr is None:
        raise HTTPException(404, "识别结果不存在")
    if not ocr.is_active:
        # 已被处理过（或已被新截图顶掉）。再放行一次会补出第二条快照
        raise HTTPException(409, "该识别结果已被处理")

    post = await session.get(SocialPost, ocr.post_id)
    if post is None:
        raise HTTPException(404, "作品不存在")
    if post.status in APPROVED_STATUSES:
        # 奖励已按当时的快照峰值结算过了。结算不回溯，再补一条快照不会重算，
        # 但**会让台账与看板对不上**——所以必须在入口拦死，而不是靠下游忽略
        raise HTTPException(409, f"作品已结算（{post.status}），不可再补数据")

    parsed = ocr.parsed or {}
    now = datetime.now(timezone.utc)
    engagement = None
    if payload.action == "accept":
        likes = int(parsed.get("likes") or 0)
        collects = int(parsed.get("collects") or 0)
        comments = int(parsed.get("comments") or 0)
        engagement = engagement_of(likes, collects, comments)
        session.add(
            MetricSnapshot(
                post_id=post.id,
                likes=likes,
                collects=collects,
                comments=comments,
                shares=int(parsed.get("shares") or 0),
                engagement=engagement,
                source="ocr",
                raw=parsed,
                captured_at=now,
            )
        )

    ocr.is_active = False
    await audit_service.log_action(
        session,
        admin_id=auth.user.id,
        action="resolve_ocr",
        target_type="ocr_result",
        target_id=ocr.id,
        detail={
            "action": payload.action,
            "note": payload.note,
            "post_id": post.id,
            "engagement": engagement,
        },
    )
    await session.commit()
    return {
        "ocr": {"ocr_id": ocr.id, "is_active": ocr.is_active},
        "engagement": engagement,
    }
