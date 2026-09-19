"""03-studio · 生成与产物把关。

对应 test_plan.md：`GN-01` ~ `GN-22`

这一组是**钱真正花掉的地方**，所以两条规矩必须钉死：

- 把关不过 → 自动重试，但 `is_active=true` 的产物**永远只有 1 条**（`GN-06`）；
- provider 报错 → **不自动换一家**（`GN-08`），换供应商就是再付一次钱。
"""

import pytest

from tests.helpers import (
    bearer,
    insert_asset,
    insert_job,
    insert_reservation,
    insert_user_key,
    login_ok,
    quota_account_for,
    register_customer,
    run_stage,
)

pytestmark = pytest.mark.asyncio


async def _job(db, studio, **over):
    row = {"status": "chatting"}
    row.update(over)
    return await insert_job(
        db, studio.task_id, studio.claim_id, studio.customer["id"], **row
    )


async def _reservation(db, studio, job_id, **over):
    return await insert_reservation(
        db, job_id, studio.merchant["id"], studio.customer["id"], studio.task_id, **over
    )


async def _start_generate(client, studio, job_id, prompt="写一段奶茶文案"):
    return await client.post(
        f"/api/jobs/{job_id}/generate",
        headers=bearer(studio.customer_token),
        json={"prompt": prompt},
    )


async def test_gn01_generate_accepted(client, studio, db, patch_dispatch, patch_ai):
    job_id = await _job(db, studio)
    r = await _start_generate(client, studio, job_id)
    assert r.status_code == 202, f"生成是异步的，应 202：{r.status_code} {r.text}"
    assert r.json()["status"] == "generating", r.text


async def test_gn02_copy_ready(client, studio, db, patch_dispatch, patch_ai):
    from app.services.ai import GenResult

    patch_ai.q_generate(
        GenResult(content="夏日柠檬茶，一口回到十八岁。", cost_cents=12, model="deepseek-chat")
    )
    job_id = await _job(db, studio)
    await _reservation(db, studio, job_id)
    await run_stage("generate", job_id)

    row = await db.fetchrow(
        "SELECT status FROM content_job WHERE id = $1", job_id
    )
    assert row["status"] == "ready", f"把关通过应就绪：{row['status']}"

    out = await db.fetchrow(
        "SELECT type, content, judge_score, attempt, is_active FROM gen_output "
        "WHERE job_id = $1",
        job_id,
    )
    assert out["type"] == "copy"
    assert out["content"] == "夏日柠檬茶，一口回到十八岁。", (
        f"产物必须原样落库（不得静默截断或改写）：{out['content']!r}"
    )
    assert out["judge_score"] == 90, "把关分数要落库"
    assert out["attempt"] == 1 and out["is_active"] is True


async def test_gn03_video_ready(client, studio, db, patch_dispatch, patch_ai):
    from app.services.ai import GenResult

    patch_ai.q_generate(
        GenResult(url="minio://bucket/out.mp4", cost_cents=200, model="jimeng-video")
    )
    job_id = await _job(db, studio, kind="video", provider="jimeng")
    await _reservation(db, studio, job_id, reserved=200)
    await run_stage("generate", job_id)

    status = await db.fetchval(
        "SELECT status FROM content_job WHERE id = $1", job_id
    )
    assert status == "ready", f"视频把关通过应就绪：{status}"

    out = await db.fetchrow(
        "SELECT type, url, cost_cents FROM gen_output WHERE job_id = $1", job_id
    )
    assert out["type"] == "video"
    assert out["url"], "视频产物必须给出可访问 URL"
    assert out["cost_cents"] == 200, "成本要落库（06 成本看板靠它）"


async def test_gn04_low_score_auto_retry(client, studio, db, patch_dispatch, patch_ai):
    from app.services.ai import GenResult, JudgeResult

    patch_ai.q_generate(
        GenResult(content="第一版", cost_cents=12, model="deepseek-chat"),
        GenResult(content="第二版", cost_cents=12, model="deepseek-chat"),
    )
    patch_ai.q_judge(
        JudgeResult(score=40, relevance=40, compliance=80, quality=30, reasons=["太平"]),
        JudgeResult(score=90, relevance=90, compliance=95, quality=85, reasons=[]),
    )
    job_id = await _job(db, studio)
    await _reservation(db, studio, job_id)
    await run_stage("generate", job_id)

    job = await db.fetchrow(
        "SELECT status, retry_count FROM content_job WHERE id = $1", job_id
    )
    assert job["retry_count"] == 1, f"自动重试要计数：{job['retry_count']}"
    assert job["status"] == "ready", f"第二次过就该就绪：{job['status']}"

    rows = await db.fetch(
        "SELECT attempt, content, is_active FROM gen_output WHERE job_id = $1 "
        "ORDER BY attempt",
        job_id,
    )
    assert len(rows) == 2, f"两次尝试都要留档（审计用）：{len(rows)}"
    assert rows[0]["is_active"] is False, "被重试掉的旧产物必须置 is_active=false"
    assert rows[1]["is_active"] is True


