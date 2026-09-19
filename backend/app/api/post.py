"""04-tracking · 作品提交、数据快照、我的作品。

## 提交那道闸的**判定顺序**是契约的一部分

`SP-03`（同 claim 同链接 → `409`）与 `SP-12`（同 claim 同链接、但那条已过审 →
`422`）用的是同一份输入，期望的码却不同。所以顺序只能是：

1. 归属（`403`）
2. 平台 / 链接合法性（`422`）
3. job 就绪（`409`）
4. **该 claim 已有过审作品 → `422`** ← 必须先于第 5 条
5. 同 `(claim_id, post_url)` 重复 → `409`
6. 该 claim 已有任何作品 → `422`

第 4 条排在第 5 条前面，`SP-12` 才不会退化成 `409`。反过来把 5 提到 4 前面，
`SP-03` 又会从 `409` 变成 `422`——这两条用例互为对方的顺序约束。

## `engagement` 与 `captured_at` 一律 `422`，不是忽略

`extra="forbid"` 让客户端多传的字段直接报错。静默忽略会使插件以为自己算的
`engagement` 生效了，而实际落库的是后端算的另一个数——这种「两边都觉得自己
对」的分歧最贵。`captured_at` 同理：允许客户端声称「我是昨天采的」，就能绕过
「审核之后产生的快照不参与结算」。

## 快照可追加的状态

`pending` 与 `appealed` 都允许。申诉期间用户还在涨互动，裁决时按峰值算
（`AP-09`）；一过审就关上这道门（`MS-07`），否则「先报低位过审、再涨上来」
仍然走得通。
"""

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.post_serializers import (
    ocr_public,
    post_detail,
    post_public,
    snapshot_public,
    utcnow,
    validate_post_url,
)
from app.core.deps import AuthContext, get_current_auth
from app.db import get_session
from app.models.studio import ContentJob
from app.models.task import TaskClaim
from app.models.tracking import (
    APPROVED_STATUSES,
    MAX_SCREENSHOT_BYTES,
    METRIC_SOURCES,
    MISMATCH_CONFIDENCE,
    PLATFORMS,
    POST_SOURCES,
    SCREENSHOT_MIMES,
    MetricSnapshot,
    OcrResult,
    SocialPost,
    TrackEvent,
)
from app.services import ai as ai_service
from app.services import model_key as key_service
from app.services import storage as storage_service
from app.services.ai import using_key
from app.services.review import deadline_for, engagement_of

logger = logging.getLogger(__name__)

router = APIRouter(tags=["post"])

# 快照还能追加的状态。过审之后一律 409
SNAPSHOT_OPEN_STATUSES = ("pending", "appealed")


class PostCreateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim_id: int
    job_id: int
    platform: str
    post_url: str
    source: str
    post_title: str | None = None
    post_body: str | None = None


class MetricsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    likes: int = Field(ge=0)
    collects: int = Field(ge=0)
    comments: int = Field(ge=0)
    shares: int = Field(ge=0)
    source: str
    raw: dict | None = None


class ScreenshotIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    image_url: str
    mime: str
    size_bytes: int = Field(ge=0)


