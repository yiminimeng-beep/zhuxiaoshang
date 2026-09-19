"""RT · 报销触发（与 05 奖励同源同期）。

报销是**两笔钱里的第二笔**：奖励来自 `reward_rule`，报销来自商户额度。
`RT-04` 盯的是「一个失败不影响另一个」——spec 明写两者独立计算、独立上限。
让 05 的故障拖垮 07 的兑现，等于用户白垫一笔钱。

`RT-12` 盯的是池子口径里的一个具体写法：可用额取 `pool − used`，**不减去
`reserved`**。这个 job 自己的预占就在 `reserved` 里，减掉等于把自己的额度
也排除在外，那笔钱就永远报不出来。
"""

from tests.helpers import (
    REASON_10,
    bearer,
    expire_post,
    insert_task,
    post_under_task,
    reimburse_scene,
    run_expiry,
    set_post_status,
)


async def approve(client, token, post_id):
    return await client.post(
        f"/api/merchant/reviews/{post_id}/approve", headers=bearer(token)
    )


async def reject(client, token, post_id, reason=REASON_10):
    return await client.post(
        f"/api/merchant/reviews/{post_id}/reject",
        headers=bearer(token),
        json={"reason": reason},
    )


async def appeal(client, token, post_id):
    return await client.post(
        f"/api/posts/{post_id}/appeal", headers=bearer(token), json={"reason": REASON_10}
    )


async def decide(client, token, appeal_id: int, action: str):
    return await client.post(
        f"/api/admin/appeals/{appeal_id}/decide",
        headers=bearer(token),
        json={"action": action},
    )


async def batch(client, token, post_ids):
    return await client.post(
        "/api/merchant/reviews/batch-approve",
        headers=bearer(token),
        json={"post_ids": list(post_ids), "confirm": True},
    )


async def claim_of(db, post_id: int):
    return await db.fetchrow(
        "SELECT * FROM reimburse_claim WHERE post_id = $1", post_id
    )


async def claim_ok(db, post_id: int):
    """断言报销单存在再取列。

    `claim_of(...)["base_points"]` 在端点未实现时会红成
    `TypeError: 'NoneType' object is not subscriptable`——那句话读不出
    「报销没落地」，只读出「测试写得糙」。
    """
    row = await claim_of(db, post_id)
    assert row is not None, "该过审动作应当落下一张报销单"
    return row


async def appeal_ok(client, token, post_id) -> dict:
    r = await appeal(client, token, post_id)
    assert r.status_code == 201, f"申诉失败：{r.status_code} {r.text}"
    return r.json()["appeal"]


async def test_rt01_claim_on_approve(client, db, merchant, customer):
    """RT-01 `user_pay_reimburse` 任务过审 → 落一张 `reimburse_claim`，`post_id` 唯一。"""
    m, m_token, _ = merchant
    c, _, _ = customer
    scene = await reimburse_scene(db, m["id"], c["id"])

    r = await approve(client, m_token, scene.post_id)
    assert r.status_code == 200, r.text

    row = await claim_ok(db, scene.post_id)
    assert row["claim_id"] == scene.claim_id
    assert row["task_id"] == scene.task_id
    assert row["merchant_id"] == m["id"]
    assert row["user_id"] == c["id"]
    assert row["base_points"] == 100, "基数 = 成本价 1 × 100 单位"
    assert row["covered_points"] == 100
    assert row["status"] == "settled"

    n = await db.fetchval(
        "SELECT count(*) FROM reimburse_claim WHERE post_id = $1", scene.post_id
    )
    assert n == 1


async def test_rt02_merchant_pay_no_claim(client, db, merchant, customer):
    """RT-02 `merchant_pay` 任务过审 → **不产生**报销单。"""
    m, m_token, _ = merchant
    c, _, _ = customer
    task_id = await insert_task(db, m["id"])
    post_id = await post_under_task(db, task_id, c["id"])

    r = await approve(client, m_token, post_id)
    assert r.status_code == 200, r.text

    assert await claim_of(db, post_id) is None, (
        "商户先付的任务里用户没垫钱，没有可报的"
    )


