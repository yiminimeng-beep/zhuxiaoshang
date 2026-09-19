"""03-studio · prompt 模板（商户预设 / 模板市场）。

对应 test_plan.md：`TP-01` ~ `TP-29`

可见性三条规则是本组的骨架：公开 → 所有商户；私有 → 创建者商户 + 该商户任务下的客户；
软删 → 任何人不可见，**但已复制的副本不受影响**（复制是「拿走一份」，不是「挂个引用」）。
"""

import asyncio

import pytest

from tests.helpers import (
    assert_route_registered,
    bearer,
    insert_job,
    insert_template,
    login_ok,
    register_customer,
)

pytestmark = pytest.mark.asyncio

GOOD = {
    "name": "夏日奶茶模板",
    "content": "写一段温暖的奶茶店文案，突出当季新品与到店体验",
}


async def _post(client, token, **over):
    body = dict(GOOD)
    body.update(over)
    return await client.post(
        "/api/merchant/prompt-templates", headers=bearer(token), json=body
    )


async def _ids(items):
    return [i["id"] for i in items]


async def _market_ids(client, token):
    r = await client.get("/api/prompt-templates", headers=bearer(token))
    assert r.status_code == 200, r.text
    return await _ids(r.json()["items"])


# --------------------------------------------------------------------------- #
# 建 / 改 / 删
# --------------------------------------------------------------------------- #
async def test_tp01_create(client, studio):
    r = await _post(client, studio.merchant_token)
    assert r.status_code == 201, r.text
    tpl = r.json()["template"]
    assert tpl["usage_count"] == 0, f"新模板使用数为 0：{tpl}"
    assert tpl["is_public"] is False, f"默认不进市场（商户自己决定）：{tpl}"


async def test_tp02_name_1_422(client, studio):
    r = await _post(client, studio.merchant_token, name="短")
    assert r.status_code == 422, r.text


async def test_tp03_name_65_422(client, studio):
    r = await _post(client, studio.merchant_token, name="奶" * 65)
    assert r.status_code == 422, r.text


async def test_tp04_name_bounds_ok(client, studio):
    assert (await _post(client, studio.merchant_token, name="两字")).status_code == 201
    r = await _post(client, studio.merchant_token, name="奶" * 64)
    assert r.status_code == 201, f"64 字是上限内的合法值：{r.status_code} {r.text}"


async def test_tp05_content_9_422(client, studio):
    r = await _post(client, studio.merchant_token, content="奶" * 9)
    assert r.status_code == 422, r.text


async def test_tp06_content_bounds_ok(client, studio):
    assert (
        await _post(client, studio.merchant_token, content="奶" * 10)
    ).status_code == 201
    r = await _post(client, studio.merchant_token, content="奶" * 2000)
    assert r.status_code == 201, f"2000 字是上限内的合法值：{r.status_code} {r.text}"


async def test_tp07_content_2001_422(client, studio):
    r = await _post(client, studio.merchant_token, content="奶" * 2001)
    assert r.status_code == 422, r.text


async def test_tp08_tags_6_422(client, studio):
    r = await _post(
        client, studio.merchant_token, tags=[f"标签{i}" for i in range(6)]
    )
    assert r.status_code == 422, r.text


async def test_tp09_tag_len_422(client, studio):
    r = await _post(client, studio.merchant_token, tags=["奶" * 17])
    assert r.status_code == 422, f"单个标签 17 字超限：{r.status_code} {r.text}"
    r = await _post(client, studio.merchant_token, tags=["奶" * 16])
    assert r.status_code == 201, f"16 字是上限内的合法值：{r.status_code} {r.text}"


async def test_tp10_customer_403(client, studio):
    r = await _post(client, studio.customer_token)
    assert r.status_code == 403, f"客户不得建模板：{r.status_code} {r.text}"


async def test_tp11_patch_others_403(client, studio, merchant_b, db):
    tpl_id = await insert_template(db, studio.merchant["id"], name="我的模板")
    r = await client.patch(
        f"/api/merchant/prompt-templates/{tpl_id}",
        headers=bearer(merchant_b),
        json={"name": "抢过来"},
    )
    assert r.status_code == 403, f"不得改别人的模板：{r.status_code} {r.text}"

    r = await client.patch(
        f"/api/merchant/prompt-templates/{tpl_id}",
        headers=bearer(studio.merchant_token),
        json={"name": "改自己的"},
    )
    assert r.status_code == 200, f"改自己的应放行：{r.status_code} {r.text}"


async def test_tp12_delete_others_403(client, studio, merchant_b, db):
    tpl_id = await insert_template(db, studio.merchant["id"])
    r = await client.delete(
        f"/api/merchant/prompt-templates/{tpl_id}", headers=bearer(merchant_b)
    )
    assert r.status_code == 403, r.text

    deleted = await db.fetchval(
        "SELECT deleted_at FROM prompt_template WHERE id = $1", tpl_id
    )
    assert deleted is None, "被拒的删除不得留下痕迹"


