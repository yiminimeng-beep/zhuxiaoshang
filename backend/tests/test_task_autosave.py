"""02-task · 草稿自动保存。

对应 test_plan.md：AS-01 ~ AS-10

自动保存是**部分更新**语义：没传的字段必须保持原值，不能当成「置空」。
"""

import pytest

from tests.helpers import (
    bearer,
    create_task_ok,
    published_task,
)

pytestmark = pytest.mark.asyncio


async def _autosave(client, token, task_id, body):
    return await client.put(
        f"/api/merchant/tasks/{task_id}/draft", headers=bearer(token), json=body
    )


async def test_as01_partial_update(client, merchant):
    _, token, _ = merchant
    task = await create_task_ok(client, token, title="原始标题", description="原始描述内容原样保留")

    r = await _autosave(client, token, task["id"], {"title": "新标题"})
    assert r.status_code == 200, r.text

    body = r.json()["task"]
    assert body["title"] == "新标题"
    assert body["description"] == task["description"], (
        "只传 title 时，未传的 description 必须保持原值"
    )


async def test_as02_idempotent_no_new_rows(client, merchant, db):
    _, token, _ = merchant
    task = await create_task_ok(client, token)

    stamps = []
    for _ in range(5):
        r = await _autosave(client, token, task["id"], {"title": "重复提交的标题"})
        assert r.status_code == 200, r.text
        stamps.append(
            await db.fetchval("SELECT updated_at FROM task WHERE id = $1", task["id"])
        )

    count = await db.fetchval("SELECT count(*) FROM task WHERE id = $1", task["id"])
    assert count == 1, "自动保存是更新，不得产生新行"
    assert stamps[-1] >= stamps[0], "updated_at 应被刷新"


async def test_as03_published_409(client, merchant):
    _, token, _ = merchant
    task = await published_task(client, token)
    r = await _autosave(client, token, task["id"], {"title": "已发布还想自动保存"})
    assert r.status_code == 409, r.text


async def test_as04_closed_409(client, merchant):
    _, token, _ = merchant
    task = await published_task(client, token)
    await client.post(f"/api/merchant/tasks/{task['id']}/pause", headers=bearer(token))
    await client.post(f"/api/merchant/tasks/{task['id']}/close", headers=bearer(token))

    r = await _autosave(client, token, task["id"], {"title": "已关闭还想自动保存"})
    assert r.status_code == 409, r.text


async def test_as05_invalid_tags_rolls_back(client, merchant, db):
    _, token, _ = merchant
    task = await create_task_ok(client, token, title="不能被破坏的标题")

    r = await _autosave(
        client, token, task["id"], {"title": "想改成的标题", "tags": [f"t{i}" for i in range(6)]}
    )
    assert r.status_code == 422, r.text

    row = await db.fetchrow(
        "SELECT title, tags FROM task WHERE id = $1", task["id"]
    )
    assert row["title"] == "不能被破坏的标题", "校验失败必须整体回滚，不得留下半截改动"


async def test_as06_invalid_title_rolls_back(client, merchant, db):
    _, token, _ = merchant
    task = await create_task_ok(client, token, title="同样不能被破坏")

    r = await _autosave(client, token, task["id"], {"title": "x" * 65})
    assert r.status_code == 422, r.text

    title = await db.fetchval("SELECT title FROM task WHERE id = $1", task["id"])
    assert title == "同样不能被破坏"


async def test_as07_saved_value_readable(client, merchant):
    _, token, _ = merchant
    task = await create_task_ok(client, token)

    await _autosave(client, token, task["id"], {"title": "保存后立即可见"})

    r = await client.get(f"/api/merchant/tasks/{task['id']}", headers=bearer(token))
    assert r.status_code == 200, r.text
    assert r.json()["task"]["title"] == "保存后立即可见"


async def test_as08_no_rule_check(client, merchant):
    """草稿允许半成品：没配奖励规则也照样能自动保存。"""
    _, token, _ = merchant
    task = await create_task_ok(client, token)

    r = await _autosave(client, token, task["id"], {"title": "还没想好奖励规则"})
    assert r.status_code == 200, f"自动保存不得校验奖励规则完整性：{r.status_code} {r.text}"


async def test_as09_not_owner_403(client, merchant, merchant_b):
    _, token_a, _ = merchant
    task = await create_task_ok(client, token_a)

    r = await _autosave(client, merchant_b, task["id"], {"title": "别人的草稿"})
    assert r.status_code == 403, r.text


async def test_as10_customer_cannot_autosave(client, merchant, customer):
    _, m_token, _ = merchant
    _, c_token, _ = customer
    task = await create_task_ok(client, m_token)

    r = await _autosave(client, c_token, task["id"], {"title": "客户来改草稿"})
    assert r.status_code == 403, r.text
