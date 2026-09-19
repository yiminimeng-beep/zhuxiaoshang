"""07-token · 管理员额度记账（E 组）。

对应 test_plan.md 的 `E-12` ~ `E-18` / `RC-01` ~ `RC-07` / `AD-01` ~ `AD-15`。

本期只有**线下对公转账 + 管理员记账**这一条充值通道（在线支付涉二清，明确不做）。
所以「谁在什么时候改了谁的钱」必须留痕——`admin_action_log` 是账务底线，不是可选项。
"""

from datetime import datetime, timedelta, timezone

import pytest

from tests.helpers import (
    admin_token,
    assert_route_registered,
    bearer,
    grant,
    insert_ledger,
    insert_price,
    quota_account_for,
)

pytestmark = pytest.mark.asyncio


async def _recharge(client, token, user_id, **body):
    body.setdefault("amount_cents", 5000)
    body.setdefault("points", 5000)
    return await client.post(
        f"/api/admin/quota/accounts/{user_id}/recharge",
        headers=bearer(token),
        json=body,
    )


# --------------------------------------------------------------------------- #
# 记账充值
# --------------------------------------------------------------------------- #
async def test_e12_recharge(client, seed_accounts, merchant, db):
    token = await admin_token(client)
    uid = merchant[0]["id"]
    await quota_account_for(db, uid, balance=0)

    r = await _recharge(client, token, uid)
    assert r.status_code == 201, r.text

    body = r.json()
    assert body["balance"] == 5000
    assert "recharge" in body


async def test_rc01_points_zero_422(client, seed_accounts, merchant):
    token = await admin_token(client)
    r = await _recharge(client, token, merchant[0]["id"], points=0)
    assert r.status_code == 422, r.text


async def test_rc02_points_negative_422(client, seed_accounts, merchant):
    token = await admin_token(client)
    r = await _recharge(client, token, merchant[0]["id"], points=-1)
    assert r.status_code == 422, r.text


async def test_rc03_amount_zero_422(client, seed_accounts, merchant):
    token = await admin_token(client)
    r = await _recharge(client, token, merchant[0]["id"], amount_cents=0)
    assert r.status_code == 422, r.text


async def test_rc04_channel_online_422(client, seed_accounts, merchant):
    token = await admin_token(client)
    r = await _recharge(client, token, merchant[0]["id"], channel="online")
    assert r.status_code == 422, "本期只有 manual 一条通道，在线支付涉二清，明确不做"


async def test_rc05_unknown_account_404(client, seed_accounts):
    assert_route_registered("POST", "/api/admin/quota/accounts/{account_id}/recharge")
    token = await admin_token(client)
    r = await _recharge(client, token, 999999)
    assert r.status_code == 404, r.text


async def test_rc06_recharge_writes_ledger(client, seed_accounts, merchant, db):
    token = await admin_token(client)
    uid = merchant[0]["id"]
    await quota_account_for(db, uid, balance=100)

    r = await _recharge(client, token, uid, amount_cents=5000, points=5000)
    assert r.status_code == 201, r.text

    recharge_count = await db.fetchval(
        "SELECT count(*) FROM quota_recharge WHERE user_id = $1", uid
    )
    assert recharge_count == 1, "充值必须落一条充值单"

    ledger = await db.fetch(
        "SELECT * FROM quota_ledger WHERE user_id = $1", uid
    )
    assert len(ledger) == 1
    assert ledger[0]["source"] == "recharge"
    assert ledger[0]["change"] == 5000

    total = await db.fetchval(
        "SELECT total_recharged FROM quota_account WHERE user_id = $1", uid
    )
    assert total == 5000


