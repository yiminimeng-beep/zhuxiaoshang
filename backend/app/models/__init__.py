"""ORM 模型。

表结构以 specs/<module>/spec.md 为契约；本包的内容在实现阶段逐个模块补齐。
测试通过 tests/conftest.py 的 `db` fixture（裸 SQL）直接断言表与列，
因此**表名、列名必须与 spec 完全一致**。

这里集中 import，保证 `Base.metadata` 在 `create_all` 前收集到全部表。
"""

from app.models.admin import AdminActionLog, UserFeedback  # noqa: F401
from app.models.quota import (  # noqa: F401
    ModelPrice,
    QuotaAccount,
    QuotaLedger,
    QuotaRecharge,
    QuotaReservation,
    ReimburseClaim,
    ReimburseClaimJob,
    UserModelKey,
)
from app.models.reward import (  # noqa: F401
    CashPayout,
    Coupon,
    MallItem,
    PointLedger,
    Redemption,
    RewardGrant,
    UserCoupon,
)
from app.models.studio import (  # noqa: F401
    BudgetAlert,
    ChatMessage,
    ContentJob,
    GenOutput,
    GuardResult,
    JobEvent,
    JobInputAsset,
    PromptDraft,
    PromptTemplate,
)
from app.models.task import RewardRule, Task, TaskClaim  # noqa: F401
from app.models.tracking import (  # noqa: F401
    Appeal,
    MetricSnapshot,
    OcrResult,
    ReviewLog,
    SocialPost,
    TrackEvent,
)
from app.models.user import (  # noqa: F401
    ClosedIdentifier,
    LoginAttempt,
    MerchantProfile,
    RefreshToken,
    User,
    UserDevice,
    UserFollow,
)

__all__ = [
    "AdminActionLog",
    "Appeal",
    "BudgetAlert",
    "CashPayout",
    "ChatMessage",
    "ClosedIdentifier",
    "ContentJob",
    "Coupon",
    "GenOutput",
    "GuardResult",
    "JobEvent",
    "JobInputAsset",
    "LoginAttempt",
    "MallItem",
    "MerchantProfile",
    "MetricSnapshot",
    "ModelPrice",
    "OcrResult",
    "PointLedger",
    "PromptDraft",
    "PromptTemplate",
    "QuotaAccount",
    "QuotaLedger",
    "QuotaRecharge",
    "QuotaReservation",
    "Redemption",
    "RefreshToken",
    "ReimburseClaim",
    "ReimburseClaimJob",
    "ReviewLog",
    "RewardGrant",
    "RewardRule",
    "SocialPost",
    "Task",
    "TaskClaim",
    "TrackEvent",
    "User",
    "UserCoupon",
    "UserDevice",
    "UserFollow",
    "UserFeedback",
    "UserModelKey",
]