async def test_rt03_four_trigger_points(
    client, db, merchant, customer, seed_accounts, patch_reward
):
    """RT-03 四个触发点各测一次：单条 `approve` / `batch-approve` 中的每条 /
    `auto_approve` / `appeal_accept`。

    四个点是**四条不同的代码路径**（端点各写各的，超时那条还是无人值守的）。
    漏掉任何一个，那一类的用户就白垫——而系统不会报错，只是钱不动。
    """
    from tests.helpers import admin_token

    m, m_token, _ = merchant
    c, c_token, _ = customer
    admin = await admin_token(client)

    single = await reimburse_scene(db, m["id"], c["id"])
    assert (await approve(client, m_token, single.post_id)).status_code == 200

    batched = await reimburse_scene(db, m["id"], c["id"])
    assert (await batch(client, m_token, [batched.post_id])).status_code == 200

    auto = await reimburse_scene(db, m["id"], c["id"])
    await expire_post(db, auto.post_id, hours=73)
    assert await run_expiry() == [auto.post_id]

    appealed = await reimburse_scene(db, m["id"], c["id"])
    await set_post_status(db, appealed.post_id, "rejected")
    created = await appeal_ok(client, c_token, appealed.post_id)
    assert (await decide(client, admin, created["id"], "accept")).status_code == 200

    for scene in (single, batched, auto, appealed):
        n = await db.fetchval(
            "SELECT count(*) FROM reimburse_claim WHERE post_id = $1", scene.post_id
        )
        assert n == 1, f"post {scene.post_id} 应有且仅有一张报销单，实际 {n} 张"


async def test_rt04_reward_failure_does_not_block_reimburse(
    client, db, merchant, customer, patch_reward
):
    """RT-04 审批动作同时调用奖励接缝与报销，**一个失败不影响另一个**。

    让奖励接缝抛异常，报销仍须落地。实现若把两笔钱写在同一个 try 里、或
    顺序执行而不加 savepoint，PostgreSQL 会让整条事务作废——用户看到的是
    「过审了但钱没到」。
    """
    m, m_token, _ = merchant
    c, _, _ = customer
    scene = await reimburse_scene(db, m["id"], c["id"])
    patch_reward.raises = RuntimeError("05 的结算炸了")

    r = await approve(client, m_token, scene.post_id)
    assert r.status_code == 200, "一笔钱失败不该让审核本身 500"

    row = await claim_ok(db, scene.post_id)
    assert row["covered_points"] == 100, "奖励崩了不能连累报销"

    status = await db.fetchval(
        "SELECT status FROM social_post WHERE id = $1", scene.post_id
    )
    assert status == "approved", "审核本身也必须落地"


async def test_rt05_claim_job_rows(client, db, merchant, customer):
    """RT-05 `reimburse_claim_job` 记录本次报销覆盖的 job，`job_id` 唯一。"""
    m, m_token, _ = merchant
    c, _, _ = customer
    scene = await reimburse_scene(db, m["id"], c["id"])
    await approve(client, m_token, scene.post_id)

    rows = await db.fetch("SELECT * FROM reimburse_claim_job")
    assert [r["job_id"] for r in rows] == [scene.job_id]
    assert rows[0]["points"] == 100

    uniq = await db.fetchval(
        "SELECT count(*) FROM pg_indexes WHERE tablename = 'reimburse_claim_job' "
        "AND indexdef ILIKE '%unique%' AND indexdef ILIKE '%job_id%'"
    )
    assert uniq >= 1, "job_id 上的唯一索引是「一个 job 只报一次」的最后一道闸"


