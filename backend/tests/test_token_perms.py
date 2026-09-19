"""07-token · 权限（G 组）。

对应 test_plan.md 的 `P-01` ~ `P-05`。

跨租户一律 **403 而不是 404**：404 会把「这个 id 存不存在」透出去，
等于给攻击者一个枚举接口。这条与 02 的 `MG-*` / 01 的 `O-03` 是同一条规矩。
"""

import pytest

from tests.helpers import (
    admin_token,
    bearer,
    quota_account_for,
)

pytestmark = pytest.mark.asyncio

MERCHANT_QUOTA_PATHS = (
    "/api/merchant/quota",
    "/api/merchant/quota/ledger",
    "/api/merchant/quota/usage",
    "/api/merchant/quota/price",
    "/api/merchant/quota/debt",
    "/api/merchant/quota/recharges",
)

ADMIN_QUOTA_PATHS = (
    "/api/admin/quota/accounts",
    "/api/admin/quota/byok",
)


async def test_p01_me_endpoints_401(client):
    for path in ("/api/me/quota", "/api/me/quota/ledger", "/api/me/model-keys"):
        r = await client.get(path)
        assert r.status_code == 401, f"{path} 无 token 应 401：{r.status_code} {r.text}"


async def test_p02_customer_merchant_403(client, customer):
    for path in MERCHANT_QUOTA_PATHS:
        r = await client.get(path, headers=bearer(customer[1]))
        assert r.status_code == 403, f"{path} 客户应 403：{r.status_code} {r.text}"


async def test_p03_merchant_admin_403(client, merchant, seed_accounts):
    for path in ADMIN_QUOTA_PATHS:
        r = await client.get(path, headers=bearer(merchant[1]))
        assert r.status_code == 403, f"{path} 商户应 403：{r.status_code} {r.text}"


async def test_p04_cross_account_403(client, merchant, merchant_b, seed_accounts, db):
    """商户 A 用 B 的账户 id 查 admin 额度详情 → 403，**不是 404**。

    `merchant_b` 必须显式声明：建 `shop0002` 的是它，不是 `seed_accounts`。
    漏了它，下面那句查库会返回 None，用例崩在 `NotNullViolation`，
    看起来像实现的锅，其实是这里的 fixture 少写了一个。
    """
    token = await admin_token(client)
    assert token, "管理员令牌要能拿到，否则这条测不到"

    b_id = await db.fetchval("SELECT id FROM \"user\" WHERE account = 'shop0002'")
    assert b_id is not None, "merchant_b fixture 应该已经建好 shop0002"
    await quota_account_for(db, b_id, balance=100)

    r = await client.get(
        f"/api/admin/quota/accounts/{b_id}", headers=bearer(merchant[1])
    )
    assert r.status_code == 403, (
        f"跨租户必须 403，不得用 404 泄露 id 是否存在：{r.status_code} {r.text}"
    )


async def test_p05_models_401(client):
    r = await client.get("/api/models")
    assert r.status_code == 401, r.text