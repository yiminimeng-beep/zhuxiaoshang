"""05-reward · `TI` 组：阶梯匹配。

对应 test_plan.md 的 `TI-01` ~ `TI-09`。

匹配是**纯算法**，所以这一组直接调 `reward.settle`，不走 HTTP：走一遍
「建 job → 提交作品 → 过审」只会让红的定位成本变高，而这里要验的是
「哪个数落进哪一档」和「脏数据进来会不会炸」。

默认阶梯（`make_tiers(3)`）：`[0,99] → [100,199] → [200,∞)`，每档 50 积分。
默认快照 `engagement = 10 + 5 + 3 = 18`——但本组大多**显式传** `engagement`，
因为被测的是匹配而不是「峰值怎么算」（后者归 04，已在 `PS-*` 覆盖）。
"""

import json

import pytest

from tests.helpers import (
    as_json,
    grant_row,
    insert_reward_rule,
    make_tiers,
    point_ledger_rows,
    reward_scene,
    settle_now,
)

pytestmark = pytest.mark.asyncio


async def test_ti01_min_boundary_hits_first_tier(db, merchant, customer):
    """`engagement=0` 且首档 `min=0` → 命中第 0 档，按档发奖。

    边界是**闭区间**：`min=0` 的第一档必须收得下 0。写成 `>` 的话，
    「发了作品但一点互动都没有」会掉进 `below_threshold` 而颗粒无收。
    """
    merchant_id, customer_id = merchant[0]["id"], customer[0]["id"]
    scene = await reward_scene(db, merchant_id, customer_id)

    await settle_now(
        post_id=scene.post_id,
        user_id=customer_id,
        task_id=scene.task_id,
        engagement=0,
    )

    row = await grant_row(db, scene.post_id)
    assert row["tier_index"] == 0
    assert row["status"] == "granted"
    assert as_json(row["reward_detail"])["points"] == 50


async def test_ti02_lower_bound_of_second_tier(db, merchant, customer):
    """`engagement=100`（二档下界 `min=100`）→ `tier_index=1`。"""
    merchant_id, customer_id = merchant[0]["id"], customer[0]["id"]
    scene = await reward_scene(db, merchant_id, customer_id)

    await settle_now(
        post_id=scene.post_id,
        user_id=customer_id,
        task_id=scene.task_id,
        engagement=100,
    )

    assert (await grant_row(db, scene.post_id))["tier_index"] == 1


async def test_ti03_upper_bound_inclusive(db, merchant, customer):
    """`engagement=199`（二档上界）→ `tier_index=1`。**含 max**，不是 `<`。

    写成半开区间 `[min, max)` 的话，199 会掉到三档去——商户按「100~199 发 50 分」
    配的规则，实际按 200 以上那一档发，多出来的钱没人对得上账。
    """
    merchant_id, customer_id = merchant[0]["id"], customer[0]["id"]
    scene = await reward_scene(db, merchant_id, customer_id)

    await settle_now(
        post_id=scene.post_id,
        user_id=customer_id,
        task_id=scene.task_id,
        engagement=199,
    )

    assert (await grant_row(db, scene.post_id))["tier_index"] == 1


async def test_ti04_gap_between_tiers_goes_to_next(db, merchant, customer):
    """`engagement=200` → 落到三档（`tier_index=2`，末档 `max=null`）。"""
    merchant_id, customer_id = merchant[0]["id"], customer[0]["id"]
    scene = await reward_scene(db, merchant_id, customer_id)

    await settle_now(
        post_id=scene.post_id,
        user_id=customer_id,
        task_id=scene.task_id,
        engagement=200,
    )

    assert (await grant_row(db, scene.post_id))["tier_index"] == 2


async def test_ti05_last_tier_open_ended(db, merchant, customer):
    """spec 原文用例：`engagement=2000` 且第三档 `{min:2000, max:null}` → `tier_index=2`。"""
    merchant_id, customer_id = merchant[0]["id"], customer[0]["id"]
    tiers = [
        {"min": 0, "max": 999, "reward": {"points": 10}},
        {"min": 1000, "max": 1999, "reward": {"points": 30}},
        {"min": 2000, "max": None, "reward": {"points": 90}},
    ]
    scene = await reward_scene(db, merchant_id, customer_id, tiers=tiers)

    await settle_now(
        post_id=scene.post_id,
        user_id=customer_id,
        task_id=scene.task_id,
        engagement=2000,
    )

    row = await grant_row(db, scene.post_id)
    assert row["tier_index"] == 2
    assert as_json(row["reward_detail"])["points"] == 90


