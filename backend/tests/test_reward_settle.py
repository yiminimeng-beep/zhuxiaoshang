"""05-reward · `RE` 组：结算与落库。

对应 test_plan.md 的 `RE-01` ~ `RE-11`。

这一组**一律走过审**（`POST /api/merchant/reviews/{id}/approve` 等四个动作），
不直调 `settle`——因为被测的是「过审之后到底落了什么」。

三个动作（72h 超时 / 批量 / 申诉受理）与人工通过共用同一条实现，是本组
后半段的主线：人工路径有人复核，系统路径没人看，最容易漏的就是后者。
"""

import pytest

from tests.helpers import (
    REASON_10,
    another_post,
    admin_token,
    as_json,
    approve_ok,
    approve_post,
    bearer,
    expire_post,
    grant_rows,
    insert_coupon,
    make_tiers,
    only_grant,
    point_ledger_rows,
    reward_scene,
    run_expiry,
    settle_now,
)

pytestmark = pytest.mark.asyncio


async def _count(db, sql: str, *args) -> int:
    return int(await db.fetchval(sql, *args) or 0)


async def test_re01_approve_writes_one_grant_row(db, client, merchant, customer):
    """过审后恰好 1 条 `reward_grant`，列列都对。"""
    _, token, _ = merchant
    merchant_id, customer_id = merchant[0]["id"], customer[0]["id"]
    scene = await reward_scene(db, merchant_id, customer_id)

    await approve_ok(client, token, scene.post_id)

    rows = await grant_rows(db, scene.post_id)
    assert len(rows) == 1
    row = rows[0]
    assert row["status"] == "granted"
    assert row["granted_at"] is not None
    assert row["tier_index"] == 0
    assert row["claim_id"] == scene.claim_id
    assert row["task_id"] == scene.task_id
    assert row["reward_rule_id"] == scene.rule_id
    assert row["user_id"] == customer_id
    # 峰值 = 10 + 5 + 3，由 04 的 peak_engagement 算出来传进来
    assert row["engagement"] == 18


async def test_re02_settle_is_idempotent(db, merchant, customer):
    """同一 post 连投 3 次 → 仍 1 行，积分只加 1 次。

    `post_id` 唯一约束是最后一道闸。应用层的 `if exists: return` 是第一道，
    并发下会漏（两个请求同时查到「不存在」，然后各写一行）。
    """
    merchant_id, customer_id = merchant[0]["id"], customer[0]["id"]
    scene = await reward_scene(db, merchant_id, customer_id)

    for _ in range(3):
        await settle_now(
            post_id=scene.post_id,
            user_id=customer_id,
            task_id=scene.task_id,
            engagement=18,
        )

    assert len(await grant_rows(db, scene.post_id)) == 1
    ledger = await point_ledger_rows(db, customer_id)
    assert len(ledger) == 1
    assert sum(r["change"] for r in ledger) == 50


async def test_re03_second_approve_conflicts_without_second_grant(
    db, client, merchant, customer
):
    """重复过审 → 409（04 的状态机拦的），且不会多出第二行 grant。"""
    _, token, _ = merchant
    merchant_id, customer_id = merchant[0]["id"], customer[0]["id"]
    scene = await reward_scene(db, merchant_id, customer_id)

    await approve_ok(client, token, scene.post_id)
    again = await approve_post(client, token, scene.post_id)

    assert again.status_code == 409
    assert len(await grant_rows(db, scene.post_id)) == 1


async def test_re04_cash_and_points_both_granted(db, client, merchant, customer):
    """`reward = {cash, points}` → 两个都发。"""
    _, token, _ = merchant
    merchant_id, customer_id = merchant[0]["id"], customer[0]["id"]
    scene = await reward_scene(
        db, merchant_id, customer_id, tiers=make_tiers(3, reward={"cash": 100, "points": 50})
    )

    await approve_ok(client, token, scene.post_id)

    payouts = await db.fetch(
        "SELECT * FROM cash_payout WHERE user_id = $1", customer_id
    )
    assert len(payouts) == 1
    assert payouts[0]["amount"] == 100
    assert payouts[0]["user_id"] == customer_id

    ledger = await point_ledger_rows(db, customer_id)
    assert len(ledger) == 1
    assert ledger[0]["source"] == "task_reward"
    assert ledger[0]["change"] == 50
    assert ledger[0]["balance_after"] == 50


