"""01-auth · 关注端点正常路径 + 关注边界。

对应 test_plan.md：E-12 ~ E-15 / F-01 ~ F-10

方向约束：**客户 → 商户**，反向与同向一律 422。
"""

import pytest

from tests.helpers import (
    assert_route_registered,
    bearer,
    login_ok,
    register_customer,
    register_merchant,
)

pytestmark = pytest.mark.asyncio


async def _pair(client, cust="cust0001", shop="shop0001"):
    """返回 (customer_login_body, merchant_login_body, merchant_id)。"""
    await register_merchant(client, account=shop, shop_name="老王奶茶")
    m_body = await login_ok(client, shop, "pass1234", fp="fp-shop")
    await register_customer(client, account=cust)
    c_body = await login_ok(client, cust, "pass1234", fp="fp-cust")
    return c_body, m_body, m_body["user"]["id"]


# --------------------------------------------------------------------------- #
# 端点正常路径
# --------------------------------------------------------------------------- #
async def test_e12_follow_201(client):
    c, _, mid = await _pair(client)
    r = await client.post(
        f"/api/merchant/{mid}/follow", headers=bearer(c["access_token"])
    )
    assert r.status_code == 201, r.text


async def test_e13_unfollow_204(client):
    c, _, mid = await _pair(client)
    h = bearer(c["access_token"])
    assert (await client.post(f"/api/merchant/{mid}/follow", headers=h)).status_code == 201
    r = await client.delete(f"/api/merchant/{mid}/follow", headers=h)
    assert r.status_code == 204, r.text


async def test_e14_follower_count(client):
    c, _, mid = await _pair(client)
    await client.post(f"/api/merchant/{mid}/follow", headers=bearer(c["access_token"]))

    r = await client.get(f"/api/merchant/{mid}/followers/count")
    assert r.status_code == 200, r.text
    assert r.json()["count"] == 1


async def test_e15_my_following(client):
    c, _, mid = await _pair(client)
    await client.post(f"/api/merchant/{mid}/follow", headers=bearer(c["access_token"]))

    r = await client.get("/api/me/following", headers=bearer(c["access_token"]))
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert len(items) == 1
    assert items[0]["id"] == mid


# --------------------------------------------------------------------------- #
# 边界
# --------------------------------------------------------------------------- #
async def test_f01_duplicate_follow_409(client):
    c, _, mid = await _pair(client)
    h = bearer(c["access_token"])
    assert (await client.post(f"/api/merchant/{mid}/follow", headers=h)).status_code == 201
    r = await client.post(f"/api/merchant/{mid}/follow", headers=h)
    assert r.status_code == 409, r.text


async def test_f02_unfollow_twice_404(client):
    assert_route_registered("DELETE", "/api/merchant/1/follow")
    c, _, mid = await _pair(client)
    h = bearer(c["access_token"])
    await client.post(f"/api/merchant/{mid}/follow", headers=h)
    assert (await client.delete(f"/api/merchant/{mid}/follow", headers=h)).status_code == 204
    r = await client.delete(f"/api/merchant/{mid}/follow", headers=h)
    assert r.status_code == 404, r.text


async def test_f03_customer_follow_customer_422(client):
    c, _, _ = await _pair(client)
    await register_customer(client, account="cust0002")
    other = await login_ok(client, "cust0002", "pass1234", fp="fp-other")

    r = await client.post(
        f"/api/merchant/{other['user']['id']}/follow",
        headers=bearer(c["access_token"]),
    )
    assert r.status_code == 422, f"客户关注客户应 422：{r.status_code} {r.text}"


async def test_f04_merchant_follow_customer_422(client):
    c, m, _ = await _pair(client)
    r = await client.post(
        f"/api/merchant/{c['user']['id']}/follow",
        headers=bearer(m["access_token"]),
    )
    assert r.status_code == 422, f"商户关注客户应 422：{r.status_code} {r.text}"


async def test_f05_merchant_follow_merchant_422(client):
    _, m, _ = await _pair(client)
    await register_merchant(client, account="shop0002", shop_name="B 店")
    other = await login_ok(client, "shop0002", "pass1234", fp="fp-shop2")

    r = await client.post(
        f"/api/merchant/{other['user']['id']}/follow",
        headers=bearer(m["access_token"]),
    )
    assert r.status_code == 422, f"商户关注商户应 422：{r.status_code} {r.text}"


async def test_f06_follow_self_403(client):
    c, _, _ = await _pair(client)
    r = await client.post(
        f"/api/merchant/{c['user']['id']}/follow", headers=bearer(c["access_token"])
    )
    assert r.status_code == 403, f"关注自己应 403：{r.status_code} {r.text}"


async def test_f07_follow_deleted_merchant_422(client, db):
    c, _, mid = await _pair(client)
    await db.execute('UPDATE "user" SET status = $2 WHERE id = $1', mid, "deleted")

    r = await client.post(
        f"/api/merchant/{mid}/follow", headers=bearer(c["access_token"])
    )
    assert r.status_code == 422, f"关注已注销商户应 422：{r.status_code} {r.text}"


async def test_f08_follow_nonexistent_404(client):
    assert_route_registered("POST", "/api/merchant/1/follow")
    c, _, _ = await _pair(client)
    r = await client.post(
        "/api/merchant/99999999/follow", headers=bearer(c["access_token"])
    )
    assert r.status_code == 404, r.text


async def test_f09_follow_survives_customer_ban(client, db, set_user_status):
    c, _, mid = await _pair(client)
    await client.post(f"/api/merchant/{mid}/follow", headers=bearer(c["access_token"]))

    await set_user_status(c["user"]["id"], "banned")

    assert await db.fetchval("SELECT count(*) FROM user_follow") == 1, (
        "客户被封禁后关注关系应保留，不得自动清理"
    )


async def test_f10_banned_merchant_profile_blocked(client, db, set_user_status):
    c, _, mid = await _pair(client)
    await client.post(f"/api/merchant/{mid}/follow", headers=bearer(c["access_token"]))
    await set_user_status(mid, "banned")

    # 粉丝数仍可查
    r = await client.get(f"/api/merchant/{mid}/followers/count")
    assert r.status_code == 200, f"被封禁商户的粉丝数仍应可查：{r.status_code} {r.text}"

    # spec 另要求「商户主页不可访问」。商户主页接口不在 01 范围内，
    # 归属 02-task 的商户主页；此处不强断言，避免与未定端点死绑。
    # TODO(02-task 落地后)：补「被封禁商户主页不可访问」的断言。