async def test_ti06_gap_in_tiers_yields_below_threshold(db, merchant, customer):
    """**缺口**：`[0,99]` + `[200,∞)`，`engagement=150` → `below_threshold`，不得 500。

    这种阶梯 02 的 `validate_tiers` 不让建（会 422），所以只能直插——测的是
    「历史脏数据进来了会不会炸」，不是 02 的校验对不对。spec 明写
    「不得报 500」，因为商户改过规则之后，库里真可能出现这种形状。
    """
    merchant_id, customer_id = merchant[0]["id"], customer[0]["id"]
    tiers = [
        {"min": 0, "max": 99, "reward": {"points": 50}},
        {"min": 200, "max": None, "reward": {"points": 500}},
    ]
    scene = await reward_scene(db, merchant_id, customer_id, tiers=tiers)

    await settle_now(
        post_id=scene.post_id,
        user_id=customer_id,
        task_id=scene.task_id,
        engagement=150,
    )

    row = await grant_row(db, scene.post_id)
    assert row["status"] == "below_threshold"
    assert row["tier_index"] is None
    assert row["granted_at"] is None
    # 一分不发，但「没发」这件事本身要留痕（RE-08 的同一句话）
    assert await point_ledger_rows(db, customer_id) == []


async def test_ti07_no_rule_returns_none(db, merchant, customer):
    """任务没配规则 → `settle` 返回 `None`，不落行，**不得 500**。

    这是 04 真实会遇到的形状：任务可以不配规则就发布，客户照样交作品、
    商户照样能过审。此时「没有奖励」是正确结果。
    """
    merchant_id, customer_id = merchant[0]["id"], customer[0]["id"]
    scene = await reward_scene(db, merchant_id, customer_id, rule=False)

    out = await settle_now(
        post_id=scene.post_id,
        user_id=customer_id,
        task_id=scene.task_id,
        engagement=500,
    )

    assert out is None
    assert await grant_row(db, scene.post_id) is None


async def test_ti08_engagement_snapshotted(db, merchant, customer):
    """`reward_grant.engagement` 落库 == 传入的峰值。

    审计「当时按多少分算的」只能靠这一列：快照表会继续追加，事后回查
    看到的是新数据，不是结算那一刻的数。
    """
    merchant_id, customer_id = merchant[0]["id"], customer[0]["id"]
    scene = await reward_scene(db, merchant_id, customer_id)

    await settle_now(
        post_id=scene.post_id,
        user_id=customer_id,
        task_id=scene.task_id,
        engagement=250,
    )

    # 快照里是 18，落库的必须是传进来的 250
    assert (await grant_row(db, scene.post_id))["engagement"] == 250


async def test_ti09_rule_change_does_not_touch_settled_rows(db, merchant, customer):
    """商户中途改阶梯 → **已结算的行逐列不变**，未结算的用新规则。

    结算时把档位与金额都算完落库，事后改规则只影响「下一次」。若实现是
    「先记 engagement、发奖时再回查规则」，改一次规则就会把历史全部重算。
    """
    merchant_id, customer_id = merchant[0]["id"], customer[0]["id"]
    scene = await reward_scene(db, merchant_id, customer_id)

    await settle_now(
        post_id=scene.post_id,
        user_id=customer_id,
        task_id=scene.task_id,
        engagement=18,
    )
    before = await grant_row(db, scene.post_id)
    assert as_json(before["reward_detail"])["points"] == 50

    await db.execute(
        "UPDATE reward_rule SET tiers = $2::jsonb WHERE id = $1",
        scene.rule_id,
        json.dumps(make_tiers(3, reward={"points": 999})),
    )
    # 再投一次结算事件：幂等应当把这一行原样留下
    await settle_now(
        post_id=scene.post_id,
        user_id=customer_id,
        task_id=scene.task_id,
        engagement=18,
    )

    after = await grant_row(db, scene.post_id)
    assert dict(after) == dict(before)
