"""05-reward · `RD` 组：兑换。

对应 test_plan.md 的 `RD-01` ~ `RD-13`。

本模块**并发最密**的地方：`stock` 与积分是两个独立的共享量。校验与扣减之间
不加锁就会被穿——`RD-03`（不超卖）与 `RD-12`（不少卖）是一对：
只测一条的话，把 `sold` 加两次也过得了。
"""

import asyncio

import pytest

from tests.helpers import (
    balance_of,
    bearer,
    insert_mall_item,
    insert_point_ledger,
    point_ledger_rows,
    redeem,
    redeem_ok,
    seed_customers,
    token_for,
)

pytestmark = pytest.mark.asyncio

PURSE = 10_000


async def _ready(db, merchant, customer, **item_over):
    """一个「有积分、有货」的兑换场景。"""
    merchant_id, customer_id = merchant[0]["id"], customer[0]["id"]
    item_id = await insert_mall_item(db, merchant_id, **item_over)
    await insert_point_ledger(db, customer_id, PURSE, PURSE, source="adjust")
    return merchant_id, customer_id, item_id


async def test_rd01_redeem_returns_redemption_and_balance(
    db, client, merchant, customer
):
    _, c_token, _ = customer
    _, customer_id, item_id = await _ready(db, merchant, customer)

    body = await redeem_ok(client, c_token, item_id)

    assert body["redeem_code"]
    assert body["redemption"]["status"] == "pending"
    assert body["redemption"]["quantity"] == 1
    assert body["points_balance"] == PURSE - 100
    assert await balance_of(client, c_token) == PURSE - 100
    assert body["redemption"]["user_id"] == customer_id


async def test_rd02_redeem_deducts_points_and_stock(db, client, merchant, customer):
    """兑换后：扣积分、写一条 `redemption` 流水、`stock-1`、`sold+1`。"""
    _, c_token, _ = customer
    _, customer_id, item_id = await _ready(db, merchant, customer, stock=3)

    await redeem_ok(client, c_token, item_id)

    ledger = await point_ledger_rows(db, customer_id)
    spend = [r for r in ledger if r["source"] == "redemption"]
    assert len(spend) == 1
    assert spend[0]["change"] == -100
    assert spend[0]["balance_after"] == PURSE - 100

    item = await db.fetchrow("SELECT * FROM mall_item WHERE id = $1", item_id)
    assert item["stock"] == 2
    assert item["sold"] == 1


async def test_rd03_concurrent_redeem_never_oversells(db, client, merchant):
    """**`stock=1`，100 个并发 → 恰好 1 个 `201`，99 个 `409`。**

    必须用 `UPDATE ... WHERE stock > 0` 原子条件更新。「先查库存再写」会被
    并发穿成多个 `201`——每个请求都查到 1，然后各扣一次。
    """
    merchant_id = merchant[0]["id"]
    item_id = await insert_mall_item(db, merchant_id, stock=1, points_cost=100)
    ids = await seed_customers(db, 100, prefix="rush")
    for uid in ids:
        await insert_point_ledger(db, uid, PURSE, PURSE, source="adjust")

    responses = await asyncio.gather(
        *(redeem(client, token_for(uid), item_id) for uid in ids)
    )
    codes = [r.status_code for r in responses]
    assert codes.count(201) == 1, f"超卖了：201 × {codes.count(201)}"
    assert codes.count(409) == 99

    item = await db.fetchrow("SELECT * FROM mall_item WHERE id = $1", item_id)
    assert item["stock"] == 0
    assert item["sold"] == 1


async def test_rd04_out_of_stock_is_409(db, client, merchant, customer):
    _, c_token, _ = customer
    _, _, item_id = await _ready(db, merchant, customer, stock=0)

    r = await redeem(client, c_token, item_id)
    assert r.status_code == 409, r.text


async def test_rd05_insufficient_stock_for_quantity_is_409(
    db, client, merchant, customer
):
    """`stock=1` 但 `quantity=2` → `409`（不够就整单拒，不是只扣 1 个）。"""
    _, c_token, _ = customer
    _, _, item_id = await _ready(db, merchant, customer, stock=1)

    r = await redeem(client, c_token, item_id, quantity=2)
    assert r.status_code == 409, r.text
    assert (await db.fetchrow("SELECT * FROM mall_item WHERE id = $1", item_id))["stock"] == 1


async def test_rd06_per_user_limit_reached(db, client, merchant, customer):
    """`per_user_limit=2` 已兑 2 次 → 第 3 次 `409`。"""
    _, c_token, _ = customer
    _, _, item_id = await _ready(db, merchant, customer, stock=10, per_user_limit=2)

    assert (await redeem(client, c_token, item_id)).status_code == 201
    assert (await redeem(client, c_token, item_id)).status_code == 201
    assert (await redeem(client, c_token, item_id)).status_code == 409


