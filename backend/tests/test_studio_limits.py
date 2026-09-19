"""03-studio · 并发、熔断与结算口径。

对应 test_plan.md：`JC-01` ~ `JC-09`

这里混了两类东西，都归「花多少钱」这一件事：

- **熔断**：并发上限、商户日限额（`JC-01`~`JC-03`）——挡住的是「失控的调用量」；
- **结算口径**：预占唯一、失败释放、策略性失败照扣、改价不影响已建 job
  （`JC-04`~`JC-09`）——挡住的是「账对不上」。
"""

import pytest

from tests.helpers import (
    bearer,
    create_job,
    create_job_ok,
    insert_job,
    insert_ledger,
    insert_reservation,
    insert_user_key,
    ledger_rows,
    quota_account_for,
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


# --------------------------------------------------------------------------- #
# 并发与熔断
# --------------------------------------------------------------------------- #
async def test_jc01_running_job_cap_429(client, studio, db, patch_dispatch):
    for _ in range(3):
        await _job(db, studio, status="generating")

    r = await create_job(
        client, studio.customer_token, studio.task_id, studio.claim_id
    )
    assert r.status_code == 429, (
        f"同一用户同时 3 个进行中的 job，第 4 个应拒：{r.status_code} {r.text}"
    )


async def test_jc02_existing_jobs_intact(client, studio, db, patch_dispatch):
    """被拒的新请求不得牵连在跑的 job。"""
    ids = [await _job(db, studio, status="generating") for _ in range(3)]
    await create_job(client, studio.customer_token, studio.task_id, studio.claim_id)

    rows = await db.fetch(
        "SELECT id, status, deleted_at FROM content_job WHERE id = ANY($1::bigint[])",
        ids,
    )
    assert len(rows) == 3, "在跑的 job 一条都不能少"
    assert all(r["status"] == "generating" for r in rows), (
        f"不得误杀在跑的 job：{[(r['id'], r['status']) for r in rows]}"
    )
    assert all(r["deleted_at"] is None for r in rows)


async def test_jc03_merchant_budget_429(client, studio, db, patch_dispatch):
    """商户当日额度超预算 → 429，并写一条告警，**一天一条**。"""
    await quota_account_for(db, studio.merchant["id"], balance=100_000, daily_limit=100)
    await insert_ledger(
        db, studio.merchant["id"], change=-900, balance_after=99_100,
        source="consume", task_id=studio.task_id,
    )

    for attempt in (1, 2):
        r = await create_job(
            client, studio.customer_token, studio.task_id, studio.claim_id
        )
        assert r.status_code == 429, (
            f"第 {attempt} 次超预算应 429：{r.status_code} {r.text}"
        )

    alerts = await db.fetchval(
        "SELECT count(*) FROM budget_alert WHERE merchant_id = $1",
        studio.merchant["id"],
    )
    assert alerts == 1, f"同一天同一商户只告警一次（否则告警本身会刷屏）：{alerts}"


# --------------------------------------------------------------------------- #
# 结算口径
# --------------------------------------------------------------------------- #
async def test_jc04_single_reservation_row(client, studio, db, patch_dispatch):
    body = await create_job_ok(
        client, studio.customer_token, studio.task_id, studio.claim_id
    )
    n = await db.fetchval(
        "SELECT count(*) FROM quota_reservation WHERE job_id = $1", body["job_id"]
    )
    assert n == 1, f"一个 job 只能占一份（否则会双倍扣钱）：{n}"


async def test_jc05_byok_zero_cost(client, studio, db, patch_dispatch, patch_ai):
    from app.services.ai import GenResult

    patch_ai.q_generate(GenResult(content="自带 Key", cost_cents=0, model="deepseek-chat"))
    await insert_user_key(db, studio.customer["id"])
    job_id = await _job(db, studio, billing_source="byok")
    await _reservation(db, studio, job_id, billing_source="byok", reserved=0)
    await run_stage("generate", job_id)

    out = await db.fetchrow(
        "SELECT cost_cents, billing_source FROM gen_output WHERE job_id = $1", job_id
    )
    assert out["cost_cents"] == 0, "BYOK 对平台零成本"
    assert out["billing_source"] == "byok"

    n = await db.fetchval(
        "SELECT count(*) FROM quota_ledger WHERE job_id = $1", job_id
    )
    assert n == 0, "BYOK 不该产生任何流水（0 元的流水是噪声）"


async def test_jc06_infra_failure_releases(client, studio, db, patch_dispatch, patch_ai):
    """系统故障 → 预占释放、**不产生流水**（用户不该为平台的故障付钱）。"""
    patch_ai.q_generate(RuntimeError("队列丢弃"))
    job_id = await _job(db, studio)
    await _reservation(db, studio, job_id, reserved=120)
    await quota_account_for(db, studio.merchant["id"], balance=100_000, reserved=120)

    await run_stage("generate", job_id)

    row = await db.fetchrow(
        "SELECT status, actual FROM quota_reservation WHERE job_id = $1", job_id
    )
    assert row["status"] == "released", f"故障应释放：{row['status']}"
    assert row["actual"] is None, "释放不该记实际消耗"

    n = await db.fetchval(
        "SELECT count(*) FROM quota_ledger WHERE job_id = $1", job_id
    )
    assert n == 0, "释放不写流水"

    reserved = await db.fetchval(
        "SELECT reserved FROM quota_account WHERE user_id = $1", studio.merchant["id"]
    )
    assert reserved == 0, f"预占要真退回：{reserved}"


async def test_jc07_need_review_still_billed(
    client, studio, db, patch_dispatch, patch_ai
):
    """把关 3 次不过 → 转人工，但钱照扣（调用成本是真的付了）。"""
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
    await _reservation(db, studio, job_id, reserved=120)
    await quota_account_for(db, studio.merchant["id"], balance=100_000, reserved=120)

    await run_stage("generate", job_id)

    status = await db.fetchval(
        "SELECT status FROM quota_reservation WHERE job_id = $1", job_id
    )
    assert status == "settled", f"转人工也要结算：{status}"

    rows = await ledger_rows(db, studio.merchant["id"])
    consumes = [r for r in rows if r["source"] == "consume"]
    assert len(consumes) == 1, f"应产生 1 条消费流水：{len(consumes)}"


async def test_jc08_price_frozen_at_job_time(client, studio, db, patch_dispatch):
    """改价不影响已建 job：预扣额按建 job 那一刻的计价行。"""
    from tests.helpers import insert_price

    body = await create_job_ok(
        client, studio.customer_token, studio.task_id, studio.claim_id
    )
    reserved = await db.fetchval(
        "SELECT reserved FROM quota_reservation WHERE job_id = $1", body["job_id"]
    )

    await insert_price(
        db, "deepseek", "deepseek-chat", "chat", max_price_per_call=999
    )

    after = await db.fetchval(
        "SELECT reserved FROM quota_reservation WHERE job_id = $1", body["job_id"]
    )
    assert after == reserved, (
        f"改价后已建 job 的预扣额不得变：{after} vs {reserved}"
    )
    assert reserved == 120, f"应按建 job 时刻的 120 定价：{reserved}"


async def test_jc09_settle_idempotent(client, studio, db, patch_dispatch, patch_ai):
    """同一 job 重复投递结算 → 流水只多 1 条。"""
    from app.services.ai import GenResult

    patch_ai.q_generate(GenResult(content="一次就过", cost_cents=12, model="deepseek-chat"))
    job_id = await _job(db, studio)
    await _reservation(db, studio, job_id, reserved=120)
    await quota_account_for(db, studio.merchant["id"], balance=100_000, reserved=120)

    await run_stage("generate", job_id)
    await run_stage("generate", job_id)
    await run_stage("generate", job_id)

    # 只看 consume：`studio` fixture 自己那条 recharge 是造数据，不是本用例的账
    rows = [
        r
        for r in await ledger_rows(db, studio.merchant["id"])
        if r["source"] == "consume"
    ]
    assert len(rows) == 1, (
        f"重复投递结算只能记一次账，实际 {len(rows)} 条：{[r['source'] for r in rows]}"
    )
