"""AP · 申诉 / 复议状态机（`AP-09` 在 `test_track_metrics.py`，那里测的是「申诉期间还能涨互动」）。

`AP-17` 与 `AP-07` 拦的是两件事：`AP-07` 是「`appealed` 状态下不能再提」，
`AP-17` 是「申诉被驳回之后也不能再提」。若实现只按 `post.status == "rejected"`
来判断「能不能申诉」（这是最自然的写法），`AP-17` 会红——**被驳回的申诉会让
post 回到 `rejected`，那个状态天然满足「可以申诉」**。所以「仅一次」这条规则
必须以 `appeal.post_id` 唯一为准，而不能从 post 状态反推。
"""

from tests.helpers import (
    REASON_10,
    bearer,
    insert_appeal,
    insert_task,
    post_under_task,
    reimburse_scene,
    set_post_status,
)

TOO_LONG = "长" * 501


async def appeal(client, token, post_id, reason=REASON_10):
    return await client.post(
        f"/api/posts/{post_id}/appeal", headers=bearer(token), json={"reason": reason}
    )


async def appeal_ok(client, token, post_id, reason=REASON_10) -> dict:
    """申诉并断言 201，返回 `appeal` 本体。

    直接 `r.json()["appeal"]` 的话，端点未实现时拿到的是
    `{"detail": "Not Found"}`，红成 `KeyError: 'appeal'`——看起来像响应形状
    写错，其实是路由还没写。
    """
    r = await appeal(client, token, post_id, reason=reason)
    assert r.status_code == 201, f"申诉失败：{r.status_code} {r.text}"
    return r.json()["appeal"]


async def decide(client, token, appeal_id: int, action, **over):
    body = {"action": action}
    body.update(over)
    return await client.post(
        f"/api/admin/appeals/{appeal_id}/decide", headers=bearer(token), json=body
    )


async def rejected_post(db, merchant_id: int, user_id: int, **post_over) -> int:
    """造一条已 `rejected` 的作品——申诉的唯一合法入口状态。"""
    task_id = await insert_task(db, merchant_id)
    post_id = await post_under_task(db, task_id, user_id, **post_over)
    await set_post_status(db, post_id, "rejected")
    return post_id


async def another_customer(db, account: str = "ap-other") -> int:
    return await db.fetchval(
        'INSERT INTO "user" (account, password_hash, role, nickname, status) '
        "VALUES ($1, 'x', 'customer', $1, 'active') RETURNING id",
        account,
    )


async def test_ap01_appeal_ok(client, db, merchant, customer):
    """AP-01 `rejected` 的作品申诉 → `201`，`status=appealed`，`appeal.status=pending`。"""
    m, _, _ = merchant
    c, c_token, _ = customer
    post_id = await rejected_post(db, m["id"], c["id"])

    r = await appeal(client, c_token, post_id)
    assert r.status_code == 201, r.text
    assert r.json()["appeal"]["status"] == "pending"

    status = await db.fetchval("SELECT status FROM social_post WHERE id = $1", post_id)
    assert status == "appealed"


async def test_ap02_reason_length_boundary(client, db, merchant, customer):
    """AP-02 `reason` 9 字 → `422`；**10 字 → `201`**（下界含等号）。"""
    m, _, _ = merchant
    c, c_token, _ = customer
    post_id = await rejected_post(db, m["id"], c["id"])

    short = await appeal(client, c_token, post_id, reason=REASON_10[:9])
    assert short.status_code == 422, short.text

    ok = await appeal(client, c_token, post_id, reason=REASON_10)
    assert ok.status_code == 201, ok.text


async def test_ap03_reason_too_long(client, db, merchant, customer):
    """AP-03 `reason` 501 字 → `422`。"""
    m, _, _ = merchant
    c, c_token, _ = customer
    post_id = await rejected_post(db, m["id"], c["id"])

    r = await appeal(client, c_token, post_id, reason=TOO_LONG)
    assert r.status_code == 422, r.text
    assert len(TOO_LONG) == 501


async def test_ap04_pending_cannot_appeal(client, db, merchant, customer):
    """AP-04 对 `pending` 的作品申诉 → `409`（还没被驳回，无需申诉）。"""
    m, _, _ = merchant
    c, c_token, _ = customer
    task_id = await insert_task(db, m["id"])
    post_id = await post_under_task(db, task_id, c["id"])

    r = await appeal(client, c_token, post_id)
    assert r.status_code == 409, r.text


async def test_ap05_approved_cannot_appeal(client, db, merchant, customer):
    """AP-05 对 `approved` 的作品申诉 → `409`（已过审，没有申诉标的）。"""
    m, _, _ = merchant
    c, c_token, _ = customer
    post_id = await rejected_post(db, m["id"], c["id"])
    await set_post_status(db, post_id, "approved")

    r = await appeal(client, c_token, post_id)
    assert r.status_code == 409, r.text