async def test_tp13_unknown_404(client, studio):
    assert_route_registered("PATCH", "/api/merchant/prompt-templates/{template_id}")
    assert_route_registered("DELETE", "/api/merchant/prompt-templates/{template_id}")

    r = await client.patch(
        "/api/merchant/prompt-templates/999999",
        headers=bearer(studio.merchant_token),
        json={"name": "不存在"},
    )
    assert r.status_code == 404, r.text

    r = await client.delete(
        "/api/merchant/prompt-templates/999999", headers=bearer(studio.merchant_token)
    )
    assert r.status_code == 404, r.text


async def _shop0002_id(db) -> int:
    """`merchant_b` fixture 只给令牌，直插模板需要它的 user id。"""
    uid = await db.fetchval("SELECT id FROM \"user\" WHERE account = 'shop0002'")
    assert uid is not None, "merchant_b fixture 应该已经建好 shop0002"
    return uid


async def test_tp14_my_templates(client, studio, merchant_b, db):
    mine = await insert_template(db, studio.merchant["id"], name="私有模板")
    pub = await insert_template(db, studio.merchant["id"], name="公开模板", is_public=True)
    await insert_template(db, await _shop0002_id(db), name="别人的模板")

    r = await client.get(
        "/api/merchant/prompt-templates", headers=bearer(studio.merchant_token)
    )
    assert r.status_code == 200, r.text
    body = r.json()
    for key in ("items", "total", "page", "size"):
        assert key in body, f"列表缺 {key}：{list(body)}"
    ids = await _ids(body["items"])
    assert set(ids) == {mine, pub}, f"只含自己的（私有 + 公开都在）：{ids}"


# --------------------------------------------------------------------------- #
# 可见性三条规则
# --------------------------------------------------------------------------- #
async def test_tp15_public_in_market(client, studio, db):
    pub = await insert_template(db, studio.merchant["id"], is_public=True)
    assert pub in await _market_ids(client, studio.customer_token)


async def test_tp16_private_not_in_market(client, studio, db):
    private = await insert_template(db, studio.merchant["id"], is_public=False)
    assert private not in await _market_ids(client, studio.customer_token)


async def test_tp17_private_visible_to_claimer(client, studio, db):
    """商户私有模板 → 该商户任务下的领取者能套用。"""
    private = await insert_template(db, studio.merchant["id"], is_public=False)

    r = await client.get(
        f"/api/tasks/{studio.task_id}/prompt-templates",
        headers=bearer(studio.customer_token),
    )
    assert r.status_code == 200, r.text
    assert private in await _ids(r.json()["items"]), (
        f"领取者应看到该商户的私有模板：{r.json()['items']}"
    )


async def test_tp18_other_merchant_private_hidden(client, studio, merchant_b, db):
    """别的商户看不到 A 的私有模板——拒掉（403）也算「看不到」。"""
    private = await insert_template(db, studio.merchant["id"], is_public=False)

    r = await client.get(
        f"/api/tasks/{studio.task_id}/prompt-templates", headers=bearer(merchant_b)
    )
    if r.status_code == 403:
        return
    assert r.status_code == 200, r.text
    assert private not in await _ids(r.json()["items"]), (
        f"B 不是该任务的商户也不是领取者，不该看到 A 的私有模板：{r.json()['items']}"
    )


async def test_tp19_not_claimed_403(client, studio):
    await register_customer(client, account="cust9002")
    other = await login_ok(client, "cust9002", "pass1234", fp="fp-x")

    r = await client.get(
        f"/api/tasks/{studio.task_id}/prompt-templates",
        headers=bearer(other["access_token"]),
    )
    assert r.status_code == 403, f"没领任务不得查其可用模板：{r.status_code} {r.text}"


# --------------------------------------------------------------------------- #
# 复制
# --------------------------------------------------------------------------- #
async def test_tp20_copy_public(client, studio, merchant_b, db):
    src = await insert_template(db, studio.merchant["id"], is_public=True, usage_count=7)

    r = await client.post(
        f"/api/merchant/prompt-templates/{src}/copy", headers=bearer(merchant_b)
    )
    assert r.status_code == 201, r.text
    tpl = r.json()["template"]
    assert tpl["is_public"] is False, "复制来的副本默认私有（避免自己转手又公开）"
    assert tpl["source_template_id"] == src, f"要留溯源：{tpl}"
    assert tpl["usage_count"] == 0, f"副本从 0 开始，不继承原模板的使用数：{tpl}"


async def test_tp21_copy_private_404(client, studio, merchant_b, db):
    src = await insert_template(db, studio.merchant["id"], is_public=False)
    r = await client.post(
        f"/api/merchant/prompt-templates/{src}/copy", headers=bearer(merchant_b)
    )
    assert r.status_code == 404, f"私有模板不可复制：{r.status_code} {r.text}"


async def test_tp22_copy_deleted_404(client, studio, merchant_b, db):
    from datetime import datetime, timezone

    src = await insert_template(
        db,
        studio.merchant["id"],
        is_public=True,
        deleted_at=datetime.now(timezone.utc),
    )
    r = await client.post(
        f"/api/merchant/prompt-templates/{src}/copy", headers=bearer(merchant_b)
    )
    assert r.status_code == 404, f"已软删的模板不可复制：{r.status_code} {r.text}"


