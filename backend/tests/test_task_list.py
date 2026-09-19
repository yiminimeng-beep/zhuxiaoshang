"""02-task · 列表、详情、搜索、归属可见性。

对应 test_plan.md：E-12 / E-13 / LS-01 ~ LS-19 / MG-01 ~ MG-05
"""

from datetime import datetime, timedelta, timezone

import pytest

from tests.helpers import (
    assert_route_registered,
    bearer,
    claim,
    create_task_ok,
    insert_task,
    published_task,
)

pytestmark = pytest.mark.asyncio


async def _list(client, token=None, **params):
    headers = bearer(token) if token else {}
    r = await client.get("/api/tasks", headers=headers, params=params)
    return r


# --------------------------------------------------------------------------- #
# 正常路径
# --------------------------------------------------------------------------- #
async def test_e12_list_tasks(client, merchant, db):
    _, token, _ = merchant
    uid = (await client.get("/api/me", headers=bearer(token))).json()["user"]["id"]
    await insert_task(db, uid, title="列表里的任务")

    r = await _list(client, status="published")
    assert r.status_code == 200, r.text
    body = r.json()
    for key in ("items", "total", "page", "size"):
        assert key in body, f"列表响应缺少 {key}：{r.text}"


async def test_e13_task_detail(client, merchant, customer):
    _, m_token, _ = merchant
    _, c_token, _ = customer
    task = await published_task(client, m_token)

    r = await client.get(f"/api/tasks/{task['id']}", headers=bearer(c_token))
    assert r.status_code == 200, r.text
    body = r.json()
    for key in ("task", "rule", "merchant", "claimed_by_me"):
        assert key in body, f"详情响应缺少 {key}：{r.text}"


# --------------------------------------------------------------------------- #
# 列表过滤与排序
# --------------------------------------------------------------------------- #
async def test_ls01_only_published(client, merchant, db):
    _, token, _ = merchant
    uid = (await client.get("/api/me", headers=bearer(token))).json()["user"]["id"]

    for status in ("draft", "published", "paused", "closed"):
        await insert_task(db, uid, title=f"{status} 的任务", status=status)

    body = (await _list(client)).json()
    statuses = {i["status"] for i in body["items"]}
    assert statuses == {"published"}, f"列表只应返回 published，实际 {statuses}"


async def test_ls02_order_desc(client, merchant, db):
    _, token, _ = merchant
    uid = (await client.get("/api/me", headers=bearer(token))).json()["user"]["id"]

    base = datetime.now(timezone.utc)
    await insert_task(db, uid, title="最早", created_at=base - timedelta(hours=3))
    await insert_task(db, uid, title="居中", created_at=base - timedelta(hours=2))
    await insert_task(db, uid, title="最新", created_at=base - timedelta(hours=1))

    titles = [i["title"] for i in (await _list(client)).json()["items"]]
    assert titles == ["最新", "居中", "最早"], f"应按 created_at 倒序：{titles}"


async def test_ls03_size_over_100(client):
    r = await _list(client, size=101)
    assert r.status_code == 422, r.text


async def test_ls04_size_100_ok(client):
    r = await _list(client, size=100)
    assert r.status_code == 200, r.text


# --------------------------------------------------------------------------- #
# 搜索
# --------------------------------------------------------------------------- #
async def test_ls05_keyword_title(client, merchant, db):
    _, token, _ = merchant
    uid = (await client.get("/api/me", headers=bearer(token))).json()["user"]["id"]
    await insert_task(db, uid, title="奶茶新品上市", description="和标题无关的描述内容")
    await insert_task(db, uid, title="别的任务", description="也和关键词无关的描述")

    items = (await _list(client, keyword="奶茶")).json()["items"]
    assert [i["title"] for i in items] == ["奶茶新品上市"]


