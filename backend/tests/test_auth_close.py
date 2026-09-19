"""01-auth · 注销端点正常路径 + 注销边界。

对应 test_plan.md：E-16 / C-01 ~ C-12

注销 = 软删 + 匿名化：释放标识符占用，但审计表（reward_grant / point_ledger /
review_log / cash_payout）永久保留，且能按 user_id 查到。
"""

import pytest

from tests.helpers import (
    bearer,
    login,
    login_ok,
    register_customer,
    register_merchant,
)

pytestmark = pytest.mark.asyncio


async def _logged_in(client, account="cust0001", password="pass1234"):
    r = await register_customer(client, account=account, password=password)
    body = r.json()
    body["_password"] = password
    return body


# --------------------------------------------------------------------------- #
# 端点正常路径
# --------------------------------------------------------------------------- #
async def test_e16_close_202(client, db):
    body = await _logged_in(client)
    r = await client.post(
        "/api/me/close",
        headers=bearer(body["access_token"]),
        json={"password": "pass1234", "confirm": True},
    )
    assert r.status_code == 202, r.text

    row = await db.fetchrow(
        'SELECT status, deleted_at FROM "user" WHERE id = $1', body["user"]["id"]
    )
    assert row["status"] == "deleted"
    assert row["deleted_at"] is not None


# --------------------------------------------------------------------------- #
# 边界
# --------------------------------------------------------------------------- #
async def test_c01_missing_confirm_422(client):
    body = await _logged_in(client)
    r = await client.post(
        "/api/me/close",
        headers=bearer(body["access_token"]),
        json={"password": "pass1234"},
    )
    assert r.status_code == 422, r.text


async def test_c02_wrong_password_401(client, db):
    body = await _logged_in(client)
    r = await client.post(
        "/api/me/close",
        headers=bearer(body["access_token"]),
        json={"password": "wrongpass", "confirm": True},
    )
    assert r.status_code == 401, r.text

    status = await db.fetchval(
        'SELECT status FROM "user" WHERE id = $1', body["user"]["id"]
    )
    assert status == "active", "密码错时不得改动状态"


async def test_c03_soft_deleted(client, db):
    body = await _logged_in(client)
    r = await client.post(
        "/api/me/close",
        headers=bearer(body["access_token"]),
        json={"password": "pass1234", "confirm": True},
    )
    assert r.status_code == 202, r.text

    row = await db.fetchrow(
        'SELECT status, deleted_at FROM "user" WHERE id = $1', body["user"]["id"]
    )
    assert row["status"] == "deleted"
    assert row["deleted_at"] is not None, "deleted_at 必须非空"
    assert await db.fetchval('SELECT count(*) FROM "user"') == 1, (
        "必须是软删，行不得被物理删除"
    )


async def test_c04_identifiers_freed(client, db):
    await register_customer(
        client, account="cust0001", password="pass1234", phone="13800000001",
        email="freed@example.com",
    )
    body = await login_ok(client, "cust0001", "pass1234")

    r = await client.post(
        "/api/me/close",
        headers=bearer(body["access_token"]),
        json={"password": "pass1234", "confirm": True},
    )
    assert r.status_code == 202, r.text

    row = await db.fetchrow(
        'SELECT account, email, phone FROM "user" WHERE id = $1', body["user"]["id"]
    )
    assert row["account"] is None
    assert row["email"] is None
    assert row["phone"] is None


async def test_c05_anonymized_profile(client, db):
    await register_customer(client, account="cust0001", password="pass1234")
    body = await login_ok(client, "cust0001", "pass1234")
    await client.patch(
        "/api/me",
        headers=bearer(body["access_token"]),
        json={"nickname": "真名", "avatar_url": "https://cdn.example.com/a.png"},
    )

    r = await client.post(
        "/api/me/close",
        headers=bearer(body["access_token"]),
        json={"password": "pass1234", "confirm": True},
    )
    assert r.status_code == 202, r.text

    row = await db.fetchrow(
        'SELECT nickname, avatar_url FROM "user" WHERE id = $1', body["user"]["id"]
    )
    assert row["nickname"] == "已注销用户"
    assert row["avatar_url"] is None


async def test_c06_password_hash_replaced(client, db):
    await register_customer(client, account="cust0001", password="pass1234")
    body = await login_ok(client, "cust0001", "pass1234")

    before = await db.fetchval(
        'SELECT password_hash FROM "user" WHERE id = $1', body["user"]["id"]
    )

    r = await client.post(
        "/api/me/close",
        headers=bearer(body["access_token"]),
        json={"password": "pass1234", "confirm": True},
    )
    assert r.status_code == 202, r.text

    after = await db.fetchval(
        'SELECT password_hash FROM "user" WHERE id = $1', body["user"]["id"]
    )
    assert after != before
    assert after not in (None, "")
    assert "pass1234" not in after


