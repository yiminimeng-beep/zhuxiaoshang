"""01-auth · 个人资料端点正常路径 + 归属与角色边界。

对应 test_plan.md：E-10 / E-11 / O-01 ~ O-06

⚠️ 跨模块依赖：O-02 / O-03 校验的是全局归属规则（00-overview 约定 #7），
需要借用 07 / 05 的商户端点；这两条要等对应模块落地才会转绿。
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


# --------------------------------------------------------------------------- #
# 端点正常路径
# --------------------------------------------------------------------------- #
async def test_e10_patch_me(client, db):
    await register_customer(client, account="cust0001", password="pass1234")
    body = await login_ok(client, "cust0001", "pass1234")

    r = await client.patch(
        "/api/me",
        headers=bearer(body["access_token"]),
        json={"nickname": "新昵称", "avatar_url": "https://cdn.example.com/a.png"},
    )
    assert r.status_code == 200, r.text

    row = await db.fetchrow(
        'SELECT nickname, avatar_url FROM "user" WHERE account = $1', "cust0001"
    )
    assert row["nickname"] == "新昵称"
    assert row["avatar_url"] == "https://cdn.example.com/a.png"


async def test_e11_change_password(client):
    await register_customer(client, account="cust0001", password="pass1234")
    body = await login_ok(client, "cust0001", "pass1234")

    r = await client.post(
        "/api/me/password",
        headers=bearer(body["access_token"]),
        json={"old_password": "pass1234", "new_password": "newpass5678"},
    )
    assert r.status_code == 204, r.text

    assert (await login(client, "cust0001", "pass1234", fp="fp-old")).status_code == 401
    assert (
        await login(client, "cust0001", "newpass5678", fp="fp-new")
    ).status_code == 200


# --------------------------------------------------------------------------- #
# 归属与角色
# --------------------------------------------------------------------------- #
async def test_o01_banned_with_valid_token_403(client, set_user_status):
    r = await register_customer(client, account="cust0001", password="pass1234")
    uid = r.json()["user"]["id"]
    token = r.json()["access_token"]

    await set_user_status(uid, "banned")

    resp = await client.get("/api/me", headers=bearer(token))
    assert resp.status_code == 403, (
        f"封禁用户带有效 token 访问应 403：{resp.status_code} {resp.text}"
    )


async def test_o02_customer_blocked_from_merchant_api(client):
    """借用 02 的 /api/merchant/tasks（纯角色守卫，无需数据准备）。

    原先借的是 07 的 /api/merchant/quota，07 未落地时框架一律 404，
    断言不到守卫本身；02 落地后改用已注册的同前缀端点。
    """
    await register_customer(client, account="cust0001", password="pass1234")
    body = await login_ok(client, "cust0001", "pass1234")

    r = await client.get("/api/merchant/tasks", headers=bearer(body["access_token"]))
    assert r.status_code == 403, (
        f"客户访问 /api/merchant/* 应 403：{r.status_code} {r.text}"
    )


async def test_o03_cross_tenant_403_not_404(client, db):
    """商户 A 读商户 B 的任务 → 403，**不得是 404**。

    直接按 spec 的 task 表结构造数据，绕开 02 的建任务接口。
    原先打的是 GET /api/merchant/reward-rules/{id}，但 02 的 spec 只为
    reward-rules 定义了 PUT 与 POST validate（没有 GET），故改用 02
    确实提供的 GET /api/merchant/tasks/{id} 来验同一条规则。
    """
    a = await register_merchant(client, account="shopA0001", shop_name="A 店")
    b = await register_merchant(client, account="shopB0001", shop_name="B 店")
    b_uid = b.json()["user"]["id"]

    task_id = await db.fetchval(
        """
        INSERT INTO task (merchant_id, title, description, category, start_at, end_at, status)
        VALUES ($1, 'B 的任务', '描述描述描述描述', '餐饮',
                now(), now() + interval '7 days', 'published')
        RETURNING id
        """,
        b_uid,
    )

    r = await client.get(
        f"/api/merchant/tasks/{task_id}",
        headers=bearer(a.json()["access_token"]),
    )
    assert r.status_code == 403, (
        f"跨租户必须 403 而不是 404（不得泄露资源是否存在）：{r.status_code} {r.text}"
    )


@pytest.mark.parametrize("field,value", [
    ("account", "hacked01"),
    ("email", "hacked@example.com"),
    ("role", "admin"),
    ("status", "banned"),
])
async def test_o04_cannot_patch_protected_fields(client, db, field, value):
    await register_customer(client, account="cust0001", password="pass1234")
    body = await login_ok(client, "cust0001", "pass1234")

    before = await db.fetchrow(
        'SELECT account, email, role, status FROM "user" WHERE account = $1', "cust0001"
    )

    r = await client.patch(
        "/api/me", headers=bearer(body["access_token"]), json={field: value}
    )
    assert r.status_code == 400, f"改 {field} 应 400：{r.status_code} {r.text}"

    after = await db.fetchrow(
        'SELECT account, email, role, status FROM "user" WHERE account = $1', "cust0001"
    )
    assert dict(after) == dict(before), f"{field} 不得被改动"


async def test_o05_nickname_too_long(client):
    await register_customer(client, account="cust0001", password="pass1234")
    body = await login_ok(client, "cust0001", "pass1234")

    r = await client.patch(
        "/api/me", headers=bearer(body["access_token"]), json={"nickname": "字" * 33}
    )
    assert r.status_code == 422, r.text


@pytest.mark.parametrize("bad_url", [
    "javascript:alert(1)",
    "data:text/html;base64,PHNjcmlwdD4=",
    "ftp://example.com/a.png",
    "/relative/path.png",
])
async def test_o06_avatar_url_scheme(client, bad_url):
    await register_customer(client, account="cust0001", password="pass1234")
    body = await login_ok(client, "cust0001", "pass1234")

    r = await client.patch(
        "/api/me", headers=bearer(body["access_token"]), json={"avatar_url": bad_url}
    )
    assert r.status_code == 422, f"{bad_url} 应被拒：{r.status_code} {r.text}"
