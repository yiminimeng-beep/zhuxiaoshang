"""RV · 商户审核（单条通过 / 驳回）+ 待审列表 + OW-02 / OW-03。

状态机的每一条非法跃迁都要有主：`approve` / `reject` 能挡住的，申诉组
（`AP-*`）里那些也要能挡住，但**理由不同**——这里挡的是「商户不该再审」，
那里挡的是「申诉流程不接纳」。
"""

from tests.helpers import (
    REASON_10,
    REASON_9,
    bearer,
    post_under_task,
    set_post_status,
    submit_post_ok,
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


async def test_rv01_approve_ok(client, db, make_scene):
    """RV-01 `approve` → 200，`status=approved`，`reviewed_at` / `reviewer_id` 落库。"""
    scene = await make_scene()
    post_id = (await submit_post_ok(client, scene.customer_token, scene))["post"]["id"]

    r = await approve(client, scene.merchant_token, post_id)
    assert r.status_code == 200, r.text

    row = await db.fetchrow("SELECT * FROM social_post WHERE id = $1", post_id)
    assert row["status"] == "approved"
    assert row["reviewed_at"] is not None
    assert row["reviewer_id"] == scene.merchant["id"]


async def test_rv02_review_log_written(client, db, make_scene):
    """RV-02 `review_log` 写入 `action="approve"`，`operator_id` = 该商户。"""
    scene = await make_scene()
    post_id = (await submit_post_ok(client, scene.customer_token, scene))["post"]["id"]
    await approve(client, scene.merchant_token, post_id)

    row = await db.fetchrow("SELECT * FROM review_log WHERE post_id = $1", post_id)
    assert row["action"] == "approve"
    assert row["operator_id"] == scene.merchant["id"]


async def test_rv03_empty_reason(client, db, make_scene):
    """RV-03 驳回时 `reason` 为空 → 422。"""
    scene = await make_scene()
    post_id = (await submit_post_ok(client, scene.customer_token, scene))["post"]["id"]

    r = await reject(client, scene.merchant_token, post_id, reason="")
    assert r.status_code == 422, r.text


async def test_rv04_reason_length_boundary(client, db, make_scene):
    """RV-04 `reason` 9 字 → 422；**10 字 → 200**（下界含等号）。"""
    scene = await make_scene()
    post_id = (await submit_post_ok(client, scene.customer_token, scene))["post"]["id"]

    short = await reject(client, scene.merchant_token, post_id, reason=REASON_9)
    assert short.status_code == 422, short.text
    assert len(REASON_9) == 9

    ok = await reject(client, scene.merchant_token, post_id, reason=REASON_10)
    assert ok.status_code == 200, ok.text
    assert len(REASON_10) == 10


async def test_rv05_reject_ok(client, db, make_scene):
    """RV-05 `reject` → 200，`status=rejected`，`reject_reason` 落库。"""
    scene = await make_scene()
    post_id = (await submit_post_ok(client, scene.customer_token, scene))["post"]["id"]
    await reject(client, scene.merchant_token, post_id)

    row = await db.fetchrow("SELECT * FROM social_post WHERE id = $1", post_id)
    assert row["status"] == "rejected"
    assert row["reject_reason"] == REASON_10
    assert row["reviewer_id"] == scene.merchant["id"]

    log = await db.fetchrow("SELECT * FROM review_log WHERE post_id = $1", post_id)
    assert log["action"] == "reject"
    assert log["reason"] == REASON_10


async def test_rv06_other_merchants_post(client, db, make_scene, merchant_b):
    """RV-06 审核**别的商户**任务下的作品 → 403。

    另一条完全独立的任务 + 作品，只是审核者换了个人。
    """
    scene = await make_scene()
    post_id = (await submit_post_ok(client, scene.customer_token, scene))["post"]["id"]

    # 作品挂在本商户名下，审核者是别人
    other_post = await _post_owned_by(db, scene, scene.merchant["id"])

    r = await approve(client, merchant_b, other_post)
    assert r.status_code == 403, r.text


async def _post_owned_by(db, scene, merchant_id: int) -> int:
    """另造一条**指定商户**任务下的作品。

    归属必须写明：`RV-06` 要看的是「别人来审我的」→ 作品得挂在本商户名下；
    `RV-12` 要看的是「我的列表里不该有别人的」→ 作品得挂在别家名下。
    同一个 helper 两种用法，归属写错的那一边断言必然站不住。
    """
    other_task = await db.fetchval(
        "INSERT INTO task (merchant_id, title, description, category, start_at, "
        "end_at, claimed_count, pay_mode, status, created_at, updated_at) "
        "VALUES ($1, '别人家的任务', '一段足够长的任务描述', '餐饮', now() - interval '1 hour', "
        "now() + interval '7 days', 0, 'merchant_pay', 'published', now(), now()) "
        "RETURNING id",
        merchant_id,
    )
    return await post_under_task(db, other_task, scene.customer["id"])


async def _other_merchant_id(db) -> int:
    """`shop0002` 的 id——`merchant_b` fixture 建的那个商户。"""
    uid = await db.fetchval('SELECT id FROM "user" WHERE account = $1', "shop0002")
    assert uid is not None, "merchant_b fixture 没建出 shop0002"
    return uid


async def test_rv07_already_approved(client, db, make_scene):
    """RV-07 已 `approved` 再 `approve` → 409。"""
    scene = await make_scene()
    post_id = (await submit_post_ok(client, scene.customer_token, scene))["post"]["id"]
    await set_post_status(db, post_id, "approved")

    r = await approve(client, scene.merchant_token, post_id)
    assert r.status_code == 409, r.text


async def test_rv08_rejected_then_approve(client, db, make_scene):
    """RV-08 已 `rejected` 再 `approve` → 409（只能走申诉流程）。"""
    scene = await make_scene()
    post_id = (await submit_post_ok(client, scene.customer_token, scene))["post"]["id"]
    await set_post_status(db, post_id, "rejected")

    r = await approve(client, scene.merchant_token, post_id)
    assert r.status_code == 409, r.text


async def test_rv09_appealed_then_approve(client, db, make_scene):
    """RV-09 已 `appealed` 再 `approve` → 409。"""
    scene = await make_scene()
    post_id = (await submit_post_ok(client, scene.customer_token, scene))["post"]["id"]
    await set_post_status(db, post_id, "appealed")

    r = await approve(client, scene.merchant_token, post_id)
    assert r.status_code == 409, r.text


async def test_rv10_appealed_then_reject(client, db, make_scene):
    """RV-10 已 `appealed` 再 `reject` → 409（等 admin 裁决，商户不得介入）。"""
    scene = await make_scene()
    post_id = (await submit_post_ok(client, scene.customer_token, scene))["post"]["id"]
    await set_post_status(db, post_id, "appealed")

    r = await reject(client, scene.merchant_token, post_id)
    assert r.status_code == 409, r.text


async def test_rv11_auto_approved_then_reject(client, db, make_scene):
    """RV-11 已 `auto_approved` 再 `reject` → 409（超时后不得反悔）。"""
    scene = await make_scene()
    post_id = (await submit_post_ok(client, scene.customer_token, scene))["post"]["id"]
    await set_post_status(db, post_id, "auto_approved")

    r = await reject(client, scene.merchant_token, post_id)
    assert r.status_code == 409, r.text


async def test_rv12_pending_list_only_mine_sorted(client, db, make_scene, merchant_b):
    """RV-12 待审列表**只含**本商户任务的作品，且按 `review_deadline` **升序**。

    造三条：最急的（最早到期）、中间的、别人家的。别人家的那条若混进来，
    商户会看到并误批别家的作品。

    三条各占**一个任务**：`uq_task_claim_active` 只允许同任务同用户有一条
    未关闭的领取，共用任务会先撞唯一索引，红在造数上。
    """
    from datetime import datetime, timedelta, timezone

    scene = await make_scene()
    now = datetime.now(timezone.utc)

    ids = []
    for hours in (1, 50, 20):  # 故意乱序造
        pid = await _post_owned_by(db, scene, scene.merchant["id"])
        await db.execute(
            "UPDATE social_post SET submitted_at = $2, review_deadline = $3 "
            "WHERE id = $1",
            pid,
            now,
            now + timedelta(hours=hours),
        )
        ids.append((hours, pid))

    foreign = await _post_owned_by(db, scene, await _other_merchant_id(db))

    r = await client.get(
        "/api/merchant/reviews?status=pending", headers=bearer(scene.merchant_token)
    )
    assert r.status_code == 200, r.text
    got = [i["id"] for i in r.json()["items"]]

    assert got == [pid for _, pid in sorted(ids)]
    assert foreign not in got


async def test_rv13_appealed_not_in_pending_list(client, db, make_scene):
    """RV-13 待审列表**不含** `appealed` 的作品（已升级到平台，不再压商户）。"""
    scene = await make_scene()
    post_id = (await submit_post_ok(client, scene.customer_token, scene))["post"]["id"]
    await set_post_status(db, post_id, "appealed")

    r = await client.get(
        "/api/merchant/reviews?status=pending", headers=bearer(scene.merchant_token)
    )
    assert post_id not in [i["id"] for i in r.json()["items"]]


# --------------------------------------------------------------------------- #
# OW-02 / OW-03
# --------------------------------------------------------------------------- #
async def test_ow02_customer_cannot_list_reviews(client, db, make_scene):
    """OW-02 客户打 `GET /api/merchant/reviews` → 403。"""
    scene = await make_scene()
    r = await client.get(
        "/api/merchant/reviews?status=pending", headers=bearer(scene.customer_token)
    )
    assert r.status_code == 403, r.text


async def test_ow03_other_merchant_gets_403_not_404(client, db, make_scene, merchant_b):
    """OW-03 别的商户对本商户的作品 `approve` / `reject` → 403（不是 404）。

    回 404 会让人以为「这条作品不存在」——而它明明存在，只是不归你管。
    两者的排查方向完全不同。
    """
    scene = await make_scene()
    post_id = (await submit_post_ok(client, scene.customer_token, scene))["post"]["id"]

    a = await approve(client, merchant_b, post_id)
    b = await reject(client, merchant_b, post_id)
    assert a.status_code == 403, a.text
    assert b.status_code == 403, b.text
