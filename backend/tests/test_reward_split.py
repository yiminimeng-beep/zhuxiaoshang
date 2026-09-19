"""05-reward · `AC` 组：与 07 报销的分账。

对应 test_plan.md 的 `AC-01` ~ `AC-05`。

这一段是**合规**相关：报销是「成本返还」，奖励是「激励」，两者来源不同、
账本不同、上限不同，不得合并计算。

04 的 `_run_hooks` 已经把两笔钱各自包进 `begin_nested()`（一笔失败不拖垮
另一笔），所以本组要验的是**05 自己这一半不抛不该抛的异常**，以及两本账
真的各写各的。
"""

import pytest

from tests.helpers import (
    approve_ok,
    as_json,
    grant_rows,
    insert_reward_rule,
    make_tiers,
    only_grant,
    reimburse_scene,
    settle_now,
)

pytestmark = pytest.mark.asyncio


def _tiers(cash: int = 0, points: int = 50):
    return make_tiers(3, reward={"cash": cash, "points": points})


async def _approve(client, token, scene):
    """过审：走 04 的同一个入口，触发两笔钱。"""
    await approve_ok(client, token, scene.post_id)


async def test_ac01_one_post_yields_both_ledgers(db, client, merchant, customer):
    """同一 post 过审 → 1 条 `reward_grant` 与 1 条 `reimburse_claim`，互不影响。"""
    _, token, _ = merchant
    merchant_id, customer_id = merchant[0]["id"], customer[0]["id"]
    scene = await reimburse_scene(db, merchant_id, customer_id)
    await insert_reward_rule(db, scene.task_id, tiers=_tiers())

    await _approve(client, token, scene)

    assert len(await grant_rows(db, scene.post_id)) == 1
    claims = await db.fetch(
        "SELECT * FROM reimburse_claim WHERE post_id = $1", scene.post_id
    )
    assert len(claims) == 1
    assert claims[0]["status"] == "settled"


async def test_ac02_reimburse_pool_exhausted_reward_still_granted(
    db, client, merchant, customer
):
    """报销**失败**（池子不足）→ 奖励照发。"""
    _, token, _ = merchant
    merchant_id, customer_id = merchant[0]["id"], customer[0]["id"]
    scene = await reimburse_scene(
        db, merchant_id, customer_id, pool=1000, pool_used=1000
    )
    await insert_reward_rule(db, scene.task_id, tiers=_tiers())

    await _approve(client, token, scene)

    claim = await db.fetchrow(
        "SELECT * FROM reimburse_claim WHERE post_id = $1", scene.post_id
    )
    assert claim["status"] == "pool_exhausted"
    assert claim["covered_points"] == 0

    row = await only_grant(db, scene.post_id)
    assert row["status"] == "granted"
    assert as_json(row["reward_detail"])["points"] == 50


async def test_ac03_missing_rule_reward_fails_reimburse_still_lands(
    db, client, merchant, customer
):
    """奖励**失败**（任务没配规则）→ 报销照发。"""
    _, token, _ = merchant
    merchant_id, customer_id = merchant[0]["id"], customer[0]["id"]
    scene = await reimburse_scene(db, merchant_id, customer_id)
    # 故意不配 reward_rule

    out = await settle_now(
        post_id=scene.post_id,
        user_id=customer_id,
        task_id=scene.task_id,
        engagement=18,
    )
    assert out is None

    await _approve(client, token, scene)

    claims = await db.fetch(
        "SELECT * FROM reimburse_claim WHERE post_id = $1", scene.post_id
    )
    assert len(claims) == 1
    assert claims[0]["status"] == "settled"
    assert claims[0]["covered_points"] == 100  # base = 1 分/单位 × 100 单位


async def test_ac04_zero_reward_cap_does_not_touch_reimburse(
    db, client, merchant, customer
):
    """`max_reward_per_user=0` → 现金被截到 0，但**报销不受影响**（照额报）。"""
    _, token, _ = merchant
    merchant_id, customer_id = merchant[0]["id"], customer[0]["id"]
    scene = await reimburse_scene(db, merchant_id, customer_id)
    await insert_reward_rule(
        db, scene.task_id, tiers=_tiers(cash=300), max_reward_per_user=0
    )

    await _approve(client, token, scene)

    row = await only_grant(db, scene.post_id)
    assert as_json(row["reward_detail"])["cash"] == 0

    claim = await db.fetchrow(
        "SELECT * FROM reimburse_claim WHERE post_id = $1", scene.post_id
    )
    # 奖励的封顶只约束奖励：上限是 0 也得把该报的报出去
    assert claim["covered_points"] == 100
    assert claim["status"] == "settled"


async def test_ac05_ledgers_are_not_shared(db, client, merchant, customer):
    """账本**不共用**：报销只写 `quota_ledger`，奖励只写 `point_ledger`。"""
    _, token, _ = merchant
    merchant_id, customer_id = merchant[0]["id"], customer[0]["id"]
    scene = await reimburse_scene(db, merchant_id, customer_id)
    await insert_reward_rule(db, scene.task_id, tiers=_tiers(cash=100))

    await _approve(client, token, scene)

    point_sources = {
        r["source"]
        for r in await db.fetch(
            "SELECT source FROM point_ledger WHERE user_id = $1", customer_id
        )
    }
    assert point_sources == {"task_reward"}, f"积分账本混进了别的来源：{point_sources}"

    quota_sources = {
        r["source"]
        for r in await db.fetch(
            "SELECT source FROM quota_ledger WHERE user_id = $1", customer_id
        )
    }
    assert quota_sources <= {"reimburse_in"}, f"额度账本混进了别的来源：{quota_sources}"
