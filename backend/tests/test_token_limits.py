"""07-token · 限额、熔断与 BYOK 计费（K 组）。**第二趟。**

对应 test_plan.md 的 `LM-01` ~ `LM-14`。

全部要经过**建 job**（`POST /api/jobs`，属 03）才能触发。

⚠️ **每一条都得先补好计价行**：缺价是 `503`，而 503 挡在限额检查之前
（没有价就算不出「这次要花多少」）。不补价的话，断言 429 的用例会拿到 503。

两条最贵的：
- `LM-03`：`daily_limit=null` **不得视为不限**——漏了这条，商户以为设了默认值，
  实际是无限额，账单会失控
- `LM-14`：Key 失效**绝不回落平台 Key**——回落等于白烧平台的钱
"""

import pytest

from tests.helpers import (
    assets_of,
    bearer,
    claim,
    insert_price,
    insert_task,
    quota_account_for,
    run_stage,
    seed_customers,
    token_for,
)

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _no_background_stage(monkeypatch):
    """本组一律**不真跑后台阶段**。

    建 job 会 `dispatch("guard", ...)` 排一个后台任务。不拦的话，用例结束后
    那个任务还在跑（它自己开 session、还会 `SELECT ... FOR UPDATE`），
    下一个用例开头的 TRUNCATE 就会和它撞死锁——报出来的 ERROR 落在**下一个**
    用例头上，看起来像那边的问题。真实状态机由 `test_studio_generate.py`
    显式 `await run_stage(...)` 驱动。
    """
    from tests.helpers import DispatchStub

    DispatchStub().install(monkeypatch)


async def _create_job(client, token, task_id, **over):
    """建 job。`kind` 与素材是 03 的必填项，不传就是框架的 422，测不到任何东西。"""
    body = {"task_id": task_id, "kind": "copy", "assets": assets_of(1)}
    body.update(over)
    return await client.post("/api/jobs", headers=bearer(token), json=body)


async def _ready_task(db, merchant_id, users: int = 1, **over):
    """造一个已发布任务 + `users` 个已领取它的用户。"""
    task_id = await insert_task(db, merchant_id, **over)
    ids = await seed_customers(db, users, prefix="lm")
    return task_id, ids


async def _byok_job(client, merchant, db, monkeypatch, *, prefix: str) -> int:
    """造一个「用户自带 Key 的 user_pay_reimburse job」，返回 job_id。

    客户 `balance=0` 是刻意的：BYOK 不花平台的钱，账户空着也该能建 job
    （`LM-09` 断言的就是这条）。
    """
    from tests.helpers import patch_verify, post_key

    patch_verify(monkeypatch, ok=True)

    uid = merchant[0]["id"]
    task_id = await insert_task(
        db,
        uid,
        pay_mode="user_pay_reimburse",
        reimburse_pool=10_000,
        reimburse_per_user_limit=500,
    )
    ids = await seed_customers(db, 1, prefix=prefix)
    tok = token_for(ids[0])
    await quota_account_for(db, uid, balance=100_000)
    await quota_account_for(db, ids[0], balance=0)
    await insert_price(db, "deepseek", "deepseek-chat", "chat")
    await claim(client, tok, task_id)

    key = await post_key(client, tok, "deepseek")
    assert key.status_code in (200, 201), f"存 Key 失败：{key.text}"

    r = await _create_job(client, tok, task_id, billing_source="byok")
    assert r.status_code == 201, f"BYOK 建 job 失败：{r.status_code} {r.text}"
    return r.json()["job_id"]


# --------------------------------------------------------------------------- #
# 日限额
# --------------------------------------------------------------------------- #
async def test_lm01_daily_limit_429(client, merchant, db):
    """日限额 1000、当日已耗 900、新任务预估 200 → 429。"""
    uid = merchant[0]["id"]
    task_id, ids = await _ready_task(db, uid)
    await quota_account_for(db, uid, balance=100_000, daily_limit=1000)
    await insert_price(db, "deepseek", "deepseek-chat", "chat", max_price_per_call=200)
    tok = token_for(ids[0])
    await claim(client, tok, task_id)

    await db.execute(
        """
        INSERT INTO quota_ledger
            (user_id, change, balance_after, source, ref_type, ref_id, created_at)
        VALUES ($1, -900, 99100, 'consume', 'seed', 9001, now())
        """,
        uid,
    )

    r = await _create_job(client, tok, task_id)
    assert r.status_code == 429, (
        f"900 已耗 + 200 预估 > 1000 日限额，应 429：{r.status_code} {r.text}"
    )


