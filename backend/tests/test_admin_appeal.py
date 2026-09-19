"""06-admin · `AP` 组：申诉裁决。

对应 test_plan.md 的 `AP-01` ~ `AP-10`。

⚠️ **两个端点归 04**（`app/api/appeal.py`），本轮 06 只**回改** `decide` 两处：
补写 `admin_action_log`、给 appeal 行加 `FOR UPDATE` 锁。所以这一组测的是
**已存在的端点**——它在 06 之前就已经是绿的（端点本身），
`AP-04` 才是新要求（两张表都要留痕）。

`AP-02` 是本组最重的一条：`accept` 必须走 `services.review.approve` 的
**同一条 hook**，而不是另写一条「裁决专用」的结算路径——后者的典型症状是
奖励发了、报销没发（或反过来）。
"""

import pytest

from tests.helpers import (
    action_logs,
    admin_token,
    assert_route_registered,
    bearer,
    decide_appeal,
    insert_appeal,
    insert_claim,
    insert_job,
    insert_post,
    insert_task,
    make_tiers,
    insert_reward_rule,
)

pytestmark = pytest.mark.asyncio


async def _appeal_scene(db, merchant_id: int, user_id: int, **appeal_over):
    """一条 `rejected` 的作品 + 一条 `pending` 的申诉。返回 (post_id, appeal_id)。"""
    task_id = await insert_task(db, merchant_id)
    await insert_reward_rule(db, task_id, tiers=make_tiers(3, reward={"points": 50}))
    claim_id = await insert_claim(db, task_id, user_id)
    job_id = await insert_job(db, task_id, claim_id, user_id, status="ready")
    post_id = await insert_post(
        db, claim_id, job_id, user_id, status="appealed", reject_reason="内容不符"
    )
    appeal_id = await insert_appeal(db, post_id, user_id, **appeal_over)
    return post_id, appeal_id


async def test_ap01_accept_makes_post_approved(
    client, seed_accounts, merchant, customer, db
):
    """`accept` → `200`，post `status='approved'`。"""
    token = await admin_token(client)
    post_id, appeal_id = await _appeal_scene(
        db, merchant[0]["id"], customer[0]["id"]
    )

    r = await decide_appeal(client, token, appeal_id, action="accept")
    assert r.status_code == 200, r.text
    assert await db.fetchval(
        "SELECT status FROM social_post WHERE id = $1", post_id
    ) == "approved"


async def test_ap02_accept_triggers_settlement(
    client, seed_accounts, merchant, customer, db
):
    """⚠️ `accept` **触发 05 结算**：`reward_grant` 恰好 1 行。

    走的是 `services.review.approve`（`allowed_from=("appealed",)`）——
    与商户手工过审、72h 自动通过**同一条** `_run_hooks`。
    另开一条裁决专用路径的话，报销那一半迟早会漏。
    """
    token = await admin_token(client)
    post_id, appeal_id = await _appeal_scene(
        db, merchant[0]["id"], customer[0]["id"]
    )

    assert (await decide_appeal(client, token, appeal_id, action="accept")).status_code == 200

    rows = await db.fetch(
        "SELECT id FROM reward_grant WHERE post_id = $1", post_id
    )
    assert len(rows) == 1, f"裁决受理必须触发结算，且只一次：{rows}"


async def test_ap03_reject_keeps_post_rejected(
    client, seed_accounts, merchant, customer, db
):
    """`reject` → `200`，post `status='rejected'`。"""
    token = await admin_token(client)
    post_id, appeal_id = await _appeal_scene(
        db, merchant[0]["id"], customer[0]["id"]
    )

    r = await decide_appeal(client, token, appeal_id, action="reject", admin_note="证据不足")
    assert r.status_code == 200, r.text
    assert await db.fetchval(
        "SELECT status FROM social_post WHERE id = $1", post_id
    ) == "rejected"


async def test_ap04_both_tables_logged(client, seed_accounts, merchant, customer, db):
    """⚠️ **裁决必须同时写 `review_log` 与 `admin_action_log`（各一条）**。

    两张表不是重复：`review_log` 记的是**作品的状态跃迁**（04 的账），
    `admin_action_log` 记的是**管理员的动作**（06 的账）。查「这条作品怎么了」
    与查「这个管理员干了什么」是两个问题，不能靠一张表回答。

    断言写 `== 1` 而不是「查到了就行」：后者漏写表时返回空集，
    `all([])` 恒真，这条会假绿。
    """
    token = await admin_token(client)
    post_id, appeal_id = await _appeal_scene(
        db, merchant[0]["id"], customer[0]["id"]
    )

    assert (await decide_appeal(client, token, appeal_id, action="accept")).status_code == 200

    review_rows = await db.fetch(
        "SELECT action FROM review_log WHERE post_id = $1", post_id
    )
    assert len(review_rows) == 1, f"review_log 应恰好 1 条：{review_rows}"
    assert review_rows[0]["action"] == "appeal_accept", review_rows

    admin_rows = await action_logs(db, target_type="appeal", target_id=appeal_id)
    assert len(admin_rows) == 1, f"admin_action_log 应恰好 1 条：{admin_rows}"
    assert admin_rows[0]["action"] == "appeal_accept", admin_rows