async def test_tp23_copy_survives_source_delete(client, studio, merchant_b, db):
    """复制是「拿走一份」：原模板被删，副本照样能用。"""
    src = await insert_template(db, studio.merchant["id"], is_public=True)
    r = await client.post(
        f"/api/merchant/prompt-templates/{src}/copy", headers=bearer(merchant_b)
    )
    copy_id = r.json()["template"]["id"]

    d = await client.delete(
        f"/api/merchant/prompt-templates/{src}",
        headers=bearer(studio.merchant_token),
    )
    assert d.status_code == 204, d.text

    r = await client.get("/api/merchant/prompt-templates", headers=bearer(merchant_b))
    assert copy_id in await _ids(r.json()["items"]), (
        "副本不得被级联删除"
    )


# --------------------------------------------------------------------------- #
# 套用
# --------------------------------------------------------------------------- #
async def _chatting_job(db, studio, **over):
    row = {"status": "chatting"}
    row.update(over)
    return await insert_job(
        db, studio.task_id, studio.claim_id, studio.customer["id"], **row
    )


async def test_tp24_apply_bumps_usage(client, studio, db):
    tpl = await insert_template(db, studio.merchant["id"], is_public=True)
    job_id = await _chatting_job(db, studio)

    r = await client.post(
        f"/api/prompt-templates/{tpl}/apply",
        headers=bearer(studio.customer_token),
        json={"job_id": job_id},
    )
    assert r.status_code == 200, r.text
    assert r.json()["content"], "套用要返回模板内容"

    usage = await db.fetchval(
        "SELECT usage_count FROM prompt_template WHERE id = $1", tpl
    )
    assert usage == 1, f"使用数应恰好 +1：{usage}"


async def test_tp25_concurrent_apply_atomic(client, studio, db):
    """100 并发套用 → 恰好 +100（原子自增，不得丢更新）。"""
    tpl = await insert_template(db, studio.merchant["id"], is_public=True)
    job_id = await _chatting_job(db, studio)

    results = await asyncio.gather(
        *[
            client.post(
                f"/api/prompt-templates/{tpl}/apply",
                headers=bearer(studio.customer_token),
                json={"job_id": job_id},
            )
            for _ in range(100)
        ]
    )
    codes = {r.status_code for r in results}
    assert codes == {200}, f"100 次都该成功：{codes}"

    usage = await db.fetchval(
        "SELECT usage_count FROM prompt_template WHERE id = $1", tpl
    )
    assert usage == 100, (
        f"读改写的实现会丢更新（典型只到 30~70）：实际 {usage}"
    )


async def test_tp26_apply_forbidden_403(client, studio, merchant_b, db):
    """商户 B 的私有模板，A 任务下的客户无权套用。"""
    tpl = await insert_template(db, await _shop0002_id(db), is_public=False)
    job_id = await _chatting_job(db, studio)

    r = await client.post(
        f"/api/prompt-templates/{tpl}/apply",
        headers=bearer(studio.customer_token),
        json={"job_id": job_id},
    )
    assert r.status_code == 403, f"无权使用的模板应 403：{r.status_code} {r.text}"


async def test_tp27_apply_bad_status_409(client, studio, db):
    tpl = await insert_template(db, studio.merchant["id"], is_public=True)
    job_id = await _chatting_job(db, studio, status="generating")

    r = await client.post(
        f"/api/prompt-templates/{tpl}/apply",
        headers=bearer(studio.customer_token),
        json={"job_id": job_id},
    )
    assert r.status_code == 409, f"生成中的 job 不得套用：{r.status_code} {r.text}"


async def test_tp28_apply_does_not_mutate_job(client, studio, db):
    """套用只回内容，**不动 job 状态**——用户还要自己编辑后再复写。"""
    tpl = await insert_template(db, studio.merchant["id"], is_public=True)
    job_id = await _chatting_job(db, studio)

    await client.post(
        f"/api/prompt-templates/{tpl}/apply",
        headers=bearer(studio.customer_token),
        json={"job_id": job_id},
    )
    status = await db.fetchval(
        "SELECT status FROM content_job WHERE id = $1", job_id
    )
    assert status == "chatting", f"套用不得改 job 状态：{status}"


async def test_tp29_soft_delete(client, studio, db):
    tpl = await insert_template(db, studio.merchant["id"], is_public=True)
    r = await client.delete(
        f"/api/merchant/prompt-templates/{tpl}", headers=bearer(studio.merchant_token)
    )
    assert r.status_code == 204, r.text

    assert tpl not in await _market_ids(client, studio.customer_token), "不该在市场里"
    r = await client.get(
        "/api/merchant/prompt-templates", headers=bearer(studio.merchant_token)
    )
    assert tpl not in await _ids(r.json()["items"]), "不该在自己的列表里"

    row = await db.fetchrow(
        "SELECT deleted_at FROM prompt_template WHERE id = $1", tpl
    )
    assert row is not None and row["deleted_at"] is not None, (
        "软删不物理删（历史 prompt_draft 可能引用过）"
    )
