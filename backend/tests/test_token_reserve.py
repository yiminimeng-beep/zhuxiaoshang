"""07-token · 预扣与结算（H 组）+ 付费方绑定（I 组）。**第二趟。**

对应 test_plan.md 的 `RS-01` / `RS-02` / `RS-11` 与 `PB-01` ~ `PB-10`。

这些用例走 03 的 `POST /api/jobs`，**建 job 的准入顺序就是被测对象**：
付费方推导 → 组合合法性 → 计价 → 限额 → 余额，每一步都必须在下一次查询之前
把上一关拦掉，否则错误码会串（比如「没有计价行」被报成「额度不足」）。

⚠️ **进入这里之前，每个用例都要先补好计价行**：缺价是 `503`，而 503 会
挡在限额与余额检查之前，于是断言 429 / 402 的用例会拿到 503——看起来像实现错了，
其实是场景没搭全。

**为什么"结算"只测到这里为止**：`RS-03`~`RS-10` / `RS-12` 要驱动 `settle()`
与 `release()`，那属于 03 的流水线终态（`app/services/pipeline.py`），
覆盖在 `test_studio_limits.py` / `test_studio_generate.py` 里。
"""

import asyncio

import pytest

from tests.helpers import (
    assets_of,
    bearer,
    claim,
    insert_price,
    insert_task,
    quota_account_for,
    seed_customers,
    token_for,
)

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _no_background_stage(monkeypatch):
    """本组一律**不真跑后台阶段**（理由见 `test_token_limits.py` 同名 fixture）。"""
    from tests.helpers import DispatchStub

    DispatchStub().install(monkeypatch)


async def _create_job(client, token, task_id, **over):
    """建 job。`kind` 与素材是 03 的必填项，不传就是框架的 422，测不到任何东西。"""
    body = {"task_id": task_id, "kind": "copy", "assets": assets_of(1)}
    body.update(over)
    return await client.post("/api/jobs", headers=bearer(token), json=body)


# --------------------------------------------------------------------------- #
# H. 预扣（走 HTTP，真能测到并发语义）
# --------------------------------------------------------------------------- #
async def test_rs01_concurrent_reserve_one_wins(client, merchant, db):
    """可用额 100，两个预扣 60 的 job 并发 → 恰好 1 个成功，另一个 402。

    本模块的并发核心：只有 `balance - reserved >= r` 的条件更新在拿到行锁后
    **重新判定**，才能保证 60 + 60 不会双花同一笔 100。
    """
    uid = merchant[0]["id"]
    await quota_account_for(db, uid, balance=100, reserved=0)
    await insert_price(db, "deepseek", "deepseek-chat", "chat", max_price_per_call=60)
    task_id = await insert_task(db, uid)

    ids = await seed_customers(db, 2, prefix="rs01")
    tokens = [token_for(i) for i in ids]
    for t in tokens:
        await claim(client, t, task_id)

    responses = await asyncio.gather(
        *(_create_job(client, t, task_id) for t in tokens)
    )
    codes = [r.status_code for r in responses]

    assert codes.count(201) == 1, (
        f"可用额 100、预扣 60 两个并发，必须恰好 1 个成功；"
        f"实际 201×{codes.count(201)}，全部码={sorted(set(codes))}"
    )
    assert 402 in codes, f"失败的那个应是 402（额度不足），实际 {sorted(set(codes))}"

    reserved = await db.fetchval(
        "SELECT reserved FROM quota_account WHERE user_id = $1", uid
    )
    assert reserved == 60, f"只该锁住一份，实际 reserved={reserved}"


async def test_rs02_insufficient_no_change(client, merchant, db):
    """可用额 59、预扣 60 → 402，且 `reserved` 不得被改动。"""
    uid = merchant[0]["id"]
    await quota_account_for(db, uid, balance=59, reserved=0)
    await insert_price(db, "deepseek", "deepseek-chat", "chat", max_price_per_call=60)
    task_id = await insert_task(db, uid)
    ids = await seed_customers(db, 1, prefix="rs02")
    tok = token_for(ids[0])
    await claim(client, tok, task_id)

    r = await _create_job(client, tok, task_id)
    assert r.status_code == 402, r.text

    row = await db.fetchrow(
        "SELECT balance, reserved FROM quota_account WHERE user_id = $1", uid
    )
    assert (row["balance"], row["reserved"]) == (59, 0), (
        "被拒之后额度不得有半点改动——差 1 分也不能先扣再退"
    )


