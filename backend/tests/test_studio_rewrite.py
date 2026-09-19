"""03-studio · 提示词复写。

对应 test_plan.md：`RW-01` ~ `RW-14`

这一组最要紧的是**「复写没干活」这条路径**（`RW-05`/`RW-06`）：模型原样吐回输入
是真实的高频故障，若不当成失败，用户会拿到"优化后"的原始粗糙提示词却不自知。
但它也不能把用户卡死——3 次还不行就回落原文，**接口仍返回 200**。
"""

import pytest

from tests.helpers import (
    bearer,
    insert_job,
    login_ok,
    quota_account_for,
    register_customer,
)

pytestmark = pytest.mark.asyncio


async def _chatting_job(db, studio, **over):
    return await insert_job(
        db,
        studio.task_id,
        studio.claim_id,
        studio.customer["id"],
        status="chatting",
        **over,
    )


async def _rewrite(client, studio, job_id, raw="帮我写个奶茶文案"):
    return await client.post(
        f"/api/jobs/{job_id}/rewrite-prompt",
        headers=bearer(studio.customer_token),
        json={"raw_prompt": raw},
    )


async def test_rw01_rewrite_ok(client, studio, db, patch_ai):
    job_id = await _chatting_job(db, studio)
    r = await _rewrite(client, studio, job_id)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["optimized_prompt"], "复写要给出结果"
    assert body["optimized_prompt"] != "帮我写个奶茶文案", "复写必须真的改了东西"
    assert 0 <= body["quality_score"] <= 100
    assert body["rewrite_failed"] is False

    row = await db.fetchrow(
        "SELECT raw_prompt, optimized_prompt, quality_score FROM prompt_draft "
        "WHERE job_id = $1",
        job_id,
    )
    assert row is not None, "prompt_draft 必须落库"
    assert row["raw_prompt"] == "帮我写个奶茶文案", "原文要留档，便于对比分数"


async def test_rw02_empty_422(client, studio, db, patch_ai):
    job_id = await _chatting_job(db, studio)
    r = await _rewrite(client, studio, job_id, raw="")
    assert r.status_code == 422, r.text


async def test_rw03_2001_chars_422(client, studio, db, patch_ai):
    job_id = await _chatting_job(db, studio)
    r = await _rewrite(client, studio, job_id, raw="奶" * 2001)
    assert r.status_code == 422, r.text


async def test_rw04_2000_chars_ok(client, studio, db, patch_ai):
    job_id = await _chatting_job(db, studio)
    r = await _rewrite(client, studio, job_id, raw="奶" * 2000)
    assert r.status_code == 200, f"2000 字是上限内的合法值：{r.status_code} {r.text}"


async def test_rw05_identical_retried(client, studio, db, patch_ai):
    """复写结果与输入完全相同 → 视为失败，必须重试。"""
    from app.services.ai import RewriteResult

    raw = "奶茶文案"
    patch_ai.q_rewrite(
        RewriteResult(optimized_prompt=raw, quality_score=90, model="prompt-optimizer"),
        RewriteResult(optimized_prompt=f"优化版：{raw}", quality_score=90,
                      model="prompt-optimizer"),
    )
    job_id = await _chatting_job(db, studio)
    r = await _rewrite(client, studio, job_id, raw=raw)
    assert r.status_code == 200, r.text

    calls = patch_ai.calls["rewrite"]
    assert len(calls) >= 2, f"原样返回必须触发重试，实际只调了 {len(calls)} 次"
    assert r.json()["optimized_prompt"] == f"优化版：{raw}", "重试后应取到真结果"


async def test_rw06_persistent_identical_falls_back(client, studio, db, patch_ai):
    """3 次都原样返回 → `rewrite_failed=true` + 回落原文，**接口仍 200**。"""
    from app.services.ai import RewriteResult

    raw = "奶茶文案"
    patch_ai.q_rewrite(
        *[
            RewriteResult(optimized_prompt=raw, quality_score=70,
                          model="prompt-optimizer")
            for _ in range(5)
        ]
    )
    job_id = await _chatting_job(db, studio)
    r = await _rewrite(client, studio, job_id, raw=raw)

    assert r.status_code == 200, (
        f"复写本来就失败，不能让用户卡死在这里：{r.status_code} {r.text}"
    )
    body = r.json()
    assert body["rewrite_failed"] is True
    assert body["optimized_prompt"] == raw, "回落为原文，用户还能自己接着改"

    iterations = await db.fetchval(
        "SELECT iterations FROM prompt_draft WHERE job_id = $1", job_id
    )
    assert iterations == 3, f"迭代上限 3 次：{iterations}"