async def test_rt06_transfer_and_ledgers(client, db, merchant, customer):
    """RT-06 商户额度减少、用户额度增加，金额相等；两条流水互为 `counterparty_id`。"""
    m, m_token, _ = merchant
    c, _, _ = customer
    scene = await reimburse_scene(
        db, m["id"], c["id"], merchant_balance=1000, user_balance=0
    )
    await approve(client, m_token, scene.post_id)

    merchant_acc = await db.fetchrow(
        "SELECT balance, reserved, total_reimburse_out FROM quota_account "
        "WHERE user_id = $1",
        m["id"],
    )
    user_acc = await db.fetchrow(
        "SELECT balance, total_reimburse_in FROM quota_account WHERE user_id = $1",
        c["id"],
    )
    assert merchant_acc["balance"] == 900
    assert user_acc["balance"] == 100
    assert merchant_acc["total_reimburse_out"] == 100
    assert user_acc["total_reimburse_in"] == 100
    assert merchant_acc["reserved"] == 900, (
        "池子口径：`reserved == pool − used`。付出去的钱要从锁里同步减掉，"
        "否则任务关闭时 `_release_pool` 归还会多还一笔"
    )

    rows = await db.fetch(
        "SELECT * FROM quota_ledger WHERE source IN ('reimburse_out', 'reimburse_in') "
        "ORDER BY id"
    )
    assert len(rows) == 2, "两笔钱要各留一条流水，缺一条对账就对不上"
    out, inn = rows
    assert out["source"] == "reimburse_out"
    assert out["change"] == -100
    assert inn["source"] == "reimburse_in"
    assert inn["change"] == 100
    assert out["counterparty_id"] == c["id"]
    assert inn["counterparty_id"] == m["id"], "互为 counterparty 才能从任一端查到另一端"


async def test_rt07_duplicate_delivery_single_claim(client, db, merchant, customer):
    """RT-07 重复投递 → 报销单**恰好 1 张**，重复那次返回 `None` 不报错。"""
    m, m_token, _ = merchant
    c, _, _ = customer
    scene = await reimburse_scene(db, m["id"], c["id"])
    await approve(client, m_token, scene.post_id)

    from app.db import SessionLocal
    from app.services import quota as quota_service

    session = SessionLocal()
    try:
        again = await quota_service.reimburse(session, post_id=scene.post_id)
        await session.commit()
    finally:
        await session.close()

    assert again is None, "第二次投递不该报错，也不该再报一笔"
    n = await db.fetchval(
        "SELECT count(*) FROM reimburse_claim WHERE post_id = $1", scene.post_id
    )
    assert n == 1

    user_balance = await db.fetchval(
        "SELECT balance FROM quota_account WHERE user_id = $1", c["id"]
    )
    assert user_balance == 100, "钱只该进账一次"


async def test_rt08_capped_by_per_user_limit(client, db, merchant, customer):
    """RT-08 三重截断①：单用户上限 500、应报 1000 → 报 **500**，`status=capped`。"""
    m, m_token, _ = merchant
    c, _, _ = customer
    scene = await reimburse_scene(
        db, m["id"], c["id"], units=1000, per_user_limit=500,
        pool=10_000, reimburse_reserved=1000,
    )
    await approve(client, m_token, scene.post_id)

    row = await claim_ok(db, scene.post_id)
    assert row["base_points"] == 1000
    assert row["covered_points"] == 500
    assert row["status"] == "capped"
    assert row["reason"], "被截断时必须写清是哪个上限卡的，否则用户无从申诉"

    user_balance = await db.fetchval(
        "SELECT balance FROM quota_account WHERE user_id = $1", c["id"]
    )
    assert user_balance == 500


async def test_rt09_capped_by_pool(client, db, merchant, customer):
    """RT-09 三重截断②：池子可用 300、应报 500 → 报 **300**，`status=capped`。"""
    m, m_token, _ = merchant
    c, _, _ = customer
    scene = await reimburse_scene(
        db, m["id"], c["id"], units=500, pool=1000, pool_used=700,
        per_user_limit=10_000, reimburse_reserved=300,
    )
    await approve(client, m_token, scene.post_id)

    row = await claim_ok(db, scene.post_id)
    assert row["base_points"] == 500
    assert row["covered_points"] == 300
    assert row["status"] == "capped"

    task = await db.fetchrow(
        "SELECT reimburse_pool_used FROM task WHERE id = $1", scene.task_id
    )
    assert task["reimburse_pool_used"] == 1000, "实报的 300 要计进已用"