async def test_gn05_three_fails_need_review(client, studio, db, patch_dispatch, patch_ai):
    from app.services.ai import GenResult, JudgeResult

    patch_ai.q_generate(
        *[GenResult(content=f"第 {i} 版", cost_cents=12, model="deepseek-chat")
          for i in range(1, 6)]
    )
    patch_ai.q_judge(
        *[JudgeResult(score=40, relevance=40, compliance=80, quality=30, reasons=["反复不过"])
          for _ in range(6)]
    )
    job_id = await _job(db, studio)
    await _reservation(db, studio, job_id)
    await run_stage("generate", job_id)

    job = await db.fetchrow(
        "SELECT status, retry_count FROM content_job WHERE id = $1", job_id
    )
    assert job["status"] == "need_review", f"三次仍不过应转人工：{job['status']}"
    assert job["retry_count"] == 3, f"重试上限 3：{job['retry_count']}"

    n = await db.fetchval(
        "SELECT count(*) FROM gen_output WHERE job_id = $1 AND is_active", job_id
    )
    assert n == 1, f"转人工时产物一个都不能删，但活跃的要有且仅有 1 条：{n}"


async def test_gn06_single_active_output(client, studio, db, patch_dispatch, patch_ai):
    from app.services.ai import GenResult, JudgeResult

    patch_ai.q_generate(
        *[GenResult(content=f"v{i}", cost_cents=12, model="deepseek-chat")
          for i in range(1, 6)]
    )
    patch_ai.q_judge(
        *[JudgeResult(score=30, relevance=30, compliance=70, quality=30, reasons=[])
          for _ in range(6)]
    )
    job_id = await _job(db, studio)
    await _reservation(db, studio, job_id)
    await run_stage("generate", job_id)

    rows = await db.fetch(
        "SELECT attempt, is_active FROM gen_output WHERE job_id = $1 ORDER BY attempt",
        job_id,
    )
    assert [r["attempt"] for r in rows] == [1, 2, 3, 4], (
        f"attempt 应从 1 起严格递增、无重复：{[r['attempt'] for r in rows]}"
    )
    active = [r["attempt"] for r in rows if r["is_active"]]
    assert active == [4], f"活跃的必须恰好 1 条且是最新那条：{active}"


async def test_gn07_generation_timeout(client, studio, db, patch_dispatch, patch_ai):
    patch_ai.q_generate(TimeoutError("生成超过 10 分钟"))
    job_id = await _job(db, studio)
    await _reservation(db, studio, job_id)
    await run_stage("generate", job_id)

    row = await db.fetchrow(
        "SELECT status, fail_reason FROM content_job WHERE id = $1", job_id
    )
    assert row["status"] == "failed", f"超时应失败：{row['status']}"
    assert row["fail_reason"], "失败原因不能空着"

    status = await db.fetchval(
        "SELECT status FROM quota_reservation WHERE job_id = $1", job_id
    )
    assert status == "released", f"系统故障要释放预占：{status}"


async def test_gn08_video_error_no_fallback(client, studio, db, patch_dispatch, patch_ai):
    """即梦报错 → **不自动切可灵**（换了就是再付一次钱）。"""
    patch_ai.q_generate(RuntimeError("jimeng 502"))
    job_id = await _job(db, studio, kind="video", provider="jimeng")
    await _reservation(db, studio, job_id, reserved=200)
    await run_stage("generate", job_id)

    status = await db.fetchval(
        "SELECT status FROM content_job WHERE id = $1", job_id
    )
    assert status == "failed", f"provider 报错应失败：{status}"

    used = {c["provider"] for c in patch_ai.calls["generate"]}
    assert used == {"jimeng"}, f"不得自动换供应商：实际用过 {used}"

    row = await db.fetchrow(
        "SELECT provider, billing_source FROM content_job WHERE id = $1", job_id
    )
    assert row["provider"] == "jimeng", "前端要让用户自己选，故 provider 不能被偷换"


