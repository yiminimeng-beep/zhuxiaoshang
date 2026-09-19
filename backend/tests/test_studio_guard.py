"""03-studio · 素材预检。

对应 test_plan.md：`GD-01` ~ `GD-13`

预检是**产品底线**：它挡不住，后面整条链路都在为一个无关素材烧钱。所以这一组
里有两条与别处不同的规矩：

- `GD-05`：预检服务自己挂了 → `failed`，**绝不默认放行**（fail closed）。
- `GD-08`：预检不通过 → **额度不退**（策略性失败），且不写退款流水。
"""

import pytest

from tests.helpers import (
    assert_route_registered,
    bearer,
    create_job_ok,
    insert_asset,
    insert_job,
    run_stage,
)

pytestmark = pytest.mark.asyncio


async def test_gd01_guard_dispatched(client, studio, patch_dispatch, patch_ai):
    await create_job_ok(client, studio.customer_token, studio.task_id, studio.claim_id)
    assert patch_dispatch.stages() == ["guard"], (
        f"建 job 后必须把预检排出去：{patch_dispatch.records}"
    )


async def test_gd02_guard_pass_to_chatting(
    client, studio, db, patch_dispatch, patch_ai
):
    body = await create_job_ok(
        client, studio.customer_token, studio.task_id, studio.claim_id
    )
    await run_stage("guard", body["job_id"])

    row = await db.fetchrow(
        "SELECT status FROM content_job WHERE id = $1", body["job_id"]
    )
    assert row["status"] == "chatting", f"预检通过应进 chatting：{row['status']}"

    guard = await db.fetchrow(
        "SELECT passed, model FROM guard_result WHERE job_id = $1", body["job_id"]
    )
    assert guard is not None and guard["passed"] is True, "guard_result 必须落库"
    assert guard["model"], "记下是哪个模型判的（换模型后要能回溯）"


async def test_gd03_guard_reject_records_reason(
    client, studio, db, patch_dispatch, patch_ai
):
    from app.services.ai import GuardVerdict

    patch_ai.q_guard(
        GuardVerdict(passed=False, model="qwen3-vl", reason="宠物照片与奶茶任务无关")
    )
    body = await create_job_ok(
        client, studio.customer_token, studio.task_id, studio.claim_id
    )
    await run_stage("guard", body["job_id"])

    job = await db.fetchrow(
        "SELECT status FROM content_job WHERE id = $1", body["job_id"]
    )
    assert job["status"] == "guard_failed", f"应终止在 guard_failed：{job['status']}"

    guard = await db.fetchrow(
        "SELECT passed, reason FROM guard_result WHERE job_id = $1", body["job_id"]
    )
    assert guard["passed"] is False
    assert guard["reason"] == "宠物照片与奶茶任务无关", (
        f"不通过的理由要原样落库，便于前端解释：{guard['reason']}"
    )


async def test_gd04_rejected_never_generates(
    client, studio, db, patch_dispatch, patch_ai
):
    from app.services.ai import GuardVerdict

    patch_ai.q_guard(GuardVerdict(passed=False, model="qwen3-vl", reason="风景照无商品主体"))
    body = await create_job_ok(
        client, studio.customer_token, studio.task_id, studio.claim_id
    )
    await run_stage("guard", body["job_id"])

    n = await db.fetchval(
        "SELECT count(*) FROM gen_output WHERE job_id = $1", body["job_id"]
    )
    assert n == 0, "预检不过**不得**进入生成"
    assert patch_ai.calls["generate"] == [], (
        f"生成接缝不应被调用：{patch_ai.calls['generate']}"
    )


async def test_gd05_guard_error_fails_closed(
    client, studio, db, patch_dispatch, patch_ai
):
    """预检服务超时/报错 → failed，**不允许默认放行**。"""
    patch_ai.q_guard(TimeoutError("qwen3-vl 超时"))
    body = await create_job_ok(
        client, studio.customer_token, studio.task_id, studio.claim_id
    )
    await run_stage("guard", body["job_id"])

    row = await db.fetchrow(
        "SELECT status, fail_reason FROM content_job WHERE id = $1", body["job_id"]
    )
    assert row["status"] == "failed", (
        f"预检自身故障必须 fail closed，而不是放行：{row['status']}"
    )
    assert row["fail_reason"], "失败要给得出原因"


