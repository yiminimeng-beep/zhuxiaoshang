"""TO · 72 小时超时（**核心规则**）。

三条最容易写错、也最贵的地方：
- `review_deadline` **不因任何操作延期**（TO-01）；
- 边界用 `<=` 而不是 `<`（TO-03）；
- 系统路径**同样**触发奖励与报销，且同样取峰值（TO-08）。
"""

from datetime import datetime, timedelta, timezone

from tests.helpers import (
    REVIEW_WINDOW_HOURS,
    bearer,
    expire_post,
    insert_snapshot,
    insert_task,
    post_under_task,
    run_expiry,
    submit_post_ok,
)


async def _post_id(client, scene) -> int:
    return (await submit_post_ok(client, scene.customer_token, scene))["post"]["id"]


async def test_to01_expiring_does_not_move_deadline(client, db, make_scene):
    """TO-01 把 `submitted_at` 前推 73h 后，`review_deadline` 仍 == `submitted_at + 72h`。"""
    scene = await make_scene()
    post_id = await _post_id(client, scene)
    await expire_post(db, post_id, hours=73)

    row = await db.fetchrow(
        "SELECT submitted_at, review_deadline FROM social_post WHERE id = $1", post_id
    )
    delta = (row["review_deadline"] - row["submitted_at"]).total_seconds()
    assert abs(delta - REVIEW_WINDOW_HOURS * 3600) <= 1


async def test_to02_expired_pending_gets_auto_approved(client, db, make_scene):
    """TO-02 `pending` 且已过期 → `run_expiry` 后 `status == "auto_approved"`。"""
    scene = await make_scene()
    post_id = await _post_id(client, scene)
    await expire_post(db, post_id, hours=73)

    swept = await run_expiry()
    assert swept == [post_id]

    status = await db.fetchval("SELECT status FROM social_post WHERE id = $1", post_id)
    assert status == "auto_approved"


async def test_to03_deadline_exactly_now_is_expired(client, db, make_scene):
    """TO-03 **边界**：`review_deadline` 恰好 `== now` → 判定超时（`<=` 而非 `<`）。

    实现若写成 `<`，这条红而 TO-02 仍绿——差的这一个等号，会让恰好卡在
    72 小时整点的作品永远躺在待审队列里。

    必须把 `now` 喂进去：靠 `time.sleep` 或「写库时的 now」都有毫秒级抖动，
    那样 `<` 和 `<=` 都能过，等于没测。
    """
    scene = await make_scene()
    post_id = await _post_id(client, scene)

    moment = datetime.now(timezone.utc).replace(microsecond=0)
    await db.execute(
        "UPDATE social_post SET submitted_at = $2, review_deadline = $3 WHERE id = $1",
        post_id,
        moment - timedelta(hours=REVIEW_WINDOW_HOURS),
        moment,
    )

    swept = await run_expiry(now=moment)
    assert swept == [post_id], "恰好等于 now 必须算超时"
    assert (
        await db.fetchval("SELECT status FROM social_post WHERE id = $1", post_id)
        == "auto_approved"
    )


async def test_to04_not_yet_expired_stays_pending(client, db, make_scene):
    """TO-04 未到期（`deadline = now + 1h`）→ `run_expiry` 后仍是 `pending`。"""
    scene = await make_scene()
    post_id = await _post_id(client, scene)

    moment = datetime.now(timezone.utc)
    await db.execute(
        "UPDATE social_post SET submitted_at = $2, review_deadline = $3 WHERE id = $1",
        post_id,
        moment - timedelta(hours=REVIEW_WINDOW_HOURS) + timedelta(hours=1),
        moment + timedelta(hours=1),
    )

    assert await run_expiry(now=moment) == []
    assert (
        await db.fetchval("SELECT status FROM social_post WHERE id = $1", post_id)
        == "pending"
    )


async def test_to05_merchant_approves_before_deadline(client, db, make_scene):
    """TO-05 到期前通过 → `review_log.action == "approve"`，且**没有** `auto_approve` 行。"""
    scene = await make_scene()
    post_id = await _post_id(client, scene)

    r = await client.post(
        f"/api/merchant/reviews/{post_id}/approve",
        headers=bearer(scene.merchant_token),
    )
    assert r.status_code == 200, r.text

    await run_expiry()
    rows = await db.fetch(
        "SELECT action FROM review_log WHERE post_id = $1", post_id
    )
    assert [r["action"] for r in rows] == ["approve"]


async def test_to06_merchant_approves_after_deadline(client, db, make_scene):
    """TO-06 到期后通过 → `409`。

    注意这里**故意不先跑 `run_expiry`**：此时库里还是 `pending`，唯一能挡住
    商户的就是端点自己看的 `review_deadline`。不这么测，「窗口一过商户还能
    抢在调度器之前通过」这个漏洞就漏过去了。
    """
    scene = await make_scene()
    post_id = await _post_id(client, scene)
    await expire_post(db, post_id, hours=73)

    r = await client.post(
        f"/api/merchant/reviews/{post_id}/approve",
        headers=bearer(scene.merchant_token),
    )
    assert r.status_code == 409, r.text
    assert (
        await db.fetchval("SELECT status FROM social_post WHERE id = $1", post_id)
        == "pending"
    )


