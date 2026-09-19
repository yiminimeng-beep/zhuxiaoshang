"""01-auth 的表结构。

表名、列名、约束严格照 `specs/01-auth/spec.md`——测试用裸 SQL 直接断言，
所以这里的名字就是契约，改名字等于改协议。
"""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

ROLES = ("merchant", "customer", "admin")
STATUSES = ("active", "banned", "deleted")


class User(Base):
    __tablename__ = "user"
    __table_args__ = (
        # spec 同时要「两者至少有一个」与「注销时全部置 null 以释放占用」。
        # 两条的交集：约束只对**存活**账号成立，已注销行是匿名墓碑，不适用。
        CheckConstraint(
            "account IS NOT NULL OR email IS NOT NULL OR status = 'deleted'",
            name="ck_user_identifier",
        ),
        CheckConstraint(f"role IN {ROLES}", name="ck_user_role"),
        CheckConstraint(f"status IN {STATUSES}", name="ck_user_status"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    account: Mapped[str | None] = mapped_column(String(20), unique=True)
    email: Mapped[str | None] = mapped_column(String(254), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(11), unique=True)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    nickname: Mapped[str] = mapped_column(String(32), nullable=False)
    avatar_url: Mapped[str | None] = mapped_column(String(512))
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="active"
    )
    email_verified: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    openid: Mapped[str | None] = mapped_column(String(64))
    unionid: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MerchantProfile(Base):
    __tablename__ = "merchant_profile"

    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("user.id", ondelete="CASCADE"), primary_key=True
    )
    shop_name: Mapped[str] = mapped_column(String(64), nullable=False)
    category: Mapped[str] = mapped_column(String(32), nullable=False)
    address: Mapped[str | None] = mapped_column(String(256))
    logo_url: Mapped[str | None] = mapped_column(String(512))
    description: Mapped[str | None] = mapped_column(String(512))
    contact: Mapped[str | None] = mapped_column(String(64))


class UserDevice(Base):
    __tablename__ = "user_device"
    __table_args__ = (
        UniqueConstraint("user_id", "device_fingerprint", name="uq_user_device"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("user.id", ondelete="CASCADE"), nullable=False
    )
    device_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    device_name: Mapped[str | None] = mapped_column(String(64))
    last_ip: Mapped[str | None] = mapped_column(String(45))
    last_active_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    revoked: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class RefreshToken(Base):
    __tablename__ = "refresh_token"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("user.id", ondelete="CASCADE"), nullable=False
    )
    device_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("user_device.id", ondelete="CASCADE"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    revoked: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class LoginAttempt(Base):
    __tablename__ = "login_attempt"

    identifier: Mapped[str] = mapped_column(String(254), primary_key=True)
    fail_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ClosedIdentifier(Base):
    """注销后释放掉的标识符墓碑。

    注销要求 `account`/`email`/`phone` 全部置 null 以释放占用，但登录又必须
    把「这个标识符曾属于已注销账号」与「这个标识符从未存在过」区分开
    （前者 403，后者 404）。标识符已经不在 `user` 上了，只能单独记在这里。
    唯一索引只在 `user` 上，所以墓碑**不阻碍**别人重新注册同一标识符。
    """

    __tablename__ = "closed_identifier"

    identifier: Mapped[str] = mapped_column(String(254), primary_key=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("user.id", ondelete="CASCADE"), nullable=False
    )
    closed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class UserFollow(Base):
    __tablename__ = "user_follow"
    __table_args__ = (
        UniqueConstraint("follower_id", "followee_id", name="uq_user_follow"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    follower_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("user.id", ondelete="CASCADE"), nullable=False
    )
    followee_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("user.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
