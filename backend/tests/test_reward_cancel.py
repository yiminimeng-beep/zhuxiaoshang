"""05-reward · `CN` 组：取消与退款。

对应 test_plan.md 的 `CN-01` ~ `CN-05`。

取消是**把钱退回去**的动作，所以「重复取消」比别处更危险：状态机把第二次
拦成 `409` 之后，若实现是先退款再判状态，钱已经出去了。`CN-03` 因此不断言
状态码，而是断言**流水行数**。
"""

import pytest

from tests.helpers import (
    balance_of,
    bearer,
    insert_mall_item,
    insert_point_ledger,
    point_ledger_rows,
    redeem_ok,
)

pytestmark = pytest.mark.asyncio

PURSE = 10_000


async def _scene(db, merchant, customer, **item_over):
    merchant_id, customer_id = merchant[0]["id"], customer[0]["id"]
    item_id = await insert_mall_item(
        db, merchant_id, points_cost=100, stock=5, **item_over
    )
    await insert_point_ledger(db, customer_id, PURSE, PURSE, source="adjust")
    return customer_id, item_id


async def _cancel(client, token, redemption_id):
    return await client.post(
        f"/api/merchant/redemptions/{redemption_id}/cancel", headers=bearer(token)
    )


async def test_cn01_cancel_refunds_points(db, client, merchant, customer):
    """取消 `pending` → 积分原路返还，写一条 `refund` 流水。"""
    _, token, _ = merchant
    _, c_token, _ = customer
    customer_id, item_id = await _scene(db, merchant, customer)

    body = await redeem_ok(client, c_token, item_id)
    redemption_id = body["redemption"]["id"]
    assert await balance_of(client, c_token) == PURSE - 100

    r = await _cancel(client, token, redemption_id)
    assert r.status_code == 200, r.text

    refunds = [
        row
        for row in await point_ledger_rows(db, customer_id)
        if row["source"] == "refund"
    ]
    assert len(refunds) == 1
    assert refunds[0]["change"] == 100
    assert refunds[0]["balance_after"] == PURSE
    assert await balance_of(client, c_token) == refunds[0]["balance_after"]


async def test_cn02_cancel_returns_stock_symmetrically(
    db, client, merchant, customer
):
    """取消后库存与已售**对称**地还原：兑了 2 个就退 2 个。"""
    _, token, _ = merchant
    _, c_token, _ = customer
    _, item_id = await _scene(db, merchant, customer)

    body = await redeem_ok(client, c_token, item_id, quantity=2)
    after_redeem = await db.fetchrow("SELECT * FROM mall_item WHERE id = $1", item_id)
    assert (after_redeem["stock"], after_redeem["sold"]) == (3, 2)

    r = await _cancel(client, token, body["redemption"]["id"])
    assert r.status_code == 200, r.text

    after_cancel = await db.fetchrow("SELECT * FROM mall_item WHERE id = $1", item_id)
    assert (after_cancel["stock"], after_cancel["sold"]) == (5, 0)


async def test_cn03_repeat_cancel_does_not_refund_twice(
    db, client, merchant, customer
):
    """重复取消 → `409`，**积分不得重复返还**。

    靠 `(source, ref_type, ref_id)` 唯一约束兜底，不是靠 `if status == 'pending'`：
    后者在并发两次取消时会双双通过。所以这里断言的是**流水行数**。
    """
    _, token, _ = merchant
    _, c_token, _ = customer
    customer_id, item_id = await _scene(db, merchant, customer)

    body = await redeem_ok(client, c_token, item_id)
    redemption_id = body["redemption"]["id"]

    assert (await _cancel(client, token, redemption_id)).status_code == 200
    again = await _cancel(client, token, redemption_id)
    assert again.status_code == 409, again.text

    refunds = await db.fetch(
        "SELECT * FROM point_ledger WHERE source = $1 AND ref_id = $2",
        "refund",
        redemption_id,
    )
    assert len(refunds) == 1, f"重复退款：{len(refunds)} 行"
    assert await balance_of(client, c_token) == PURSE

    item = await db.fetchrow("SELECT * FROM mall_item WHERE id = $1", item_id)
    assert (item["stock"], item["sold"]) == (5, 0)


async def test_cn04_cancel_confirmed_is_409(db, client, merchant, customer):
    """取消 `confirmed` 的兑换 → `409`（已履约的不能退）。"""
    _, token, _ = merchant
    _, c_token, _ = customer
    customer_id, item_id = await _scene(db, merchant, customer)

    body = await redeem_ok(client, c_token, item_id)
    redemption_id = body["redemption"]["id"]

    confirmed = await client.post(
        f"/api/merchant/redemptions/{redemption_id}/confirm", headers=bearer(token)
    )
    assert confirmed.status_code == 200, confirmed.text

    r = await _cancel(client, token, redemption_id)
    assert r.status_code == 409, r.text
    assert await balance_of(client, c_token) == PURSE - 100


async def test_cn05_cancel_foreign_redemption_is_403(
    db, client, merchant, merchant_b, customer
):
    """商户取消**别人商品**下的兑换 → `403`（不是 404）。"""
    _, c_token, _ = customer
    customer_id, item_id = await _scene(db, merchant, customer)
    body = await redeem_ok(client, c_token, item_id)

    r = await _cancel(client, merchant_b, body["redemption"]["id"])

    assert r.status_code == 403, r.text
    # 别人的兑换也不能被顺手改掉
    row = await db.fetchrow(
        "SELECT * FROM redemption WHERE id = $1", body["redemption"]["id"]
    )
    assert row["status"] == "pending"
