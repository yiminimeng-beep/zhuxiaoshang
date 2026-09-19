"""05-reward 的表结构。

表名、列名、约束严格照 `specs/05-reward/spec.md`——测试用裸 SQL 直接断言，
所以这里的名字就是契约。

## 外键一律不带 `ON DELETE CASCADE`

`point_ledger` / `reward_grant` / `cash_payout` 是**审计账**：注销一个用户
不得把「他曾经拿过多少奖励」一起抹掉。01 的 `C-09` / `C-09b` 盯的正是这条
（注销后审计行数不变）。给 `user_id` 挂上 `ondelete="CASCADE"`，那两条立刻变红。

## 四本账各管各的

| 表 | 记什么 | 幂等键 |
|---|---|---|
| `reward_grant` | 一篇作品结算了一次 | `post_id` **唯一** |
| `point_ledger` | 积分进了出 | `(source, ref_type, ref_id)` **唯一** |
| `user_coupon` | 发出去的券 | `code` **唯一** |
| `cash_payout` | 该打多少现金（本期不打） | `reward_grant_id` **唯一** |

前三道的唯一约束是「不能多发」的最后一道闸：应用层的 `if` 判断是第一道，
并发下会漏，数据库不会。
"""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
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

GRANT_STATUSES = ("granted", "below_threshold", "capped", "failed")
COUPON_TYPES = ("cash_off", "discount", "gift")
COUPON_STATUSES = ("active", "inactive")
USER_COUPON_STATUSES = ("unused", "used", "expired")
POINT_SOURCES = ("task_reward", "redemption", "refund", "adjust")
MALL_ITEM_STATUSES = ("on", "off")
REDEMPTION_STATUSES = ("pending", "confirmed", "cancelled")
PAYOUT_STATUSES = ("pending", "paid")


def _in(column: str, values: tuple[str, ...]) -> str:
    joined = ", ".join(f"'{v}'" for v in values)
    return f"{column} IN ({joined})"


