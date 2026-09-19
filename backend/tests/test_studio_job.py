"""03-studio · 建 job 与素材校验。

对应 test_plan.md：`JB-01` ~ `JB-27`

这一组钉的是「**入口的准入与计费**」：素材合法性、归属、付费方推导、预扣落单。
真正的 AI 链路在 `test_studio_guard.py` 之后。
"""

import pytest

from tests.helpers import (
    MAX_ASSET_BYTES,
    assets_of,
    bearer,
    create_job,
    create_job_ok,
    grant,
    insert_price,
    published_task,
    quota_account_for,
)

pytestmark = pytest.mark.asyncio


async def test_jb01_create_job(client, studio, patch_dispatch):
    body = await create_job_ok(
        client, studio.customer_token, studio.task_id, studio.claim_id
    )
    assert body["status"] == "guarding", f"建 job 应立即进入 guarding：{body}"
    for key in ("job_id", "provider", "billing_source", "reserved_points"):
        assert key in body, f"建 job 响应缺 {key}：{body}"
    assert body["reserved_points"] > 0, "平台付费的 job 必须真预扣"


async def test_jb02_no_asset_422(client, studio, patch_dispatch):
    r = await create_job(
        client, studio.customer_token, studio.task_id, studio.claim_id, assets=[]
    )
    assert r.status_code == 422, r.text


async def test_jb03_ten_assets_422(client, studio, patch_dispatch):
    r = await create_job(
        client,
        studio.customer_token,
        studio.task_id,
        studio.claim_id,
        assets=assets_of(10),
    )
    assert r.status_code == 422, r.text


async def test_jb04_nine_assets_ok(client, studio, patch_dispatch):
    r = await create_job(
        client,
        studio.customer_token,
        studio.task_id,
        studio.claim_id,
        assets=assets_of(9),
    )
    assert r.status_code == 201, f"9 张是上限内的合法值：{r.status_code} {r.text}"


async def test_jb05_pdf_415(client, studio, patch_dispatch):
    r = await create_job(
        client,
        studio.customer_token,
        studio.task_id,
        studio.claim_id,
        assets=assets_of(1, mime="application/pdf"),
    )
    assert r.status_code == 415, r.text


async def test_jb06_gif_415(client, studio, patch_dispatch):
    r = await create_job(
        client,
        studio.customer_token,
        studio.task_id,
        studio.claim_id,
        assets=assets_of(1, mime="image/gif"),
    )
    assert r.status_code == 415, r.text


async def test_jb07_exactly_20mb_ok(client, studio, patch_dispatch):
    r = await create_job(
        client,
        studio.customer_token,
        studio.task_id,
        studio.claim_id,
        assets=assets_of(1, size_bytes=MAX_ASSET_BYTES),
    )
    assert r.status_code == 201, f"20MB 整必须放行（上限含等号）：{r.status_code} {r.text}"


async def test_jb08_over_20mb_413(client, studio, patch_dispatch):
    r = await create_job(
        client,
        studio.customer_token,
        studio.task_id,
        studio.claim_id,
        assets=assets_of(1, size_bytes=MAX_ASSET_BYTES + 1),
    )
    assert r.status_code == 413, r.text


async def test_jb09_not_claimed_403(client, merchant, customer, db, patch_dispatch):
    """没领过任务就建 job → 403。"""
    merchant_user, merchant_token, _ = merchant
    task = await published_task(client, merchant_token)
    await grant(db, merchant_user["id"], 100_000)
    await insert_price(db, "deepseek", "deepseek-chat", "chat")

    _, customer_token, _ = customer
    r = await create_job(client, customer_token, task["id"])
    assert r.status_code == 403, f"未领取不得建 job：{r.status_code} {r.text}"


async def test_jb10_others_claim_403(client, studio, merchant_b, db, patch_dispatch):
    """拿别人的 claim_id 建 job → 403。"""
    from tests.helpers import login_ok

    await login_ok(client, "shop0002", "pass1234", fp="fp-b")
    other_user_id = await db.fetchval(
        "SELECT id FROM \"user\" WHERE account = 'shop0002'"
    )

    # 直造一条属于别人的 claim
    other_claim = await db.fetchval(
        """
        INSERT INTO task_claim (task_id, user_id, status, claimed_at)
        VALUES ($1, $2, 'in_progress', now()) RETURNING id
        """,
        studio.task_id,
        other_user_id,
    )
    r = await create_job(
        client, studio.customer_token, studio.task_id, other_claim
    )
    assert r.status_code == 403, f"不得用他人 claim 建 job：{r.status_code} {r.text}"


