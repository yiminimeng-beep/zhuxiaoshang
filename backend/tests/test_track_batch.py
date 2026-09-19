"""BA · 批量通过（一键全通过）+ 部分失败的边界。

这一组最贵的一条是 `BA-11`：spec 明写「不做全事务回滚」，而实现里最顺手的
写法恰恰是「一个事务包住整个循环」。故意把坏的那条**放在末尾**——若是全事务
实现，前面已通过的三条会跟着一起被回滚，断言立刻红。
"""

from tests.helpers import (
    bearer,
    insert_snapshot,
    insert_task,
    post_under_task,
    post_with_task,
    reimburse_scene,
    run_expiry,
    set_post_status,
)

_MISSING = object()


async def batch(client, token, post_ids, confirm=True):
    body = {"post_ids": list(post_ids)}
    if confirm is not _MISSING:
        body["confirm"] = confirm
    return await client.post(
        "/api/merchant/reviews/batch-approve", headers=bearer(token), json=body
    )


def body_of(r) -> dict:
    """先断 200 再取键。

    直接 `r.json()["succeeded"]` 的话，端点还没实现时拿到的是
    `{"detail": "Not Found"}`，于是红成一句 `KeyError: 'succeeded'`——
    看的人分不清是「没实现」还是「响应形状写错了」。
    """
    assert r.status_code == 200, f"批量通过失败：{r.status_code} {r.text}"
    return r.json()


def failed_map(body) -> dict:
    """`failed` 摊成 `{post_id: reason}`——比逐个比列表稳，也不受顺序影响。"""
    return {f["post_id"]: f["reason"] for f in body["failed"]}


def succeeded(body) -> list[int]:
    return body["succeeded"]


async def posts_under(db, merchant_id: int, user_id: int, n: int, **post_over) -> list[int]:
    """造 n 条 pending 作品——**每条一个任务**。

    同一个任务下、同一个用户只能有一条未关闭的领取
    （`uq_task_claim_active` 这个部分唯一索引），而一条领取只算一篇作品
    （04 spec「一个领取只算一个作品」）。所以「同任务同用户的多篇作品」
    在这个模型里根本不成立，要造 n 条就得有 n 个任务。
    """
    out = []
    for _ in range(n):
        task_id = await insert_task(db, merchant_id)
        out.append(await post_under_task(db, task_id, user_id, **post_over))
    return out


async def user_id_of(db, account: str) -> int:
    return await db.fetchval('SELECT id FROM "user" WHERE account = $1', account)


async def test_ba01_all_ok(client, db, merchant, customer):
    """BA-01 全部合法 → `200`，`succeeded` 全量，`failed == []`。"""
    m, m_token, _ = merchant
    c, _, _ = customer
    ids = await posts_under(db, m["id"], c["id"], 3)

    r = await batch(client, m_token, ids)
    assert r.status_code == 200, r.text
    body = r.json()
    assert sorted(succeeded(body)) == sorted(ids)
    assert body["failed"] == []


async def test_ba02_empty_list(client, db, merchant):
    """BA-02 `post_ids` 空数组 → `422`（空批不是「无事发生」，是调用方写错了）。"""
    _, m_token, _ = merchant
    r = await batch(client, m_token, [])
    assert r.status_code == 422, r.text


async def test_ba03_size_boundary(client, db, merchant):
    """BA-03 51 条 → `422`；**50 条 → `200`**（上限含等号）。

    长度用不存在的 id 造：这一条测的是**条数**，不是存在性。真建 50 条作品
    会把一条边界用例拖成一分钟，而它对「50 是不是合法长度」毫无帮助。
    """
    _, m_token, _ = merchant

    over = await batch(client, m_token, list(range(8_000_001, 8_000_052)))
    assert over.status_code == 422, over.text
    assert len(range(8_000_001, 8_000_052)) == 51

    exact = await batch(client, m_token, list(range(8_000_101, 8_000_151)))
    assert exact.status_code == 200, exact.text
    assert len(failed_map(exact.json())) == 50


async def test_ba04_confirm_required(client, db, merchant, customer):
    """BA-04 缺 `confirm`（或 `confirm=false`）→ `422`，且**一条都没被改动**。

    只断言 422 是不够的：实现若先改了库再校验 `confirm`，状态码照样是 422，
    而作品已经被批了。「二次确认」这道闸的价值就在**零副作用**上。
    """
    m, m_token, _ = merchant
    c, _, _ = customer
    ids = await posts_under(db, m["id"], c["id"], 2)

    missing = await batch(client, m_token, ids, confirm=_MISSING)
    assert missing.status_code == 422, missing.text

    false = await batch(client, m_token, ids, confirm=False)
    assert false.status_code == 422, false.text

    rows = await db.fetch(
        "SELECT status FROM social_post WHERE id = ANY($1::bigint[])", ids
    )
    assert {r["status"] for r in rows} == {"pending"}
    assert await db.fetchval("SELECT count(*) FROM review_log") == 0