async def test_ap06_auto_approved_cannot_appeal(client, db, merchant, customer):
    """AP-06 对 `auto_approved` 的作品申诉 → `409`（超时通过也算通过）。"""
    m, _, _ = merchant
    c, c_token, _ = customer
    post_id = await rejected_post(db, m["id"], c["id"])
    await set_post_status(db, post_id, "auto_approved")

    r = await appeal(client, c_token, post_id)
    assert r.status_code == 409, r.text


async def test_ap07_appealed_twice(client, db, merchant, customer):
    """AP-07 对已 `appealed` 的作品再申诉 → `409`。"""
    m, _, _ = merchant
    c, c_token, _ = customer
    post_id = await rejected_post(db, m["id"], c["id"])
    assert (await appeal(client, c_token, post_id)).status_code == 201

    again = await appeal(client, c_token, post_id, reason=REASON_10)
    assert again.status_code == 409, again.text

    n = await db.fetchval("SELECT count(*) FROM appeal WHERE post_id = $1", post_id)
    assert n == 1


async def test_ap08_someone_elses_post(client, db, merchant, customer):
    """AP-08 对**别人**的作品申诉 → `403`。"""
    m, _, _ = merchant
    _, c_token, _ = customer
    other_id = await another_customer(db, "ap08other")
    post_id = await rejected_post(db, m["id"], other_id)

    r = await appeal(client, c_token, post_id)
    assert r.status_code == 403, r.text


async def test_ap10_my_appeals_only(client, db, merchant, customer):
    """AP-10 `GET /api/me/appeals` 只返回本人的申诉。"""
    m, _, _ = merchant
    c, c_token, _ = customer

    mine = await rejected_post(db, m["id"], c["id"])
    created = await appeal_ok(client, c_token, mine)

    other_id = await another_customer(db, "ap10other")
    theirs = await rejected_post(db, m["id"], other_id)
    await insert_appeal(db, theirs, other_id)

    r = await client.get("/api/me/appeals", headers=bearer(c_token))
    assert r.status_code == 200, r.text
    assert [i["id"] for i in r.json()["items"]] == [created["id"]]


async def test_ap11_admin_list_only_pending(client, db, merchant, customer, seed_accounts):
    """AP-11 `GET /api/admin/appeals?status=pending` 只返回待裁决的。"""
    from tests.helpers import admin_token

    m, _, _ = merchant
    c, c_token, _ = customer
    token = await admin_token(client)

    pending_post = await rejected_post(db, m["id"], c["id"])
    pending = await appeal_ok(client, c_token, pending_post)

    settled_post = await rejected_post(db, m["id"], c["id"])
    await insert_appeal(db, settled_post, c["id"], status="accepted")

    r = await client.get(
        "/api/admin/appeals?status=pending", headers=bearer(token)
    )
    assert r.status_code == 200, r.text
    assert [i["id"] for i in r.json()["items"]] == [pending["id"]]


async def test_ap12_non_admin_cannot_list(client, db, merchant, customer):
    """AP-12 非 admin 打 `GET /api/admin/appeals` → `403`。"""
    _, c_token, _ = customer
    r = await client.get("/api/admin/appeals?status=pending", headers=bearer(c_token))
    assert r.status_code == 403, r.text

    _, m_token, _ = merchant
    r2 = await client.get("/api/admin/appeals?status=pending", headers=bearer(m_token))
    assert r2.status_code == 403, r2.text


async def test_ap13_admin_accept(client, db, merchant, customer, seed_accounts):
    """AP-13 admin `accept` → `200`，post `status=approved`，`appeal.status=accepted`，`decided_at` 落库。"""
    from tests.helpers import admin_token

    m, _, _ = merchant
    c, c_token, _ = customer
    post_id = await rejected_post(db, m["id"], c["id"])
    created = await appeal_ok(client, c_token, post_id)
    token = await admin_token(client)

    r = await decide(client, token, created["id"], "accept")
    assert r.status_code == 200, r.text

    post = await db.fetchrow(
        "SELECT status, reviewer_id FROM social_post WHERE id = $1", post_id
    )
    assert post["status"] == "approved"

    row = await db.fetchrow("SELECT * FROM appeal WHERE id = $1", created["id"])
    assert row["status"] == "accepted"
    assert row["decided_at"] is not None
    assert row["admin_id"] is not None


