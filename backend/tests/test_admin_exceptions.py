"""06-admin · `EX` / `EC` / `EO` 三组：异常列表、内容异常处理、截图异常处理。

对应 test_plan.md 的 `EX-01` ~ `EO-08`。

异常处理是 06 里**唯一会写别的模块状态**的地方（把 job 改成 `ready`/`failed`、
给 post 补一条快照）。所以这一组盯的是**准入闸**：状态不对就 409、动作非法
就 422、被拒的动作**不留痕**。放行条件写松了，管理员可以事后翻案——
而奖励可能已经结算完了。
"""

import pytest

from tests.helpers import (
    action_logs,
    admin_token,
    assert_route_registered,
    bearer,
    insert_claim,
    insert_job,
    insert_ocr,
    insert_post,
    insert_snapshot,
    json_of,
    insert_task,
    resolve_content,
    resolve_ocr,
)

pytestmark = pytest.mark.asyncio


async def _exceptions(client, token, **params):
    return await client.get(
        "/api/admin/exceptions", headers=bearer(token), params=params or None
    )


async def _job_scene(db, merchant_id: int, user_id: int, **over):
    """一个 `content_job`，状态由 `status` 指定。"""
    task_id = await insert_task(db, merchant_id)
    claim_id = await insert_claim(db, task_id, user_id)
    job_id = await insert_job(db, task_id, claim_id, user_id, **over)
    return task_id, claim_id, job_id


async def _ocr_scene(db, merchant_id: int, user_id: int, **over):
    """一个带 `ocr_result` 的 post。返回 (post_id, ocr_id)。

    `post.status` 默认 `pending`——`EO-07` 要的是「已过审」那条分支。
    """
    task_id, claim_id, job_id = await _job_scene(db, merchant_id, user_id, status="ready")
    post_id = await insert_post(db, claim_id, job_id, user_id)
    ocr_id = await insert_ocr(db, post_id, **over)
    return post_id, ocr_id


# --------------------------------------------------------------------------- #
# EX · 异常列表
# --------------------------------------------------------------------------- #
async def test_ex01_bad_type_is_422(client, seed_accounts):
    """`?type=foo` → `422`（四个合法值见 spec 的 type 表）。"""
    token = await admin_token(client)
    r = await _exceptions(client, token, type="foo")
    assert r.status_code == 422, f"type 非法应 422：{r.status_code} {r.text}"


async def test_ex02_content_review_only_need_review(
    client, seed_accounts, merchant, customer, db
):
    """`?type=content_review` 只含 `status='need_review'`；`ready` 的不出现。"""
    token = await admin_token(client)
    m_id, u_id = merchant[0]["id"], customer[0]["id"]
    _, _, need = await _job_scene(db, m_id, u_id, status="need_review")
    await _job_scene(db, m_id, u_id, status="ready")
    await _job_scene(db, m_id, u_id, status="failed")

    r = await _exceptions(client, token, type="content_review")
    assert r.status_code == 200, r.text
    ids = [i["job_id"] for i in json_of(r)["items"]]
    assert ids == [need], f"只该列出 need_review 的 job：{ids}"


async def test_ex03_ocr_low_confidence_threshold(
    client, seed_accounts, merchant, customer, db
):
    """`?type=ocr_low_confidence` 只含 `confidence < 0.70` **且** `is_active`。

    边界成对：`0.55` 进、**`0.70`（等于阈值）不进**。写成 `<=` 会把刚
    及格的识别结果也算成异常，运营会收到一堆不需要看的单子。
    """
    token = await admin_token(client)
    m_id, u_id = merchant[0]["id"], customer[0]["id"]
    _, low = await _ocr_scene(db, m_id, u_id, confidence=0.55)
    _, edge = await _ocr_scene(db, m_id, u_id, confidence=0.70)
    _, high = await _ocr_scene(db, m_id, u_id, confidence=0.95)
    _, inactive = await _ocr_scene(db, m_id, u_id, confidence=0.30, is_active=False)

    r = await _exceptions(client, token, type="ocr_low_confidence")
    assert r.status_code == 200, r.text
    ids = [i["ocr_id"] for i in json_of(r)["items"]]
    assert low in ids, f"0.55 应进名单：{ids}"
    assert edge not in ids, f"0.70 恰好等于阈值，不该进：{ids}"
    assert high not in ids, f"0.95 不该进：{ids}"
    assert inactive not in ids, f"已不是 active 的不该进：{ids}"