async def test_rw07_low_score_iterates(client, studio, db, patch_ai):
    """质量分低于阈值 → 继续迭代，取到及格的那次。"""
    from app.services.ai import RewriteResult

    patch_ai.q_rewrite(
        RewriteResult(optimized_prompt="初版", quality_score=40,
                      model="prompt-optimizer"),
        RewriteResult(optimized_prompt="改进版", quality_score=88,
                      model="prompt-optimizer"),
    )
    job_id = await _chatting_job(db, studio)
    body = (await _rewrite(client, studio, job_id)).json()

    assert body["quality_score"] == 88, f"应取到及格的版本：{body}"
    assert body["optimized_prompt"] == "改进版"
    iterations = await db.fetchval(
        "SELECT iterations FROM prompt_draft WHERE job_id = $1", job_id
    )
    assert iterations == 2, f"迭代次数应为 2：{iterations}"


async def test_rw08_iteration_cap_3(client, studio, db, patch_ai):
    """三次都不及格 → 停在上限，取最后一次结果。"""
    from app.services.ai import RewriteResult

    patch_ai.q_rewrite(
        *[
            RewriteResult(optimized_prompt=f"第 {i} 版", quality_score=40 + i,
                          model="prompt-optimizer")
            for i in range(1, 6)
        ]
    )
    job_id = await _chatting_job(db, studio)
    body = (await _rewrite(client, studio, job_id)).json()

    assert len(patch_ai.calls["rewrite"]) == 3, (
        f"上限 3 次，实际调了 {len(patch_ai.calls['rewrite'])} 次"
    )
    assert body["optimized_prompt"] == "第 3 版", f"取最后一次结果：{body}"


async def test_rw09_timeout_falls_back(client, studio, db, patch_ai):
    """复写超时 → 记 `rewrite_failed=true` 并返回原文。"""
    patch_ai.q_rewrite(TimeoutError("复写 30 秒超时"))
    job_id = await _chatting_job(db, studio)
    r = await _rewrite(client, studio, job_id, raw="别让我卡死")

    assert r.status_code == 200, f"超时不该 5xx：{r.status_code} {r.text}"
    body = r.json()
    assert body["rewrite_failed"] is True
    assert body["optimized_prompt"] == "别让我卡死"


async def test_rw10_rate_limit_429(client, studio, db, patch_ai):
    """连续 10 次/分钟 → 第 11 次 429。"""
    job_id = await _chatting_job(db, studio)
    for i in range(10):
        r = await _rewrite(client, studio, job_id, raw=f"第 {i} 次调用")
        assert r.status_code == 200, f"第 {i + 1} 次不该被限流：{r.status_code} {r.text}"

    r = await _rewrite(client, studio, job_id, raw="第 11 次调用")
    assert r.status_code == 429, f"超频应 429：{r.status_code} {r.text}"


async def test_rw11_others_403(client, studio, db, patch_ai):
    await register_customer(client, account="cust9002")
    other = await login_ok(client, "cust9002", "pass1234", fp="fp-x")

    job_id = await _chatting_job(db, studio)
    r = await client.post(
        f"/api/jobs/{job_id}/rewrite-prompt",
        headers=bearer(other["access_token"]),
        json={"raw_prompt": "别人的 job"},
    )
    assert r.status_code == 403, r.text


async def test_rw12_draft_upsert(client, studio, db, patch_ai):
    """一个 job 一份草稿：再复写是**更新**，不是新增。"""
    job_id = await _chatting_job(db, studio)
    await _rewrite(client, studio, job_id, raw="第一版需求")
    await _rewrite(client, studio, job_id, raw="改过的需求")

    rows = await db.fetch(
        "SELECT raw_prompt FROM prompt_draft WHERE job_id = $1", job_id
    )
    assert len(rows) == 1, f"job_id 唯一，草稿只能 1 份：{len(rows)}"
    assert rows[0]["raw_prompt"] == "改过的需求", "应是更新而非新增"


async def test_rw13_score_exposed(client, studio, db, patch_ai):
    """分数要能被详情读到——前端要展示「从 42 分优化到 88 分」。"""
    job_id = await _chatting_job(db, studio)
    await _rewrite(client, studio, job_id)

    r = await client.get(
        f"/api/jobs/{job_id}", headers=bearer(studio.customer_token)
    )
    assert r.status_code == 200, r.text
    draft = r.json()["prompt_draft"]
    assert draft is not None, "详情必须带上草稿"
    assert draft["quality_score"] is not None
    assert draft["optimized_prompt"], "详情要带上优化后的提示词"


async def test_rw14_insufficient_402(client, studio, db, patch_ai):
    job_id = await _chatting_job(db, studio)
    await quota_account_for(db, studio.merchant["id"], balance=0, reserved=0)

    r = await _rewrite(client, studio, job_id)
    assert r.status_code == 402, f"额度不足应 402：{r.status_code} {r.text}"
