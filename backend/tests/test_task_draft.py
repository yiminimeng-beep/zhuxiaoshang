"""02-task · 建草稿 / 改任务 / 自动保存 的正常路径与校验。

对应 test_plan.md：E-01 ~ E-03 / V-01 ~ V-15
"""

from datetime import datetime, timedelta, timezone

import pytest

from tests.helpers import (
    bearer,
    create_task,
    create_task_ok,
    task_payload,
    token_for,
)

pytestmark = pytest.mark.asyncio


# --------------------------------------------------------------------------- #
# 正常路径
# --------------------------------------------------------------------------- #
async def test_e01_create_draft(client, merchant):
    _, token, _ = merchant
    task = await create_task_ok(client, token)
    assert task["id"]
    assert task["status"] == "draft"


async def test_e02_patch_task(client, merchant):
    _, token, _ = merchant
    task = await create_task_ok(client, token)

    r = await client.patch(
        f"/api/merchant/tasks/{task['id']}",
        headers=bearer(token),
        json={"title": "改过的标题"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["task"]["title"] == "改过的标题"


async def test_e03_autosave(client, merchant):
    _, token, _ = merchant
    task = await create_task_ok(client, token)

    r = await client.put(
        f"/api/merchant/tasks/{task['id']}/draft",
        headers=bearer(token),
        json={"title": "自动保存的标题"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["saved_at"]
    assert body["task"]["title"] == "自动保存的标题"


# --------------------------------------------------------------------------- #
# 校验
# --------------------------------------------------------------------------- #
async def test_v01_title_too_short(client, merchant):
    _, token, _ = merchant
    r = await create_task(client, token, title="x")
    assert r.status_code == 422, r.text


async def test_v02_title_64_ok(client, merchant):
    _, token, _ = merchant
    r = await create_task(client, token, title="x" * 64)
    assert r.status_code == 201, r.text


async def test_v03_title_too_long(client, merchant):
    _, token, _ = merchant
    r = await create_task(client, token, title="x" * 65)
    assert r.status_code == 422, r.text


async def test_v04_description_too_short(client, merchant):
    _, token, _ = merchant
    r = await create_task(client, token, description="x" * 9)
    assert r.status_code == 422, r.text


async def test_v05_description_10_ok(client, merchant):
    _, token, _ = merchant
    r = await create_task(client, token, description="x" * 10)
    assert r.status_code == 201, r.text


async def test_v06_end_before_start(client, merchant):
    _, token, _ = merchant
    now = datetime.now(timezone.utc)
    r = await create_task(
        client,
        token,
        start_at=(now + timedelta(days=1)).isoformat(),
        end_at=(now + timedelta(hours=1)).isoformat(),
    )
    assert r.status_code == 422, r.text


async def test_v07_end_equals_start(client, merchant):
    _, token, _ = merchant
    same = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    r = await create_task(client, token, start_at=same, end_at=same)
    assert r.status_code == 422, r.text


async def test_v08_quota_zero(client, merchant):
    _, token, _ = merchant
    r = await create_task(client, token, quota=0)
    assert r.status_code == 422, r.text


async def test_v09_quota_one_ok(client, merchant):
    _, token, _ = merchant
    r = await create_task(client, token, quota=1)
    assert r.status_code == 201, r.text


async def test_v10_quota_null_ok(client, merchant):
    _, token, _ = merchant
    r = await create_task(client, token, quota=None)
    assert r.status_code == 201, r.text


async def test_v11_start_in_past(client, merchant):
    _, token, _ = merchant
    past = datetime.now(timezone.utc) - timedelta(seconds=10)
    r = await create_task(
        client, token, start_at=past.isoformat(), end_at=task_payload()["end_at"]
    )
    assert r.status_code == 422, r.text


async def test_v12_customer_cannot_create(client, customer):
    _, token, _ = customer
    r = await create_task(client, token)
    assert r.status_code == 403, r.text


async def test_v13_admin_cannot_create(client, seed_accounts):
    admin_id = next(uid for uid, _, role in seed_accounts if role == "admin")
    r = await create_task(client, token_for(admin_id))
    assert r.status_code == 403, r.text


async def test_v14_tags_too_many(client, merchant):
    _, token, _ = merchant
    r = await create_task(client, token, tags=[f"t{i}" for i in range(6)])
    assert r.status_code == 422, r.text


async def test_v15_tag_too_long(client, merchant):
    _, token, _ = merchant
    r = await create_task(client, token, tags=["x" * 17])
    assert r.status_code == 422, r.text