async def test_jb11_bad_kind_422(client, studio, patch_dispatch):
    r = await create_job(
        client, studio.customer_token, studio.task_id, studio.claim_id, kind="audio"
    )
    assert r.status_code == 422, r.text


async def test_jb12_payer_in_body_422(client, studio, db, patch_dispatch):
    """请求体带付费方字段 → 422，**且不得按传入值扣费**。"""
    await create_job_ok(client, studio.customer_token, studio.task_id, studio.claim_id)

    before = await db.fetchval(
        "SELECT balance FROM quota_account WHERE user_id = $1", studio.merchant["id"]
    )
    r = await create_job(
        client,
        studio.customer_token,
        studio.task_id,
        studio.claim_id,
        merchant_id=studio.merchant["id"],
        payer=studio.customer["id"],
        user_id=studio.customer["id"],
    )
    assert r.status_code == 422, f"付费方只由 task_id 推导：{r.status_code} {r.text}"

    after = await db.fetchval(
        "SELECT balance FROM quota_account WHERE user_id = $1", studio.merchant["id"]
    )
    assert after == before, "被拒的请求不得动账"


async def test_jb13_frozen_merchant_403(client, studio, db, patch_dispatch):
    await quota_account_for(db, studio.merchant["id"], balance=100_000, status="frozen")
    r = await create_job(
        client, studio.customer_token, studio.task_id, studio.claim_id
    )
    assert r.status_code == 403, f"冻结商户的任务不得建 job：{r.status_code} {r.text}"


async def test_jb14_missing_price_503(client, studio, db, patch_dispatch):
    """计价表缺该组合 → 503，**不得按 0 计费**。"""
    await db.execute("DELETE FROM model_price WHERE op = 'chat'")
    r = await create_job(
        client, studio.customer_token, studio.task_id, studio.claim_id
    )
    assert r.status_code == 503, f"缺价必须拒绝而不是免费：{r.status_code} {r.text}"


async def test_jb15_insufficient_402_no_job(client, studio, db, patch_dispatch):
    """付款方可用额 < 预扣上界 → 402，且 job 不得创建。"""
    await quota_account_for(db, studio.merchant["id"], balance=1)

    r = await create_job(
        client, studio.customer_token, studio.task_id, studio.claim_id
    )
    assert r.status_code == 402, f"额度不足应 402：{r.status_code} {r.text}"

    count = await db.fetchval(
        "SELECT count(*) FROM content_job WHERE task_id = $1", studio.task_id
    )
    assert count == 0, "402 之后不得留下 job 行（不得先扣再校验）"
    reserved = await db.fetchval(
        "SELECT reserved FROM quota_account WHERE user_id = $1", studio.merchant["id"]
    )
    assert reserved == 0, "被拒的请求不得改动 reserved"


async def test_jb16_unknown_provider_422(client, studio, patch_dispatch):
    r = await create_job(
        client,
        studio.customer_token,
        studio.task_id,
        studio.claim_id,
        provider="openai",
    )
    assert r.status_code == 422, r.text


async def test_jb17_hidden_provider_422(client, studio, db, patch_dispatch):
    await insert_price(
        db, "deepseek", "deepseek-chat-hidden", "chat", provider_visible=False
    )
    # 隐藏行生效更晚，故必须显式指定才可能被选中
    r = await create_job(
        client,
        studio.customer_token,
        studio.task_id,
        studio.claim_id,
        provider="deepseek",
        model="deepseek-chat-hidden",
    )
    assert r.status_code == 422, f"不可见模型不得被直接指定：{r.status_code} {r.text}"


async def test_jb18_provider_kind_mismatch_422(client, studio, patch_dispatch):
    """拿文案厂商出视频 → 422。"""
    r = await create_job(
        client,
        studio.customer_token,
        studio.task_id,
        studio.claim_id,
        kind="video",
        provider="deepseek",
        model="deepseek-chat",
    )
    assert r.status_code == 422, r.text