async def test_ba05_duplicates_deduped(client, db, merchant, customer):
    """BA-05 `post_ids` 含重复 id → 去重，`succeeded` 不重复出现。"""
    m, m_token, _ = merchant
    c, _, _ = customer
    a, b = await posts_under(db, m["id"], c["id"], 2)

    r = await batch(client, m_token, [a, a, b, b, a])
    assert r.status_code == 200, r.text
    body = r.json()
    assert sorted(succeeded(body)) == sorted([a, b])
    assert len(succeeded(body)) == 2, "重复 id 不得让同一条出现两次"

    n = await db.fetchval(
        "SELECT count(*) FROM review_log WHERE post_id = $1 "
        "AND action = 'batch_approve'",
        a,
    )
    assert n == 1, "去重必须在处理之前生效，否则会落下两条日志"


async def test_ba06_foreign_post_failed(client, db, merchant, customer, merchant_b):
    """BA-06 混入**别的商户**的作品 → 该条 `forbidden`，其余照常成功。"""
    m, m_token, _ = merchant
    c, _, _ = customer
    mine = await posts_under(db, m["id"], c["id"], 2)

    b_id = await user_id_of(db, "shop0002")
    _, foreign = await post_with_task(db, b_id, c["id"])

    r = await batch(client, m_token, [*mine, foreign])
    assert r.status_code == 200, r.text
    body = r.json()
    assert sorted(succeeded(body)) == sorted(mine)
    assert failed_map(body) == {foreign: "forbidden"}

    status = await db.fetchval("SELECT status FROM social_post WHERE id = $1", foreign)
    assert status == "pending", "别人家的作品一个字节都不该动"


async def test_ba07_already_approved(client, db, merchant, customer):
    """BA-07 混入已 `approved` 的作品 → `already_reviewed`，其余成功。"""
    m, m_token, _ = merchant
    c, _, _ = customer
    good, done = await posts_under(db, m["id"], c["id"], 2)
    await set_post_status(db, done, "approved")

    body = body_of(await batch(client, m_token, [good, done]))
    assert succeeded(body) == [good]
    assert failed_map(body) == {done: "already_reviewed"}


async def test_ba08_rejected_is_invalid_status(client, db, merchant, customer):
    """BA-08 混入 `rejected` 的作品 → `invalid_status`。"""
    m, m_token, _ = merchant
    c, _, _ = customer
    good, done = await posts_under(db, m["id"], c["id"], 2)
    await set_post_status(db, done, "rejected")

    body = body_of(await batch(client, m_token, [good, done]))
    assert succeeded(body) == [good]
    assert failed_map(body) == {done: "invalid_status"}


async def test_ba09_appealed_is_invalid_status(client, db, merchant, customer):
    """BA-09 混入 `appealed` 的作品 → `invalid_status`（已升级平台，商户不得介入）。"""
    m, m_token, _ = merchant
    c, _, _ = customer
    good, done = await posts_under(db, m["id"], c["id"], 2)
    await set_post_status(db, done, "appealed")

    body = body_of(await batch(client, m_token, [good, done]))
    assert succeeded(body) == [good]
    assert failed_map(body) == {done: "invalid_status"}


async def test_ba10_missing_id(client, db, merchant, customer):
    """BA-10 混入不存在的 id → `not_found`，其余成功。"""
    m, m_token, _ = merchant
    c, _, _ = customer
    good = (await posts_under(db, m["id"], c["id"], 1))[0]

    ghost = 7_777_777
    body = body_of(await batch(client, m_token, [good, ghost]))
    assert succeeded(body) == [good]
    assert failed_map(body) == {ghost: "not_found"}


async def test_ba11_no_whole_batch_rollback(client, db, merchant, customer):
    """BA-11 **部分失败不整批回滚**：3 好 1 坏（坏的在末尾）→ 那 3 条真的是 `approved`。

    坏的那条**故意放末尾**。若实现把整个循环包进一个事务、末尾那条一炸就
    整体回滚，前面三条在库里会退回 `pending`——状态码仍是 200，只有查库才看得见。
    """
    m, m_token, _ = merchant
    c, _, _ = customer
    ids = await posts_under(db, m["id"], c["id"], 3)
    bad = 7_777_778

    r = await batch(client, m_token, [*ids, bad])
    assert r.status_code == 200, r.text
    body = r.json()
    assert sorted(succeeded(body)) == sorted(ids)
    assert failed_map(body) == {bad: "not_found"}

    rows = await db.fetch(
        "SELECT id, status FROM social_post WHERE id = ANY($1::bigint[])", ids
    )
    assert {r["status"] for r in rows} == {"approved"}, "已成功的必须真的成功"