async def test_re05_exhausted_coupon_does_not_roll_back_points(
    db, client, merchant, customer
):
    """券发完了 → 券那项失败，但**积分照发**，`status=granted`。

    spec 原话：「不得整体回滚」。最顺手的写法是把整笔奖励包在一个事务里，
    券那一步抛异常就把积分一起回滚——状态码仍是 `200`，只有查库才看得见。
    """
    _, token, _ = merchant
    merchant_id, customer_id = merchant[0]["id"], customer[0]["id"]
    coupon_id = await insert_coupon(db, merchant_id, total=1, issued=1)
    scene = await reward_scene(
        db,
        merchant_id,
        customer_id,
        tiers=make_tiers(3, reward={"points": 50, "coupon_id": coupon_id}),
    )

    await approve_ok(client, token, scene.post_id)

    row = await only_grant(db, scene.post_id)
    assert row["status"] == "granted"
    detail = as_json(row["reward_detail"])
    assert detail["points"] == 50
    assert not detail.get("coupon_ids")

    assert len(await point_ledger_rows(db, customer_id)) == 1
    assert await _count(db, "SELECT count(*) FROM user_coupon") == 0


async def test_re06_inactive_coupon_skipped_rest_still_granted(
    db, client, merchant, customer
):
    """券模板已 `inactive` → 不发券，积分照发，`reward_detail` 不含 `coupon_ids`。"""
    _, token, _ = merchant
    merchant_id, customer_id = merchant[0]["id"], customer[0]["id"]
    coupon_id = await insert_coupon(db, merchant_id, status="inactive")
    scene = await reward_scene(
        db,
        merchant_id,
        customer_id,
        tiers=make_tiers(3, reward={"points": 50, "coupon_id": coupon_id}),
    )

    await approve_ok(client, token, scene.post_id)

    row = await only_grant(db, scene.post_id)
    assert row["status"] == "granted"
    assert "coupon_ids" not in as_json(row["reward_detail"])
    assert len(await point_ledger_rows(db, customer_id)) == 1
    assert await _count(db, "SELECT count(*) FROM user_coupon") == 0


async def test_re07_benefit_only_recorded_as_text(db, client, merchant, customer):
    """`benefit` 只记文本，**不产生任何一本账**（spec：不做 benefit 核销）。"""
    _, token, _ = merchant
    merchant_id, customer_id = merchant[0]["id"], customer[0]["id"]
    scene = await reward_scene(
        db, merchant_id, customer_id, tiers=make_tiers(3, reward={"benefit": "免费到店一次"})
    )

    await approve_ok(client, token, scene.post_id)

    row = await only_grant(db, scene.post_id)
    assert as_json(row["reward_detail"])["benefit"] == "免费到店一次"
    assert await point_ledger_rows(db, customer_id) == []
    assert await _count(db, "SELECT count(*) FROM cash_payout") == 0
    assert await _count(db, "SELECT count(*) FROM user_coupon") == 0


async def test_re08_below_threshold_still_writes_row(db, client, merchant, customer):
    """`below_threshold` 也落一行——「没发」本身要可审计。"""
    _, token, _ = merchant
    merchant_id, customer_id = merchant[0]["id"], customer[0]["id"]
    tiers = [
        {"min": 0, "max": 9, "reward": {"points": 10}},
        {"min": 100, "max": None, "reward": {"points": 500}},
    ]
    scene = await reward_scene(db, merchant_id, customer_id, tiers=tiers)

    # 峰值 18 落在 [10,99] 的缺口里
    await approve_ok(client, token, scene.post_id)

    row = await only_grant(db, scene.post_id)
    assert row["status"] == "below_threshold"
    assert row["granted_at"] is None
    assert await point_ledger_rows(db, customer_id) == []