async def test_ap14_accept_writes_appeal_accept_log(
    client, db, merchant, customer, seed_accounts
):
    """AP-14 `accept` 写 `review_log(action="appeal_accept")`。"""
    from tests.helpers import admin_token

    m, _, _ = merchant
    c, c_token, _ = customer
    post_id = await rejected_post(db, m["id"], c["id"])
    created = await appeal_ok(client, c_token, post_id)
    await decide(client, await admin_token(client), created["id"], "accept")

    rows = await db.fetch(
        "SELECT action, operator_id FROM review_log WHERE post_id = $1", post_id
    )
    assert [r["action"] for r in rows] == ["appeal_accept"], (
        "受理要留一条独立的 action，否则事后分不清「商户批的」和「平台裁决的」"
    )


async def test_ap15_accept_settles_both(
    client, db, merchant, customer, seed_accounts, patch_reward
):
    """AP-15 `accept` 触发奖励结算与报销（两个接缝都被调用）。"""
    from tests.helpers import admin_token

    m, _, _ = merchant
    c, c_token, _ = customer
    scene = await reimburse_scene(db, m["id"], c["id"])
    await set_post_status(db, scene.post_id, "rejected")
    created = await appeal_ok(client, c_token, scene.post_id)

    r = await decide(client, await admin_token(client), created["id"], "accept")
    assert r.status_code == 200, r.text

    assert len(patch_reward.calls) == 1, "受理必须等同于过审：奖励要结算"
    assert patch_reward.calls[0]["post_id"] == scene.post_id

    n = await db.fetchval(
        "SELECT count(*) FROM reimburse_claim WHERE post_id = $1", scene.post_id
    )
    assert n == 1, "报销也要照常落地——用户垫的钱不能因为走了申诉就没人还"


async def test_ap16_admin_reject(client, db, merchant, customer, seed_accounts):
    """AP-16 admin `reject` → post `status` 回到 `rejected`，`appeal.status=rejected`。"""
    from tests.helpers import admin_token

    m, _, _ = merchant
    c, c_token, _ = customer
    post_id = await rejected_post(db, m["id"], c["id"])
    created = await appeal_ok(client, c_token, post_id)

    r = await decide(client, await admin_token(client), created["id"], "reject")
    assert r.status_code == 200, r.text

    status = await db.fetchval("SELECT status FROM social_post WHERE id = $1", post_id)
    assert status == "rejected"
    row = await db.fetchrow("SELECT * FROM appeal WHERE id = $1", created["id"])
    assert row["status"] == "rejected"
    assert row["decided_at"] is not None


async def test_ap17_cannot_appeal_after_reject(client, db, merchant, customer, seed_accounts):
    """AP-17 `reject` 后**不可再次申诉**：再 `POST /appeal` → `409`。

    这一条与 `AP-07` 不同。被驳回的申诉把 post 放回 `rejected`，而 `rejected`
    正是「可以申诉」的那个状态——只看 post 状态的实现会在这里放行。
    """
    from tests.helpers import admin_token

    m, _, _ = merchant
    c, c_token, _ = customer
    post_id = await rejected_post(db, m["id"], c["id"])
    created = await appeal_ok(client, c_token, post_id)
    await decide(client, await admin_token(client), created["id"], "reject")

    assert await db.fetchval(
        "SELECT status FROM social_post WHERE id = $1", post_id
    ) == "rejected"

    again = await appeal(client, c_token, post_id, reason=REASON_10)
    assert again.status_code == 409, again.text
    assert await db.fetchval("SELECT count(*) FROM appeal") == 1


async def test_ap18_decide_twice(client, db, merchant, customer, seed_accounts):
    """AP-18 已裁决的申诉再 `decide` → `409`。"""
    from tests.helpers import admin_token

    m, _, _ = merchant
    c, c_token, _ = customer
    post_id = await rejected_post(db, m["id"], c["id"])
    created = await appeal_ok(client, c_token, post_id)
    token = await admin_token(client)

    first = await decide(client, token, created["id"], "accept")
    assert first.status_code == 200, first.text

    second = await decide(client, token, created["id"], "reject")
    assert second.status_code == 409, second.text

    status = await db.fetchval("SELECT status FROM social_post WHERE id = $1", post_id)
    assert status == "approved", "第二次裁决不得翻掉第一次的结果"


async def test_ap19_bad_action_and_merchant_forbidden(
    client, db, merchant, customer, seed_accounts
):
    """AP-19 `action` 传非法值 → `422`；商户调 `decide` → `403`。"""
    from tests.helpers import admin_token

    m, m_token, _ = merchant
    c, c_token, _ = customer
    post_id = await rejected_post(db, m["id"], c["id"])
    created = await appeal_ok(client, c_token, post_id)
    token = await admin_token(client)

    bad = await decide(client, token, created["id"], "maybe")
    assert bad.status_code == 422, bad.text

    forbidden = await decide(client, m_token, created["id"], "accept")
    assert forbidden.status_code == 403, forbidden.text

    still = await db.fetchrow("SELECT status, decided_at FROM appeal WHERE id = $1", created["id"])
    assert still["status"] == "pending" and still["decided_at"] is None
