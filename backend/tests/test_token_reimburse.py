"""07-token · 报销（J 组）。**第二趟。**

对应 test_plan.md 的 `RB-03` / `RB-14` ~ `RB-19`。

报销链路要「审核通过」这个触发点，那是 04 的事（`social_post` → `approved`）。
在 04 落地前，能真测的只有两处：

- **池子在建 job 时就拦住**（`RB-03`）——「不是等报销时才发现池子空了」是这条的要害
- **用户决策前的预览与两端的报销台账**（`RB-14` ~ `RB-19`）——纯读端点，只要
  表在就能测

`RB-01` / `RB-02` / `RB-04` ~ `RB-13` 要造真实的 job + post 才能驱动
`reimburse(post_id)`。届时按 `app.services.quota.reimburse` 接缝补写——
现在写只能靠手工改库再断言改库结果，那种用例实现一行不写也会绿。
"""

import pytest

from tests.helpers import (
    admin_token,
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
    """不真跑后台阶段（理由见 `test_token_limits.py` 同名 fixture）。"""
    from tests.helpers import DispatchStub

    DispatchStub().install(monkeypatch)

POOL_TASK = {
    "pay_mode": "user_pay_reimburse",
    "reimburse_pool": 1000,
    "reimburse_per_user_limit": 500,
}


async def _preview(client, token, task_id):
    return await client.get(
        "/api/me/reimburse-preview",
        headers=bearer(token),
        params={"task_id": task_id},
    )


# --------------------------------------------------------------------------- #
# 池子在建 job 时就拦
# --------------------------------------------------------------------------- #
async def test_rb03_pool_exhausted_at_job(client, merchant, db):
    """池子可用额不足预占 → 建 job 直接 429，**不是**等报销时才发现池子空了。

    注意**不**能用 `reimburse_per_user_limit=0` 来造这个场景：spec 明写
    「0 = 不报销，即纯用户自费」，那是一个合法配置，预占额也是 0，不该被拦。
    池子真被穿的样子是「已报出去的钱把池子占满了」——故这里直接把
    `reimburse_pool_used` 灌满，让可用额归零。
    """
    uid = merchant[0]["id"]
    await quota_account_for(db, uid, balance=100_000)
    task_id = await insert_task(
        db,
        uid,
        pay_mode="user_pay_reimburse",
        reimburse_pool=200,
        reimburse_per_user_limit=200,
        reimburse_pool_used=200,  # 池子已报销满，可用额 = 0
    )
    ids = await seed_customers(db, 1, prefix="rb03")
    tok = token_for(ids[0])
    await claim(client, tok, task_id)

    # 用户自己得有钱垫付，否则先撞上的会是 402，把池子的问题盖住
    await quota_account_for(db, ids[0], balance=100_000)
    await insert_price(db, "deepseek", "deepseek-chat", "chat", max_price_per_call=200)

    r = await client.post(
        "/api/jobs",
        headers=bearer(tok),
        json={"task_id": task_id, "kind": "copy", "assets": assets_of(1)},
    )
    assert r.status_code == 429, (
        f"池子可用额 0、本次要占 200，就该在建 job 时拦住：{r.status_code} {r.text}"
    )


# --------------------------------------------------------------------------- #
# 预览与台账（纯读）
# --------------------------------------------------------------------------- #
async def test_rb14_preview(client, merchant, db):
    uid = merchant[0]["id"]
    await quota_account_for(db, uid, balance=100_000)
    task_id = await insert_task(db, uid, **POOL_TASK)
    ids = await seed_customers(db, 1, prefix="rb14")
    tok = token_for(ids[0])
    await claim(client, tok, task_id)

    r = await _preview(client, tok, task_id)
    assert r.status_code == 200, r.text

    body = r.json()
    for key in (
        "pay_mode",
        "per_user_limit",
        "my_consumed_points",
        "my_reimbursed_points",
        "my_remaining",
        "pool_remaining",
        "est_points",
    ):
        assert key in body, f"预览缺字段 {key}——不给用户看这些就是让他盲赌垫付"

    assert body["pay_mode"] == "user_pay_reimburse"
    assert body["per_user_limit"] == 500
    assert body["pool_remaining"] == 1000


async def test_rb15_preview_not_claimed_403(client, merchant, db):
    uid = merchant[0]["id"]
    await quota_account_for(db, uid, balance=100_000)
    task_id = await insert_task(db, uid, **POOL_TASK)
    ids = await seed_customers(db, 1, prefix="rb15")
    tok = token_for(ids[0])  # 故意不 claim

    r = await _preview(client, tok, task_id)
    assert r.status_code == 403, (
        f"没领过这个任务的人不该看到池子余额（商户经营数据）：{r.status_code} {r.text}"
    )


async def test_rb16_my_reimbursements(client, customer):
    r = await client.get("/api/me/reimbursements", headers=bearer(customer[1]))
    assert r.status_code == 200, r.text

    body = r.json()
    assert "items" in body and "total" in body


async def test_rb17_merchant_reimbursements(client, merchant):
    r = await client.get(
        "/api/merchant/reimbursements", headers=bearer(merchant[1])
    )
    assert r.status_code == 200, r.text

    body = r.json()
    for key in ("items", "total", "page", "size"):
        assert key in body


async def test_rb18_bad_status_422(client, merchant):
    r = await client.get(
        "/api/merchant/reimbursements",
        headers=bearer(merchant[1]),
        params={"status": "banana"},
    )
    assert r.status_code == 422, r.text


async def test_rb19_admin_reimbursements(client, seed_accounts):
    token = await admin_token(client)
    r = await client.get(
        "/api/admin/quota/reimbursements", headers=bearer(token)
    )
    assert r.status_code == 200, r.text

    body = r.json()
    for key in ("items", "total", "page", "size"):
        assert key in body
