"""02-task · 发布与状态流转。

对应 test_plan.md：E-06 ~ E-08 / PB-01 ~ PB-10
"""

from datetime import datetime, timedelta, timezone

import pytest

from tests.helpers import (
    bearer,
    create_task_ok,
    published_task,
    put_reward_rule,
)

pytestmark = pytest.mark.asyncio


async def _post(client, token, task_id, action):
    return await client.post(
        f"/api/merchant/tasks/{task_id}/{action}", headers=bearer(token)
    )


# --------------------------------------------------------------------------- #
# 正常路径
# --------------------------------------------------------------------------- #
async def test_e06_publish(client, merchant):
    _, token, _ = merchant
    task = await published_task(client, token)
    assert task["id"]


async def test_e07_pause(client, merchant):
    _, token, _ = merchant
    task = await published_task(client, token)
    r = await _post(client, token, task["id"], "pause")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "paused"


async def test_e08_close(client, merchant):
    _, token, _ = merchant
    task = await published_task(client, token)
    assert (await _post(client, token, task["id"], "pause")).status_code == 200
    r = await _post(client, token, task["id"], "close")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "closed"


# --------------------------------------------------------------------------- #
# 状态流转边界
# --------------------------------------------------------------------------- #
async def test_pb01_publish_without_rule(client, merchant):
    _, token, _ = merchant
    task = await create_task_ok(client, token)
    r = await _post(client, token, task["id"], "publish")
    assert r.status_code == 409, r.text


async def test_pb02_publish_twice(client, merchant):
    _, token, _ = merchant
    task = await published_task(client, token)
    r = await _post(client, token, task["id"], "publish")
    assert r.status_code == 409, r.text


async def test_pb03_closed_cannot_publish(client, merchant):
    _, token, _ = merchant
    task = await published_task(client, token)
    await _post(client, token, task["id"], "pause")
    await _post(client, token, task["id"], "close")
    r = await _post(client, token, task["id"], "publish")
    assert r.status_code == 409, r.text


async def test_pb04_paused_can_resume(client, merchant):
    _, token, _ = merchant
    task = await published_task(client, token)
    await _post(client, token, task["id"], "pause")
    r = await _post(client, token, task["id"], "publish")
    assert r.status_code == 200, f"paused → published 应允许恢复：{r.status_code} {r.text}"


async def test_pb05_published_end_in_past(client, merchant, db):
    _, token, _ = merchant
    task = await published_task(client, token)

    past = datetime.now(timezone.utc) - timedelta(hours=1)
    await db.execute(
        "UPDATE task SET start_at = $2, end_at = $3 WHERE id = $1",
        task["id"],
        past - timedelta(hours=1),
        past,
    )

    r = await client.patch(
        f"/api/merchant/tasks/{task['id']}",
        headers=bearer(token),
        json={"end_at": (past - timedelta(minutes=1)).isoformat()},
    )
    assert r.status_code == 422, r.text


async def test_pb06_quota_below_claimed(client, merchant, db):
    _, token, _ = merchant
    task = await published_task(client, token, quota=10)

    await db.execute("UPDATE task SET claimed_count = 2 WHERE id = $1", task["id"])

    r = await client.patch(
        f"/api/merchant/tasks/{task['id']}",
        headers=bearer(token),
        json={"quota": 1},
    )
    assert r.status_code == 422, r.text


async def test_pb07_published_cannot_change_title(client, merchant):
    _, token, _ = merchant
    task = await published_task(client, token)
    r = await client.patch(
        f"/api/merchant/tasks/{task['id']}",
        headers=bearer(token),
        json={"title": "发布后想改标题"},
    )
    assert r.status_code == 409, r.text


async def test_pb08_published_cannot_change_start(client, merchant):
    _, token, _ = merchant
    task = await published_task(client, token)
    r = await client.patch(
        f"/api/merchant/tasks/{task['id']}",
        headers=bearer(token),
        json={"start_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()},
    )
    assert r.status_code == 409, r.text


async def test_pb09_published_can_change_description(client, merchant):
    _, token, _ = merchant
    task = await published_task(client, token)
    r = await client.patch(
        f"/api/merchant/tasks/{task['id']}",
        headers=bearer(token),
        json={"description": "发布后允许修改的描述内容"},
    )
    assert r.status_code == 200, r.text


async def test_pb10_publish_expired(client, merchant, db):
    _, token, _ = merchant
    task = await create_task_ok(client, token)
    rr = await put_reward_rule(client, token, task["id"])
    assert rr.status_code == 200, rr.text

    past = datetime.now(timezone.utc) - timedelta(hours=1)
    await db.execute(
        "UPDATE task SET start_at = $2, end_at = $3 WHERE id = $1",
        task["id"],
        past - timedelta(hours=1),
        past,
    )

    r = await _post(client, token, task["id"], "publish")
    assert r.status_code == 422, r.text