async def test_to06b_merchant_rejects_after_deadline(client, db, make_scene):
    """TO-06b 到期后**驳回** → `409`（窗口一过，商户两边都不能反悔）。"""
    scene = await make_scene()
    post_id = await _post_id(client, scene)
    await expire_post(db, post_id, hours=73)

    r = await client.post(
        f"/api/merchant/reviews/{post_id}/reject",
        headers=bearer(scene.merchant_token),
        json={"reason": "过了时限也想驳回的十个字"},
    )
    assert r.status_code == 409, r.text


async def test_to07_auto_approve_log_has_no_operator(client, db, make_scene):
    """TO-07 自动通过的 `review_log.operator_id` 为 `null`（系统件的身份是空的）。"""
    scene = await make_scene()
    post_id = await _post_id(client, scene)
    await expire_post(db, post_id, hours=73)
    await run_expiry()

    row = await db.fetchrow(
        "SELECT action, operator_id FROM review_log WHERE post_id = $1", post_id
    )
    assert row["action"] == "auto_approve"
    assert row["operator_id"] is None


async def test_to08_auto_approve_settles_with_peak(client, db, make_scene, patch_reward):
    """TO-08 自动通过会调奖励结算接缝，且传入的是**峰值**。

    把「峰值」与「系统路径」绑在一起测：人工路径有人复核，系统路径没人看，
    峰值口径最容易在这里被漏成「取最后一条」。
    """
    scene = await make_scene()
    post_id = await _post_id(client, scene)
    await insert_snapshot(db, post_id, likes=100, collects=0, comments=0, shares=0)
    await insert_snapshot(db, post_id, likes=1800, collects=0, comments=0, shares=0)
    await insert_snapshot(db, post_id, likes=900, collects=0, comments=0, shares=0)
    await expire_post(db, post_id, hours=73)

    await run_expiry()

    assert len(patch_reward.calls) == 1
    assert patch_reward.calls[0]["engagement"] == 1800


async def test_to09_run_expiry_is_idempotent(client, db, make_scene):
    """TO-09 `run_expiry` 连跑 3 次 → `auto_approve` 日志**恰好 1 条**。"""
    scene = await make_scene()
    post_id = await _post_id(client, scene)
    await expire_post(db, post_id, hours=73)

    await run_expiry()
    await run_expiry()
    await run_expiry()

    count = await db.fetchval(
        "SELECT count(*) FROM review_log WHERE post_id = $1 AND action = 'auto_approve'",
        post_id,
    )
    assert count == 1


async def test_to10_backlog_all_swept(client, db, merchant, customer):
    """TO-10 积压：过期时间各不相同的 3 条，一次扫描全部处理。

    题面就是「停服 30 小时后再启动」：扫描不能只看「刚过期的那些」，
    必须把所有 `<= now` 的都翻出来。

    **一条作品一个任务**：`uq_task_claim_active` 限定同任务同用户只能有一条
    未关闭的领取，而一条领取只算一篇作品。三条作品共用一个任务会直接撞唯一
    索引，红在造数上而不是红在被测规则上。
    """
    merchant_user, _, _ = merchant
    customer_user, _, _ = customer

    # `expire_post(hours=N)` 把 `submitted_at` 推到 N 小时前，所以到期时刻是
    # 「N − 72」小时之前。要造「逾期 30h / 8h / 1 分钟」这三条，N 必须加上
    # 72——直接写 30 / 2 / 1 会让三条**都还没到期**，扫描器一条也扫不到，
    # 用例红在造数上而不是红在被测规则上。
    ids = []
    for overdue in (30, 8, 1 / 60):
        task_id = await insert_task(db, merchant_user["id"])
        pid = await post_under_task(db, task_id, customer_user["id"])
        await expire_post(db, pid, hours=REVIEW_WINDOW_HOURS + overdue)
        ids.append(pid)

    swept = await run_expiry()
    assert sorted(swept) == sorted(ids)

    rows = await db.fetch(
        "SELECT status FROM social_post WHERE id = ANY($1::bigint[])", ids
    )
    assert {r["status"] for r in rows} == {"auto_approved"}


async def test_to11_pending_but_not_scanned_when_deadline_future(client, db, make_scene):
    """TO-11 反向：还没到期就**不该**被扫到（不能把「扫得到」写成「扫一切」）。"""
    scene = await make_scene()
    post_id = await _post_id(client, scene)

    assert await run_expiry() == []
    assert (
        await db.fetchval("SELECT status FROM social_post WHERE id = $1", post_id)
        == "pending"
    )