async def test_gd06_watermark_rejected(client, studio, db, patch_dispatch, patch_ai):
    from app.services.ai import GuardVerdict

    patch_ai.q_guard(
        GuardVerdict(passed=False, model="qwen3-vl", reason="含其他品牌水印，有侵权风险")
    )
    body = await create_job_ok(
        client, studio.customer_token, studio.task_id, studio.claim_id
    )
    await run_stage("guard", body["job_id"])

    status = await db.fetchval(
        "SELECT status FROM content_job WHERE id = $1", body["job_id"]
    )
    assert status == "guard_failed", f"水印同样要挡住：{status}"


async def test_gd07_generate_after_guard_fail_409(
    client, studio, db, patch_dispatch, patch_ai
):
    job_id = await insert_job(
        db, studio.task_id, studio.claim_id, studio.customer["id"],
        status="guard_failed",
    )
    r = await client.post(
        f"/api/jobs/{job_id}/generate",
        headers=bearer(studio.customer_token),
        json={"prompt": "写一段奶茶文案"},
    )
    assert r.status_code == 409, f"预检不过不得生成：{r.status_code} {r.text}"


async def test_gd08_no_refund_on_guard_fail(
    client, studio, db, patch_dispatch, patch_ai
):
    """策略性失败：额度照扣，不退。"""
    from app.services.ai import GuardVerdict

    patch_ai.q_guard(GuardVerdict(passed=False, model="qwen3-vl", reason="无关"))
    body = await create_job_ok(
        client, studio.customer_token, studio.task_id, studio.claim_id
    )
    await run_stage("guard", body["job_id"])

    status = await db.fetchval(
        "SELECT status FROM quota_reservation WHERE job_id = $1", body["job_id"]
    )
    assert status == "settled", (
        f"策略性失败照常结算（预检真的花过钱）：{status}"
    )
    refunds = await db.fetchval(
        "SELECT count(*) FROM quota_ledger WHERE job_id = $1 AND source = 'refund'",
        body["job_id"],
    )
    assert refunds == 0, "预检不通过不退款，不得出现 refund 流水"


async def test_gd09_get_guard_result(client, studio, db, patch_dispatch, patch_ai):
    body = await create_job_ok(
        client, studio.customer_token, studio.task_id, studio.claim_id
    )
    await run_stage("guard", body["job_id"])

    r = await client.get(
        f"/api/jobs/{body['job_id']}/guard", headers=bearer(studio.customer_token)
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["passed"] is True
    assert "model" in data, f"要能回答「谁判的」：{data}"


async def test_gd10_guard_pending_409(client, studio, db, patch_dispatch, patch_ai):
    job_id = await insert_job(
        db, studio.task_id, studio.claim_id, studio.customer["id"], status="guarding"
    )
    r = await client.get(
        f"/api/jobs/{job_id}/guard", headers=bearer(studio.customer_token)
    )
    assert r.status_code == 409, f"预检未完成应 409 而不是 200 空结果：{r.status_code}"


async def test_gd11_others_guard_403(client, studio, db, patch_dispatch, patch_ai):
    from tests.helpers import register_customer

    await register_customer(client, account="cust9002")
    from tests.helpers import login_ok

    other = await login_ok(client, "cust9002", "pass1234", fp="fp-x")

    body = await create_job_ok(
        client, studio.customer_token, studio.task_id, studio.claim_id
    )
    await run_stage("guard", body["job_id"])

    r = await client.get(
        f"/api/jobs/{body['job_id']}/guard", headers=bearer(other["access_token"])
    )
    assert r.status_code == 403, f"非本人应 403：{r.status_code} {r.text}"


async def test_gd12_unknown_job_404(client, studio):
    assert_route_registered("GET", "/api/jobs/{job_id}/guard")
    r = await client.get(
        "/api/jobs/999999/guard", headers=bearer(studio.customer_token)
    )
    assert r.status_code == 404, r.text


async def test_gd13_reason_never_null(client, studio, db, patch_dispatch, patch_ai):
    """判定为「不通过」却不给理由 → 落库时必须有兜底文案。"""
    from app.services.ai import GuardVerdict

    patch_ai.q_guard(GuardVerdict(passed=False, model="qwen3-vl", reason=None))
    body = await create_job_ok(
        client, studio.customer_token, studio.task_id, studio.claim_id
    )
    await run_stage("guard", body["job_id"])

    reason = await db.fetchval(
        "SELECT reason FROM guard_result WHERE job_id = $1", body["job_id"]
    )
    assert reason, "不通过却没理由，前端只能显示空白——必须有兜底文案"