class RewardGrant(Base):
    """一次结算的**唯一**凭证。`post_id` 唯一是幂等的核心。

    结算事件可能被投递多次（重试、超时扫描与人工通过撞车），靠这条唯一约束
    保证同一篇作品只发一次。上面的 `status` 一并把「没发成」记下来：
    `below_threshold`（没够档）与 `capped`（截到 0）**都要留行**，
    否则「为什么没发」事后无从追查。
    """

    __tablename__ = "reward_grant"
    __table_args__ = (
        UniqueConstraint("post_id", name="uq_reward_grant_post"),
        CheckConstraint(_in("status", GRANT_STATUSES), name="ck_reward_grant_status"),
        CheckConstraint("engagement >= 0", name="ck_reward_grant_engagement"),
        Index("ix_reward_grant_user_task", "user_id", "task_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    post_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("social_post.id"), nullable=False
    )
    claim_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("task_claim.id"), nullable=False
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("user.id"), nullable=False
    )
    task_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("task.id"), nullable=False
    )
    reward_rule_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("reward_rule.id"), nullable=False
    )
    # 结算时用于匹配档位的值（峰值快照）。落库是为了审计「当时按多少算的」：
    # 商户中途改阶梯后，这一行仍能自证它按哪一档、按什么数发的。
    engagement: Mapped[int] = mapped_column(Integer, nullable=False)
    # 没命中任何档时为 null——「没命中」与「命中第 0 档」是两件事
    tier_index: Mapped[int | None] = mapped_column(Integer)
    reward_detail: Mapped[dict] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    fail_reason: Mapped[str | None] = mapped_column(Text)
    granted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Coupon(Base):
    """商户发布的券模板。`issued` 与 `user_coupon` 行数必须始终一致。"""

    __tablename__ = "coupon"
    __table_args__ = (
        CheckConstraint(_in("type", COUPON_TYPES), name="ck_coupon_type"),
        CheckConstraint(_in("status", COUPON_STATUSES), name="ck_coupon_status"),
        CheckConstraint("total > 0", name="ck_coupon_total"),
        CheckConstraint("issued >= 0", name="ck_coupon_issued"),
        CheckConstraint("issued <= total", name="ck_coupon_issued_le_total"),
        CheckConstraint("value >= 0", name="ck_coupon_value"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    merchant_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("user.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    type: Mapped[str] = mapped_column(String(16), nullable=False)
    # 满减 = 减多少分；折扣 = 折扣百分比（85 表示 8.5 折）
    value: Mapped[int] = mapped_column(Integer, nullable=False)
    min_amount: Mapped[int | None] = mapped_column(Integer)
    total: Mapped[int] = mapped_column(Integer, nullable=False)
    issued: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    valid_from: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    valid_to: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="active"
    )


class UserCoupon(Base):
    """发到用户手上的券。`code` 全局唯一——重复生成的券号必须被拒绝。"""

    __tablename__ = "user_coupon"
    __table_args__ = (
        CheckConstraint(
            _in("status", USER_COUPON_STATUSES), name="ck_user_coupon_status"
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("user.id"), nullable=False, index=True
    )
    coupon_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("coupon.id"), nullable=False
    )
    code: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="unused"
    )
    obtained_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    # 恒等于 `coupon.valid_to`：券的有效期由模板定，发出去那一刻就冻结
    expire_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PointLedger(Base):
    """积分账本：**只追加**，不得 UPDATE / DELETE。

    `balance_after` 不是「算出来的」，是「锁住上一行再算出来的」——并发两笔
    入账时，若各读一次旧余额再写，两行会写出同一个 `balance_after`。
    """

    __tablename__ = "point_ledger"
    __table_args__ = (
        UniqueConstraint(
            "source", "ref_type", "ref_id", name="uq_point_ledger_ref"
        ),
        CheckConstraint("balance_after >= 0", name="ck_point_ledger_balance_after"),
        CheckConstraint(_in("source", POINT_SOURCES), name="ck_point_ledger_source"),
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
    remark: Mapped[str | None] = mapped_column(String(256))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class MallItem(Base):
    """商户上架的积分商品。`stock` 靠 `UPDATE ... WHERE stock >= n` 原子扣。"""

    __tablename__ = "mall_item"
    __table_args__ = (
        CheckConstraint(_in("status", MALL_ITEM_STATUSES), name="ck_mall_item_status"),
        CheckConstraint("points_cost >= 1", name="ck_mall_item_points_cost"),
        CheckConstraint("stock >= 0", name="ck_mall_item_stock"),
        CheckConstraint("sold >= 0", name="ck_mall_item_sold"),
        CheckConstraint(
            "per_user_limit IS NULL OR per_user_limit >= 1",
            name="ck_mall_item_per_user_limit",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    merchant_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("user.id"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    cover_url: Mapped[str | None] = mapped_column(String(512))
    description: Mapped[str | None] = mapped_column(Text)
    points_cost: Mapped[int] = mapped_column(Integer, nullable=False)
    stock: Mapped[int] = mapped_column(Integer, nullable=False)
    sold: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    per_user_limit: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="on"
    )


class Redemption(Base):
    """兑换单。`redeem_code` 唯一；取消要靠 `point_ledger` 的唯一约束兜底防重复退款。"""

    __tablename__ = "redemption"
    __table_args__ = (
        CheckConstraint(
            _in("status", REDEMPTION_STATUSES), name="ck_redemption_status"
        ),
        CheckConstraint("quantity >= 1", name="ck_redemption_quantity"),
        CheckConstraint("points_spent >= 0", name="ck_redemption_points_spent"),
        Index("ix_redemption_user", "user_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("user.id"), nullable=False
    )
    mall_item_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("mall_item.id"), nullable=False
    )
    points_spent: Mapped[int] = mapped_column(Integer, nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="pending"
    )
    redeem_code: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    # 只存不校验：spec「不做地址簿管理，兑换时填一次」
    address: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class CashPayout(Base):
    """现金奖励台账。**本期只记账，不打款**（spec「明确不做」）。

    `reward_grant_id` 唯一：一笔结算对应一笔应付，重复结算拿不到第二行。
    """

    __tablename__ = "cash_payout"
    __table_args__ = (
        CheckConstraint(_in("status", PAYOUT_STATUSES), name="ck_cash_payout_status"),
        CheckConstraint("amount >= 0", name="ck_cash_payout_amount"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("user.id"), nullable=False, index=True
    )
    reward_grant_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("reward_grant.id"), nullable=False, unique=True
    )
    amount: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="pending"
    )
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    operator_id: Mapped[int | None] = mapped_column(BigInteger)