async def test_ls06_keyword_description(client, merchant, db):
    _, token, _ = merchant
    uid = (await client.get("/api/me", headers=bearer(token))).json()["user"]["id"]
    await insert_task(db, uid, title="标题里没有那个词", description="描述里有 珍珠 这个词")
    await insert_task(db, uid, title="无关任务", description="完全无关的描述内容")

    items = (await _list(client, keyword="珍珠")).json()["items"]
    assert len(items) == 1, f"应能按 description 命中：{items}"


async def test_ls07_no_keyword_all(client, merchant, db):
    _, token, _ = merchant
    uid = (await client.get("/api/me", headers=bearer(token))).json()["user"]["id"]
    await insert_task(db, uid, title="任务甲")
    await insert_task(db, uid, title="任务乙")

    body = (await _list(client)).json()
    assert body["total"] == 2, f"不传 keyword 应返回全量：{body}"


async def test_ls08_empty_keyword(client, merchant, db):
    _, token, _ = merchant
    uid = (await client.get("/api/me", headers=bearer(token))).json()["user"]["id"]
    await insert_task(db, uid, title="任务甲")

    body = (await _list(client, keyword="")).json()
    assert body["total"] == 1, f"空 keyword 不应过滤：{body}"


async def test_ls09_keyword_literal_percent(client, merchant, db):
    _, token, _ = merchant
    uid = (await client.get("/api/me", headers=bearer(token))).json()["user"]["id"]
    await insert_task(db, uid, title="折扣50%的任务")
    await insert_task(db, uid, title="完全不含特殊符号的任务")

    items = (await _list(client, keyword="%")).json()["items"]
    assert [i["title"] for i in items] == ["折扣50%的任务"], (
        f"% 必须按字面量处理，不得当通配符：{items}"
    )


async def test_ls10_keyword_literal_underscore(client, merchant, db):
    _, token, _ = merchant
    uid = (await client.get("/api/me", headers=bearer(token))).json()["user"]["id"]
    await insert_task(db, uid, title="带_下划线的任务")
    await insert_task(db, uid, title="普通任务标题")

    items = (await _list(client, keyword="_")).json()["items"]
    assert [i["title"] for i in items] == ["带_下划线的任务"], (
        f"_ 必须按字面量处理，不得通配单字符：{items}"
    )


async def test_ls11_no_match_empty_ok(client):
    r = await _list(client, keyword="zzz不存在的关键词zzz")
    assert r.status_code == 200, f"0 命中不得 404：{r.status_code} {r.text}"
    body = r.json()
    assert body["items"] == [] and body["total"] == 0


async def test_ls12_keyword_case_insensitive(client, merchant, db):
    _, token, _ = merchant
    uid = (await client.get("/api/me", headers=bearer(token))).json()["user"]["id"]
    await insert_task(db, uid, title="MILK 新品")

    items = (await _list(client, keyword="milk")).json()["items"]
    assert len(items) == 1, f"英文关键词应大小写不敏感：{items}"


async def test_ls13_tags_and_semantics(client, merchant, db):
    _, token, _ = merchant
    uid = (await client.get("/api/me", headers=bearer(token))).json()["user"]["id"]
    await insert_task(db, uid, title="双标签任务", tags=["奶茶", "新品"])
    await insert_task(db, uid, title="单标签任务", tags=["奶茶"])

    items = (await _list(client, tags="奶茶,新品")).json()["items"]
    assert [i["title"] for i in items] == ["双标签任务"], (
        f"tags 是 AND 语义，应同时含两个标签：{items}"
    )


async def test_ls14_tags_too_many(client):
    r = await _list(client, tags=",".join(f"t{i}" for i in range(6)))
    assert r.status_code == 422, r.text


async def test_ls15_tags_five_ok(client):
    r = await _list(client, tags=",".join(f"t{i}" for i in range(5)))
    assert r.status_code == 200, r.text


async def test_ls16_tags_empty_string(client):
    r = await _list(client, tags="")
    assert r.status_code == 422, r.text