async def test_rc07_balance_after_correct(client, seed_accounts, merchant, db):
    token = await admin_token(client)
    uid = merchant[0]["id"]
    await quota_account_for(db, uid, balance=100, reserved=30)

    await _recharge(client, token, uid, amount_cents=5000, points=5000)

    row = await db.fetchrow(
        "SELECT balance, reserved FROM quota_account WHERE user_id = $1", uid
    )
    assert row["balance"] == 5100
    assert row["reserved"] == 30, "充值不得动预占"

    after = await db.fetchval(
        "SELECT balance_after FROM quota_ledger WHERE user_id = $1", uid
    )
    assert after == 5100, "balance_after 必须等于充值后的真实余额"


# --------------------------------------------------------------------------- #
# 账户列表与详情
# --------------------------------------------------------------------------- #
async def test_e13_admin_accounts(client, seed_accounts, merchant, db):
    token = await admin_token(client)
    await grant(db, merchant[0]["id"], 500)

    r = await client.get(
        "/api/admin/quota/accounts", headers=bearer(token)
    )
    assert r.status_code == 200, r.text

    body = r.json()
    for key in ("items", "total", "page", "size"):
        assert key in body
    assert body["total"] >= 1


async def test_ad01_bad_role_422(client, seed_accounts):
    token = await admin_token(client)
    r = await client.get(
        "/api/admin/quota/accounts",
        headers=bearer(token),
        params={"role": "banana"},
    )
    assert r.status_code == 422, r.text


async def test_ad02_role_filter(client, seed_accounts, merchant, customer, db):
    token = await admin_token(client)
    await grant(db, merchant[0]["id"], 500)
    await grant(db, customer[0]["id"], 100)

    r = await client.get(
        "/api/admin/quota/accounts",
        headers=bearer(token),
        params={"role": "merchant"},
    )
    assert r.status_code == 200, r.text

    roles = {i["role"] for i in r.json()["items"]}
    assert roles == {"merchant"}, f"role=merchant 只该出商户，实际 {roles}"


async def test_ad03_keyword_filter(client, seed_accounts, merchant, db):
    token = await admin_token(client)
    await grant(db, merchant[0]["id"], 500)

    r = await client.get(
        "/api/admin/quota/accounts",
        headers=bearer(token),
        params={"keyword": "shop0001"},
    )
    assert r.status_code == 200, r.text
    assert any("shop0001" in str(i.get("account", "")) for i in r.json()["items"])


async def test_ad04_no_token_401(client):
    r = await client.get("/api/admin/quota/accounts")
    assert r.status_code == 401, r.text


async def test_ad05_merchant_403(client, merchant):
    r = await client.get(
        "/api/admin/quota/accounts", headers=bearer(merchant[1])
    )
    assert r.status_code == 403, r.text


async def test_e14_account_detail(client, seed_accounts, merchant, db):
    token = await admin_token(client)
    uid = merchant[0]["id"]
    await grant(db, uid, 500)
    await insert_ledger(db, uid, -100, 400, source="consume")

    r = await client.get(
        f"/api/admin/quota/accounts/{uid}", headers=bearer(token)
    )
    assert r.status_code == 200, r.text

    body = r.json()
    for key in ("account", "recent_ledger", "by_day", "byok_call_count"):
        assert key in body, f"账户详情缺字段 {key}"


async def test_ad06_unknown_detail_404(client, seed_accounts):
    assert_route_registered("GET", "/api/admin/quota/accounts/{account_id}")
    token = await admin_token(client)
    r = await client.get(
        "/api/admin/quota/accounts/999999", headers=bearer(token)
    )
    assert r.status_code == 404, r.text