# --------------------------------------------------------------------------- #
# 提交作品
# --------------------------------------------------------------------------- #
@router.post("/api/posts", status_code=201)
async def submit_post(
    payload: PostCreateIn,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> dict:
    if payload.source not in POST_SOURCES:
        raise HTTPException(422, f"source 只能是 {POST_SOURCES}")
    if payload.platform not in PLATFORMS:
        raise HTTPException(422, f"不支持的平台：{payload.platform}")
    validate_post_url(payload.platform, payload.post_url)

    claim = await session.get(TaskClaim, payload.claim_id)
    if claim is None or claim.user_id != auth.user.id:
        raise HTTPException(403, "这不是你的领取记录")

    job = await session.get(ContentJob, payload.job_id)
    if job is None or job.user_id != auth.user.id or job.task_id != claim.task_id:
        raise HTTPException(403, "这不是你的内容任务")
    if job.status != "ready":
        raise HTTPException(409, "内容尚未生成完成，暂时不能提交")

    approved = await session.scalar(
        select(SocialPost.id)
        .where(
            SocialPost.claim_id == claim.id,
            SocialPost.status.in_(APPROVED_STATUSES),
        )
        .limit(1)
    )
    if approved is not None:
        raise HTTPException(422, "该领取已有通过审核的作品")

    duplicated = await session.scalar(
        select(SocialPost.id)
        .where(
            SocialPost.claim_id == claim.id,
            SocialPost.post_url == payload.post_url,
        )
        .limit(1)
    )
    if duplicated is not None:
        raise HTTPException(409, "同一个链接已经提交过")

    existing = await session.scalar(
        select(SocialPost.id).where(SocialPost.claim_id == claim.id).limit(1)
    )
    if existing is not None:
        raise HTTPException(422, "一个领取只对应一个作品")

    now = utcnow()
    post = SocialPost(
        claim_id=claim.id,
        job_id=job.id,
        user_id=auth.user.id,
        platform=payload.platform,
        post_url=payload.post_url,
        post_title=payload.post_title,
        post_body=payload.post_body,
        source=payload.source,
        status="pending",
        submitted_at=now,
        review_deadline=deadline_for(now),
    )
    session.add(post)
    await session.flush()
    await session.commit()

    return {"post": post_public(post), "review_deadline": post.review_deadline}


# --------------------------------------------------------------------------- #
# 数据快照
# --------------------------------------------------------------------------- #
@router.post("/api/posts/{post_id}/metrics", status_code=201)
async def add_metrics(
    post_id: int,
    payload: MetricsIn,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> dict:
    post = await session.get(SocialPost, post_id)
    if post is None:
        raise HTTPException(404, "作品不存在")
    if post.user_id != auth.user.id:
        raise HTTPException(403, "这不是你的作品")
    if post.status not in SNAPSHOT_OPEN_STATUSES:
        raise HTTPException(409, "作品已审核完成，不能再追加数据")
    if payload.source not in METRIC_SOURCES:
        raise HTTPException(422, f"source 只能是 {METRIC_SOURCES}")

    snapshot = MetricSnapshot(
        post_id=post.id,
        likes=payload.likes,
        collects=payload.collects,
        comments=payload.comments,
        shares=payload.shares,
        engagement=engagement_of(
            payload.likes, payload.collects, payload.comments
        ),
        source=payload.source,
        raw=payload.raw,
        captured_at=utcnow(),
    )
    session.add(snapshot)
    await session.flush()
    await session.commit()

    return {"snapshot": snapshot_public(snapshot)}


# --------------------------------------------------------------------------- #
# 截图识别（L2 保底）
# --------------------------------------------------------------------------- #
async def _latest_plugin_snapshot(
    session: AsyncSession, post_id: int
) -> MetricSnapshot | None:
    """链接侧（插件）最新一条快照。没有它就没有可比对的基准。

    刻意按 `source == "plugin"` 过滤：识别结果自己也会写快照，
    拿「上一次识别的结果」当基准，等于让识别跟自己比。
    """
    return await session.scalar(
        select(MetricSnapshot)
        .where(
            MetricSnapshot.post_id == post_id,
            MetricSnapshot.source == "plugin",
        )
        .order_by(MetricSnapshot.captured_at.desc(), MetricSnapshot.id.desc())
        .limit(1)
    )


def _mismatch(confidence: float, parsed: dict, link: MetricSnapshot | None) -> bool:
    """两个独立判据的**并集**：模型没把握，或模型有把握但与链接侧打架。

    合并成一个布尔是给前端用的（有它就只给「人工核对」，不给「一键通过」）；
    判据本身保持两条，将来「只信插件」或「只信识别」的调整才有下手的地方。
    """
    if float(confidence) < MISMATCH_CONFIDENCE:
        return True
    if link is None:
        return False
    recognized = engagement_of(
        parsed.get("likes", 0), parsed.get("collects", 0), parsed.get("comments", 0)
    )
    return recognized != link.engagement


@router.post("/api/posts/{post_id}/screenshot", status_code=202)
async def upload_screenshot(
    post_id: int,
    payload: ScreenshotIn,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> dict:
    post = await session.get(SocialPost, post_id)
    if post is None:
        raise HTTPException(404, "作品不存在")
    if post.user_id != auth.user.id:
        raise HTTPException(403, "这不是你的作品")

    if payload.size_bytes > MAX_SCREENSHOT_BYTES:
        raise HTTPException(413, "截图不得超过 20MB")

    # 自述类型先过一遍白名单：`application/pdf` 即便内容真的是图也不认，
    # 「说的和给的不一样」要么是客户端有 bug，要么是在试探校验
    if payload.mime not in SCREENSHOT_MIMES:
        raise HTTPException(415, "只接受图片截图（jpeg / png / webp）")

    sniffed = storage_service.sniff_mime(payload.image_url)
    if sniffed is None or sniffed not in SCREENSHOT_MIMES:
        raise HTTPException(415, "截图的真实类型不是受支持的图片")
    if sniffed != payload.mime:
        raise HTTPException(415, "截图的真实类型与声明不符")

    # 识别失败（超时 / 模型异常）不是上传失败：截图已经收下了，
    # 只是结果还没出来。回 202 让客户端稍后查 `GET /api/ocr/{post_id}`。
    try:
        plaintext = await key_service.load_active_plaintext(
            session, post.user_id, "deepseek"
        )
    except key_service.NoActiveKey:
        plaintext = None

    try:
        with using_key(plaintext):
            verdict = await ai_service.ocr_metrics(payload.image_url)
    except Exception:
        logger.exception("作品 %s 的截图识别失败", post.id)
        return {"ocr_result_id": None}

    if verdict.parsed is None:
        raise HTTPException(422, "没能从截图里识别出互动数据，请重新上传")

    link = await _latest_plugin_snapshot(session, post.id)

    # 多张截图只留最新一张 active，旧的置回 false 但**不删**（留痕）
    await session.execute(
        update(OcrResult)
        .where(OcrResult.post_id == post.id, OcrResult.is_active.is_(True))
        .values(is_active=False)
    )
    row = OcrResult(
        post_id=post.id,
        image_url=payload.image_url,
        raw_text=verdict.raw_text,
        parsed=verdict.parsed,
        model=verdict.model,
        confidence=verdict.confidence,
        mismatch_flag=_mismatch(verdict.confidence, verdict.parsed, link),
        is_active=True,
        created_at=utcnow(),
    )
    session.add(row)
    await session.flush()

    recognized = engagement_of(
        verdict.parsed.get("likes", 0),
        verdict.parsed.get("collects", 0),
        verdict.parsed.get("comments", 0),
    )
    if link is not None and recognized < link.engagement:
        # 降级本身合法（社媒数据会因删帖回落），但它是刷分失败后的常见形态，
        # 必须留痕而不是静默
        session.add(
            TrackEvent(
                post_id=post.id,
                kind="metric_downgrade",
                detail={
                    "previous": link.engagement,
                    "recognized": recognized,
                    "ocr_result_id": row.id,
                },
                created_at=utcnow(),
            )
        )

    await session.commit()
    return {"ocr_result_id": row.id}


@router.get("/api/ocr/{post_id}")
async def get_ocr_result(
    post_id: int,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> dict:
    post = await session.get(SocialPost, post_id)
    if post is None:
        raise HTTPException(404, "作品不存在")
    if post.user_id != auth.user.id:
        raise HTTPException(403, "这不是你的作品")

    row = await session.scalar(
        select(OcrResult)
        .where(OcrResult.post_id == post.id, OcrResult.is_active.is_(True))
        .order_by(OcrResult.id.desc())
        .limit(1)
    )
    if row is None:
        # 还没好 ≠ 不存在。回 404 会让客户端以为这个作品根本没有截图
        raise HTTPException(409, "识别尚未完成，请稍后再试")

    return ocr_public(row)


# --------------------------------------------------------------------------- #
# 我的作品
# --------------------------------------------------------------------------- #
@router.get("/api/me/posts")
async def my_posts(
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> dict:
    posts = (
        (
            await session.execute(
                select(SocialPost)
                .where(SocialPost.user_id == auth.user.id)
                .order_by(SocialPost.id.desc())
            )
        )
        .scalars()
        .all()
    )

    now = utcnow()
    return {"items": [await post_detail(session, p, now=now) for p in posts]}
