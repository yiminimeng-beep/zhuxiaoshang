"""04-tracking 的表结构。

表名、列名、约束严格照 `specs/04-tracking/spec.md`——测试用裸 SQL 直接断言，
所以这里的名字就是契约。

六张表分三层，正对 spec 的漏斗：

```
social_post           L1/L2/L3 的同一个壳：链接、来源、状态、72h 倒计时
  ├── metric_snapshot 只追加的快照（插件 / OCR / 人工），结算取峰值
  ├── ocr_result      截图识别的留痕（多张时只有最新一张 is_active）
  ├── review_log      不可变审计：每一次状态跃迁一条
  └── appeal          仅一条（post_id 唯一即「只能申诉一次」）
track_event           04 自己的告警事件（暂只用于「识别值降级」）
```

**`metric_snapshot` 只追加**：spec 明写「不覆盖」。结算取峰值这条规则
只有在历史快照都还在的前提下才成立——覆盖式写入会让「先报低位博通过、
再涨上来」变得无从发现。
"""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

PLATFORMS = ("xhs", "douyin", "kuaishou", "bilibili", "shipinhao")
POST_SOURCES = ("plugin", "screenshot", "manual")
METRIC_SOURCES = ("plugin", "ocr", "manual")
POST_STATUSES = (
    "pending",
    "approved",
    "rejected",
    "auto_approved",
    "appealed",
)
REVIEW_ACTIONS = (
    "approve",
    "reject",
    "auto_approve",
    "batch_approve",
    "appeal_accept",
    "appeal_reject",
)
APPEAL_STATUSES = ("pending", "accepted", "rejected")

# 72 小时审核窗口。写死在模型里而不是配置项：它是 spec 的核心规则，
# 不是可调的运维参数（改它等于改产品承诺）。
REVIEW_WINDOW_HOURS = 72
MIN_REJECT_REASON_CHARS = 10
MIN_APPEAL_REASON_CHARS = 10
MAX_APPEAL_REASON_CHARS = 500
MAX_BATCH_APPROVE = 50
MAX_SCREENSHOT_BYTES = 20 * 1024 * 1024
MISMATCH_CONFIDENCE = 0.70
SCREENSHOT_MIMES = ("image/jpeg", "image/png", "image/webp")
TRACK_EVENT_KINDS = ("metric_downgrade",)

# 已过审的状态：结算动作只该在这些状态上发生
APPROVED_STATUSES = ("approved", "auto_approved")


def _in(column: str, values: tuple[str, ...]) -> str:
    joined = ", ".join(f"'{v}'" for v in values)
    return f"{column} IN ({joined})"


class SocialPost(Base):
    __tablename__ = "social_post"
    __table_args__ = (
        # 同一领取下同一个链接只能交一次；「一 claim 一作品」由应用层判
        # （它的错误码是 422，与这里 409 的重复链接不是同一道闸）
        UniqueConstraint("claim_id", "post_url", name="uq_social_post_claim_url"),
        CheckConstraint(_in("platform", PLATFORMS), name="ck_social_post_platform"),
        CheckConstraint(_in("source", POST_SOURCES), name="ck_social_post_source"),
        CheckConstraint(_in("status", POST_STATUSES), name="ck_social_post_status"),
        Index("ix_social_post_status_deadline", "status", "review_deadline"),
        Index("ix_social_post_user_id", "user_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    claim_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("task_claim.id"), nullable=False
    )
    job_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("content_job.id"), nullable=False
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("user.id"), nullable=False
    )
    platform: Mapped[str] = mapped_column(String(16), nullable=False)
    post_url: Mapped[str] = mapped_column(String(1024), nullable=False)
    post_title: Mapped[str | None] = mapped_column(String(512))
    post_body: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    submitted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    review_deadline: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reviewer_id: Mapped[int | None] = mapped_column(BigInteger)
    reject_reason: Mapped[str | None] = mapped_column(Text)


class MetricSnapshot(Base):
    """只追加。**没有 UNIQUE(post_id)**——多条快照正是峰值结算的前提。"""

    __tablename__ = "metric_snapshot"
    __table_args__ = (
        CheckConstraint(
            "likes >= 0 AND collects >= 0 AND comments >= 0 AND shares >= 0",
            name="ck_metric_snapshot_non_negative",
        ),
        CheckConstraint("engagement >= 0", name="ck_metric_snapshot_engagement"),
        CheckConstraint(_in("source", METRIC_SOURCES), name="ck_metric_snapshot_source"),
        Index("ix_metric_snapshot_post_id", "post_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    post_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("social_post.id"), nullable=False
    )
    likes: Mapped[int] = mapped_column(Integer, nullable=False)
    collects: Mapped[int] = mapped_column(Integer, nullable=False)
    comments: Mapped[int] = mapped_column(Integer, nullable=False)
    shares: Mapped[int] = mapped_column(Integer, nullable=False)
    # 由后端算（likes + collects + comments），**不接受客户端传入**：
    # 让客户端填等于让它自己挑奖励档位
    engagement: Mapped[int] = mapped_column(Integer, nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    raw: Mapped[dict | None] = mapped_column(JSONB)
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class OcrResult(Base):
    __tablename__ = "ocr_result"
    __table_args__ = (
        CheckConstraint(
            "confidence >= 0 AND confidence <= 1", name="ck_ocr_result_confidence"
        ),
        Index("ix_ocr_result_post_id", "post_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    post_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("social_post.id"), nullable=False
    )
    image_url: Mapped[str] = mapped_column(String(512), nullable=False)
    raw_text: Mapped[str | None] = mapped_column(Text)
    parsed: Mapped[dict | None] = mapped_column(JSONB)
    model: Mapped[str] = mapped_column(String(64), nullable=False)
    confidence: Mapped[float] = mapped_column(Numeric(3, 2), nullable=False)
    # 与链接侧数据不一致（或模型自己没把握）→ 只能人工核对，前端不得给「一键通过」
    mismatch_flag: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    # 多张截图时只有最新一张为 true，旧的被置回 false（不删，留痕）
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ReviewLog(Base):
    """不可变审计：只 INSERT。改历史就等于改证据。"""

    __tablename__ = "review_log"
    __table_args__ = (
        CheckConstraint(_in("action", REVIEW_ACTIONS), name="ck_review_log_action"),
        Index("ix_review_log_post_id", "post_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    post_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("social_post.id"), nullable=False
    )
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    # 系统自动通过时为 null——「谁批的」这件事必须能区分人与机器
    operator_id: Mapped[int | None] = mapped_column(BigInteger)
    reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Appeal(Base):
    """`post_id` 唯一 = 「仅可申诉一次」。这条约束本身就是规则。"""

    __tablename__ = "appeal"
    __table_args__ = (
        UniqueConstraint("post_id", name="uq_appeal_post"),
        CheckConstraint(_in("status", APPEAL_STATUSES), name="ck_appeal_status"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    post_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("social_post.id"), nullable=False
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("user.id"), nullable=False
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    admin_id: Mapped[int | None] = mapped_column(BigInteger)
    admin_note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class TrackEvent(Base):
    """04 自己的告警事件。

    spec 只说「识别数字降级 → 允许，但记录告警」，没给落点。不塞进 03 的
    `job_event`（那是按 `job_id` 记流水线阶段的表），故自建一张按 `post_id`
    记事的。将来若并入统一告警中心，改查询目标即可。
    """

    __tablename__ = "track_event"
    __table_args__ = (
        CheckConstraint(_in("kind", TRACK_EVENT_KINDS), name="ck_track_event_kind"),
        Index("ix_track_event_post_id", "post_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    post_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("social_post.id"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    detail: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