async def test_ba12_one_log_per_post(client, db, merchant, customer):
    """BA-12 通过 3 条 → `review_log(action="batch_approve")` **恰好 3 条**。

    「一条汇总日志」看起来更省，但它让审计答不出「这条是谁批的」——
    批量通过恰恰是最需要能逐条追责的一批操作。
    """
    m, m_token, _ = merchant
    c, _, _ = customer
    ids = await posts_under(db, m["id"], c["id"], 3)

    r = await batch(client, m_token, ids)
    assert r.status_code == 200, r.text

    rows = await db.fetch(
        "SELECT post_id, action FROM review_log WHERE action = 'batch_approve'"
    )
    assert len(rows) == 3
    assert sorted(r["post_id"] for r in rows) == sorted(ids)


async def test_ba13_operator_and_distinguishable_action(client, db, merchant, customer):
    """BA-13 每条日志的 `operator_id` 都是该商户，且 `action` 与单条 `approve` 可区分。"""
    m, m_token, _ = merchant
    c, _, _ = customer
    ids = await posts_under(db, m["id"], c["id"], 2)

    await batch(client, m_token, ids)
    rows = await db.fetch("SELECT * FROM review_log ORDER BY post_id")
    assert {r["operator_id"] for r in rows} == {m["id"]}
    assert {r["action"] for r in rows} == {"batch_approve"}

    actions = await db.fetchval("SELECT count(*) FROM review_log WHERE action = 'approve'")
    assert actions == 0, "批量通过不得写成单条 approve 的 action"


async def test_ba14_no_double_settle_with_sweeper(
    client, db, merchant, customer, patch_reward
):
    """BA-14 批量通过后再跑 72h 扫描 → 不双重结算（每条恰好 1 条终态日志）。

    批量通过之后这些 post 已离开 `pending`，扫描必须一条都扫不到；
    反过来说，若扫描只看 `review_deadline <= now` 而不看状态，就会再结算一遍。
    """
    m, m_token, _ = merchant
    c, _, _ = customer
    ids = await posts_under(db, m["id"], c["id"], 3)

    r = await batch(client, m_token, ids)
    assert r.status_code == 200, r.text

    swept = await run_expiry()
    assert swept == [], "已通过的 post 不该被超时扫描再处理一次"
    assert len(patch_reward.calls) == 3, "结算接缝每篇只该被调用一次"

    for pid in ids:
        n = await db.fetchval(
            "SELECT count(*) FROM review_log WHERE post_id = $1 "
            "AND action IN ('approve', 'batch_approve', 'auto_approve', 'appeal_accept')",
            pid,
        )
        assert n == 1, f"post {pid} 落了 {n} 条终态日志"


async def test_ba15_peak_engagement_same_as_single(
    client, db, merchant, customer, patch_reward
):
    """BA-15 批量通过的 `engagement` 取值规则与单条通过**完全一致**（都取峰值）。"""
    m, m_token, _ = merchant
    c, _, _ = customer
    post_id = (await posts_under(db, m["id"], c["id"], 1))[0]
    await insert_snapshot(db, post_id, likes=100, collects=0, comments=0, shares=0)
    await insert_snapshot(db, post_id, likes=1800, collects=0, comments=0, shares=0)
    await insert_snapshot(db, post_id, likes=900, collects=0, comments=0, shares=0)

    r = await batch(client, m_token, [post_id])
    assert r.status_code == 200, r.text

    assert len(patch_reward.calls) == 1
    assert patch_reward.calls[0]["engagement"] == 1800


async def test_ba16_batch_triggers_reimburse(client, db, merchant, customer):
    """BA-16 批量通过触发报销：3 条 `user_pay_reimburse` → 恰好 3 张报销单。"""
    m, m_token, _ = merchant
    c, _, _ = customer
    scenes = [await reimburse_scene(db, m["id"], c["id"]) for _ in range(3)]
    post_ids = [s.post_id for s in scenes]

    r = await batch(client, m_token, post_ids)
    assert r.status_code == 200, r.text
    assert sorted(succeeded(r.json())) == sorted(post_ids)

    rows = await db.fetch(
        "SELECT post_id FROM reimburse_claim WHERE post_id = ANY($1::bigint[])",
        post_ids,
    )
    assert len(rows) == 3
    assert sorted(r["post_id"] for r in rows) == sorted(post_ids)