# --------------------------------------------------------------------------- #
# 人工调整
# --------------------------------------------------------------------------- #
async def test_e15_adjust(client, seed_accounts, merchant, db):
    token = await admin_token(client)
    uid = merchant[0]["id"]
    await quota_account_for(db, uid, balance=100)

    r = await client.post(
        f"/api/admin/quota/accounts/{uid}/adjust",
        headers=bearer(token),
        json={"points": 50, "remark": "线下补偿"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["balance"] == 150


async def test_ad07_adjust_zero_422(client, seed_accounts, merchant, db):
    token = await admin_token(client)
    uid = merchant[0]["id"]
    await quota_account_for(db, uid, balance=100)

    r = await client.post(
        f"/api/admin/quota/accounts/{uid}/adjust",
        headers=bearer(token),
        json={"points": 0, "remark": "没有变化"},
    )
    assert r.status_code == 422, r.text


async def test_ad08_remark_too_short_422(client, seed_accounts, merchant, db):
    token = await admin_token(client)
    uid = merchant[0]["id"]
    await quota_account_for(db, uid, balance=100)

    r = await client.post(
        f"/api/admin/quota/accounts/{uid}/adjust",
        headers=bearer(token),
        json={"points": 50, "remark": "短"},
    )
    assert r.status_code == 422, "人工调账必须有可追溯的理由，备注过短直接拒"


async def test_ad09_negative_after_422(client, seed_accounts, merchant, db):
    token = await admin_token(client)
    uid = merchant[0]["id"]
    await quota_account_for(db, uid, balance=10)

    r = await client.post(
        f"/api/admin/quota/accounts/{uid}/adjust",
        headers=bearer(token),
        json={"points": -50, "remark": "尝试扣成负数"},
    )
    assert r.status_code == 422, r.text

    balance = await db.fetchval(
        "SELECT balance FROM quota_account WHERE user_id = $1", uid
    )
    assert balance == 10, "被拒后余额不得改动"


# --------------------------------------------------------------------------- #
# 冻结 / 解冻
# --------------------------------------------------------------------------- #
async def test_e16_admin_freeze(client, seed_accounts, merchant, db):
    token = await admin_token(client)
    uid = merchant[0]["id"]
    await quota_account_for(db, uid)

    r = await client.post(
        f"/api/admin/quota/accounts/{uid}/status",
        headers=bearer(token),
        json={"status": "frozen", "reason": "风控核查中"},
    )
    assert r.status_code == 200, r.text

    status = await db.fetchval(
        "SELECT status FROM quota_account WHERE user_id = $1", uid
    )
    assert status == "frozen"


async def test_ad10_same_status_409(client, seed_accounts, merchant, db):
    token = await admin_token(client)
    uid = merchant[0]["id"]
    await quota_account_for(db, uid, status="frozen")

    r = await client.post(
        f"/api/admin/quota/accounts/{uid}/status",
        headers=bearer(token),
        json={"status": "frozen", "reason": "重复冻结"},
    )
    assert r.status_code == 409, r.text


async def test_ad11_reason_too_short_422(client, seed_accounts, merchant, db):
    token = await admin_token(client)
    uid = merchant[0]["id"]
    await quota_account_for(db, uid)

    r = await client.post(
        f"/api/admin/quota/accounts/{uid}/status",
        headers=bearer(token),
        json={"status": "frozen", "reason": "短"},
    )
    assert r.status_code == 422, r.text


# --------------------------------------------------------------------------- #
# BYOK 使用情况与计价表
# --------------------------------------------------------------------------- #
async def test_e17_byok_stats(client, seed_accounts, merchant, db):
    token = await admin_token(client)
    uid = merchant[0]["id"]
    await insert_ledger(
        db, uid, 0, 0, source="consume", billing_source="byok", provider="deepseek"
    )

    now = datetime.now(timezone.utc)
    r = await client.get(
        "/api/admin/quota/byok",
        headers=bearer(token),
        params={
            "from": (now - timedelta(days=1)).date().isoformat(),
            "to": (now + timedelta(days=1)).date().isoformat(),
        },
    )
    assert r.status_code == 200, r.text

    items = r.json()["items"]
    assert items
    for key in ("provider", "call_count", "zero_cost_count", "invalid_key_count"):
        assert key in items[0], f"BYOK 统计缺字段 {key}"


async def test_ad12_bad_range_422(client, seed_accounts):
    token = await admin_token(client)
    r = await client.get(
        "/api/admin/quota/byok",
        headers=bearer(token),
        params={"from": "2026-03-01", "to": "2026-01-01"},
    )
    assert r.status_code == 422, r.text


async def test_e18_admin_create_price(client, seed_accounts):
    token = await admin_token(client)
    r = await client.post(
        "/api/admin/quota/prices",
        headers=bearer(token),
        json={
            "provider": "dashscope",
            "model": "qwen-max",
            "op": "chat",
            "unit": "token",
            "cost_price_per_unit": 0.000003,
            "price_per_unit": 0.000006,
            "max_price_per_call": 200,
            "markup_rate": 2.00,
            "supports_byok": True,
            "provider_visible": True,
            "effective_from": datetime.now(timezone.utc).isoformat(),
        },
    )
    assert r.status_code == 201, r.text


async def test_ad13_duplicate_price_409(client, seed_accounts, db):
    token = await admin_token(client)
    eff = datetime.now(timezone.utc)
    await insert_price(
        db, "dashscope", "qwen-max", "chat", effective_from=eff
    )

    r = await client.post(
        "/api/admin/quota/prices",
        headers=bearer(token),
        json={
            "provider": "dashscope",
            "model": "qwen-max",
            "op": "chat",
            "unit": "token",
            "cost_price_per_unit": 0.000003,
            "price_per_unit": 0.000006,
            "max_price_per_call": 200,
            "markup_rate": 2.00,
            "supports_byok": True,
            "provider_visible": True,
            "effective_from": eff.isoformat(),
        },
    )
    assert r.status_code == 409, r.text


async def test_ad14_cross_merchant_403(client, seed_accounts, merchant, merchant_b, db):
    """商户 A 拿商户 B 的账户 id 去查额度详情 → 403，不是 404。

    404 会把「这个 id 存不存在」透给调用方；403 只说「你没权限」。
    `merchant_b` fixture 只给令牌，B 的 user id 得查库——它没返回 body。
    """
    b_id = await db.fetchval(
        "SELECT id FROM \"user\" WHERE account = 'shop0002'"
    )
    assert b_id is not None, "merchant_b fixture 应该已经建好 shop0002"

    r = await client.get(
        f"/api/admin/quota/accounts/{b_id}", headers=bearer(merchant[1])
    )
    assert r.status_code == 403, (
        f"admin 端点对非 admin 一律 403，不得用 404 泄露 id 是否存在：{r.status_code} {r.text}"
    )


async def test_ad15_admin_action_logged(client, seed_accounts, merchant, db):
    """充值 / 调整 / 冻结各自写一条 `admin_action_log`（形状见 06 spec）。

    查询用 spec 的形状：`target_type='quota_account'` + `target_id`，
    不是 07 早期那套 `operator_id` / `target_user_id`。
    """
    token = await admin_token(client)
    uid = merchant[0]["id"]
    await quota_account_for(db, uid, balance=100)

    await _recharge(client, token, uid)
    await client.post(
        f"/api/admin/quota/accounts/{uid}/adjust",
        headers=bearer(token),
        json={"points": 50, "remark": "线下补偿"},
    )
    await client.post(
        f"/api/admin/quota/accounts/{uid}/status",
        headers=bearer(token),
        json={"status": "frozen", "reason": "风控核查中"},
    )

    rows = await db.fetch(
        "SELECT action, detail FROM admin_action_log "
        "WHERE target_type = 'quota_account' AND target_id = $1",
        uid,
    )
    actions = {r["action"] for r in rows}
    assert len(rows) >= 3, f"三类操作各要留一条痕，实际 {rows}"
    assert actions == {"quota_recharge", "quota_adjust", "quota_status"}, actions
    assert all(r["detail"] for r in rows), f"三类操作都要带 detail：{rows}"
