"""05-reward · `ML` 组：商城商品。

对应 test_plan.md 的 `ML-01` ~ `ML-09`。

两处「不是 404」的分寸是本组的重点：

- 商品已下架 → **404**（对客户而言它不存在）
- `PATCH` 别人家的商品 → **403**（对商户而言是「不归你管」）

回错会把「不归你管」和「不存在」压成同一个答案，排查方向完全相反。
"""

import pytest

from tests.helpers import assert_route_registered, bearer, insert_mall_item

pytestmark = pytest.mark.asyncio


def _item_payload(**over) -> dict:
    body = {"name": "9.9 元咖啡券", "points_cost": 100, "stock": 10}
    body.update(over)
    return body


async def test_ml01_merchant_publishes_item(db, client, merchant):
    _, token, _ = merchant

    r = await client.post(
        "/api/merchant/mall/items", headers=bearer(token), json=_item_payload()
    )
    assert r.status_code == 201, r.text
    item = r.json()["item"]
    assert item["status"] == "on"
    assert item["sold"] == 0
    assert item["points_cost"] == 100
    # 建的是自己的商品
    assert await db.fetchval("SELECT merchant_id FROM mall_item WHERE id = $1", item["id"]) == merchant[0]["id"]


async def test_ml02_customer_cannot_publish(db, client, merchant, customer):
    _, c_token, _ = customer

    r = await client.post(
        "/api/merchant/mall/items", headers=bearer(c_token), json=_item_payload()
    )
    assert r.status_code == 403, r.text


async def test_ml03_list_only_on_items_of_that_merchant(
    db, client, merchant, merchant_b
):
    _, token, _ = merchant
    merchant_id = merchant[0]["id"]

    await insert_mall_item(db, merchant_id, name="上架的")
    await insert_mall_item(db, merchant_id, name="下架的", status="off")
    other_id = await db.fetchval(
        'SELECT id FROM "user" WHERE account = $1', "shop0002"
    )
    await insert_mall_item(db, other_id, name="别家的")

    r = await client.get(f"/api/mall/items?merchant_id={merchant_id}")
    assert r.status_code == 200, r.text
    names = [i["name"] for i in r.json()["items"]]
    assert names == ["上架的"]


async def test_ml04_list_shape_and_page_cap(db, client, merchant):
    merchant_id = merchant[0]["id"]
    for i in range(3):
        await insert_mall_item(db, merchant_id, name=f"商品{i}")

    r = await client.get(f"/api/mall/items?merchant_id={merchant_id}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(("items", "total", "page", "size")) <= set(body)
    assert body["total"] == 3

    big = await client.get(f"/api/mall/items?merchant_id={merchant_id}&size=101")
    assert big.status_code in (200, 422), big.text
    if big.status_code == 200:
        assert big.json()["size"] <= 100


async def test_ml05_unknown_merchant_is_404(client):
    """spec 明写：`merchant_id` 不存在 → `404`。"""
    assert_route_registered("GET", "/api/mall/items")

    r = await client.get("/api/mall/items?merchant_id=999999")
    assert r.status_code == 404, r.text


async def test_ml06_off_item_detail_is_404(db, client, merchant):
    """上架 → `200`；下架 → `404`（spec 原话「非上架返回 404」，不是 403）。"""
    merchant_id = merchant[0]["id"]
    on_id = await insert_mall_item(db, merchant_id)
    off_id = await insert_mall_item(db, merchant_id, status="off")
    assert_route_registered("GET", f"/api/mall/items/{off_id}")

    ok = await client.get(f"/api/mall/items/{on_id}")
    assert ok.status_code == 200, ok.text
    gone = await client.get(f"/api/mall/items/{off_id}")
    assert gone.status_code == 404, gone.text


async def test_ml07_invalid_points_cost_or_stock(db, client, merchant):
    """`points_cost=0`、`stock=-1` → `422`（spec：`points_cost >= 1`、`stock >= 0`）。"""
    _, token, _ = merchant

    zero = await client.post(
        "/api/merchant/mall/items",
        headers=bearer(token),
        json=_item_payload(points_cost=0),
    )
    assert zero.status_code == 422, zero.text

    negative = await client.post(
        "/api/merchant/mall/items",
        headers=bearer(token),
        json=_item_payload(stock=-1),
    )
    assert negative.status_code == 422, negative.text


async def test_ml08_patch_stock_below_sold(db, client, merchant):
    """`stock` 改成小于 `sold` → `422`（已卖出去的收不回来）。"""
    _, token, _ = merchant
    item_id = await insert_mall_item(db, merchant[0]["id"], stock=5, sold=3)

    r = await client.patch(
        f"/api/merchant/mall/items/{item_id}", headers=bearer(token), json={"stock": 2}
    )
    assert r.status_code == 422, r.text


async def test_ml09_patch_others_item_is_403(db, client, merchant_b, merchant):
    """改别人家的商品 → `403`（不是 404）。"""
    item_id = await insert_mall_item(db, merchant[0]["id"])

    r = await client.patch(
        f"/api/merchant/mall/items/{item_id}",
        headers=bearer(merchant_b),
        json={"stock": 99},
    )
    assert r.status_code == 403, r.text
