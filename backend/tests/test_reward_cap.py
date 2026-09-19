"""05-reward · `CP` 组：现金上限。

对应 test_plan.md 的 `CP-01` ~ `CP-05`。

上限只截断 **`cash`**（spec 把这条规则挂在「现金上限」小节下，示例与
`cash_payout.amount` 都指现金）。累计口径是 **(user, task) 这个二元组**——
`CP-04` 与 `CP-05` 是互为反向的一对：只测一条，把实现写成「跨任务累计」
或「按 post 累计」都会全绿。
"""

import pytest

from tests.helpers import (
    another_post,
    as_json,
    grant_row,
    make_tiers,
    reward_scene,
    seed_cash_grant,
    settle_now,
)

pytestmark = pytest.mark.asyncio

CAP = 10_000


async def _settle(scene, engagement: int = 18):
    return await settle_now(
        post_id=scene.post_id,
        user_id=scene.user_id,
        task_id=scene.task_id,
        engagement=engagement,
    )


async def test_cp01_cash_truncated_to_remaining(db, merchant, customer):
    """已发 8000，本次应发 3000 → 实发 **2000**，`status=capped`。"""
    merchant_id, customer_id = merchant[0]["id"], customer[0]["id"]
    scene = await reward_scene(
        db,
        merchant_id,
        customer_id,
        tiers=make_tiers(3, reward={"cash": 3000}),
        max_reward_per_user=CAP,
    )
    sibling = await another_post(db, scene)
    await seed_cash_grant(db, scene, sibling, 8000)

    await _settle(scene)

    row = await grant_row(db, scene.post_id)
    assert row["status"] == "capped"
    assert as_json(row["reward_detail"])["cash"] == 2000


async def test_cp02_payout_amount_is_granted_not_owed(db, merchant, customer):
    """`cash_payout.amount` 必须等于**实发额**，不是应发额。

    台账写 3000 而实际只发 2000，对账时差额从哪来没人说得清——这 1000 分
    是永远打不出去的。
    """
    merchant_id, customer_id = merchant[0]["id"], customer[0]["id"]
    scene = await reward_scene(
        db,
        merchant_id,
        customer_id,
        tiers=make_tiers(3, reward={"cash": 3000}),
        max_reward_per_user=CAP,
    )
    sibling = await another_post(db, scene)
    await seed_cash_grant(db, scene, sibling, 8000)

    await _settle(scene)

    # 台账里有两行：种子那 8000 与本次的 2000
    amounts = await db.fetch(
        "SELECT amount FROM cash_payout WHERE user_id = $1 ORDER BY id", customer_id
    )
    assert [a["amount"] for a in amounts] == [8000, 2000]


async def test_cp03_null_cap_does_not_truncate(db, merchant, customer):
    """`max_reward_per_user=null` → 不截断，`status=granted`。"""
    merchant_id, customer_id = merchant[0]["id"], customer[0]["id"]
    scene = await reward_scene(
        db,
        merchant_id,
        customer_id,
        tiers=make_tiers(3, reward={"cash": 3000}),
        max_reward_per_user=None,
    )
    sibling = await another_post(db, scene)
    await seed_cash_grant(db, scene, sibling, 8000)

    await _settle(scene)

    row = await grant_row(db, scene.post_id)
    assert row["status"] == "granted"
    assert as_json(row["reward_detail"])["cash"] == 3000


async def test_cp04_cumulation_is_per_user_per_task(db, merchant, customer):
    """累计口径是 **(user, task)**：既不是按 post，也不是跨任务。

    造三个数把三种写法分开：
    - 本任务已发 8000、**另一个任务**已发 9000，本次应发 3000
    - 按 (user, task) → 2000（正确）
    - 按 post → 3000（没截断）
    - 按 user 全局 → 9000 + 8000 = 17000，截到 **0**
    """
    merchant_id, customer_id = merchant[0]["id"], customer[0]["id"]
    scene = await reward_scene(
        db,
        merchant_id,
        customer_id,
        tiers=make_tiers(3, reward={"cash": 3000}),
        max_reward_per_user=CAP,
    )
    sibling = await another_post(db, scene)
    await seed_cash_grant(db, scene, sibling, 8000)

    other_task = await reward_scene(db, merchant_id, customer_id)
    other_sibling = await another_post(db, other_task)
    await seed_cash_grant(db, other_task, other_sibling, 9000)

    await _settle(scene)

    row = await grant_row(db, scene.post_id)
    assert row["status"] == "capped"
    assert as_json(row["reward_detail"])["cash"] == 2000


async def test_cp05_other_tasks_do_not_count(db, merchant, customer):
    """**任务之间互不影响**：另一任务已发 9000，本任务本次 3000 → 全额放出。

    跨任务累计会使这里只剩 1000。商户各自掏自己的钱，不该被别家的账牵连。
    """
    merchant_id, customer_id = merchant[0]["id"], customer[0]["id"]
    scene = await reward_scene(
        db,
        merchant_id,
        customer_id,
        tiers=make_tiers(3, reward={"cash": 3000}),
        max_reward_per_user=CAP,
    )
    other_task = await reward_scene(db, merchant_id, customer_id)
    other_sibling = await another_post(db, other_task)
    await seed_cash_grant(db, other_task, other_sibling, 9000)

    await _settle(scene)

    row = await grant_row(db, scene.post_id)
    assert row["status"] == "granted"
    assert as_json(row["reward_detail"])["cash"] == 3000
