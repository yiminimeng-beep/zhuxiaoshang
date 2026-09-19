"""对外的 JSON 形状。

**口令相关字段一律不出现在这里**——`specs/01-auth/spec.md` 要求
`password_hash` 不得进入任何接口响应，测试也直接断言响应体里不含 "password"。
"""

from app.models.user import MerchantProfile, User


def merchant_profile_public(profile: MerchantProfile) -> dict:
    return {
        "user_id": profile.user_id,
        "shop_name": profile.shop_name,
        "category": profile.category,
        "address": profile.address,
        "logo_url": profile.logo_url,
        "description": profile.description,
        "contact": profile.contact,
    }


def user_public(
    user: User, profile: MerchantProfile | None = None
) -> dict:
    data = {
        "id": user.id,
        "account": user.account,
        "email": user.email,
        "phone": user.phone,
        "role": user.role,
        "nickname": user.nickname,
        "avatar_url": user.avatar_url,
        "status": user.status,
        "email_verified": user.email_verified,
        "created_at": user.created_at.isoformat() if user.created_at else None,
        "last_login_at": (
            user.last_login_at.isoformat() if user.last_login_at else None
        ),
    }
    if profile is not None:
        data["merchant_profile"] = merchant_profile_public(profile)
    return data