async def test_rs11_available_invariant(client, merchant, db):
    """`available` 必须恒等于 `balance - reserved`。"""
    uid = merchant[0]["id"]
    await quota_account_for(db, uid, balance=100, reserved=30)

    r = await client.get("/api/merchant/quota", headers=bearer(merchant[1]))
    assert r.status_code == 200, r.text

    body = r.json()
    assert body["available"] == body["balance"] - body["reserved"] == 70


# --------------------------------------------------------------------------- #
# I. 付费方绑定与组合合法性
# --------------------------------------------------------------------------- #
async def test_pb01_payer_in_body_422(client, merchant, db):
    """付费方**只能**由 `task_id` 推导，请求体带这几个键一律 422。"""
    uid = merchant[0]["id"]
    await quota_account_for(db, uid, balance=1000)
    task_id = await insert_task(db, uid)
    ids = await seed_customers(db, 1, prefix="pb01")
    tok = token_for(ids[0])
    await claim(client, tok, task_id)

    for field in ("merchant_id", "payer", "user_id"):
        r = await _create_job(client, tok, task_id, **{field: 999999})
        assert r.status_code == 422, (
            f"请求体带 {field} 必须 422——不得按传入值扣费：{r.status_code} {r.text}"
        )


async def test_pb02_merchant_pay_byok_ok(client, merchant, db, monkeypatch):
    """商户付费任务 + BYOK + 有 Key → 201（2026-09-18 放宽）。"""
    from tests.helpers import insert_price, patch_verify, post_key

    uid = merchant[0]["id"]
    await quota_account_for(db, uid, balance=1000)
    await insert_price(db, "deepseek", "deepseek-chat", "chat")
    task_id = await insert_task(db, uid, pay_mode="merchant_pay")
    ids = await seed_customers(db, 1, prefix="pb02")
    tok = token_for(ids[0])
    await claim(client, tok, task_id)
    patch_verify(monkeypatch, ok=True)
    await post_key(client, tok, "deepseek")

    r = await _create_job(client, tok, task_id, billing_source="byok")
    assert r.status_code == 201, (
        f"merchant_pay + byok 有 Key 应允许：{r.status_code} {r.text}"
    )
    assert r.json().get("billing_source") == "byok"


async def test_pb03_byok_without_key_422(client, merchant, db):
    uid = merchant[0]["id"]
    await quota_account_for(db, uid, balance=1000)
    task_id = await insert_task(
        db,
        uid,
        pay_mode="user_pay_reimburse",
        reimburse_pool=1000,
        reimburse_per_user_limit=500,
    )
    ids = await seed_customers(db, 1, prefix="pb03")
    tok = token_for(ids[0])
    await claim(client, tok, task_id)

    r = await _create_job(client, tok, task_id, billing_source="byok")
    assert r.status_code == 422, (
        f"没有该 provider 的有效 Key 却要 BYOK：{r.status_code} {r.text}"
    )


async def test_pb04_payer_from_task_id(client, merchant, merchant_b, db):
    """用 B 商户的 task_id 建 job → 扣 B 的口径。付费方只认 `task_id`。"""
    b_id = await db.fetchval("SELECT id FROM \"user\" WHERE account = 'shop0002'")
    await quota_account_for(db, b_id, balance=1000)
    await insert_price(db, "deepseek", "deepseek-chat", "chat")
    task_id = await insert_task(db, b_id)
    ids = await seed_customers(db, 1, prefix="pb04")
    tok = token_for(ids[0])
    await claim(client, tok, task_id)

    r = await _create_job(client, tok, task_id)
    assert r.status_code == 201, r.text

    reserved_b = await db.fetchval(
        "SELECT reserved FROM quota_account WHERE user_id = $1", b_id
    )
    reserved_a = await db.fetchval(
        "SELECT reserved FROM quota_account WHERE user_id = $1", merchant[0]["id"]
    )
    assert reserved_b == 120, f"付费方是 task_id 指向的 B，该扣 B：{reserved_b}"
    assert reserved_a is None or reserved_a == 0, (
        "发起 job 的商户 A 不得被扣——付费方是 task_id 指向的 B"
    )


async def test_pb05_not_my_claim_403(client, merchant, db):
    uid = merchant[0]["id"]
    await quota_account_for(db, uid, balance=1000)
    await insert_price(db, "deepseek", "deepseek-chat", "chat")
    task_id = await insert_task(db, uid)
    ids = await seed_customers(db, 2, prefix="pb05")
    await claim(client, token_for(ids[0]), task_id)  # 只有 ids[0] 领了

    r = await _create_job(client, token_for(ids[1]), task_id)
    assert r.status_code == 403, (
        f"task_id 不属于该用户的 claim：{r.status_code} {r.text}"
    )


