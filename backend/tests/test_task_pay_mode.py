"""02-task · 付费模式与报销池。

对应 test_plan.md：PM-01 ~ PM-12

⚠️ PM-10 / PM-11 / PM-12 依赖 07-token 的 `quota_account` 表；07 未落地前必然红，
红因是 `SchemaMissing`（表不存在），**不是 02 的功能缺陷**。见 test_plan「已知取舍」#2。
"""

import pytest

from tests.helpers import (
    bearer,
    create_task,
    create_task_ok,
    published_task,
)

pytestmark = pytest.mark.asyncio

REIMBURSE = {
    "pay_mode": "user_pay_reimburse",
    "reimburse_pool": 1000,
    "reimburse_per_user_limit": 100,
}


# --------------------------------------------------------------------------- #
# 默认值与缺字段
# --------------------------------------------------------------------------- #
async def test_pm01_default_pay_mode(client, merchant, db):
    _, token, _ = merchant
    task = await create_task_ok(client, token)
    row = await db.fetchval("SELECT pay_mode FROM task WHERE id = $1", task["id"])
    assert row == "merchant_pay", f"不传 pay_mode 应默认 merchant_pay，实际 {row}"


async def test_pm02_missing_pool(client, merchant):
    _, token, _ = merchant
    r = await create_task(
        client,
        token,
        pay_mode="user_pay_reimburse",
        reimburse_per_user_limit=100,
    )
    assert r.status_code == 422, r.text


async def test_pm03_missing_per_user_limit(client, merchant):
    _, token, _ = merchant
    r = await create_task(
        client,
        token,
        pay_mode="user_pay_reimburse",
        reimburse_pool=1000,
    )
    assert r.status_code == 422, r.text


async def test_pm04_limit_gt_pool(client, merchant):
    _, token, _ = merchant
    r = await create_task(
        client,
        token,
        pay_mode="user_pay_reimburse",
        reimburse_pool=100,
        reimburse_per_user_limit=200,
    )
    assert r.status_code == 422, r.text


async def test_pm05_zero_per_user_limit_ok(client, merchant):
    _, token, _ = merchant
    r = await create_task(
        client, token, pay_mode="user_pay_reimburse", reimburse_pool=100,
        reimburse_per_user_limit=0,
    )
    assert r.status_code == 201, r.text


async def test_pm06_zero_pool_ok(client, merchant):
    _, token, _ = merchant
    r = await create_task(
        client, token, pay_mode="user_pay_reimburse", reimburse_pool=0,
        reimburse_per_user_limit=0,
    )
    assert r.status_code == 201, r.text


async def test_pm07_merchant_pay_rejects_pool(client, merchant):
    _, token, _ = merchant
    r = await create_task(
        client, token, pay_mode="merchant_pay", reimburse_pool=1000
    )
    assert r.status_code == 422, f"merchant_pay 不该接受 reimburse_pool：{r.status_code} {r.text}"


async def test_pm08_cannot_change_pay_mode(client, merchant):
    _, token, _ = merchant
    task = await published_task(client, token)
    r = await client.patch(
        f"/api/merchant/tasks/{task['id']}",
        headers=bearer(token),
        json={"pay_mode": "user_pay_reimburse"},
    )
    assert r.status_code == 409, r.text


# --------------------------------------------------------------------------- #
# 报销池可见性
# --------------------------------------------------------------------------- #
async def test_pm09_remaining_exposed(client, merchant, db):
    _, token, _ = merchant

    # 07 落地后，发布 `user_pay_reimburse` 任务必须真有额度去锁池子
    # （PM-11 断言的就是这条）。本条只想验「详情把剩余额摆出来」，
    # 所以得先把商户喂饱——不喂的话发布就 402，测的就不是可见性了。
    await db.execute(
        """
        INSERT INTO quota_account (user_id, balance, reserved)
        SELECT id, 5000, 0 FROM "user" WHERE account = 'shop0001'
        """
    )

    task = await published_task(client, token, **REIMBURSE)

    r = await client.get(f"/api/tasks/{task['id']}", headers=bearer(token))
    assert r.status_code == 200, r.text
    assert "reimburse_pool_remaining" in r.json()["task"], (
        f"详情须给出剩余可报销额（否则客户盲赌）：{r.text}"
    )


# --------------------------------------------------------------------------- #
# 依赖 07-token 的 quota_account（07 落地前为 SchemaMissing 红）
# --------------------------------------------------------------------------- #
async def test_pm10_insufficient_quota_402(client, merchant, db):
    """商户可用额度 < reimburse_pool → 402，且任务不得发布。"""
    _, token, _ = merchant

    await db.execute(
        """
        INSERT INTO quota_account (user_id, balance, reserved)
        SELECT id, 50, 0 FROM "user" WHERE account = 'shop0001'
        """
    )

    task = await create_task_ok(client, token, **REIMBURSE)
    from tests.helpers import put_reward_rule

    assert (await put_reward_rule(client, token, task["id"])).status_code == 200

    r = await client.post(
        f"/api/merchant/tasks/{task['id']}/publish", headers=bearer(token)
    )
    assert r.status_code == 402, f"额度不足应 402：{r.status_code} {r.text}"

    status = await db.fetchval("SELECT status FROM task WHERE id = $1", task["id"])
    assert status != "published", "402 之后任务不得进入 published"


async def test_pm11_pool_reserved_on_publish(client, merchant, db):
    """发布 user_pay_reimburse 任务 → 商户 reserved 真金白银地加上去。"""
    _, token, _ = merchant

    await db.execute(
        """
        INSERT INTO quota_account (user_id, balance, reserved)
        SELECT id, 5000, 0 FROM "user" WHERE account = 'shop0001'
        """
    )

    task = await published_task(client, token, **REIMBURSE)

    reserved = await db.fetchval(
        """
        SELECT qa.reserved FROM quota_account qa
        JOIN "user" u ON u.id = qa.user_id WHERE u.account = 'shop0001'
        """
    )
    assert reserved >= REIMBURSE["reimburse_pool"], (
        f"发布后须锁定 reimburse_pool={REIMBURSE['reimburse_pool']}，实际 reserved={reserved}"
    )


async def test_pm12_pause_releases_reservation(client, merchant, db):
    _, token, _ = merchant

    await db.execute(
        """
        INSERT INTO quota_account (user_id, balance, reserved)
        SELECT id, 5000, 0 FROM "user" WHERE account = 'shop0001'
        """
    )

    task = await published_task(client, token, **REIMBURSE)
    await client.post(f"/api/merchant/tasks/{task['id']}/pause", headers=bearer(token))

    reserved = await db.fetchval(
        """
        SELECT qa.reserved FROM quota_account qa
        JOIN "user" u ON u.id = qa.user_id WHERE u.account = 'shop0001'
        """
    )
    assert reserved == 0, f"暂停应释放未使用的报销池预占，实际 reserved={reserved}"
