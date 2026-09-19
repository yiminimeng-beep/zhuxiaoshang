"""03-studio 的表结构。

表名、列名、约束严格照 `specs/03-studio/spec.md`——测试用裸 SQL 直接断言，
所以这里的名字就是契约。

八张表分两类：

- **流水线**：`content_job` 是主干，`job_input_asset` / `chat_message` /
  `prompt_draft` / `gen_output` / `job_event` / `guard_result` 都挂在它下面。
  `guard_result` 用 `job_id` 直接做主键（一 job 一次预检，天然一对一）。
- **模板**：`prompt_template` 挂在商户上，与 job 无外键关系（复制是「拿走一份」，
  不是「挂个引用」——见 spec 可见性规则 3）。

`budget_alert` 不在本模块的 spec 里——它的权威定义在 06-admin（成本熔断告警），
但 03 的建 job 熔断与 07 的日限额都要写它，故提前建表。等 06 落地后应搬过去。
"""

from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

JOB_KINDS = ("copy", "video")
# 每种 kind 的「主 op」——预扣上界与报销基数都取自它的计价行。
# 放在这里而不是某个 api 模块：建 job（03）与报销（07）都要用，
# 两处各写一份迟早会分家。
PRIMARY_OP = {"copy": "chat", "video": "generate"}
JOB_STATUSES = (
    "created",
    "guarding",
    "guard_failed",
    "chatting",
    "generating",
    "judging",
    "ready",
    "need_review",
    "failed",
)
# 已产出/已转人工的 job 不得软删（spec「删除」边界）
DELETABLE_STATUSES = ("created", "guard_failed")

MIME_TYPES = ("image/jpeg", "image/png", "image/webp")
MAX_ASSET_BYTES = 20 * 1024 * 1024
MAX_ASSETS = 9

CHAT_ROLES = ("user", "assistant")
MAX_CHAT_CHARS = 4000
MAX_CHAT_ROUNDS = 20

MAX_PROMPT_CHARS = 2000
MAX_ITERATIONS = 3
MAX_RETRIES = 3

MAX_TEMPLATE_TAGS = 5
MAX_TAG_CHARS = 16


def _in(column: str, values: tuple[str, ...]) -> str:
    joined = ", ".join(f"'{v}'" for v in values)
    return f"{column} IN ({joined})"