async def test_c07_login_after_close_403(client):
    await register_customer(client, account="cust0001", password="pass1234")
    body = await login_ok(client, "cust0001", "pass1234")
    await client.post(
        "/api/me/close",
        headers=bearer(body["access_token"]),
        json={"password": "pass1234", "confirm": True},
    )

    r = await login(client, "cust0001", "pass1234", fp="fp-after")
    assert r.status_code == 403, f"注销后原账号密码登录应 403：{r.status_code} {r.text}"


async def test_c08_account_reusable(client):
    await register_customer(client, account="cust0001", password="pass1234")
    body = await login_ok(client, "cust0001", "pass1234")
    await client.post(
        "/api/me/close",
        headers=bearer(body["access_token"]),
        json={"password": "pass1234", "confirm": True},
    )

    r = await register_customer(client, account="cust0001", password="otherpass9")
    assert r.status_code == 201, f"注销后原 account 应可被重新注册：{r.status_code} {r.text}"


async def test_c09_audit_rows_retained(client, db):
    """注销必须保留审计行，且仍能按 user_id 查到。

    这里只探 `point_ledger` 与 `admin_action_log` —— 它们仅依赖 `user` 表，
    让 01 的测试套件可以独立跑绿。
    `reward_grant` / `review_log` / `cash_payout` 需要完整链路
    （task → reward_rule → claim → job → post → …），依赖 02/03/04/05 的表，
    见 test_plan.md「已知取舍」#4，待那些模块落地后补测。
    """
    body = await _logged_in(client)
    uid = body["user"]["id"]

    await db.execute(
        """
        INSERT INTO point_ledger (user_id, change, balance_after, source, ref_type, ref_id)
        VALUES ($1, 100, 100, 'task_reward', 'reward_grant', 1)
        """,
        uid,
    )
    await db.execute(
        """
        INSERT INTO admin_action_log (admin_id, action, target_type, target_id, detail)
        VALUES ($1, 'ban_user', 'user', $1, '{"reason":"测试"}'::jsonb)
        """,
        uid,
    )

    r = await client.post(
        "/api/me/close",
        headers=bearer(body["access_token"]),
        json={"password": "pass1234", "confirm": True},
    )
    assert r.status_code == 202, r.text

    assert (
        await db.fetchval("SELECT count(*) FROM point_ledger WHERE user_id = $1", uid) == 1
    ), "point_ledger 必须保留"
    assert (
        await db.fetchval(
            "SELECT count(*) FROM admin_action_log WHERE admin_id = $1", uid
        )
        == 1
    ), "admin_action_log 必须保留"


async def test_c09b_audit_tables_not_cascade_deleted(client, db):
    """注销不得触发审计表的级联删除。

    断言的是**表还在、行数不变**，不依赖具体链路能否建起来：
    若某张审计表尚未实现，本用例会以 SchemaMissing 明确失败。
    """
    body = await _logged_in(client)
    uid = body["user"]["id"]

    counts_before = {}
    for table in ("point_ledger", "reward_grant", "review_log", "cash_payout"):
        counts_before[table] = await db.fetchval(f"SELECT count(*) FROM {table}")

    r = await client.post(
        "/api/me/close",
        headers=bearer(body["access_token"]),
        json={"password": "pass1234", "confirm": True},
    )
    assert r.status_code == 202, r.text

    for table, before in counts_before.items():
        after = await db.fetchval(f"SELECT count(*) FROM {table}")
        assert after == before, f"{table} 在注销后行数变了：{before} → {after}"


async def test_c10_merchant_with_published_task_409(client, db):
    await register_merchant(client, account="shop0001", shop_name="老王奶茶")
    body = await login_ok(client, "shop0001", "pass1234")
    uid = body["user"]["id"]

    await db.execute(
        """
        INSERT INTO task (merchant_id, title, description, category, start_at, end_at, status)
        VALUES ($1, '进行中的任务', '描述描述描述描述', '餐饮',
                now(), now() + interval '7 days', 'published')
        """,
        uid,
    )

    r = await client.post(
        "/api/me/close",
        headers=bearer(body["access_token"]),
        json={"password": "pass1234", "confirm": True},
    )
    assert r.status_code == 409, (
        f"商户尚有 published 任务时不允许注销：{r.status_code} {r.text}"
    )


async def test_c11_double_close_403(client):
    await register_customer(client, account="cust0001", password="pass1234")
    body = await login_ok(client, "cust0001", "pass1234")
    h = bearer(body["access_token"])

    first = await client.post(
        "/api/me/close", headers=h, json={"password": "pass1234", "confirm": True}
    )
    assert first.status_code == 202, first.text

    second = await client.post(
        "/api/me/close", headers=h, json={"password": "pass1234", "confirm": True}
    )
    assert second.status_code == 403, f"重复注销应 403：{second.status_code} {second.text}"


async def test_c12_close_without_token_401(client):
    r = await client.post(
        "/api/me/close", json={"password": "pass1234", "confirm": True}
    )
    assert r.status_code == 401, r.text