async def test_ex04_ocr_mismatch_only_flagged_active(
    client, seed_accounts, merchant, customer, db
):
    """`?type=ocr_mismatch` 只含 `mismatch_flag=true` 且 `is_active=true`。"""
    token = await admin_token(client)
    m_id, u_id = merchant[0]["id"], customer[0]["id"]
    _, flagged = await _ocr_scene(db, m_id, u_id, mismatch_flag=True)
    _, clean = await _ocr_scene(db, m_id, u_id, mismatch_flag=False)
    _, stale = await _ocr_scene(db, m_id, u_id, mismatch_flag=True, is_active=False)

    r = await _exceptions(client, token, type="ocr_mismatch")
    ids = [i["ocr_id"] for i in json_of(r)["items"]]
    assert ids == [flagged], f"只该列出 flagged 且 active 的：{ids}（clean={clean} stale={stale}）"


async def test_ex05_appeal_only_pending(client, seed_accounts, merchant, customer, db):
    """`?type=appeal` 只含 `appeal.status='pending'`；已裁决的不出现。"""
    from tests.helpers import insert_appeal

    token = await admin_token(client)
    m_id, u_id = merchant[0]["id"], customer[0]["id"]
    _, claim_id, job_id = await _job_scene(db, m_id, u_id, status="ready")

    post_id = await insert_post(db, claim_id, job_id, u_id, status="appealed")
    pending = await insert_appeal(db, post_id, u_id)

    post2 = await insert_post(db, claim_id, job_id, u_id, status="approved")
    await insert_appeal(db, post2, u_id, status="accepted")

    r = await _exceptions(client, token, type="appeal")
    assert r.status_code == 200, r.text
    ids = [i["appeal_id"] for i in json_of(r)["items"]]
    assert ids == [pending], f"只该列出 pending 的申诉：{ids}"


async def test_ex06_shape_and_size_limit(client, seed_accounts):
    """`{items,total,page,size}` 齐全；`size=101` → `422`。"""
    token = await admin_token(client)
    r = await _exceptions(client, token, type="content_review")
    assert r.status_code == 200, r.text
    body = json_of(r)
    assert set(("items", "total", "page", "size")) <= set(body), body
    assert (await _exceptions(client, token, type="content_review", size=101)).status_code == 422


# --------------------------------------------------------------------------- #
# EC · 内容异常处理
# --------------------------------------------------------------------------- #
async def test_ec01_approve_makes_ready(client, seed_accounts, merchant, customer, db):
    """`approve` → job `status='ready'`（用户可下载）。"""
    token = await admin_token(client)
    m_id, u_id = merchant[0]["id"], customer[0]["id"]
    _, _, job_id = await _job_scene(db, m_id, u_id, status="need_review")

    r = await resolve_content(client, token, job_id, action="approve")
    assert r.status_code == 200, r.text
    assert await db.fetchval(
        "SELECT status FROM content_job WHERE id = $1", job_id
    ) == "ready"


