"""07-token 的表结构。

表名、列名、约束严格照 `specs/07-token/spec.md`——测试用裸 SQL 直接断言，
所以这里的名字就是契约。

跨模块外键：`quota_reservation.job_id` 与 `quota_ledger.job_id` 指向 03 的
`content_job`（03 已落地）。**唯独 `reimburse_claim.post_id` 仍是裸列**——
04 的 `social_post` 还没建，挂外键会直接报 `relation does not exist`。
它上面先压一个唯一索引（幂等语义不丢），外键等 04 落地后补。
"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

ACCOUNT_STATUSES = ("active", "frozen")

LEDGER_SOURCES = (
    "recharge",
    "consume",
    "refund",
    "adjust",
    "debt_clear",
    "reimburse_out",
    "reimburse_in",
)
BILLING_SOURCES = ("platform", "byok")

KEY_STATUSES = ("active", "invalid", "disabled")
PROVIDERS = ("deepseek", "dashscope", "jimeng", "kling")

RESERVATION_STATUSES = ("reserved", "settled", "released")
REIMBURSE_STATUSES = ("settled", "released", "capped", "pool_exhausted")

OPS = ("guard", "chat", "rewrite", "generate", "judge")
UNITS = ("token", "call", "second", "image")


def _in(column: str, values: tuple[str, ...]) -> str:
    joined = ", ".join(f"'{v}'" for v in values)
    return f"{column} IN ({joined})"


class QuotaAccount(Base):
    """用户与商户共用一套账户。`可用额 = balance - reserved`。"""

    __tablename__ = "quota_account"
    __table_args__ = (
        CheckConstraint("balance >= 0", name="ck_quota_account_balance"),
        CheckConstraint("reserved >= 0", name="ck_quota_account_reserved"),
        # 未结算的预扣不得吃掉本金——否则「已承诺但未发生」的钱会被花两次
        CheckConstraint("balance - reserved >= 0", name="ck_quota_account_available"),
        CheckConstraint(_in("status", ACCOUNT_STATUSES), name="ck_quota_account_status"),
    )

    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("user.id"), primary_key=True
    )
    balance: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    reserved: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    debt: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    total_recharged: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    total_consumed: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    total_reimburse_in: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    total_reimburse_out: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    # 商户语义：自设日预算 / 单用户单日 / 单用户单任务
    daily_limit: Mapped[int | None] = mapped_column(Integer)
    per_user_daily_limit: Mapped[int | None] = mapped_column(Integer)
    per_user_task_limit: Mapped[int | None] = mapped_column(Integer)
    # 用户语义：平台给该用户设的单日上限
    user_daily_limit: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="active"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class QuotaLedger(Base):
    """账本：**只追加**，不得 UPDATE / DELETE。

    `(source, ref_type, ref_id)` 唯一——同一笔业务重复入账会被数据库挡下，
    这是幂等的最后一道闸。
    """

    __tablename__ = "quota_ledger"
    __table_args__ = (
        UniqueConstraint(
            "source", "ref_type", "ref_id", name="uq_quota_ledger_ref"
        ),
        CheckConstraint("balance_after >= 0", name="ck_quota_ledger_balance_after"),
        CheckConstraint(_in("source", LEDGER_SOURCES), name="ck_quota_ledger_source"),
        CheckConstraint(
            "billing_source IS NULL OR " + _in("billing_source", BILLING_SOURCES),
            name="ck_quota_ledger_billing_source",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("user.id"), nullable=False, index=True
    )
    change: Mapped[int] = mapped_column(Integer, nullable=False)
    balance_after: Mapped[int] = mapped_column(Integer, nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    ref_type: Mapped[str] = mapped_column(String(32), nullable=False)
    ref_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # 03 已落地，外键补上（`content_job` 的物理删只发生在运维层面，不影响审计）
    job_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("content_job.id")
    )
    task_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("task.id"))
    # 商户付费时 = 那个真正消耗的用户；报表按它切是唯一的正确口径
    spender_id: Mapped[int | None] = mapped_column(BigInteger)
    counterparty_id: Mapped[int | None] = mapped_column(BigInteger)
    billing_source: Mapped[str | None] = mapped_column(String(16))
    provider: Mapped[str | None] = mapped_column(String(32))
    remark: Mapped[str | None] = mapped_column(String(256))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class QuotaRecharge(Base):
    """充值单。本期只有 `manual`（线下转账 + 管理员记账）。"""

    __tablename__ = "quota_recharge"
    __table_args__ = (
        CheckConstraint("amount_cents >= 1", name="ck_quota_recharge_amount"),
        CheckConstraint("points >= 1", name="ck_quota_recharge_points"),
        CheckConstraint("channel IN ('manual')", name="ck_quota_recharge_channel"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("user.id"), nullable=False
    )
    amount_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    points: Mapped[int] = mapped_column(Integer, nullable=False)
    channel: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="manual"
    )
    operator_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    remark: Mapped[str | None] = mapped_column(String(256))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class UserModelKey(Base):
    """BYOK 密钥。明文只在请求里出现一次，落库的是密文 + 掩码。"""

    __tablename__ = "user_model_key"
    __table_args__ = (
        UniqueConstraint("user_id", "provider", name="uq_user_model_key_provider"),
        CheckConstraint(_in("provider", PROVIDERS), name="ck_user_model_key_provider"),
        CheckConstraint(_in("status", KEY_STATUSES), name="ck_user_model_key_status"),
        CheckConstraint("fail_count >= 0", name="ck_user_model_key_fail_count"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("user.id"), nullable=False
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    label: Mapped[str | None] = mapped_column(String(32))
    key_cipher: Mapped[str] = mapped_column(Text, nullable=False)
    key_masked: Mapped[str] = mapped_column(String(32), nullable=False)
    key_version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="active"
    )
    fail_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ModelPrice(Base):
    """计价表，配置驱动。**改价只插新行，不改旧行**——已建 job 用建时的价。"""

    __tablename__ = "model_price"
    __table_args__ = (
        UniqueConstraint(
            "provider", "model", "op", "effective_from", name="uq_model_price_row"
        ),
        CheckConstraint(_in("op", OPS), name="ck_model_price_op"),
        CheckConstraint(_in("unit", UNITS), name="ck_model_price_unit"),
        CheckConstraint("max_price_per_call >= 0", name="ck_model_price_max"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str] = mapped_column(String(64), nullable=False)
    op: Mapped[str] = mapped_column(String(16), nullable=False)
    unit: Mapped[str] = mapped_column(String(16), nullable=False)
    cost_price_per_unit: Mapped[Decimal] = mapped_column(
        Numeric(10, 6), nullable=False
    )
    price_per_unit: Mapped[Decimal] = mapped_column(Numeric(10, 6), nullable=False)
    max_price_per_call: Mapped[int] = mapped_column(Integer, nullable=False)
    markup_rate: Mapped[Decimal] = mapped_column(
        Numeric(5, 2), nullable=False, server_default="1.00"
    )
    supports_byok: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    provider_visible: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )
    effective_from: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class QuotaReservation(Base):
    """预扣单。`job_id` 唯一——这是整条扣费链路幂等的核心。

    预扣**不写流水**（只增 `quota_account.reserved`），结算/释放才写。
    释放时 `change=0` 不写行，靠本表 `status` 审计。
    """

    __tablename__ = "quota_reservation"
    __table_args__ = (
        CheckConstraint("reserved >= 0", name="ck_quota_reservation_reserved"),
        CheckConstraint(
            "reimburse_reserved >= 0", name="ck_quota_reservation_reimburse"
        ),
        CheckConstraint(
            _in("status", RESERVATION_STATUSES), name="ck_quota_reservation_status"
        ),
        CheckConstraint(
            _in("billing_source", BILLING_SOURCES),
            name="ck_quota_reservation_billing_source",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    job_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("content_job.id"), nullable=False, unique=True
    )
    payer_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("user.id"), nullable=False, index=True
    )
    spender_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    task_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("task.id"), nullable=False
    )
    billing_source: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="platform"
    )
    reserved: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    actual: Mapped[int | None] = mapped_column(Integer)
    # 本次调用的计量单位数。报销基数 = `model_price.cost_price_per_unit × units`，
    # 而 BYOK 下 `actual` 恒为 0，故**只有这一列能算出 BYOK 该报多少**。
    units: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    # 该 job 占掉商户报销池多少（仅 user_pay_reimburse 非零）
    reimburse_reserved: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="reserved"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ReimburseClaim(Base):
    """报销单。`post_id` 唯一——同一篇过审内容只报一次。

    那条唯一索引是「只报一次」的最后一道闸：单条通过与 72h 自动通过竞争时，
    两个动作都会走到 `reimburse`，靠它保证只落一张单。
    """

    __tablename__ = "reimburse_claim"
    __table_args__ = (
        UniqueConstraint("post_id", name="uq_reimburse_claim_post"),
        CheckConstraint("base_points >= 0", name="ck_reimburse_claim_base"),
        CheckConstraint("covered_points >= 0", name="ck_reimburse_claim_covered"),
        CheckConstraint(
            _in("status", REIMBURSE_STATUSES), name="ck_reimburse_claim_status"
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # 04 已落地，外键补上
    post_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("social_post.id"), nullable=False
    )
    claim_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("task_claim.id"), nullable=False
    )
    task_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("task.id"), nullable=False
    )
    merchant_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("user.id"), nullable=False
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("user.id"), nullable=False
    )
    base_points: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    covered_points: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ReimburseClaimJob(Base):
    """一个 job 只能被报销一次——`job_id` 唯一，防重复报销。"""

    __tablename__ = "reimburse_claim_job"
    __table_args__ = (
        CheckConstraint("points >= 0", name="ck_reimburse_claim_job_points"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    reimburse_claim_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("reimburse_claim.id"), nullable=False
    )
    job_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("content_job.id"), nullable=False, unique=True
    )
    points: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
