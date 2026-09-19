"""03-studio · job 视图、进度流、我的列表。

对应 test_plan.md：`JQ-01` ~ `JQ-04`、`EV-01` ~ `EV-02`、`ME-01` ~ `ME-04`
"""

import pytest

from tests.helpers import (
    assert_route_registered,
    bearer,
    claimed_task,
    create_job_ok,
    grant,
    insert_asset,
    insert_job,
    insert_output,
    insert_price,
    login_ok,
    register_customer,
    run_stage,
)

pytestmark = pytest.mark.asyncio


async def _insert_draft(db, job_id: int) -> None:
    await db.execute(
        """
        INSERT INTO prompt_draft
            (job_id, raw_prompt, optimized_prompt, quality_score, iterations,
             rewrite_failed, optimizer_model)
        VALUES ($1, '粗糙的奶茶文案', '精致的奶茶文案', 88, 2, false, 'prompt-optimizer')
        """,
        job_id,
    )


async def test_jq01_job_detail_shape(client, studio, db):
    job_id = await insert_job(
        db, studio.task_id, studio.claim_id, studio.customer["id"], status="ready"
    )
    # 故意倒序插入：详情必须自己按 sort_order 排，不能靠插入顺序碰巧对
    await insert_asset(db, job_id, sort_order=1, url="minio://b/second.jpg")
    await insert_asset(db, job_id, sort_order=0, url="minio://b/first.jpg")
    await insert_output(db, job_id)
    await _insert_draft(db, job_id)

    r = await client.get(
        f"/api/jobs/{job_id}", headers=bearer(studio.customer_token)
    )
    assert r.status_code == 200, r.text
    body = r.json()

    for key in ("job", "inputs", "prompt_draft", "outputs"):
        assert key in body, f"详情缺 {key}：{list(body)}"
    assert [a["url"] for a in body["inputs"]] == [
        "minio://b/first.jpg",
        "minio://b/second.jpg",
    ], f"素材必须按 sort_order 升序：{body['inputs']}"
    assert body["prompt_draft"]["quality_score"] == 88
    assert len(body["outputs"]) == 1


async def test_jq02_billing_visible(client, studio, db):
    job_id = await insert_job(
        db,
        studio.task_id,
        studio.claim_id,
        studio.customer["id"],
        status="ready",
        provider="jimeng",
        billing_source="byok",
    )
    r = await client.get(f"/api/jobs/{job_id}", headers=bearer(studio.customer_token))
    job = r.json()["job"]
    assert job["provider"] == "jimeng", "用户得知道自己用的是哪家"
    assert job["billing_source"] == "byok", "用户得知道这次是不是花的自己的 Key"


async def test_jq03_others_403(client, studio, db):
    await register_customer(client, account="cust9002")
    other = await login_ok(client, "cust9002", "pass1234", fp="fp-x")

    job_id = await insert_job(
        db, studio.task_id, studio.claim_id, studio.customer["id"], status="ready"
    )
    r = await client.get(f"/api/jobs/{job_id}", headers=bearer(other["access_token"]))
    assert r.status_code == 403, (
        f"非本人必须 403（404 会泄露 id 存不存在）：{r.status_code} {r.text}"
    )


async def test_jq04_unknown_404(client, studio):
    assert_route_registered("GET", "/api/jobs/{job_id}")
    r = await client.get("/api/jobs/999999", headers=bearer(studio.customer_token))
    assert r.status_code == 404, r.text


async def test_ev01_events_stream(client, studio, db, patch_dispatch, patch_ai):
    body = await create_job_ok(
        client, studio.customer_token, studio.task_id, studio.claim_id
    )
    await run_stage("guard", body["job_id"])

    r = await client.get(
        f"/api/jobs/{body['job_id']}/events", headers=bearer(studio.customer_token)
    )
    assert r.status_code == 200, r.text
    assert "text/event-stream" in r.headers.get("content-type", ""), (
        f"进度流必须是 SSE：{r.headers.get('content-type')}"
    )
    assert "guard" in r.text, f"预检跑过就该在流里有痕迹：{r.text!r}"


async def test_ev02_others_403(client, studio, db):
    await register_customer(client, account="cust9002")
    other = await login_ok(client, "cust9002", "pass1234", fp="fp-x")

    job_id = await insert_job(
        db, studio.task_id, studio.claim_id, studio.customer["id"], status="ready"
    )
    r = await client.get(
        f"/api/jobs/{job_id}/events", headers=bearer(other["access_token"])
    )
    assert r.status_code == 403, r.text


async def test_me01_my_jobs(client, studio, db):
    mine = await insert_job(
        db, studio.task_id, studio.claim_id, studio.customer["id"], status="ready"
    )
    # 别人的 job（同一任务、另一条 claim）
    other_id = await _other_customer(client, db, studio)
    await insert_job(db, studio.task_id, other_id[1], other_id[0], status="ready")

    r = await client.get("/api/me/jobs", headers=bearer(studio.customer_token))
    assert r.status_code == 200, r.text
    body = r.json()
    for key in ("items", "total", "page", "size"):
        assert key in body, f"列表缺 {key}：{list(body)}"
    ids = [i["id"] for i in body["items"]]
    assert ids == [mine], f"只能看到自己的 job：{ids}"


async def _other_customer(client, db, studio):
    """造一个同样领取了该任务的另一个客户，返回 `(user_id, claim_id)`。"""
    await register_customer(client, account="cust9003")
    body = await login_ok(client, "cust9003", "pass1234", fp="fp-y")
    uid = body["user"]["id"]
    claim_id = await db.fetchval(
        """
        INSERT INTO task_claim (task_id, user_id, status, claimed_at)
        VALUES ($1, $2, 'in_progress', now()) RETURNING id
        """,
        studio.task_id,
        uid,
    )
    return uid, claim_id


async def test_me02_filter_by_task(client, studio, db, merchant):
    """`?task_id=` 只留该任务的 job。"""
    merchant_user, merchant_token, _ = merchant

    await grant(db, merchant_user["id"], 100_000)
    await insert_price(db, "deepseek", "deepseek-chat", "chat")
    other_task, other_claim = await claimed_task(
        client, merchant_token, studio.customer_token, title="另一个任务标题"
    )

    here = await insert_job(
        db, studio.task_id, studio.claim_id, studio.customer["id"], status="ready"
    )
    await insert_job(
        db, other_task["id"], other_claim, studio.customer["id"], status="ready"
    )

    r = await client.get(
        f"/api/me/jobs?task_id={studio.task_id}",
        headers=bearer(studio.customer_token),
    )
    assert r.status_code == 200, r.text
    assert [i["id"] for i in r.json()["items"]] == [here], r.text


async def test_me03_no_token_401(client, studio):
    r = await client.get("/api/me/jobs")
    assert r.status_code == 401, r.text


async def test_me04_deleted_excluded(client, studio, db):
    from datetime import datetime, timezone

    await insert_job(
        db,
        studio.task_id,
        studio.claim_id,
        studio.customer["id"],
        status="ready",
        deleted_at=datetime.now(timezone.utc),
    )
    r = await client.get("/api/me/jobs", headers=bearer(studio.customer_token))
    assert r.status_code == 200, r.text
    assert r.json()["items"] == [], f"软删的 job 不得出现在列表：{r.json()['items']}"