async def test_rt10_reject_releases(client, db, merchant, customer):
    """RT-10 驳回 → 预占释放、池子退回、**不产生报销单**。

    驳回落的是**任务侧认领的那份**（`reimburse_pool_reserved`）。商户池子的锁
    不跟着松：任务还在，池子还要给别的 job 用——松了会让 `_release_pool` 在
    关闭时多还一笔，把 `reserved` 还成负数。
    """
    m, m_token, _ = merchant
    c, _, _ = customer
    scene = await reimburse_scene(db, m["id"], c["id"], pool=1000, reimburse_reserved=100)

    before = await db.fetchrow(
        "SELECT reimburse_pool_reserved, reimburse_pool_used FROM task WHERE id = $1",
        scene.task_id,
    )
    assert before["reimburse_pool_reserved"] == 100

    r = await reject(client, m_token, scene.post_id)
    assert r.status_code == 200, r.text

    assert await claim_of(db, scene.post_id) is None

    after = await db.fetchrow(
        "SELECT reimburse_pool_reserved, reimburse_pool_used FROM task WHERE id = $1",
        scene.task_id,
    )
    assert after["reimburse_pool_reserved"] == 0, (
        "被驳回的 job 不该永久占住池子"
    )
    assert after["reimburse_pool_used"] == 0

    reserved = await db.fetchval(
        "SELECT reserved FROM quota_account WHERE user_id = $1", m["id"]
    )
    assert reserved == 1000, "商户池子的锁不该跟着松"


async def test_rt11_reject_then_accept_reports_once(
    client, db, merchant, customer, seed_accounts
):
    """RT-11 驳回后申诉成功 → 报销照常落地，且 `post_id` 唯一保证**只报一次**。"""
    from tests.helpers import admin_token

    m, m_token, _ = merchant
    c, c_token, _ = customer
    admin = await admin_token(client)
    scene = await reimburse_scene(db, m["id"], c["id"], pool=1000, reimburse_reserved=100)

    assert (await reject(client, m_token, scene.post_id)).status_code == 200
    created = await appeal_ok(client, c_token, scene.post_id)
    assert (await decide(client, admin, created["id"], "accept")).status_code == 200

    rows = await db.fetch(
        "SELECT * FROM reimburse_claim WHERE post_id = $1", scene.post_id
    )
    assert len(rows) == 1
    assert rows[0]["covered_points"] == 100
    assert rows[0]["status"] == "settled"

    user_balance = await db.fetchval(
        "SELECT balance FROM quota_account WHERE user_id = $1", c["id"]
    )
    assert user_balance == 100, "只报一次"


async def test_rt12_preemption_not_deducted_from_pool(client, db, merchant, customer):
    """RT-12 池子可用额取 `pool − used`，**不减 `reserved`**。

    这里池子正好被这一个 job 全占住（`pool=200`、`reserved=200`、`used=0`）。
    若实现写成 `pool − used − reserved`，可用额为 0，结果会是
    `pool_exhausted` 且一分不报——而那个 job 的预占本来就是为了这次报销才锁的。
    """
    m, m_token, _ = merchant
    c, _, _ = customer
    scene = await reimburse_scene(
        db, m["id"], c["id"], pool=200, pool_used=0, reimburse_reserved=200,
        per_user_limit=10_000,
    )
    await approve(client, m_token, scene.post_id)

    row = await claim_ok(db, scene.post_id)
    assert row["covered_points"] == 100, "已预占的那部分必须兑现"
    assert row["status"] == "settled"