async def test_re09_auto_approve_settles_identically(db, merchant, customer):
    """72h 自动通过走**同一条**结算：落下的行与人工过审逐列同形。

    这是四个过审动作里唯一**没有人工复核**的一条，也正是最容易漏结算的
    地方。断言取「除主键与时间之外逐列相同」，比只看「有没有行」紧一档。
    """
    merchant_id, customer_id = merchant[0]["id"], customer[0]["id"]
    auto_scene = await reward_scene(db, merchant_id, customer_id)
    # 对照作品挂在**同一个任务**下：`task_id` 与 `reward_rule_id` 也在比对列里，
    # 若另起一个任务，这两列天然不同，等于把对比放宽了两档。
    manual_post_id = await another_post(db, auto_scene)

    await expire_post(db, auto_scene.post_id)
    swept = await run_expiry()
    assert auto_scene.post_id in swept

    # 人工侧走同一个实现落一行，用来对照
    await settle_now(
        post_id=manual_post_id,
        user_id=customer_id,
        task_id=auto_scene.task_id,
        engagement=18,
    )

    auto_row = await only_grant(db, auto_scene.post_id)
    manual_row = await only_grant(db, manual_post_id)
    columns = (
        "user_id",
        "task_id",
        "reward_rule_id",
        "engagement",
        "tier_index",
        "reward_detail",
        "status",
        "fail_reason",
    )
    assert auto_row["status"] == "granted"
    for col in columns:
        assert auto_row[col] == manual_row[col], f"列 {col} 与人工过审不一致"


async def test_re11_appeal_accept_settles_once(db, client, merchant, customer, seed_accounts):
    """申诉受理（`appeal_accept`）走同一条：`review_log` 与 grant 都只有 1 份。

    先驳回 → 申诉 → admin 受理。这条路径此前只有申诉本身的用例在盯，
    「受理之后钱发了没有」没人看。
    """
    _, token, _ = merchant
    _, c_token, _ = customer
    merchant_id, customer_id = merchant[0]["id"], customer[0]["id"]
    scene = await reward_scene(db, merchant_id, customer_id)

    rejected = await client.post(
        f"/api/merchant/reviews/{scene.post_id}/reject",
        headers=bearer(token),
        json={"reason": REASON_10},
    )
    assert rejected.status_code == 200, rejected.text

    appeal = await client.post(
        f"/api/posts/{scene.post_id}/appeal",
        headers=bearer(c_token),
        json={"reason": REASON_10},
    )
    assert appeal.status_code == 201, appeal.text
    appeal_id = appeal.json()["appeal"]["id"]

    admin = await admin_token(client)
    decided = await client.post(
        f"/api/admin/appeals/{appeal_id}/decide",
        headers=bearer(admin),
        json={"action": "accept"},
    )
    assert decided.status_code == 200, decided.text

    rows = await grant_rows(db, scene.post_id)
    assert len(rows) == 1
    assert rows[0]["status"] == "granted"
    assert as_json(rows[0]["reward_detail"])["points"] == 50

    log_count = await _count(
        db,
        "SELECT count(*) FROM review_log WHERE post_id = $1 AND action = $2",
        scene.post_id,
        "appeal_accept",
    )
    assert log_count == 1
    assert await _count(
        db, "SELECT count(*) FROM point_ledger WHERE user_id = $1", customer_id
    ) == 1


async def test_re10_batch_approve_settles(db, client, merchant, customer):
    """批量通过走**同一条**结算：同样产生 `reward_grant`。"""
    _, token, _ = merchant
    merchant_id, customer_id = merchant[0]["id"], customer[0]["id"]
    scene = await reward_scene(db, merchant_id, customer_id)

    r = await client.post(
        "/api/merchant/reviews/batch-approve",
        headers=bearer(token),
        json={"post_ids": [scene.post_id], "confirm": True},
    )
    assert r.status_code == 200, r.text

    rows = await grant_rows(db, scene.post_id)
    assert len(rows) == 1
    assert rows[0]["status"] == "granted"
    assert as_json(rows[0]["reward_detail"])["points"] == 50