async def test_ec02_discard_sets_fail_reason(client, seed_accounts, merchant, customer, db):
    """`discard` → `status='failed'`，`fail_reason` **等于**传入的 `note`。

    逐字相等：`fail_reason` 是用户下次打开 job 时唯一能看到的原因。
    实现若把它拼成「人工作废：xxx」，用户的界面就会多出一段他没要求的前缀。
    """
    token = await admin_token(client)
    m_id, u_id = merchant[0]["id"], customer[0]["id"]
    _, _, job_id = await _job_scene(db, m_id, u_id, status="need_review")

    r = await resolve_content(
        client, token, job_id, action="discard", note="画面不符合要求"
    )
    assert r.status_code == 200, r.text
    row = await db.fetchrow(
        "SELECT status, fail_reason FROM content_job WHERE id = $1", job_id
    )
    assert row["status"] == "failed", row
    assert row["fail_reason"] == "画面不符合要求", row


async def test_ec03_bad_action_is_422(client, seed_accounts, merchant, customer, db):
    """`action='foo'` → `422`（不影响 job 状态）。"""
    token = await admin_token(client)
    m_id, u_id = merchant[0]["id"], customer[0]["id"]
    _, _, job_id = await _job_scene(db, m_id, u_id, status="need_review")

    r = await resolve_content(client, token, job_id, action="foo")
    assert r.status_code == 422, f"action 非法应 422：{r.status_code} {r.text}"
    assert await db.fetchval(
        "SELECT status FROM content_job WHERE id = $1", job_id
    ) == "need_review"


async def test_ec04_non_need_review_is_409(client, seed_accounts, merchant, customer, db):
    """处理一个 `status='ready'` 的 job → `409`（不允许事后改判）。"""
    token = await admin_token(client)
    m_id, u_id = merchant[0]["id"], customer[0]["id"]
    _, _, job_id = await _job_scene(db, m_id, u_id, status="ready")

    r = await resolve_content(client, token, job_id, action="discard", note="事后反悔")
    assert r.status_code == 409, f"非 need_review 应 409：{r.status_code} {r.text}"


async def test_ec05_unknown_job_is_404(client, seed_accounts):
    """不存在的 job_id → `404`。"""
    assert_route_registered("POST", "/api/admin/exceptions/content/{job_id}/resolve")
    token = await admin_token(client)
    r = await resolve_content(client, token, 999999)
    assert r.status_code == 404, f"job 不存在应 404：{r.status_code} {r.text}"


async def test_ec06_every_resolution_logged(client, seed_accounts, merchant, customer, db):
    """⚠️ `approve` 与 `discard` 各写**一条** `resolve_content` 日志，`note` 为空也照写。

    「note 为空也照写」是关键：若把写日志挂在「有 note」的分支里，
    绝大多数正常的 approve 都会不留痕——而 approve 恰恰是最需要留痕的那个。
    """
    token = await admin_token(client)
    m_id, u_id = merchant[0]["id"], customer[0]["id"]
    _, _, job_a = await _job_scene(db, m_id, u_id, status="need_review")
    _, _, job_b = await _job_scene(db, m_id, u_id, status="need_review")

    assert (await resolve_content(client, token, job_a, action="approve")).status_code == 200
    assert (
        await resolve_content(client, token, job_b, action="discard", note="画面有问题")
    ).status_code == 200

    logs = await action_logs(db, target_type="content_job")
    assert len(logs) == 2, f"两次处理应恰好两条日志，实际 {len(logs)}：{logs}"
    assert {row["action"] for row in logs} == {"resolve_content"}, logs
    assert {row["target_id"] for row in logs} == {job_a, job_b}, logs


async def test_ec07_double_resolve_is_409_and_one_log(
    client, seed_accounts, merchant, customer, db
):
    """重复处理 → `409`，且日志**仍只有 1 条**（被拒的动作不留痕）。

    审计表记的是「发生过什么」。一次没生效的重复点击被记成成功，
    翻账时会以为管理员改了两次。
    """
    token = await admin_token(client)
    m_id, u_id = merchant[0]["id"], customer[0]["id"]
    _, _, job_id = await _job_scene(db, m_id, u_id, status="need_review")

    assert (await resolve_content(client, token, job_id, action="approve")).status_code == 200
    again = await resolve_content(client, token, job_id, action="approve")
    assert again.status_code == 409, f"重复处理应 409：{again.status_code} {again.text}"

    logs = await action_logs(db, target_type="content_job", target_id=job_id)
    assert len(logs) == 1, f"被拒的那次不得留痕：{logs}"