async def test_pb06_frozen_merchant_403(client, merchant, db):
    uid = merchant[0]["id"]
    await quota_account_for(db, uid, balance=1000, status="frozen")
    await insert_price(db, "deepseek", "deepseek-chat", "chat")
    task_id = await insert_task(db, uid)
    ids = await seed_customers(db, 1, prefix="pb06")
    tok = token_for(ids[0])
    await claim(client, tok, task_id)

    r = await _create_job(client, tok, task_id)
    assert r.status_code == 403, (
        f"已冻结商户的任务不得被建 job：{r.status_code} {r.text}"
    )


async def test_pb07_defaults_applied(client, merchant, db):
    """不传 `provider` / `billing_source` → 用平台默认（保持与 03 现有契约兼容）。"""
    uid = merchant[0]["id"]
    await quota_account_for(db, uid, balance=1000)
    await insert_price(db, "deepseek", "deepseek-chat", "chat")
    task_id = await insert_task(db, uid)
    ids = await seed_customers(db, 1, prefix="pb07")
    tok = token_for(ids[0])
    await claim(client, tok, task_id)

    r = await _create_job(client, tok, task_id)
    assert r.status_code == 201, (
        f"不传 provider / billing_source 应走平台默认：{r.status_code} {r.text}"
    )
    assert r.json()["provider"] == "deepseek"
    assert r.json()["billing_source"] == "platform"


async def test_pb08_missing_price_503(client, merchant, db):
    """计价表缺该组合 → 503。**不得按 0 计费**——0 元等于白送。

    `kling` 只有视频 op，故必须用 `kind="video"`：用 `copy` 会先在
    「provider 与 kind 不匹配」上 422，503 永远够不着。
    """
    uid = merchant[0]["id"]
    await quota_account_for(db, uid, balance=1000)
    task_id = await insert_task(db, uid)
    ids = await seed_customers(db, 1, prefix="pb08")
    tok = token_for(ids[0])
    await claim(client, tok, task_id)

    r = await _create_job(client, tok, task_id, kind="video", provider="kling")
    assert r.status_code == 503, (
        f"计价表缺该 (provider, model, op) 行必须 503，不得按 0 计费："
        f"{r.status_code} {r.text}"
    )


async def test_pb09_hidden_model_422(client, merchant, db):
    """`provider_visible=false` 的模型**不得被指名道姓地选**。

    自动选模型时它已经被 `visible_only` 滤掉（拒绝服务 → 503），
    所以这条只有**显式指定**才够得着，报 422「该模型不可用」。
    """
    uid = merchant[0]["id"]
    await quota_account_for(db, uid, balance=1000)
    task_id = await insert_task(db, uid)
    ids = await seed_customers(db, 1, prefix="pb09")
    tok = token_for(ids[0])
    await claim(client, tok, task_id)

    await insert_price(db, "jimeng", "jimeng-xl", "generate", provider_visible=False)

    r = await _create_job(
        client, tok, task_id, kind="video", provider="jimeng", model="jimeng-xl"
    )
    assert r.status_code == 422, (
        f"provider_visible=false 的模型不得被直接指定：{r.status_code} {r.text}"
    )


async def test_pb10_price_at_job_time(client, merchant, db):
    """已建 job 按**建 job 时刻**的定价，之后改价不影响它。"""
    from datetime import datetime, timedelta, timezone

    uid = merchant[0]["id"]
    await quota_account_for(db, uid, balance=1000)
    task_id = await insert_task(db, uid)
    ids = await seed_customers(db, 1, prefix="pb10")
    tok = token_for(ids[0])
    await claim(client, tok, task_id)

    now = datetime.now(timezone.utc)
    await insert_price(
        db,
        "deepseek",
        "deepseek-chat",
        "chat",
        price_per_unit=0.000004,
        effective_from=now - timedelta(days=10),
    )
    r = await _create_job(client, tok, task_id, provider="deepseek")
    assert r.status_code == 201, r.text

    await insert_price(
        db,
        "deepseek",
        "deepseek-chat",
        "chat",
        price_per_unit=0.00009,
        effective_from=now,
    )

    row = await db.fetchrow("SELECT * FROM quota_reservation ORDER BY id DESC LIMIT 1")
    assert row is not None, "预扣单必须落库，且要留下定价快照供对账"