async def test_jb19_byok_on_merchant_pay_ok(
    client, studio, db, patch_dispatch, monkeypatch
):
    """`merchant_pay` 下传 byok + 有 Key → 201（2026-09-18 放宽）。"""
    from tests.helpers import patch_verify, post_key

    patch_verify(monkeypatch, ok=True)
    await post_key(client, studio.customer_token, "deepseek")
    r = await create_job(
        client,
        studio.customer_token,
        studio.task_id,
        studio.claim_id,
        billing_source="byok",
    )
    assert r.status_code == 201, f"merchant_pay 下 BYOK 应允许：{r.status_code} {r.text}"
    assert r.json().get("billing_source") == "byok"


async def test_jb20_byok_without_key_422(client, db, patch_dispatch):
    """`user_pay_reimburse` 下传 byok 但没存 Key → 422。"""
    _, _, task = await _reimburse_scene(client, db, pool=1000)
    _, cust_token = await _register_and_login(client, db, "cust9001")

    claim = await client.post(
        f"/api/tasks/{task['id']}/claim", headers=bearer(cust_token)
    )
    claim_id = claim.json()["claim"]["id"]

    r = await create_job(
        client, cust_token, task["id"], claim_id, billing_source="byok"
    )
    assert r.status_code == 422, f"无 Key 不得 BYOK：{r.status_code} {r.text}"


async def test_jb21_defaults_applied(client, studio, db, patch_dispatch):
    body = await create_job_ok(
        client, studio.customer_token, studio.task_id, studio.claim_id
    )
    row = await db.fetchrow(
        "SELECT provider, billing_source FROM content_job WHERE id = $1",
        body["job_id"],
    )
    assert row["provider"], "必须落一个 provider"
    assert row["billing_source"] == "platform", (
        f"不传 billing_source 应为平台默认：{row['billing_source']}"
    )


async def test_jb22_claim_task_bound(client, studio, db, patch_dispatch):
    body = await create_job_ok(
        client, studio.customer_token, studio.task_id, studio.claim_id
    )
    row = await db.fetchrow(
        "SELECT task_id, claim_id, user_id, kind FROM content_job WHERE id = $1",
        body["job_id"],
    )
    assert row["task_id"] == studio.task_id, "task_id 必须与请求一致"
    assert row["claim_id"] == studio.claim_id, "claim_id 必须落到本人的那条领取上"
    assert row["user_id"] == studio.customer["id"], "job 归属必须是当前用户"
    assert row["kind"] == "copy"


async def test_jb23_reservation_not_ledger(client, studio, db, patch_dispatch):
    """预扣只增 `reserved` 并落一张预扣单，**不写流水**。"""
    body = await create_job_ok(
        client, studio.customer_token, studio.task_id, studio.claim_id
    )

    n = await db.fetchval(
        "SELECT count(*) FROM quota_reservation WHERE job_id = $1", body["job_id"]
    )
    assert n == 1, f"建 job 必须恰好产生 1 张预扣单，实际 {n}"

    ledgers = await db.fetchval(
        "SELECT count(*) FROM quota_ledger WHERE job_id = $1", body["job_id"]
    )
    assert ledgers == 0, "预扣不得写流水（结算才写）"

    reserved = await db.fetchval(
        "SELECT reserved FROM quota_account WHERE user_id = $1", studio.merchant["id"]
    )
    assert reserved == body["reserved_points"], (
        f"预扣额须与响应的 reserved_points 一致：{reserved} vs {body['reserved_points']}"
    )


# --------------------------------------------------------------------------- #
# 报销池（user_pay_reimburse）
# --------------------------------------------------------------------------- #
async def _reimburse_scene(client, db, pool=1000, per_user=200, balance=100_000):
    """造一个 `user_pay_reimburse` 任务并由客户领取。

    计价行要自己补：本场景不走 `studio` fixture，没有价就只能拿到 503，
    429 / 池子预占这两条根本走不到。
    """
    from tests.helpers import create_task_ok, publish_task

    await insert_price(db, "deepseek", "deepseek-chat", "chat")

    r = await client.post(
        "/api/auth/register",
        json={
            "account": "shop9001",
            "password": "pass1234",
            "role": "merchant",
            "nickname": "报销商户",
            "shop_name": "报销店铺",
            "category": "餐饮",
        },
    )
    assert r.status_code == 201, r.text
    merchant_id = r.json()["user"]["id"]
    merchant_token = r.json()["access_token"]

    await grant(db, merchant_id, balance)

    task = await create_task_ok(
        client,
        merchant_token,
        pay_mode="user_pay_reimburse",
        reimburse_pool=pool,
        reimburse_per_user_limit=per_user,
    )
    pub = await publish_task(client, merchant_token, task["id"])
    assert pub.status_code == 200, f"发布失败：{pub.status_code} {pub.text}"
    return merchant_id, merchant_token, task


