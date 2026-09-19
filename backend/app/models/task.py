"""02-task 的表结构。

表名、列名、约束严格照 `specs/02-task/spec.md`——测试用裸 SQL 直接断言，
所以这里的名字就是契约。
"""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

PAY_MODES = ("merchant_pay", "user_pay_reimburse")
TASK_STATUSES = ("draft", "published", "paused", "closed")
CLAIM_STATUSES = ("in_progress", "submitted", "closed")


class Task(Base):
    __tablename__ = "task"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    merchant_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("user.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    cover_url: Mapped[str | None] = mapped_column(String(512))
    category: Mapped[str] = mapped_column(String(32), nullable=False)
    requirement: Mapped[str | None] = mapped_column(Text)
    tags: Mapped[list | None] = mapped_column(JSONB)

    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    quota: Mapped[int | None] = mapped_column(Integer)
    claimed_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )

    pay_mode: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default="merchant_pay"
    )
    reimburse_pool: Mapped[int | None] = mapped_column(Integer)
    reimburse_pool_used: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    reimburse_pool_reserved: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    reimburse_per_user_limit: Mapped[int | None] = mapped_column(Integer)

    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="draft"
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index("ix_task_status_created", "status", "created_at"),
        Index("ix_task_merchant", "merchant_id"),
    )


class RewardRule(Base):
    __tablename__ = "reward_rule"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("task.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    metric: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default="engagement"
    )
    tiers: Mapped[list] = mapped_column(JSONB, nullable=False)
    max_reward_per_user: Mapped[int | None] = mapped_column(Integer)


class TaskClaim(Base):
    __tablename__ = "task_claim"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("task.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("user.id", ondelete="CASCADE"), nullable=False
    )
    claimed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="in_progress"
    )

    __table_args__ = (
        # 不是整表唯一，而是**部分**唯一：放弃过的领取（status='closed'）让出位置，
        # 同一用户才能重新领取（spec「放弃后同一用户可重新领取 → 201」）。
        # 整表唯一的话第二次领取会撞唯一键，只能靠删行绕过，领取历史就没了。
        Index(
            "uq_task_claim_active",
            "task_id",
            "user_id",
            unique=True,
            postgresql_where=text("status <> 'closed'"),
        ),
    )