class ContentJob(Base):
    __tablename__ = "content_job"
    __table_args__ = (
        CheckConstraint(_in("kind", JOB_KINDS), name="ck_content_job_kind"),
        CheckConstraint(_in("status", JOB_STATUSES), name="ck_content_job_status"),
        CheckConstraint(
            "retry_count >= 0 AND retry_count <= 3", name="ck_content_job_retry_count"
        ),
        Index("ix_content_job_user_id", "user_id"),
        # 06 的成本看板按 `job → task → merchant_id` 聚合，`task_id` 是那条
        # join 的入口。spec 明列了这条索引。
        Index("ix_content_job_task_id", "task_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("task.id"), nullable=False
    )
    claim_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("task_claim.id"), nullable=False
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("user.id"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="created", index=True
    )
    provider: Mapped[str | None] = mapped_column(String(32))
    billing_source: Mapped[str | None] = mapped_column(String(16))
    fail_reason: Mapped[str | None] = mapped_column(Text)
    retry_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class JobInputAsset(Base):
    __tablename__ = "job_input_asset"
    __table_args__ = (
        CheckConstraint(_in("mime", MIME_TYPES), name="ck_job_input_asset_mime"),
        CheckConstraint("size_bytes >= 1", name="ck_job_input_asset_size"),
        CheckConstraint(
            "sort_order >= 0 AND sort_order <= 8", name="ck_job_input_asset_sort"
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    job_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("content_job.id"), nullable=False, index=True
    )
    url: Mapped[str] = mapped_column(String(512), nullable=False)
    mime: Mapped[str] = mapped_column(String(32), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")


class ChatMessage(Base):
    __tablename__ = "chat_message"
    __table_args__ = (
        CheckConstraint(_in("role", CHAT_ROLES), name="ck_chat_message_role"),
        Index("ix_chat_message_job_id", "job_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    job_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("content_job.id"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str | None] = mapped_column(String(64))
    tokens: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class PromptDraft(Base):
    """一 job 一份。复写失败不抛错——回落 `raw_prompt` 并置 `rewrite_failed`。"""

    __tablename__ = "prompt_draft"
    __table_args__ = (
        CheckConstraint(
            "char_length(raw_prompt) >= 1 AND char_length(raw_prompt) <= 2000",
            name="ck_prompt_draft_raw_len",
        ),
        CheckConstraint(
            "quality_score IS NULL OR (quality_score >= 0 AND quality_score <= 100)",
            name="ck_prompt_draft_score",
        ),
        CheckConstraint(
            "iterations >= 0 AND iterations <= 3", name="ck_prompt_draft_iterations"
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    job_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("content_job.id"),
        nullable=False,
        unique=True,
    )
    raw_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    optimized_prompt: Mapped[str | None] = mapped_column(Text)
    quality_score: Mapped[int | None] = mapped_column(Integer)
    iterations: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    rewrite_failed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    optimizer_model: Mapped[str | None] = mapped_column(String(64))


class GenOutput(Base):
    """`is_active=true` 的**有且仅有 1 条**——重试时旧的置 false。"""

    __tablename__ = "gen_output"
    __table_args__ = (
        CheckConstraint(_in("type", JOB_KINDS), name="ck_gen_output_type"),
        CheckConstraint(
            "judge_score IS NULL OR (judge_score >= 0 AND judge_score <= 100)",
            name="ck_gen_output_judge_score",
        ),
        CheckConstraint("cost_cents >= 0", name="ck_gen_output_cost"),
        CheckConstraint("attempt >= 1", name="ck_gen_output_attempt"),
        Index("ix_gen_output_job_id", "job_id"),
        # 成本看板**唯一**的窗口维度。spec 明列了这条索引。
        Index("ix_gen_output_created_at", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    job_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("content_job.id"), nullable=False
    )
    type: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str | None] = mapped_column(Text)
    url: Mapped[str | None] = mapped_column(String(512))
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    # spec「模型选择与 BYOK」：byok 产物要能对上账（cost_cents=0 + 标记）
    billing_source: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="platform"
    )
    cost_cents: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    judge_score: Mapped[int | None] = mapped_column(Integer)
    judge_detail: Mapped[dict | None] = mapped_column(JSONB)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class JobEvent(Base):
    """SSE 的落点。只追加，按 `created_at` 回放。"""

    __tablename__ = "job_event"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    job_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("content_job.id"), nullable=False, index=True
    )
    stage: Mapped[str] = mapped_column(String(32), nullable=False)
    detail: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class GuardResult(Base):
    """一 job 一条，故 `job_id` 直接做主键。"""

    __tablename__ = "guard_result"

    job_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("content_job.id"), primary_key=True
    )
    passed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    model: Mapped[str] = mapped_column(String(64), nullable=False)
    detail: Mapped[dict | None] = mapped_column(JSONB)


class PromptTemplate(Base):
    __tablename__ = "prompt_template"
    __table_args__ = (
        CheckConstraint(
            "char_length(name) >= 2 AND char_length(name) <= 64",
            name="ck_prompt_template_name_len",
        ),
        CheckConstraint(
            "char_length(content) >= 10 AND char_length(content) <= 2000",
            name="ck_prompt_template_content_len",
        ),
        CheckConstraint("usage_count >= 0", name="ck_prompt_template_usage"),
        Index("ix_prompt_template_merchant_id", "merchant_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    merchant_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("user.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str | None] = mapped_column(String(32))
    tags: Mapped[list | None] = mapped_column(JSONB)
    is_public: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    source_template_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("prompt_template.id")
    )
    usage_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class BudgetAlert(Base):
    """成本熔断告警。**表结构的权威定义在 06-admin 的 spec**，这里只是提前建表。

    唯一约束 `(merchant_id, alert_date)` 就是那条「一天一条」——否则告警本身会刷屏。
    """

    __tablename__ = "budget_alert"
    __table_args__ = (
        UniqueConstraint("merchant_id", "alert_date", name="uq_budget_alert_day"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    merchant_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("user.id"), nullable=False
    )
    alert_date: Mapped[date] = mapped_column(Date, nullable=False)
    spend_cents: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    limit_cents: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