async def _register_and_login(client, db, account="cust9001", balance=100_000):
    """注册客户并给额度。

    `user_pay_reimburse` 是**用户垫付**（spec「额度扣费」：扣用户额度垫付，
    同时预占商户报销池）——用户账户没钱的话，池子检查根本轮不到。
    """
    r = await client.post(
        "/api/auth/register",
        json={
            "account": account,
            "password": "pass1234",
            "role": "customer",
            "nickname": account,
        },
    )
    assert r.status_code == 201, r.text
    user_id, token = r.json()["user"]["id"], r.json()["access_token"]
    if balance > 0:
        await grant(db, user_id, balance)
    return user_id, token


async def test_jb24_pool_exhausted_429(client, db, patch_dispatch):
    """池子可用额不足预占 → 429（**不是等报销时才发现**）。

    池子要**被前一个 job 吃掉一部分**才可能不够：单次预占是
    `min(该 job 的计价上界, reimburse_per_user_limit)`，而
    `reimburse_per_user_limit <= reimburse_pool` 是 02 的硬校验，
    于是**第一个 job 永远吃得下**（`min(a,b) <= b <= pool == 剩余`）。
    所以这里先建一个，第二个才撞上限。
    """
    _, _, task = await _reimburse_scene(client, db, pool=200, per_user=200)
    _, cust_token = await _register_and_login(client, db, "cust9001")

    claim = await client.post(
        f"/api/tasks/{task['id']}/claim", headers=bearer(cust_token)
    )
    assert claim.status_code == 201, claim.text
    claim_id = claim.json()["claim"]["id"]

    first = await create_job(client, cust_token, task["id"], claim_id)
    assert first.status_code == 201, f"第一个 job 应吃得下池子：{first.text}"

    r = await create_job(client, cust_token, task["id"], claim_id)
    assert r.status_code == 429, (
        f"池子不够必须在建 job 时就拒：{r.status_code} {r.text}"
    )

    n = await db.fetchval(
        "SELECT count(*) FROM content_job WHERE task_id = $1", task["id"]
    )
    assert n == 1, "429 之后不得多出 job 行"


async def test_jb25_pool_reserved_on_create(client, db, patch_dispatch):
    """池子充足 → 建 job 时真占池子。"""
    _, _, task = await _reimburse_scene(client, db, pool=1000)
    _, cust_token = await _register_and_login(client, db, "cust9001")

    claim = await client.post(
        f"/api/tasks/{task['id']}/claim", headers=bearer(cust_token)
    )
    claim_id = claim.json()["claim"]["id"]

    body = await create_job_ok(client, cust_token, task["id"], claim_id)
    assert body["reimburse_reserved"] > 0, (
        f"user_pay_reimburse 必须占报销池：{body}"
    )

    reserved = await db.fetchval(
        "SELECT reimburse_pool_reserved FROM task WHERE id = $1", task["id"]
    )
    assert reserved == body["reimburse_reserved"], (
        f"任务上的池子预占须与响应一致：{reserved} vs {body['reimburse_reserved']}"
    )


async def test_jb26_no_token_401(client, studio):
    r = await client.post("/api/jobs", json={"task_id": 1, "kind": "copy"})
    assert r.status_code == 401, r.text


async def test_jb27_multi_job_per_claim_ok(client, studio, patch_dispatch):
    """spec 未限制「一 claim 一 job」，故第二个应放行（有意行为，非遗漏）。"""
    await create_job_ok(client, studio.customer_token, studio.task_id, studio.claim_id)
    r = await create_job(
        client, studio.customer_token, studio.task_id, studio.claim_id
    )
    assert r.status_code == 201, (
        f"spec 未禁止一 claim 多 job，若这里红了请先确认产品口径：{r.status_code} {r.text}"
    )
