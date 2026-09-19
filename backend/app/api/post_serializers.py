"""04-tracking 的链接校验与序列化。

## 平台域名白名单为什么在这里再写一份

`tests/helpers.py` 里也有一份。两份**刻意不共用**：共用的话白名单写错时，
测试拿去造数据的域名和实现拿去校验的域名同时错，于是「平台不匹配 → 422」
这条永远绿——测的是同一个错误的两面。各持一份，才有互相印证。

## 链接校验为什么不是「以 http 开头」

`javascript:alert(1)` 不是 http，但只判前缀的实现会放行；而只用 `urlparse`
取 host 的实现会把它当成「host 为空」而放行。两道都要判：先判 scheme 在
http/https 内，再判 host 落在所选平台的域名里。域名匹配用「等于或以 `.` 为
边界的后缀」——裸 `endswith("douyin.com")` 会被 `evildouyin.com` 骗过去。
"""

from datetime import datetime, timezone
from urllib.parse import urlparse

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tracking import MetricSnapshot, OcrResult, SocialPost

PLATFORM_DOMAINS = {
    "xhs": ("xiaohongshu.com", "xhslink.com"),
    "douyin": ("douyin.com", "v.douyin.com", "iesdouyin.com"),
    "kuaishou": ("kuaishou.com", "v.kuaishou.com"),
    "bilibili": ("bilibili.com", "b23.tv"),
    "shipinhao": ("channels.weixin.qq.com", "weixin.qq.com"),
}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _host_matches(host: str, domain: str) -> bool:
    return host == domain or host.endswith("." + domain)


def validate_post_url(platform: str, url: str) -> str:
    """校验链接合法且与该平台匹配，返回原样链接。不合规一律 `422`。"""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise HTTPException(422, "作品链接必须是 http/https 地址")

    host = (parsed.hostname or "").lower()
    if not host:
        raise HTTPException(422, "作品链接缺少域名")

    domains = PLATFORM_DOMAINS.get(platform)
    if domains is None:
        raise HTTPException(422, f"不支持的平台：{platform}")
    if not any(_host_matches(host, d) for d in domains):
        raise HTTPException(422, f"链接域名与所选平台 {platform} 不符")
    return url


def post_public(post: SocialPost) -> dict:
    return {
        "id": post.id,
        "claim_id": post.claim_id,
        "job_id": post.job_id,
        "user_id": post.user_id,
        "platform": post.platform,
        "post_url": post.post_url,
        "post_title": post.post_title,
        "post_body": post.post_body,
        "source": post.source,
        "status": post.status,
        "submitted_at": post.submitted_at,
        "review_deadline": post.review_deadline,
        "reviewed_at": post.reviewed_at,
        "reject_reason": post.reject_reason,
    }


def snapshot_public(snap: MetricSnapshot) -> dict:
    return {
        "id": snap.id,
        "post_id": snap.post_id,
        "likes": snap.likes,
        "collects": snap.collects,
        "comments": snap.comments,
        "shares": snap.shares,
        "engagement": snap.engagement,
        "source": snap.source,
        "captured_at": snap.captured_at,
    }


def ocr_public(row: OcrResult) -> dict:
    return {
        "id": row.id,
        "post_id": row.post_id,
        "image_url": row.image_url,
        "raw_text": row.raw_text,
        "parsed": row.parsed,
        "model": row.model,
        "confidence": float(row.confidence),
        "mismatch_flag": row.mismatch_flag,
        "is_active": row.is_active,
        "created_at": row.created_at,
    }


def countdown_seconds(post: SocialPost, *, now: datetime | None = None) -> int:
    """距审核截止还有多少秒。已过期即为负——前端据此显示「已超时」。

    不夹到 0：把负数抹平会让「已超时 3 小时」和「刚到期」显示成同一个样子，
    而商户正需要这个差别来决定先看哪条。
    """
    at = now or utcnow()
    return int((post.review_deadline - at).total_seconds())


async def latest_snapshot(
    session: AsyncSession, post_id: int
) -> MetricSnapshot | None:
    """最新一条快照（按 `captured_at`，同刻再按 `id`）。

    不按 `id` 单独排：采纳识别结果时会插一条 `captured_at` **更早**的快照，
    只认 `id` 的话「最新」会指向一条更旧的采集数据。
    """
    return await session.scalar(
        select(MetricSnapshot)
        .where(MetricSnapshot.post_id == post_id)
        .order_by(MetricSnapshot.captured_at.desc(), MetricSnapshot.id.desc())
        .limit(1)
    )


async def post_detail(
    session: AsyncSession, post: SocialPost, *, now: datetime | None = None
) -> dict:
    """列表项：作品本体 + 倒计时 + 最新快照。

    前端只展示数字与倒计时（spec「明确不做」：不做趋势图），所以这几项就是
    列表页需要的全部。用户侧与商户侧共用，两边看到的数字不该有两套口径。
    """
    snap = await latest_snapshot(session, post.id)
    return {
        **post_public(post),
        "countdown_seconds": countdown_seconds(post, now=now),
        "latest_snapshot": snapshot_public(snap) if snap else None,
    }