# --------------------------------------------------------------------------- #
# EO · 截图异常处理
# --------------------------------------------------------------------------- #
async def test_eo01_accept_adds_ocr_snapshot(client, seed_accounts, merchant, customer, db):
    """`accept` → 新增一条 `metric_snapshot(source='ocr')`，`engagement` **由后端算**。

    断言 `engagement == likes+collects+comments`（04 的口径，不含 shares）。
    若实现把 ocr 的 `parsed` 整体塞进快照，这个等式会不成立——
    而「谁算的」这件事正是 04 那条公式存在的理由。
    """
    token = await admin_token(client)
    m_id, u_id = merchant[0]["id"], customer[0]["id"]
    post_id, ocr_id = await _ocr_scene(
        db,
        m_id,
        u_id,
        parsed={"likes": 120, "collects": 30, "comments": 8, "shares": 999},
    )

    r = await resolve_ocr(client, token, ocr_id, action="accept")
    assert r.status_code == 200, r.text

    row = await db.fetchrow(
        "SELECT likes, collects, comments, engagement, source FROM metric_snapshot "
        "WHERE post_id = $1 AND source = 'ocr' ORDER BY id DESC LIMIT 1",
        post_id,
    )
    assert row is not None, "accept 必须新增一条 source='ocr' 的快照"
    assert row["likes"] == 120 and row["collects"] == 30 and row["comments"] == 8, row
    assert row["engagement"] == 158, (
        f"engagement 必须由后端按 likes+collects+comments 算，实际 {row['engagement']}"
    )


async def test_eo02_accept_respects_peak_rule(client, seed_accounts, merchant, customer, db):
    """⚠️ `accept` 之后结算按 04 的**峰值**口径，不是「最新一条」。

    先造一条插件快照 `engagement=500`，再 accept 一条更低的 OCR 数据。
    若实现（或 04 的口径）取「最后一条」，结算会掉到低档——这正是
    「先报低位博通过、再涨上来」要防的反面。
    """
    from app.db import SessionLocal
    from app.services import review as review_service

    token = await admin_token(client)
    m_id, u_id = merchant[0]["id"], customer[0]["id"]
    post_id, ocr_id = await _ocr_scene(
        db, m_id, u_id, parsed={"likes": 1, "collects": 1, "comments": 1}
    )
    await insert_snapshot(db, post_id, likes=300, collects=150, comments=50)

    assert (await resolve_ocr(client, token, ocr_id, action="accept")).status_code == 200

    session = SessionLocal()
    try:
        assert await review_service.peak_engagement(session, post_id) == 500, (
            "accept 补进来的低值快照不得把峰值拉低"
        )
    finally:
        await session.close()


async def test_eo03_reject_adds_no_snapshot(client, seed_accounts, merchant, customer, db):
    """`reject` → **不新增**快照，且该 ocr 被标记为已处理（`is_active=false`）。"""
    token = await admin_token(client)
    m_id, u_id = merchant[0]["id"], customer[0]["id"]
    post_id, ocr_id = await _ocr_scene(db, m_id, u_id)

    before = await db.fetchval(
        "SELECT count(*) FROM metric_snapshot WHERE post_id = $1", post_id
    )
    r = await resolve_ocr(client, token, ocr_id, action="reject")
    assert r.status_code == 200, r.text

    after = await db.fetchval(
        "SELECT count(*) FROM metric_snapshot WHERE post_id = $1", post_id
    )
    assert after == before, f"reject 不得新增快照：{before} → {after}"
    assert await db.fetchval(
        "SELECT is_active FROM ocr_result WHERE id = $1", ocr_id
    ) is False, "处理过的 ocr 必须不再是 active，否则它会一直在待办里"


