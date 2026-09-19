"""06-admin 的表结构。

本模块自己建两张表：`admin_action_log` 与 `user_feedback`。成本看板与
今日使用量都不建表（全部从既有表聚合），`budget_alert` 由 03 提前建好、
口径的权威定义在 06 的 spec。

`admin_action_log` **只追加**：spec 明写「不得 UPDATE / DELETE」。审计表一旦
可改，它记的就不是「发生过什么」，而是「最后一次说的是什么」。

## 为什么 `detail` 是 jsonb 而不是 text

spec 的字段表写 `{reason, before, after}`。调账的前后余额、封禁的原因、
异常处理的备注，三者的形状本来就不同，塞进一个字符串会退化成「自己解析」。
jsonb 让「before/after」这种结构化断言可以直接写。

## 为什么 action 是 varchar + CHECK 而不是 PG 原生 enum

这个集合已经长过两轮：04 的 `review_log.action` 从 3 值扩到 6 值，这里的
`action` 也要同时容纳 06 的封禁类、07 的记账类、04 的申诉裁决类。PG 原生
enum 加值要 `ALTER TYPE`，而 `create_all` **不会** ALTER 已存在的类型——
那正是本项目踩过的坑（见 CLAUDE.md「改了模型约束但库里还是旧定义」）。
"""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def _in(column: str, values: tuple[str, ...]) -> str:
    quoted = ", ".join(f"'{value}'" for value in values)
    return f"{column} IN ({quoted})"


#: 06 自己产生的动作
CONTENT_ACTIONS = ("ban_user", "unban_user", "resolve_content", "resolve_ocr")
#: 07 调账三类（`admin_quota.py` 的 `_log_action` 在写）
QUOTA_ACTIONS = ("quota_recharge", "quota_adjust", "quota_status")
#: 04 的申诉裁决（06 只负责补写这条痕，端点定义在 04）
APPEAL_ACTIONS = ("appeal_accept", "appeal_reject")
#: 反馈的处理状态翻转
FEEDBACK_ACTIONS = ("resolve_feedback", "reopen_feedback")

ADMIN_ACTIONS = CONTENT_ACTIONS + QUOTA_ACTIONS + APPEAL_ACTIONS + FEEDBACK_ACTIONS

TARGET_TYPES = (
    "user",
    "content_job",
    "ocr_result",
    "quota_account",
    "appeal",
    "feedback",
)

#: 反馈分类与处理状态。varchar + CHECK，理由同 `admin_action_log.action`。
FEEDBACK_CATEGORIES = ("bug", "suggestion", "other")
FEEDBACK_STATUSES = ("open", "resolved")
#: 提交者角色。`admin` 不在其中——后台不是反馈者。
FEEDBACK_ROLES = ("merchant", "customer")

FEEDBACK_MIN_CONTENT = 5
FEEDBACK_MAX_CONTENT = 500
FEEDBACK_MAX_CONTACT = 128


class AdminActionLog(Base):
    """不可变审计：只 INSERT。改历史就等于改证据。"""

    __tablename__ = "admin_action_log"
    __table_args__ = (
        CheckConstraint(_in("action", ADMIN_ACTIONS), name="ck_admin_action_log_action"),
        CheckConstraint(_in("target_type", TARGET_TYPES), name="ck_admin_action_log_target"),
        # 操作日志列表按 target 反查（`GET /api/admin/action-logs`）
        Index("ix_admin_action_log_target", "target_type", "target_id"),
        Index("ix_admin_action_log_admin", "admin_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    admin_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("user.id"), nullable=False
    )
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    target_type: Mapped[str] = mapped_column(String(32), nullable=False)
    target_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # `{reason, before, after}` 三者按需出现，故可空
    detail: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class UserFeedback(Base):
    """商家 / 用户提交的问题反馈。

    ## `role` 是**服务端快照**，不是外键推导

    提交时不查 `user.role` 现算，而是取提交那一刻的值存下来。两处考量：
    ①列表要按角色筛，每条都回表查 `user` 会让「按角色筛」变成 join 一个
    可变列——今天筛出来的结果明天就变了；②用户角色若日后变更，
    历史反馈的归类应当停在提交那一刻（全局约定 #11 的同款精神：
    归属由服务端在写入时确定）。

    ## 外键不加 `ondelete="CASCADE"`

    与 05 一致：留痕不随人消失。用户注销后这条反馈仍在列表里，
    `account` / `nickname` 回表取（取不到就为 null），但行本身留着。

    ## 只能改处理状态

    `content` / `role` / `user_id` 三列写入后不再变；表上没有删除路径。
    这不是约定，是 spec 的硬规则，`FR-11` 会扫源码盯着它。
    """

    __tablename__ = "user_feedback"
    __table_args__ = (
        CheckConstraint(
            _in("category", FEEDBACK_CATEGORIES), name="ck_user_feedback_category"
        ),
        CheckConstraint(_in("role", FEEDBACK_ROLES), name="ck_user_feedback_role"),
        CheckConstraint(
            _in("status", FEEDBACK_STATUSES), name="ck_user_feedback_status"
        ),
        # 列表的两种主查询：按角色筛 + 倒序、按处理状态筛 + 倒序
        Index("ix_user_feedback_role_created", "role", "created_at"),
        Index("ix_user_feedback_status_created", "status", "created_at"),
        Index("ix_user_feedback_user", "user_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("user.id"), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    category: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    contact: Mapped[str | None] = mapped_column(String(FEEDBACK_MAX_CONTACT))
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="open"
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_by: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("user.id")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
