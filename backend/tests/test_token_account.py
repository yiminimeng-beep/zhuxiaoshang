"""07-token · 额度账户（用户侧 B 组 + 商户侧 C 组）。

对应 test_plan.md 的 `E-02` ~ `E-08` / `Q-*` / `C-*` / `L-*` / `S-*`。

账户只有一个核心不变量：**`available == balance - reserved`**。
`reserved` 是「已承诺但未发生」的钱，它必须从可用额里扣掉——否则同一笔钱
既能预扣给 job，又能在别处花掉。
"""

import pytest

from tests.helpers import (
    bearer,
    grant,
    insert_ledger,
    insert_price,
    quota_account_for,
)

pytestmark = pytest.mark.asyncio


# --------------------------------------------------------------------------- #
# B. 用户侧
# --------------------------------------------------------------------------- #
async def test_e02_my_quota(client, customer, db):
    await grant(db, customer[0]["id"], 100)

    r = await client.get("/api/me/quota", headers=bearer(customer[1]))
    assert r.status_code == 200, r.text

    body = r.json()
    for key in (
        "balance",
        "reserved",
        "available",
        "debt",
        "total_reimburse_in",
        "user_daily_limit",
        "status",
    ):
        assert key in body, f"额度响应缺字段 {key}"


async def test_q01_no_token_401(client):
    r = await client.get("/api/me/quota")
    assert r.status_code == 401, r.text


async def test_q02_no_row_returns_zeros(client, customer):
    r = await client.get("/api/me/quota", headers=bearer(customer[1]))
    assert r.status_code == 200, (
        f"全新用户没有账户行时不得 404，否则首屏就报错：{r.status_code} {r.text}"
    )

    body = r.json()
    assert body["balance"] == 0
    assert body["reserved"] == 0
    assert body["available"] == 0
    assert body["status"] == "active"


async def test_q03_available_identity(client, customer, db):
    await quota_account_for(db, customer[0]["id"], balance=100, reserved=30)

    r = await client.get("/api/me/quota", headers=bearer(customer[1]))
    assert r.status_code == 200, r.text

    body = r.json()
    assert body["available"] == 70
    assert body["available"] == body["balance"] - body["reserved"]


async def test_q04_debt_visible(client, customer, db):
    await quota_account_for(db, customer[0]["id"], balance=0, debt=25)

    r = await client.get("/api/me/quota", headers=bearer(customer[1]))
    assert r.json()["debt"] == 25, "欠款要单独暴露，不能从可用额里悄悄抵掉"


async def test_q05_frozen_still_readable(client, customer, db):
    await quota_account_for(db, customer[0]["id"], balance=100, status="frozen")

    r = await client.get("/api/me/quota", headers=bearer(customer[1]))
    assert r.status_code == 200, f"冻结只该拦写操作，查询仍要能看：{r.status_code} {r.text}"
    assert r.json()["status"] == "frozen"


# --------------------------------------------------------------------------- #
# C. 商户侧
# --------------------------------------------------------------------------- #
async def test_e03_merchant_quota(client, merchant, db):
    await grant(db, merchant[0]["id"], 5000)

    r = await client.get("/api/merchant/quota", headers=bearer(merchant[1]))
    assert r.status_code == 200, r.text

    body = r.json()
    for key in (
        "balance",
        "reserved",
        "available",
        "debt",
        "today_consumed",
        "today_reserved",
        "daily_limit",
        "per_user_daily_limit",
        "per_user_task_limit",
        "status",
    ):
        assert key in body, f"商户额度响应缺字段 {key}"


async def test_c01_no_token_401(client):
    r = await client.get("/api/merchant/quota")
    assert r.status_code == 401, r.text


async def test_c02_customer_403(client, customer):
    r = await client.get("/api/merchant/quota", headers=bearer(customer[1]))
    assert r.status_code == 403, r.text


async def test_c03_today_consumed(client, merchant, db):
    uid = merchant[0]["id"]
    await quota_account_for(db, uid, balance=1000)
    await insert_ledger(db, uid, -120, 880, source="consume")
    await insert_ledger(db, uid, -80, 800, source="consume")

    r = await client.get("/api/merchant/quota", headers=bearer(merchant[1]))
    assert r.status_code == 200, r.text
    assert r.json()["today_consumed"] == 200, "当日消耗应为当日 consume 绝对值之和"


async def test_e04_patch_limits(client, merchant, db):
    r = await client.patch(
        "/api/merchant/quota/limits",
        headers=bearer(merchant[1]),
        json={"daily_limit": 1000},
    )
    assert r.status_code == 200, r.text

    daily = await db.fetchval(
        "SELECT daily_limit FROM quota_account WHERE user_id = $1", merchant[0]["id"]
    )
    assert daily == 1000


async def test_l01_negative_limit_422(client, merchant):
    r = await client.patch(
        "/api/merchant/quota/limits",
        headers=bearer(merchant[1]),
        json={"daily_limit": -1},
    )
    assert r.status_code == 422, r.text


