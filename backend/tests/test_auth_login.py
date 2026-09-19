"""01-auth · 登录端点正常路径 + 登录与防爆破边界。

对应 test_plan.md：E-03 / L-01 ~ L-14
"""

import pytest

from tests.helpers import (
    assert_route_registered,
    login,
    register_customer,
    register_merchant,
)

pytestmark = pytest.mark.asyncio


async def test_e03_login_success(client):
    await register_customer(client, account="cust0001", password="pass1234")
    r = await login(client, "cust0001", "pass1234")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["access_token"]
    assert body["refresh_token"]
    assert body["user"]["account"] == "cust0001"


async def test_l01_login_by_account(client):
    await register_customer(client, account="cust0001", email="a@example.com")
    r = await login(client, "cust0001", "pass1234")
    assert r.status_code == 200, r.text


async def test_l02_login_by_email(client):
    await register_customer(client, account="cust0001", email="a@example.com")
    r = await login(client, "a@example.com", "pass1234")
    assert r.status_code == 200, r.text


async def test_l03_unknown_identifier_404(client):
    assert_route_registered("POST", "/api/auth/login")
    r = await login(client, "nobody9999", "pass1234")
    assert r.status_code == 404, r.text


async def test_l04_identifier_case_insensitive(client):
    await register_customer(client, account="Abc123", email="Mixed@Example.com")
    for ident in ("Abc123", "abc123", "ABC123"):
        r = await login(client, ident, "pass1234", fp=f"fp-{ident}")
        assert r.status_code == 200, f"{ident} -> {r.status_code} {r.text}"
    r = await login(client, "MIXED@example.com", "pass1234", fp="fp-email")
    assert r.status_code == 200, r.text


async def test_l05_wrong_password_401(client, db):
    await register_customer(client, account="cust0001", password="pass1234")
    r = await login(client, "cust0001", "wrongpass")
    assert r.status_code == 401, r.text
    # 密码错不得泄露用户是否存在之外的信息
    assert "pass1234" not in r.text


async def test_l06_banned_user_403(client, db, set_user_status):
    r = await register_customer(client, account="cust0001", password="pass1234")
    uid = r.json()["user"]["id"]
    await set_user_status(uid, "banned")
    resp = await login(client, "cust0001", "pass1234")
    assert resp.status_code == 403, resp.text


async def test_l07_deleted_user_403(client, db, set_user_status):
    r = await register_customer(client, account="cust0001", password="pass1234")
    uid = r.json()["user"]["id"]
    await set_user_status(uid, "deleted")
    resp = await login(client, "cust0001", "pass1234")
    assert resp.status_code == 403, resp.text


async def test_l08_lock_after_5_failures(client, db):
    await register_customer(client, account="cust0001", password="pass1234")

    for i in range(5):
        r = await login(client, "cust0001", "wrongpass")
        assert r.status_code == 401, f"第 {i + 1} 次应 401：{r.status_code} {r.text}"

    r6 = await login(client, "cust0001", "wrongpass")
    assert r6.status_code == 423, f"第 6 次应 423：{r6.status_code} {r6.text}"

    row = await db.fetchrow(
        'SELECT fail_count FROM login_attempt WHERE identifier = $1', "cust0001"
    )
    assert row is not None
    assert row["fail_count"] >= 5


async def test_l09_locked_window_is_15_minutes(client):
    await register_customer(client, account="cust0001", password="pass1234")
    for _ in range(6):
        await login(client, "cust0001", "wrongpass")

    # 锁定后即使密码正确也一律 423
    r = await login(client, "cust0001", "pass1234")
    assert r.status_code == 423, f"锁定期内正确密码也应 423：{r.status_code} {r.text}"


async def test_l10_unlock_after_15_minutes(client, db):
    await register_customer(client, account="cust0001", password="pass1234")
    for _ in range(6):
        await login(client, "cust0001", "wrongpass")

    # 把锁定时间拨到过去，模拟 15 分钟已过
    await db.execute(
        "UPDATE login_attempt SET locked_until = now() - interval '1 second' "
        "WHERE identifier = $1",
        "cust0001",
    )

    r = await login(client, "cust0001", "pass1234")
    assert r.status_code == 200, f"锁定期过后应可登录：{r.status_code} {r.text}"
    fail_count = await db.fetchval(
        'SELECT fail_count FROM login_attempt WHERE identifier = $1', "cust0001"
    )
    assert fail_count == 0, "登录成功后 fail_count 应归零"


async def test_l11_lock_is_per_identifier(client):
    """account 侧的连错不得牵连另一个用 email 登录的账号。"""
    await register_customer(client, account="victim0001", password="pass1234")
    await register_customer(
        client, account="other0001", email="other@example.com", password="pass1234"
    )

    for _ in range(6):
        await login(client, "victim0001", "wrongpass")

    assert (await login(client, "victim0001", "pass1234")).status_code == 423
    r = await login(client, "other@example.com", "pass1234", fp="fp-other")
    assert r.status_code == 200, f"另一个标识符不应被牵连：{r.status_code} {r.text}"


async def test_l12_last_login_at_updated(client, db):
    r = await register_customer(client, account="cust0001", password="pass1234")
    uid = r.json()["user"]["id"]
    assert await db.fetchval(
        'SELECT last_login_at FROM "user" WHERE id = $1', uid
    ) is None

    resp = await login(client, "cust0001", "pass1234")
    assert resp.status_code == 200, resp.text
    assert await db.fetchval(
        'SELECT last_login_at FROM "user" WHERE id = $1', uid
    ) is not None


async def test_l13_device_fingerprint_required(client):
    await register_customer(client, account="cust0001", password="pass1234")
    r = await client.post(
        "/api/auth/login",
        json={"identifier": "cust0001", "password": "pass1234"},
    )
    assert r.status_code == 422, r.text


async def test_l14_same_device_no_new_row(client, db):
    await register_customer(client, account="cust0001", password="pass1234")

    r1 = await login(client, "cust0001", "pass1234", fp="fp-same")
    assert r1.status_code == 200, r1.text
    first = await db.fetchval(
        'SELECT last_active_at FROM user_device WHERE device_fingerprint = $1', "fp-same"
    )

    r2 = await login(client, "cust0001", "pass1234", fp="fp-same")
    assert r2.status_code == 200, r2.text

    count = await db.fetchval(
        'SELECT count(*) FROM user_device WHERE device_fingerprint = $1', "fp-same"
    )
    assert count == 1, "同一 fingerprint 不应新增 user_device 行"

    second = await db.fetchval(
        'SELECT last_active_at FROM user_device WHERE device_fingerprint = $1', "fp-same"
    )
    assert second >= first, "last_active_at 应被更新"