async def test_rd07_per_user_limit_counts_quantity(db, client, merchant, customer):
    """`per_user_limit=2` 已兑 1 件、本次 `quantity=2` → `409`。

    计数看**总量**不只是次数：「每用户限兑 2 件」若按次数算，兑两次
    `quantity=99` 就能拿走 198 件。
    """
    _, c_token, _ = customer
    _, _, item_id = await _ready(db, merchant, customer, stock=10, per_user_limit=2)

    assert (await redeem(client, c_token, item_id)).status_code == 201
    r = await redeem(client, c_token, item_id, quantity=2)
    assert r.status_code == 409, r.text


async def test_rd08_null_per_user_limit_is_unlimited(db, client, merchant, customer):
    """`per_user_limit=null` → 不限（`RD-06` 的反向）。"""
    _, c_token, _ = customer
    _, _, item_id = await _ready(db, merchant, customer, stock=5, per_user_limit=None)

    for _ in range(3):
        assert (await redeem(client, c_token, item_id)).status_code == 201


async def test_rd09_insufficient_points_is_422_without_side_effects(
    db, client, merchant, customer
):
    """积分 99、`points_cost=100` → `422`，**且积分不得被扣、库存不得变**。"""
    _, c_token, _ = customer
    merchant_id, customer_id = merchant[0]["id"], customer[0]["id"]
    item_id = await insert_mall_item(db, merchant_id, points_cost=100, stock=5)
    await insert_point_ledger(db, customer_id, 99, 99, source="adjust")

    r = await redeem(client, c_token, item_id)

    assert r.status_code == 422, r.text
    assert await balance_of(client, c_token) == 99
    item = await db.fetchrow("SELECT * FROM mall_item WHERE id = $1", item_id)
    assert (item["stock"], item["sold"]) == (5, 0)


async def test_rd10_quantity_multiples_points_cost(db, client, merchant, customer):
    """`quantity=2` 校验的是 `points_cost * 2`。"""
    _, c_token, _ = customer
    merchant_id, customer_id = merchant[0]["id"], customer[0]["id"]
    item_id = await insert_mall_item(db, merchant_id, points_cost=100, stock=5)
    await insert_point_ledger(db, customer_id, 150, 150, source="adjust")

    poor = await redeem(client, c_token, item_id, quantity=2)
    assert poor.status_code == 422, poor.text

    await insert_point_ledger(db, customer_id, 50, 200, source="adjust")
    rich = await redeem(client, c_token, item_id, quantity=2)
    assert rich.status_code == 201, rich.text
    assert rich.json()["redemption"]["points_spent"] == 200
    assert rich.json()["points_balance"] == 0


async def test_rd11_invalid_quantity_is_422(db, client, merchant, customer):
    """`quantity=0` / `-1` / `100` → `422`（spec：`<= 0` 或 `> 99`）。"""
    _, c_token, _ = customer
    _, _, item_id = await _ready(db, merchant, customer, stock=200)

    for bad in (0, -1, 100):
        r = await redeem(client, c_token, item_id, quantity=bad)
        assert r.status_code == 422, f"quantity={bad} 应 422，实际 {r.status_code}"


async def test_rd12_stock_conserved_across_users(db, client, merchant):
    """不同用户并发兑换 → `stock + sold == 初始 stock`（一个不多一个不少）。"""
    merchant_id = merchant[0]["id"]
    initial = 20
    item_id = await insert_mall_item(db, merchant_id, stock=initial, points_cost=100)
    ids = await seed_customers(db, 40, prefix="crowd")
    for uid in ids:
        await insert_point_ledger(db, uid, PURSE, PURSE, source="adjust")

    responses = await asyncio.gather(
        *(redeem(client, token_for(uid), item_id) for uid in ids)
    )
    ok = sum(1 for r in responses if r.status_code == 201)
    assert ok == initial, f"成功数 {ok} 与库存 {initial} 不符"

    item = await db.fetchrow("SELECT * FROM mall_item WHERE id = $1", item_id)
    assert item["stock"] + item["sold"] == initial
    assert item["stock"] == 0


async def test_rd13_offline_item_does_not_void_existing_redemption(
    db, client, merchant, customer
):
    """兑换后商户下架 → 已产生的 `redemption` 不受影响，`confirm` 仍 `200`。"""
    _, token, _ = merchant
    _, c_token, _ = customer
    _, _, item_id = await _ready(db, merchant, customer)

    body = await redeem_ok(client, c_token, item_id)
    redemption_id = body["redemption"]["id"]

    off = await client.patch(
        f"/api/merchant/mall/items/{item_id}",
        headers=bearer(token),
        json={"status": "off"},
    )
    assert off.status_code == 200, off.text

    confirmed = await client.post(
        f"/api/merchant/redemptions/{redemption_id}/confirm", headers=bearer(token)
    )
    assert confirmed.status_code == 200, confirmed.text