async def test_eo04_ocr_resolution_logged(client, seed_accounts, merchant, customer, db):
    """`accept` / `reject` 各写一条 `resolve_ocr`，`target_type='ocr_result'`。"""
    token = await admin_token(client)
    m_id, u_id = merchant[0]["id"], customer[0]["id"]
    _, ocr_a = await _ocr_scene(db, m_id, u_id)
    _, ocr_b = await _ocr_scene(db, m_id, u_id)

    assert (await resolve_ocr(client, token, ocr_a, action="accept")).status_code == 200
    assert (await resolve_ocr(client, token, ocr_b, action="reject")).status_code == 200

    logs = await action_logs(db, target_type="ocr_result")
    assert len(logs) == 2, f"两次处理应恰好两条日志：{logs}"
    assert {row["action"] for row in logs} == {"resolve_ocr"}, logs
    assert {row["target_id"] for row in logs} == {ocr_a, ocr_b}, logs


async def test_eo05_inactive_ocr_is_409(client, seed_accounts, merchant, customer, db):
    """对 `is_active=false` 的 ocr 处理 → `409`（已经被处理过了）。"""
    token = await admin_token(client)
    m_id, u_id = merchant[0]["id"], customer[0]["id"]
    _, ocr_id = await _ocr_scene(db, m_id, u_id, is_active=False)

    r = await resolve_ocr(client, token, ocr_id, action="accept")
    assert r.status_code == 409, f"已非 active 应 409：{r.status_code} {r.text}"


async def test_eo06_bad_action_and_unknown_ocr(client, seed_accounts, merchant, customer, db):
    """`action='foo'` → `422`；不存在的 ocr_id → `404`。"""
    token = await admin_token(client)
    m_id, u_id = merchant[0]["id"], customer[0]["id"]
    _, ocr_id = await _ocr_scene(db, m_id, u_id)

    assert (await resolve_ocr(client, token, ocr_id, action="foo")).status_code == 422

    assert_route_registered("POST", "/api/admin/exceptions/ocr/{ocr_id}/resolve")
    assert (await resolve_ocr(client, token, 999999)).status_code == 404


async def test_eo07_already_settled_post_is_409(
    client, seed_accounts, merchant, customer, db
):
    """⚠️ 处理时该 post 已 `approved` / `auto_approved` → `409`。

    奖励已按当时的快照峰值结算过了。再补一条快照不会重算（结算不回溯），
    但**会让台账与看板对不上**——所以必须在入口拦死，而不是靠下游忽略。
    """
    token = await admin_token(client)
    m_id, u_id = merchant[0]["id"], customer[0]["id"]
    post_id, ocr_id = await _ocr_scene(db, m_id, u_id)
    await db.execute(
        "UPDATE social_post SET status = 'auto_approved' WHERE id = $1", post_id
    )

    r = await resolve_ocr(client, token, ocr_id, action="accept")
    assert r.status_code == 409, (
        f"已结算的作品不得再补快照：{r.status_code} {r.text}"
    )


async def test_eo08_double_resolve_is_409(client, seed_accounts, merchant, customer, db):
    """重复处理同一个 ocr → `409`，日志仍只有 1 条。"""
    token = await admin_token(client)
    m_id, u_id = merchant[0]["id"], customer[0]["id"]
    _, ocr_id = await _ocr_scene(db, m_id, u_id)

    assert (await resolve_ocr(client, token, ocr_id, action="accept")).status_code == 200
    again = await resolve_ocr(client, token, ocr_id, action="accept")
    assert again.status_code == 409, f"重复处理应 409：{again.status_code} {again.text}"

    logs = await action_logs(db, target_type="ocr_result", target_id=ocr_id)
    assert len(logs) == 1, f"被拒的那次不得留痕：{logs}"