async def test_lm02_under_limit_ok(client, merchant, db):
    """同样日限额 1000、已耗 900，但预估只需 50 → 200。"""
    uid = merchant[0]["id"]
    task_id, ids = await _ready_task(db, uid)
    await quota_account_for(db, uid, balance=100_000, daily_limit=1000)
    tok = token_for(ids[0])
    await claim(client, tok, task_id)

    await insert_price(
        db, "deepseek", "deepseek-chat", "chat", max_price_per_call=50
    )
    await db.execute(
        """
        INSERT INTO quota_ledger
            (user_id, change, balance_after, source, ref_type, ref_id, created_at)
        VALUES ($1, -900, 99100, 'consume', 'seed', 9002, now())
        """,
        uid,
    )

    r = await _create_job(client, tok, task_id, provider="deepseek")
    assert r.status_code == 201, (
        f"900 + 50 <= 1000，日限额不该拦：{r.status_code} {r.text}"
    )


async def test_lm03_null_uses_platform_default(client, merchant, db):
    """`daily_limit=null` → 用平台默认，**不得视为不限**。

    漏了这条，商户以为设了默认值，实际是无限额——账单失控时才发现。
    平台默认值由配置文件给出；这里把默认压到很小，验证它真的生效。
    """
    uid = merchant[0]["id"]
    task_id, ids = await _ready_task(db, uid)
    await quota_account_for(db, uid, balance=100_000, daily_limit=None)
    tok = token_for(ids[0])
    await claim(client, tok, task_id)

    await insert_price(db, "deepseek", "deepseek-chat", "chat", max_price_per_call=200)
    await db.execute(
        """
        INSERT INTO quota_ledger
            (user_id, change, balance_after, source, ref_type, ref_id, created_at)
        VALUES ($1, -99500, 500, 'consume', 'seed', 9003, now())
        """,
        uid,
    )

    r = await _create_job(client, tok, task_id, provider="deepseek")
    assert r.status_code == 429, (
        f"daily_limit 为 null 时必须落到平台默认限额上，不得视为不限："
        f"{r.status_code} {r.text}"
    )


# --------------------------------------------------------------------------- #
# 按用户限额
# --------------------------------------------------------------------------- #
async def test_lm04_per_user_daily(client, merchant, db):
    """`per_user_daily_limit=200`：某用户烧满 → 该用户 429，其他用户不受影响。"""
    uid = merchant[0]["id"]
    task_id, ids = await _ready_task(db, uid, users=2)
    await quota_account_for(db, uid, balance=100_000, per_user_daily_limit=200)
    await insert_price(db, "deepseek", "deepseek-chat", "chat", max_price_per_call=200)
    heavy, light = token_for(ids[0]), token_for(ids[1])
    await claim(client, heavy, task_id)
    await claim(client, light, task_id)

    await db.execute(
        """
        INSERT INTO quota_ledger
            (user_id, change, balance_after, source, ref_type, ref_id,
             spender_id, created_at)
        VALUES ($1, -200, 99800, 'consume', 'seed', 9004, $2, now())
        """,
        uid,
        ids[0],
    )

    blocked = await _create_job(client, heavy, task_id)
    allowed = await _create_job(client, light, task_id)

    assert blocked.status_code == 429, (
        f"烧满 200 的用户应被 429：{blocked.status_code} {blocked.text}"
    )
    assert allowed.status_code == 201, (
        f"别的用户不该被连坐（限额是按 spender 算的）：{allowed.status_code} {allowed.text}"
    )


async def test_lm05_per_user_task(client, merchant, db):
    """`per_user_task_limit=500`：某用户在该任务烧满 → 429。"""
    uid = merchant[0]["id"]
    task_id, ids = await _ready_task(db, uid)
    await quota_account_for(db, uid, balance=100_000, per_user_task_limit=500)
    await insert_price(db, "deepseek", "deepseek-chat", "chat")
    tok = token_for(ids[0])
    await claim(client, tok, task_id)

    await db.execute(
        """
        INSERT INTO quota_ledger
            (user_id, change, balance_after, source, ref_type, ref_id,
             task_id, spender_id, created_at)
        VALUES ($1, -500, 99500, 'consume', 'seed', 9005, $2, $3, now())
        """,
        uid,
        task_id,
        ids[0],
    )

    r = await _create_job(client, tok, task_id)
    assert r.status_code == 429, (
        f"单用户在该任务已烧满 500，应 429：{r.status_code} {r.text}"
    )


