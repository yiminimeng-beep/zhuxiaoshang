"""01-auth · 令牌与设备端点正常路径 + 令牌与设备边界。

对应 test_plan.md：E-04 ~ E-09 / T-01 ~ T-11
"""

import pytest

from tests.helpers import (
    bearer,
    device_headers,
    forge_access_token,
    login,
    login_ok,
    register_customer,
    register_merchant,
)

pytestmark = pytest.mark.asyncio


async def _two_devices(client, account="cust0001", password="pass1234"):
    """在同一账号上开两个设备，返回 (a_body, b_body)。"""
    await register_customer(client, account=account, password=password)
    a = await login_ok(client, account, password, fp="fp-A")
    b = await login_ok(client, account, password, fp="fp-B")
    return a, b


async def _fresh_login(client, account="cust0001", password="pass1234"):
    """注册 + 登录，返回登录响应体（就绪的单设备会话）。"""
    await register_customer(client, account=account, password=password)
    return await login_ok(client, account, password)


# --------------------------------------------------------------------------- #
# 端点正常路径
# --------------------------------------------------------------------------- #
async def test_e04_refresh_success(client):
    body = await _fresh_login(client)
    r = await client.post(
        "/api/auth/refresh", json={"refresh_token": body["refresh_token"]}
    )
    assert r.status_code == 200, r.text
    new = r.json()
    assert new["access_token"]
    assert new["refresh_token"]



async def test_e05_logout_204(client):
    body = await _fresh_login(client)
    r = await client.post("/api/auth/logout", headers=bearer(body["access_token"]))
    assert r.status_code == 204, r.text


async def test_e06_get_me(client):
    body = await _fresh_login(client)
    r = await client.get("/api/me", headers=bearer(body["access_token"]))
    assert r.status_code == 200, r.text
    assert r.json()["user"]["account"] == "cust0001"


async def test_e06b_get_me_merchant_includes_profile(client):
    await register_merchant(client, account="shop0001", shop_name="老王奶茶")
    body = await login_ok(client, "shop0001", "pass1234")
    r = await client.get("/api/me", headers=bearer(body["access_token"]))
    assert r.status_code == 200, r.text
    assert r.json()["merchant_profile"]["shop_name"] == "老王奶茶"


async def test_e07_list_devices(client):
    body = await _fresh_login(client)
    r = await client.get("/api/me/devices", headers=bearer(body["access_token"]))
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert len(items) == 1
    assert items[0]["device_fingerprint"] == "fp-default"


async def test_e08_revoke_one_device(client, db):
    a, b = await _two_devices(client)
    b_dev_id = await db.fetchval(
        "SELECT id FROM user_device WHERE device_fingerprint = 'fp-B'"
    )
    r = await client.delete(
        f"/api/me/devices/{b_dev_id}", headers=bearer(a["access_token"])
    )
    assert r.status_code == 204, r.text


async def test_e09_revoke_others(client):
    a, _ = await _two_devices(client)
    r = await client.post(
        "/api/me/devices/revoke-others", headers=bearer(a["access_token"])
    )
    assert r.status_code == 204, r.text


# --------------------------------------------------------------------------- #
# 边界
# --------------------------------------------------------------------------- #
async def test_t01_no_token_401(client):
    r = await client.get("/api/me")
    assert r.status_code == 401, r.text


async def test_t02_expired_access_token_401(client, db):
    r = await register_customer(client, account="cust0001", password="pass1234")
    uid = r.json()["user"]["id"]

    expired = forge_access_token(uid, expires_delta_seconds=-60)
    resp = await client.get("/api/me", headers=bearer(expired))
    assert resp.status_code == 401, resp.text


async def test_t03_old_refresh_token_revoked(client):
    body = await _fresh_login(client)
    old_refresh = body["refresh_token"]

    r1 = await client.post("/api/auth/refresh", json={"refresh_token": old_refresh})
    assert r1.status_code == 200, r1.text

    r2 = await client.post("/api/auth/refresh", json={"refresh_token": old_refresh})
    assert r2.status_code == 401, f"旧 refresh_token 应立即失效：{r2.status_code} {r2.text}"


async def test_t04_revoked_device_refresh_401(client, db):
    body = await _fresh_login(client)
    await db.execute(
        "UPDATE user_device SET revoked = true WHERE device_fingerprint = 'fp-default'"
    )
    r = await client.post(
        "/api/auth/refresh", json={"refresh_token": body["refresh_token"]}
    )
    assert r.status_code == 401, r.text


async def test_t05_logout_isolated_per_device(client, db):
    a, b = await _two_devices(client)

    r = await client.post("/api/auth/logout", headers=bearer(a["access_token"]))
    assert r.status_code == 204, r.text

    a_revoked = await db.fetchval(
        "SELECT bool_and(rt.revoked) FROM refresh_token rt "
        "JOIN user_device d ON d.id = rt.device_id "
        "WHERE d.device_fingerprint = 'fp-A'"
    )
    assert a_revoked is True

    rb = await client.post(
        "/api/auth/refresh", json={"refresh_token": b["refresh_token"]}
    )
    assert rb.status_code == 200, f"B 设备不应受影响：{rb.status_code} {rb.text}"


