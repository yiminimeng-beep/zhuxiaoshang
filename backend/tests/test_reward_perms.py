"""05-reward · `OW` 组：归属。

对应 test_plan.md 的 `OW-01` ~ `OW-06`。

一条贯穿全局的分寸（全局约定 #7）：**别人的资源回 `403` 而不是 `404`**。
回 404 会把「不归你管」和「不存在」压成同一个答案，排查方向完全相反。

`OW-05` / `OW-06` 是 spec「用户查他人积分 / 券 / 兑换记录 → 403」的
**可实现替代**：`/api/me/*` 不接受 `user_id` 参数，「查他人」结构上不可达。
改为造**两条真实数据**断言「只返回自己的」——比「没有这个入口」更强。
"""

import pytest

from tests.helpers import (
    bearer,
    grant_rows,
    insert_coupon,
    insert_mall_item,
    insert_point_ledger,
    insert_redemption,
    insert_user_coupon,
    reward_scene,
    seed_customers,
    settle_now,
)

pytestmark = pytest.mark.asyncio


async def test_ow01_customer_cannot_read_reward_rule(db, client, merchant, customer):
    """客户访商户侧的奖励规则 → `403`。"""
    _, c_token, _ = customer
    merchant_id, customer_id = merchant[0]["id"], customer[0]["id"]
    scene = await reward_scene(db, merchant_id, customer_id)

    r = await client.get(
        f"/api/merchant/reward-rules/{scene.task_id}", headers=bearer(c_token)
    )
    assert r.status_code == 403, r.text


async def test_ow02_merchant_cannot_read_others_rule(db, client, merchant, merchant_b):
    """商户查**别人任务**的奖励规则 → `403`（不是 404）。"""
    merchant_id = merchant[0]["id"]
    scene = await reward_scene(db, merchant_id, merchant_id)

    r = await client.get(
        f"/api/merchant/reward-rules/{scene.task_id}", headers=bearer(merchant_b)
    )
    assert r.status_code == 403, r.text


async def test_ow03_merchant_cannot_touch_others_redemption(
    db, client, merchant, merchant_b, customer
):
    """`confirm` / `cancel` 别人商品下的兑换 → `403`（不是 404）。"""
    _, c_token, _ = customer
    merchant_id, customer_id = merchant[0]["id"], customer[0]["id"]
    item_id = await insert_mall_item(db, merchant_id, points_cost=100)
    await insert_point_ledger(db, customer_id, 1000, 1000, source="adjust")
    redemption_id = await insert_redemption(db, customer_id, item_id)

    for action in ("confirm", "cancel"):
        r = await client.post(
            f"/api/merchant/redemptions/{redemption_id}/{action}",
            headers=bearer(merchant_b),
        )
        assert r.status_code == 403, f"{action} 应 403，实际 {r.status_code} {r.text}"


async def test_ow04_merchant_cannot_patch_others_coupon(db, client, merchant, merchant_b):
    """改**别人家的券** → `403`（不是 404）。"""
    coupon_id = await insert_coupon(db, merchant[0]["id"])

    r = await client.patch(
        f"/api/merchant/coupons/{coupon_id}",
        headers=bearer(merchant_b),
        json={"total": 20},
    )
    assert r.status_code == 403, r.text


async def test_ow05_my_rewards_only_mine(db, client, merchant, customer):
    """`GET /api/me/rewards` 只含本人的 grant。"""
    _, c_token, _ = customer
    merchant_id = merchant[0]["id"]
    mine = customer[0]["id"]

    scene = await reward_scene(db, merchant_id, mine)
    await settle_now(
        post_id=scene.post_id, user_id=mine, task_id=scene.task_id, engagement=18
    )
    # 另一条真实的 grant，属于别人（同商户下的另一个任务）
    other = (await seed_customers(db, 1, prefix="other"))[0]
    other_scene = await reward_scene(db, merchant_id, other)
    await settle_now(
        post_id=other_scene.post_id,
        user_id=other,
        task_id=other_scene.task_id,
        engagement=18,
    )

    r = await client.get("/api/me/rewards", headers=bearer(c_token))
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert [i["post_id"] for i in items] == [scene.post_id]
    assert len(await grant_rows(db, scene.post_id)) == 1


async def test_ow06_my_redemptions_and_coupons_only_mine(
    db, client, merchant, customer
):
    """`/api/me/redemptions` 与 `/api/me/coupons` 同样只返回本人的。"""
    _, c_token, _ = customer
    merchant_id = merchant[0]["id"]
    mine = customer[0]["id"]
    other = (await seed_customers(db, 1, prefix="other"))[0]

    item_id = await insert_mall_item(db, merchant_id, points_cost=100)
    mine_redemption = await insert_redemption(db, mine, item_id)
    await insert_redemption(db, other, item_id)
    coupon_id = await insert_coupon(db, merchant_id)
    mine_coupon = await insert_user_coupon(db, mine, coupon_id, code="MINE1")
    await insert_user_coupon(db, other, coupon_id, code="THEIRS1")

    redemptions = await client.get("/api/me/redemptions", headers=bearer(c_token))
    assert redemptions.status_code == 200, redemptions.text
    assert [i["id"] for i in redemptions.json()["items"]] == [mine_redemption]

    coupons = await client.get("/api/me/coupons", headers=bearer(c_token))
    assert coupons.status_code == 200, coupons.text
    assert [i["id"] for i in coupons.json()["items"]] == [mine_coupon]