async def test_l02_below_today_422(client, merchant, db):
    uid = merchant[0]["id"]
    await grant(db, uid, 1000)
    await insert_ledger(db, uid, -300, 700, source="consume")

    r = await client.patch(
        "/api/merchant/quota/limits",
        headers=bearer(merchant[1]),
        json={"daily_limit": 100},
    )
    assert r.status_code == 422, f"限额低于今日已耗会把商户立刻卡死：{r.status_code} {r.text}"


async def test_l03_equal_today_ok(client, merchant, db):
    uid = merchant[0]["id"]
    await grant(db, uid, 1000)
    await insert_ledger(db, uid, -300, 700, source="consume")

    r = await client.patch(
        "/api/merchant/quota/limits",
        headers=bearer(merchant[1]),
        json={"daily_limit": 300},
    )
    assert r.status_code == 200, f"等于今日已耗是允许的边界：{r.status_code} {r.text}"


async def test_l04_null_clears_limit(client, merchant, db):
    uid = merchant[0]["id"]
    await client.patch(
        "/api/merchant/quota/limits",
        headers=bearer(merchant[1]),
        json={"daily_limit": 1000},
    )

    r = await client.patch(
        "/api/merchant/quota/limits",
        headers=bearer(merchant[1]),
        json={"per_user_daily_limit": None},
    )
    assert r.status_code == 200, r.text

    row = await db.fetchrow(
        "SELECT daily_limit, per_user_daily_limit FROM quota_account WHERE user_id = $1",
        uid,
    )
    assert row["per_user_daily_limit"] is None, "传 null 是清空限额，不是设成 0"
    assert row["daily_limit"] == 1000, "没传的字段保持原值"


async def test_l05_forbidden_field_400(client, merchant, db):
    uid = merchant[0]["id"]
    await grant(db, uid, 100)

    r = await client.patch(
        "/api/merchant/quota/limits",
        headers=bearer(merchant[1]),
        json={"balance": 999999, "user_id": 1},
    )
    assert r.status_code == 400, f"试图直传 balance 必须 400：{r.status_code} {r.text}"

    balance = await db.fetchval(
        "SELECT balance FROM quota_account WHERE user_id = $1", uid
    )
    assert balance == 100, "被拒之后数据库不得有半点改动"


async def test_e05_freeze_self(client, merchant, db):
    await quota_account_for(db, merchant[0]["id"], balance=100)

    r = await client.patch(
        "/api/merchant/quota/status",
        headers=bearer(merchant[1]),
        json={"status": "frozen"},
    )
    assert r.status_code == 200, r.text

    status = await db.fetchval(
        "SELECT status FROM quota_account WHERE user_id = $1", merchant[0]["id"]
    )
    assert status == "frozen"


async def test_s01_bad_enum_422(client, merchant, db):
    await quota_account_for(db, merchant[0]["id"])

    r = await client.patch(
        "/api/merchant/quota/status",
        headers=bearer(merchant[1]),
        json={"status": "banana"},
    )
    assert r.status_code == 422, r.text


async def test_s02_freeze_idempotent(client, merchant, db):
    await quota_account_for(db, merchant[0]["id"], status="frozen")

    r = await client.patch(
        "/api/merchant/quota/status",
        headers=bearer(merchant[1]),
        json={"status": "frozen"},
    )
    assert r.status_code == 200, (
        f"spec 只为 admin 的同类端点规定了「状态相同 → 409」，商户侧没规定，按幂等处理："
        f"{r.status_code} {r.text}"
    )


async def test_e06_price_list(client, merchant, db):
    await insert_price(db, "deepseek", "deepseek-chat", "chat")

    r = await client.get("/api/merchant/quota/price", headers=bearer(merchant[1]))
    assert r.status_code == 200, r.text
    assert len(r.json()["items"]) == 1


async def test_c04_customer_price_403(client, customer):
    r = await client.get("/api/merchant/quota/price", headers=bearer(customer[1]))
    assert r.status_code == 403, r.text


async def test_e07_debt_detail(client, merchant, db):
    await quota_account_for(db, merchant[0]["id"], debt=25)

    r = await client.get("/api/merchant/quota/debt", headers=bearer(merchant[1]))
    assert r.status_code == 200, r.text

    body = r.json()
    assert body["debt"] == 25
    assert "items" in body


async def test_e08_recharges(client, merchant, db):
    uid = merchant[0]["id"]
    await grant(db, uid, 5000)
    await db.execute(
        """
        INSERT INTO quota_recharge (user_id, amount_cents, points, channel, operator_id, created_at)
        VALUES ($1, 5000, 5000, 'manual', $1, now())
        """,
        uid,
    )

    r = await client.get("/api/merchant/quota/recharges", headers=bearer(merchant[1]))
    assert r.status_code == 200, r.text

    items = r.json()["items"]
    assert len(items) == 1
    for key in ("amount_cents", "points", "channel", "created_at"):
        assert key in items[0], f"充值记录缺字段 {key}"