async def test_gn09_after_guard_fail_409(client, studio, db, patch_dispatch, patch_ai):
    job_id = await _job(db, studio, status="guard_failed")
    r = await _start_generate(client, studio, job_id)
    assert r.status_code == 409, f"素材不合格不得生成：{r.status_code} {r.text}"


async def test_gn10_ready_cannot_regenerate_409(
    client, studio, db, patch_dispatch, patch_ai
):
    job_id = await _job(db, studio, status="ready")
    r = await _start_generate(client, studio, job_id)
    assert r.status_code == 409, f"已就绪不得重复生成：{r.status_code} {r.text}"


async def test_gn11_concurrency_429(client, studio, db, patch_dispatch, patch_ai):
    """并发生成数超限 → 429。"""
    for _ in range(3):
        await _job(db, studio, status="generating")

    job_id = await _job(db, studio)
    r = await _start_generate(client, studio, job_id)
    assert r.status_code == 429, f"并发超限应 429：{r.status_code} {r.text}"


async def test_gn12_insufficient_402(client, studio, db, patch_dispatch, patch_ai):
    await quota_account_for(db, studio.merchant["id"], balance=0, reserved=0)
    job_id = await _job(db, studio)
    r = await _start_generate(client, studio, job_id)
    assert r.status_code == 402, f"额度不足应 402：{r.status_code} {r.text}"


async def test_gn13_retry_after_failure(client, studio, db, patch_dispatch, patch_ai):
    job_id = await _job(db, studio, status="failed", fail_reason="生成超时")
    r = await client.post(
        f"/api/jobs/{job_id}/retry", headers=bearer(studio.customer_token)
    )
    assert r.status_code == 202, f"可重试的失败应放行：{r.status_code} {r.text}"

    job = await db.fetchrow(
        "SELECT status, retry_count FROM content_job WHERE id = $1", job_id
    )
    assert job["retry_count"] == 1, f"重试要计数：{job['retry_count']}"
    assert job["status"] == "generating", f"重试应回到生成中：{job['status']}"


async def test_gn14_retry_cap_409(client, studio, db, patch_dispatch, patch_ai):
    job_id = await _job(db, studio, status="failed", retry_count=3)
    r = await client.post(
        f"/api/jobs/{job_id}/retry", headers=bearer(studio.customer_token)
    )
    assert r.status_code == 409, f"重试上限 3：{r.status_code} {r.text}"


async def test_gn15_retry_on_guard_failed_409(
    client, studio, db, patch_dispatch, patch_ai
):
    job_id = await _job(db, studio, status="guard_failed")
    r = await client.post(
        f"/api/jobs/{job_id}/retry", headers=bearer(studio.customer_token)
    )
    assert r.status_code == 409, (
        f"素材问题重试无意义，必然再败一次：{r.status_code} {r.text}"
    )


async def test_gn16_retry_on_need_review_ok(client, studio, db, patch_dispatch, patch_ai):
    job_id = await _job(db, studio, status="need_review", retry_count=3)
    r = await client.post(
        f"/api/jobs/{job_id}/retry", headers=bearer(studio.customer_token)
    )
    assert r.status_code == 202, (
        f"转人工后重试是人工恢复口，不该被 retry_count 挡住：{r.status_code} {r.text}"
    )


async def test_gn17_others_403(client, studio, db, patch_dispatch, patch_ai):
    await register_customer(client, account="cust9002")
    other = await login_ok(client, "cust9002", "pass1234", fp="fp-x")
    job_id = await _job(db, studio)

    r = await client.post(
        f"/api/jobs/{job_id}/generate",
        headers=bearer(other["access_token"]),
        json={"prompt": "偷跑"},
    )
    assert r.status_code == 403, r.text

    r = await client.post(
        f"/api/jobs/{job_id}/retry", headers=bearer(other["access_token"])
    )
    assert r.status_code == 403, r.text


async def test_gn18_generate_dispatched(client, studio, db, patch_dispatch, patch_ai):
    job_id = await _job(db, studio)
    await _start_generate(client, studio, job_id)
    assert "generate" in patch_dispatch.stages(), (
        f"202 之后必须把生成排出去：{patch_dispatch.records}"
    )