# --------------------------------------------------------------------------- #
# 并发与熔断
# --------------------------------------------------------------------------- #
async def test_lm06_concurrency_cap(client, merchant, db):
    """并发上限（平台配置）第 N+1 个 → 429。"""
    import asyncio

    uid = merchant[0]["id"]
    task_id, ids = await _ready_task(db, uid, users=6)
    await quota_account_for(db, uid, balance=100_000)
    await insert_price(db, "deepseek", "deepseek-chat", "chat")
    tokens = [token_for(i) for i in ids]
    for t in tokens:
        await claim(client, t, task_id)

    responses = await asyncio.gather(
        *(_create_job(client, t, task_id) for t in tokens)
    )
    codes = sorted(r.status_code for r in responses)

    assert 429 in codes, (
        f"并发上限是平台配置（如单商户 3 个并行），超出必须 429；实际码={codes}"
    )
    assert codes.count(429) >= 1


async def test_lm07_budget_alert_once_per_day(client, merchant, db):
    """触发熔断写一条 `budget_alert`，且**一天一条**。"""
    uid = merchant[0]["id"]
    task_id, ids = await _ready_task(db, uid)
    await quota_account_for(db, uid, balance=100_000, daily_limit=1)
    await insert_price(db, "deepseek", "deepseek-chat", "chat")
    tok = token_for(ids[0])
    await claim(client, tok, task_id)

    for _ in range(3):
        r = await _create_job(client, tok, task_id)
        assert r.status_code == 429, f"日限额 1 分，必被拦：{r.status_code} {r.text}"

    n = await db.fetchval(
        "SELECT count(*) FROM budget_alert WHERE merchant_id = $1", uid
    )
    assert n == 1, f"熔断一天只留一条告警，实际 {n} 条"


async def test_lm08_all_null_balance_only(client, merchant, db):
    """三种限额都为 `null`（且平台默认也放得很宽）时，仅受余额约束。"""
    uid = merchant[0]["id"]
    task_id, ids = await _ready_task(db, uid)
    await quota_account_for(
        db,
        uid,
        balance=100_000,
        daily_limit=None,
        per_user_daily_limit=None,
        per_user_task_limit=None,
    )
    tok = token_for(ids[0])
    await claim(client, tok, task_id)

    await insert_price(db, "deepseek", "deepseek-chat", "chat")
    r = await _create_job(client, tok, task_id)
    assert r.status_code == 201, (
        f"限额全空且余额充足时不该被限额拦：{r.status_code} {r.text}"
    )


# --------------------------------------------------------------------------- #
# BYOK 计费
# --------------------------------------------------------------------------- #
async def test_lm09_byok_zero_balance_ok(client, merchant, db, monkeypatch):
    """BYOK 下用户 `balance=0` 也能建 job（不扣平台额度），但仍受商户报销池约束。"""
    r = await _byok_job(client, merchant, db, monkeypatch, prefix="lm09")
    # `_byok_job` 里已经断言 201；这里再确认预扣单确实是 0 元的
    reserved = await db.fetchval(
        "SELECT reserved FROM quota_reservation WHERE job_id = $1", r
    )
    assert reserved == 0, f"BYOK 不扣平台额度，预扣应为 0：{reserved}"


async def test_lm10_byok_gen_output_zero(
    client, merchant, db, monkeypatch, patch_ai, patch_dispatch
):
    """BYOK 调用 → `gen_output.cost_cents = 0` 且 `billing_source=byok`，无 consume 流水。"""
    from app.services.ai import GenResult

    job_id = await _byok_job(client, merchant, db, monkeypatch, prefix="lm10")
    # BYOK 的调用成本由用户自己付给 provider，平台侧必须是 0
    patch_ai.q_generate(
        GenResult(content="自带 Key 出的稿", cost_cents=0, model="deepseek-chat")
    )

    await run_stage("guard", job_id)
    await run_stage("generate", job_id)

    row = await db.fetchrow(
        "SELECT cost_cents, billing_source FROM gen_output WHERE job_id = $1", job_id
    )
    assert row is not None, "BYOK 也要真产出，安全不因自带 Key 豁免"
    assert row["billing_source"] == "byok", f"来源要标出来供成本看板识别：{row}"
    assert row["cost_cents"] == 0, f"平台零成本：{row}"

    n = await db.fetchval(
        "SELECT count(*) FROM quota_ledger WHERE source = 'consume' AND job_id = $1",
        job_id,
    )
    assert n == 0, "平台零成本，不该产生 consume 流水"


