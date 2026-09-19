"""02-task · 奖励规则配置与阶梯校验。

对应 test_plan.md：E-04 / E-05 / RR-01 ~ RR-14
"""

import pytest

from tests.helpers import (
    bearer,
    create_task_ok,
    make_tiers,
    put_reward_rule,
)

pytestmark = pytest.mark.asyncio


async def _draft(client, merchant):
    _, token, _ = merchant
    return token, await create_task_ok(client, token)


async def _validate(client, token, task_id, tiers):
    return await client.post(
        f"/api/merchant/reward-rules/{task_id}/validate",
        headers=bearer(token),
        json={"metric": "engagement", "tiers": tiers},
    )


# --------------------------------------------------------------------------- #
# 正常路径
# --------------------------------------------------------------------------- #
async def test_e04_put_reward_rule(client, merchant, db):
    token, task = await _draft(client, merchant)

    r = await put_reward_rule(client, token, task["id"], tiers=make_tiers(3))
    assert r.status_code == 200, r.text
    assert r.json()["rule"]["task_id"] == task["id"]

    count = await db.fetchval("SELECT count(*) FROM reward_rule WHERE task_id = $1", task["id"])
    assert count == 1


async def test_e05_validate_does_not_persist(client, merchant, db):
    token, task = await _draft(client, merchant)

    r = await _validate(client, token, task["id"], make_tiers(3))
    assert r.status_code == 200, r.text
    assert r.json()["valid"] is True

    count = await db.fetchval("SELECT count(*) FROM reward_rule WHERE task_id = $1", task["id"])
    assert count == 0, "validate 只校验，不得落库"


# --------------------------------------------------------------------------- #
# 阶梯校验
# --------------------------------------------------------------------------- #
async def test_rr01_empty_tiers(client, merchant):
    token, task = await _draft(client, merchant)
    r = await _validate(client, token, task["id"], [])
    assert r.status_code == 422, r.text


async def test_rr02_overlap(client, merchant):
    token, task = await _draft(client, merchant)
    tiers = [
        {"min": 0, "max": 500, "reward": {"points": 10}},
        {"min": 400, "max": 900, "reward": {"points": 20}},
        {"min": 901, "max": None, "reward": {"points": 30}},
    ]
    r = await _validate(client, token, task["id"], tiers)
    assert r.status_code == 422, r.text


async def test_rr03_gap(client, merchant):
    token, task = await _draft(client, merchant)
    tiers = [
        {"min": 0, "max": 500, "reward": {"points": 10}},
        {"min": 600, "max": None, "reward": {"points": 20}},
    ]
    r = await _validate(client, token, task["id"], tiers)
    assert r.status_code == 422, r.text


async def test_rr04_last_tier_open_ended(client, merchant):
    token, task = await _draft(client, merchant)
    tiers = [
        {"min": 0, "max": 499, "reward": {"points": 10}},
        {"min": 500, "max": 999, "reward": {"points": 20}},
    ]
    r = await _validate(client, token, task["id"], tiers)
    assert r.status_code == 422, r.text


async def test_rr05_first_tier_starts_at_zero(client, merchant):
    token, task = await _draft(client, merchant)
    tiers = [{"min": 1, "max": None, "reward": {"points": 10}}]
    r = await _validate(client, token, task["id"], tiers)
    assert r.status_code == 422, r.text


async def test_rr06_min_gt_max(client, merchant):
    token, task = await _draft(client, merchant)
    tiers = [{"min": 0, "max": -1, "reward": {"points": 10}}]
    r = await _validate(client, token, task["id"], tiers)
    assert r.status_code == 422, r.text


async def test_rr07_negative_min(client, merchant):
    token, task = await _draft(client, merchant)
    tiers = [{"min": -5, "max": None, "reward": {"points": 10}}]
    r = await _validate(client, token, task["id"], tiers)
    assert r.status_code == 422, r.text


async def test_rr08_empty_reward(client, merchant):
    token, task = await _draft(client, merchant)
    r = await _validate(client, token, task["id"], [{"min": 0, "max": None, "reward": {}}])
    assert r.status_code == 422, r.text


async def test_rr09_negative_cash(client, merchant):
    token, task = await _draft(client, merchant)
    tiers = [{"min": 0, "max": None, "reward": {"cash": -100}}]
    r = await _validate(client, token, task["id"], tiers)
    assert r.status_code == 422, r.text


async def test_rr10_fractional_cash(client, merchant):
    token, task = await _draft(client, merchant)
    tiers = [{"min": 0, "max": None, "reward": {"cash": 10.5}}]
    r = await _validate(client, token, task["id"], tiers)
    assert r.status_code == 422, r.text


async def test_rr11_valid_four_tiers(client, merchant):
    token, task = await _draft(client, merchant)
    tiers = [
        {"min": 0, "max": 99, "reward": {"points": 10}},
        {"min": 100, "max": 499, "reward": {"points": 50}},
        {"min": 500, "max": 1999, "reward": {"points": 200, "coupon_id": 12}},
        {"min": 2000, "max": None, "reward": {"cash": 2000, "points": 500}},
    ]
    r = await _validate(client, token, task["id"], tiers)
    assert r.status_code == 200, r.text
    assert r.json()["valid"] is True


async def test_rr12_not_owner_403(client, merchant, merchant_b):
    token_a, task = await _draft(client, merchant)
    r = await put_reward_rule(client, merchant_b, task["id"], tiers=make_tiers(2))
    assert r.status_code == 403, r.text


async def test_rr13_upsert_single_row(client, merchant, db):
    token, task = await _draft(client, merchant)

    first = await put_reward_rule(client, token, task["id"], tiers=make_tiers(2))
    assert first.status_code == 200, first.text
    second = await put_reward_rule(client, token, task["id"], tiers=make_tiers(4))
    assert second.status_code == 200, second.text

    count = await db.fetchval("SELECT count(*) FROM reward_rule WHERE task_id = $1", task["id"])
    assert count == 1, "task_id 唯一：重复配置是覆盖，不是新增"


async def test_rr14_violations_payload(client, merchant):
    token, task = await _draft(client, merchant)
    tiers = [
        {"min": 0, "max": 500, "reward": {"points": 10}},
        {"min": 400, "max": None, "reward": {"points": 20}},
    ]
    r = await _validate(client, token, task["id"], tiers)
    assert r.status_code == 422, r.text
    violations = r.json().get("detail", {}).get("violations")
    assert isinstance(violations, list) and violations, f"须给出 violations[]：{r.text}"