async def test_gn19_byok_no_consume(client, studio, db, patch_dispatch, patch_ai):
    from app.services.ai import GenResult

    patch_ai.q_generate(GenResult(content="自带 Key 产出的文案", cost_cents=0, model="deepseek-chat"))
    await insert_user_key(db, studio.customer["id"])
    job_id = await _job(db, studio, billing_source="byok")
    await _reservation(db, studio, job_id, billing_source="byok", reserved=0)
    await run_stage("generate", job_id)

    out = await db.fetchrow(
        "SELECT billing_source, cost_cents FROM gen_output WHERE job_id = $1", job_id
    )
    assert out["billing_source"] == "byok", f"要标记来源：{out['billing_source']}"
    assert out["cost_cents"] == 0, "BYOK 对平台零成本"

    n = await db.fetchval(
        "SELECT count(*) FROM quota_ledger WHERE job_id = $1 AND source = 'consume'",
        job_id,
    )
    assert n == 0, "BYOK 不得扣平台额度"


async def test_gn20_byok_still_guarded(client, studio, db, patch_dispatch, patch_ai):
    """安全不因自带 Key 豁免：预检与把关都照跑。"""
    job_id = await _job(db, studio, status="created", billing_source="byok")
    await insert_user_key(db, studio.customer["id"])
    await insert_asset(db, job_id)
    await _reservation(db, studio, job_id, billing_source="byok", reserved=0)

    await run_stage("guard", job_id)
    await run_stage("generate", job_id)

    assert patch_ai.calls["guard"], "BYOK 也必须跑预检"
    assert patch_ai.calls["judge"], "BYOK 也必须跑产物把关"


async def test_gn21_key_invalid_no_platform_fallback(
    client, studio, db, patch_dispatch, patch_ai
):
    """Key 失效 → failed(key_invalid)，**绝不回落平台 Key**。"""
    from app.services.ai import KeyInvalidError

    patch_ai.q_generate(KeyInvalidError("deepseek 返回 401"))
    await insert_user_key(db, studio.customer["id"])
    job_id = await _job(db, studio, billing_source="byok", provider="deepseek")
    await _reservation(db, studio, job_id, billing_source="byok", reserved=0)
    await run_stage("generate", job_id)

    row = await db.fetchrow(
        "SELECT status, fail_reason, billing_source FROM content_job WHERE id = $1",
        job_id,
    )
    assert row["status"] == "failed", f"Key 失效应失败：{row['status']}"
    assert row["fail_reason"] == "key_invalid", (
        f"失败原因要能让前端引导用户换 Key：{row['fail_reason']}"
    )
    assert row["billing_source"] == "byok", (
        f"绝不静默改成平台 Key（那是平台替用户付钱）：{row['billing_source']}"
    )
    assert all(
        c["provider"] == "deepseek" for c in patch_ai.calls["generate"]
    ), "不得改用别的 provider 重试"


async def test_gn22_video_kind_lands_a_readable_fail_reason(
    client, studio, db, patch_dispatch, patch_ai
):
    """`kind=video` 的 job → `failed(fail_reason="视频模型尚未接入")`。

    这条与 `AI-14` 是**两半**：那边管「`generate` 抛不抛」，这里管「抛完之后库里
    写什么」。08 追加 C 已把「做视频」按钮禁用，但 API 层不挡（见 spec
    「视频为什么不在 API 层挡住」）——绕过界面建出来的 video job 仍会走到这里，
    而 `fail_reason` 是要**给人看**的，落到通用的 `生成失败：...` 上等于把异常串
    直接糊到界面上。
    """
    from app.services.ai import VideoNotSupportedError

    patch_ai.q_generate(VideoNotSupportedError("视频模型尚未接入"))
    job_id = await _job(db, studio, kind="video", provider="jimeng")
    await _reservation(db, studio, job_id, reserved=200)
    await run_stage("generate", job_id)

    row = await db.fetchrow(
        "SELECT status, fail_reason FROM content_job WHERE id = $1", job_id
    )
    assert row["status"] == "failed", f"视频尚未接入应失败：{row['status']}"
    assert row["fail_reason"] == "视频模型尚未接入", (
        f"异常消息就是 fail_reason，必须原样落库（不得套上「生成失败：」前缀）："
        f"{row['fail_reason']}"
    )

    status = await db.fetchval(
        "SELECT status FROM quota_reservation WHERE job_id = $1", job_id
    )
    assert status == "released", (
        f"「模型尚未接入」是平台侧的能力缺失，不是用户的策略性失败，钱不该收：{status}"
    )