async def test_ls17_excludes_soft_deleted(client, merchant, db):
    _, token, _ = merchant
    uid = (await client.get("/api/me", headers=bearer(token))).json()["user"]["id"]
    await insert_task(db, uid, title="正常任务")
    await insert_task(
        db, uid, title="已进回收站", deleted_at=datetime.now(timezone.utc)
    )

    titles = [i["title"] for i in (await _list(client)).json()["items"]]
    assert titles == ["正常任务"], f"列表须过滤 deleted_at 非空的：{titles}"


async def test_ls18_pagination_no_overlap(client, merchant, db):
    _, token, _ = merchant
    uid = (await client.get("/api/me", headers=bearer(token))).json()["user"]["id"]

    base = datetime.now(timezone.utc)
    for i in range(25):
        await insert_task(
            db, uid, title=f"分页任务{i:02d}", created_at=base - timedelta(minutes=i)
        )

    page1 = (await _list(client, page=1, size=10)).json()["items"]
    page2 = (await _list(client, page=2, size=10)).json()["items"]

    ids1 = {i["id"] for i in page1}
    ids2 = {i["id"] for i in page2}
    assert len(ids1) == 10 and len(ids2) == 10, (len(ids1), len(ids2))
    assert not (ids1 & ids2), f"第 2 页与第 1 页不得重复：{ids1 & ids2}"


async def test_ls19_pagination_keeps_order(client, merchant, db):
    _, token, _ = merchant
    uid = (await client.get("/api/me", headers=bearer(token))).json()["user"]["id"]

    base = datetime.now(timezone.utc)
    for i in range(25):
        await insert_task(
            db, uid, title=f"排序任务{i:02d}", created_at=base - timedelta(minutes=i)
        )

    page2 = (await _list(client, page=2, size=10)).json()["items"]
    stamps = [i["created_at"] for i in page2]
    assert stamps == sorted(stamps, reverse=True), (
        f"翻到第 2 页也必须仍是 created_at 倒序：{stamps}"
    )


# --------------------------------------------------------------------------- #
# 归属与可见性
# --------------------------------------------------------------------------- #
async def test_mg01_cross_tenant_403(client, merchant, merchant_b):
    _, token_a, _ = merchant
    task = await create_task_ok(client, token_a)

    r = await client.patch(
        f"/api/merchant/tasks/{task['id']}",
        headers=bearer(merchant_b),
        json={"title": "别人家的任务"},
    )
    assert r.status_code == 403, r.text


async def test_mg02_owner_sees_own_draft(client, merchant):
    _, token, _ = merchant
    task = await create_task_ok(client, token)

    r = await client.get(f"/api/merchant/tasks/{task['id']}", headers=bearer(token))
    assert r.status_code == 200, r.text


async def test_mg03_customer_cannot_see_draft(client, merchant, customer):
    assert_route_registered("GET", "/api/tasks/{id}")
    _, m_token, _ = merchant
    _, c_token, _ = customer
    task = await create_task_ok(client, m_token)

    r = await client.get(f"/api/tasks/{task['id']}", headers=bearer(c_token))
    assert r.status_code == 404, f"草稿对客户应表现为不存在（404 不是 403）：{r.status_code} {r.text}"


async def test_mg04_missing_task_404(client, customer):
    assert_route_registered("GET", "/api/tasks/{id}")
    _, c_token, _ = customer
    r = await client.get("/api/tasks/999999", headers=bearer(c_token))
    assert r.status_code == 404, r.text


async def test_mg05_claimed_by_me_flag(client, merchant, customer):
    _, m_token, _ = merchant
    _, c_token, _ = customer
    task = await published_task(client, m_token, quota=5)

    before = (await client.get(f"/api/tasks/{task['id']}", headers=bearer(c_token))).json()
    assert before["claimed_by_me"] is False

    await claim(client, c_token, task["id"])

    after = (await client.get(f"/api/tasks/{task['id']}", headers=bearer(c_token))).json()
    assert after["claimed_by_me"] is True