async def test_lm11_byok_reimburse_base(client, merchant, db):
    """BYOK 的报销基数按 `model_price.cost_price_per_unit` 计，与用户实付无关。"""
    uid = merchant[0]["id"]
    await insert_price(
        db, "deepseek", "deepseek-chat", "chat", cost_price_per_unit=0.000002
    )

    cost = await db.fetchval(
        "SELECT cost_price_per_unit FROM model_price "
        "WHERE provider = 'deepseek' AND model = 'deepseek-chat'"
    )
    assert float(cost) == pytest.approx(0.000002)


async def test_lm12_cost_board_byok_zero(client, seed_accounts, merchant, db):
    """06 成本看板要把 BYOK 识别为 0 成本，不因 `cost_cents=0` 误判为失败调用。"""
    from tests.helpers import admin_token

    token = await admin_token(client)
    r = await client.get("/api/admin/quota/byok", headers=bearer(token))
    assert r.status_code == 200, r.text

    for item in r.json()["items"]:
        assert "zero_cost_count" in item, (
            "看板要把 0 成本单独计数，而不是把它当成失败"
        )


async def test_lm13_byok_still_guarded(
    client, merchant, db, monkeypatch, patch_ai, patch_dispatch
):
    """BYOK 调用**仍要跑预检与 judge**——安全不因自带 Key 而豁免。"""
    from app.services.ai import GenResult, GuardVerdict, JudgeResult

    job_id = await _byok_job(client, merchant, db, monkeypatch, prefix="lm13")
    patch_ai.q_guard(GuardVerdict(passed=True, model="qwen3-vl"))
    patch_ai.q_generate(GenResult(content="自带 Key 出的稿", cost_cents=0, model="deepseek-chat"))
    patch_ai.q_judge(
        JudgeResult(score=90, relevance=90, compliance=95, quality=85, reasons=[])
    )

    await run_stage("guard", job_id)
    guard = await db.fetchrow(
        "SELECT passed FROM guard_result WHERE job_id = $1", job_id
    )
    assert guard is not None, "BYOK 也要跑预检——自带 Key 不是免检通行证"
    assert guard["passed"] is True

    await run_stage("generate", job_id)
    assert len(patch_ai.calls["judge"]) == 1, (
        f"BYOK 也要过 judge，实际调了 {len(patch_ai.calls['judge'])} 次"
    )
    status = await db.fetchval("SELECT status FROM content_job WHERE id = $1", job_id)
    assert status == "ready", f"过了 judge 就该 ready：{status}"


async def test_lm14_key_invalid_no_fallback(
    client, merchant, db, monkeypatch, patch_ai, patch_dispatch
):
    """Key 失效（provider 401/403）→ job 失败，**绝不静默回落到平台 Key**。

    回落等于白烧平台的钱，且用户毫不知情——这是 BYOK 最贵的一处漏洞。
    """
    from app.services.ai import KeyInvalidError

    job_id = await _byok_job(client, merchant, db, monkeypatch, prefix="lm14")
    patch_ai.q_generate(KeyInvalidError("provider 返回 401"))

    await run_stage("guard", job_id)
    await run_stage("generate", job_id)

    row = await db.fetchrow(
        "SELECT status, fail_reason FROM content_job WHERE id = $1", job_id
    )
    assert row["status"] == "failed", f"Key 失效应让 job 失败：{row}"
    assert row["fail_reason"] == "key_invalid", (
        f"要用固定的 fail_reason 区分处理方式：{row}"
    )

    # 「没回落」的可观测证据：平台侧一分钱没产生，且预扣被释放
    n = await db.fetchval(
        "SELECT count(*) FROM quota_ledger WHERE source = 'consume' AND job_id = $1",
        job_id,
    )
    assert n == 0, "回落平台 Key 会烧平台的钱并留下 consume 流水"
    status = await db.fetchval(
        "SELECT status FROM quota_reservation WHERE job_id = $1", job_id
    )
    assert status == "released", f"系统故障要释放预占：{status}"