async def test_ap05_double_decide_is_409(client, seed_accounts, merchant, customer, db):
    """已裁决的申诉再裁决 → `409`（裁决即终局）。"""
    token = await admin_token(client)
    _, appeal_id = await _appeal_scene(db, merchant[0]["id"], customer[0]["id"])

    assert (await decide_appeal(client, token, appeal_id, action="accept")).status_code == 200
    r = await decide_appeal(client, token, appeal_id, action="reject")
    assert r.status_code == 409, f"已裁决再裁决应 409：{r.status_code} {r.text}"


async def test_ap06_merchant_is_403(client, seed_accounts, merchant, customer, db):
    """商户调 `/api/admin/appeals/{id}/decide` → `403`。

    让被申诉的一方自己裁决，等于把裁判权交给当事人。
    """
    _, appeal_id = await _appeal_scene(db, merchant[0]["id"], customer[0]["id"])
    _, m_token, _ = merchant

    r = await decide_appeal(client, m_token, appeal_id, action="accept")
    assert r.status_code == 403, f"商户不得裁决申诉：{r.status_code} {r.text}"


async def test_ap07_bad_action_and_unknown_appeal(client, seed_accounts, merchant, customer, db):
    """`action='foo'` → `422`；不存在的 `appeal_id` → `404`。"""
    token = await admin_token(client)
    _, appeal_id = await _appeal_scene(db, merchant[0]["id"], customer[0]["id"])

    assert (await decide_appeal(client, token, appeal_id, action="foo")).status_code == 422

    assert_route_registered("POST", "/api/admin/appeals/{appeal_id}/decide")
    assert (await decide_appeal(client, token, 999999, action="accept")).status_code == 404


async def test_ap08_admin_note_optional(client, seed_accounts, merchant, customer, db):
    """`admin_note` 为空 → 允许（选填）；非空时落进 `appeal.admin_note`。"""
    token = await admin_token(client)
    _, a1 = await _appeal_scene(db, merchant[0]["id"], customer[0]["id"])
    _, a2 = await _appeal_scene(db, merchant[0]["id"], customer[0]["id"])

    r1 = await decide_appeal(client, token, a1, action="accept")
    assert r1.status_code == 200, f"admin_note 选填：{r1.status_code} {r1.text}"

    r2 = await decide_appeal(client, token, a2, action="reject", admin_note="截图与插件数据均不足以推翻驳回")
    assert r2.status_code == 200, r2.text
    assert await db.fetchval(
        "SELECT admin_note FROM appeal WHERE id = $1", a2
    ) == "截图与插件数据均不足以推翻驳回"


async def test_ap09_cannot_appeal_twice(client, seed_accounts, merchant, customer, db):
    """`reject` 后用户不可再申诉（`appeal.post_id` 唯一兜底）。

    04 的 `AP-17` 已经测过这条规则。这里再测一次是因为**裁决驳回会把 post
    放回 `rejected`**——那个状态天然满足「可以申诉」，所以裁决路径正是这条
    规则最容易被穿的地方。用 06 的裁决动作（而不是 04 的驳回）作为前置。
    """
    token = await admin_token(client)
    post_id, appeal_id = await _appeal_scene(
        db, merchant[0]["id"], customer[0]["id"]
    )
    _, c_token, _ = customer

    assert (
        await decide_appeal(client, token, appeal_id, action="reject", admin_note="维持原判")
    ).status_code == 200

    r = await client.post(
        f"/api/posts/{post_id}/appeal",
        headers=bearer(c_token),
        json={"reason": "我认为这个判定不合理，请再核实一次"},
    )
    assert r.status_code == 409, f"申诉机会仅有一次：{r.status_code} {r.text}"


async def test_ap10_settlement_happens_once(client, seed_accounts, merchant, customer, db):
    """`accept` 后**奖励只结算一次**：再过一次审，`reward_grant` 仍 1 行。

    裁决 → 结算 → 商户事后又点了通过。第二笔不该发出来。
    兜底的是 `reward_grant.post_id` 唯一约束，不是分支里的 `if`。
    """
    token = await admin_token(client)
    _, m_token, _ = merchant
    post_id, appeal_id = await _appeal_scene(
        db, merchant[0]["id"], customer[0]["id"]
    )

    assert (await decide_appeal(client, token, appeal_id, action="accept")).status_code == 200
    # 商户再点一次通过：post 已是 approved，04 应当拒绝，但即便放行也不得多发钱
    await client.post(
        f"/api/merchant/reviews/{post_id}/approve", headers=bearer(m_token)
    )

    rows = await db.fetch("SELECT id FROM reward_grant WHERE post_id = $1", post_id)
    assert len(rows) == 1, f"奖励只结算一次：{rows}"