async def test_t06_change_password_revokes_others(client, db):
    a, b = await _two_devices(client)

    r = await client.post(
        "/api/me/password",
        headers=bearer(a["access_token"]),
        json={"old_password": "pass1234", "new_password": "newpass5678"},
    )
    assert r.status_code == 204, r.text

    rb = await client.post(
        "/api/auth/refresh", json={"refresh_token": b["refresh_token"]}
    )
    assert rb.status_code == 401, f"其他设备应被吊销：{rb.status_code} {rb.text}"

    ra = await client.post(
        "/api/auth/refresh", json={"refresh_token": a["refresh_token"]}
    )
    assert ra.status_code == 200, (
        f"spec：改密码只吊销'其他'设备，当前设备应保留：{ra.status_code} {ra.text}"
    )


async def test_t07_delete_device_kills_tokens(client, db):
    a, b = await _two_devices(client)
    b_dev_id = await db.fetchval(
        "SELECT id FROM user_device WHERE device_fingerprint = 'fp-B'"
    )

    r = await client.delete(
        f"/api/me/devices/{b_dev_id}", headers=bearer(a["access_token"])
    )
    assert r.status_code == 204, r.text

    rb = await client.post(
        "/api/auth/refresh", json={"refresh_token": b["refresh_token"]}
    )
    assert rb.status_code == 401, f"被下线设备应无法刷新：{rb.status_code} {rb.text}"


async def test_t08_cannot_delete_others_device(client, db):
    a, _ = await _two_devices(client, account="victim0001")
    await register_customer(client, account="attacker0001", password="pass1234")
    attacker = await login_ok(client, "attacker0001", "pass1234", fp="fp-X")

    victim_dev_id = await db.fetchval(
        "SELECT id FROM user_device WHERE device_fingerprint = 'fp-A'"
    )
    r = await client.delete(
        f"/api/me/devices/{victim_dev_id}", headers=bearer(attacker["access_token"])
    )
    assert r.status_code == 403, r.text


async def test_t09_can_delete_current_device(client, db):
    body = await _fresh_login(client)
    dev_id = await db.fetchval(
        "SELECT id FROM user_device WHERE device_fingerprint = 'fp-default'"
    )
    r = await client.delete(
        f"/api/me/devices/{dev_id}", headers=bearer(body["access_token"])
    )
    assert r.status_code == 204, r.text


async def test_t10_revoke_others_keeps_current(client, db):
    await register_customer(client, account="cust0001", password="pass1234")
    a = await login_ok(client, "cust0001", "pass1234", fp="fp-A")
    b = await login_ok(client, "cust0001", "pass1234", fp="fp-B")

    r = await client.post(
        "/api/me/devices/revoke-others", headers=bearer(a["access_token"])
    )
    assert r.status_code == 204, r.text

    rb = await client.post(
        "/api/auth/refresh", json={"refresh_token": b["refresh_token"]}
    )
    assert rb.status_code == 401, f"其余设备应失效：{rb.status_code} {rb.text}"

    ra = await client.post(
        "/api/auth/refresh", json={"refresh_token": a["refresh_token"]}
    )
    assert ra.status_code == 200, f"当前设备应保留：{ra.status_code} {ra.text}"


async def test_t11_11th_device_evicts_oldest(client, db):
    await register_customer(client, account="cust0001", password="pass1234")

    # 第 1 台单独登录并把 last_active_at 拨到最老
    first = await login_ok(client, "cust0001", "pass1234", fp="fp-01")
    await db.execute(
        "UPDATE user_device SET last_active_at = now() - interval '10 days' "
        "WHERE device_fingerprint = 'fp-01'"
    )

    for i in range(2, 12):  # 第 2 ~ 11 台
        r = await login_ok(
            client, "cust0001", "pass1234", fp=f"fp-{i:02d}"
        )

    count = await db.fetchval("SELECT count(*) FROM user_device")
    assert count == 10, f"最多保留 10 台设备，实际 {count}"

    oldest_gone = await db.fetchval(
        "SELECT count(*) FROM user_device WHERE device_fingerprint = 'fp-01'"
    )
    assert oldest_gone == 0, "最老的设备应被踢掉"

    # spec 要求「并记日志」。日志表归属 06-admin，本期先留 TODO，不在此处死绑表名。
    # TODO(01-auth 实现后)：确认踢设备的日志落点后补断言。

    r = await client.post(
        "/api/auth/refresh", json={"refresh_token": first["refresh_token"]}
    )
    assert r.status_code == 401, "被踢设备的 refresh_token 应失效"
