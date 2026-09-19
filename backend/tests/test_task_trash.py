"""02-task · 删除进回收站与恢复。

对应 test_plan.md：E-14 / E-15 / TR-01 ~ TR-13

删除是**软删**：`deleted_at` 置非空，`status` 不动，子表（`reward_rule` /
`task_claim`）与审计表一律保留。回收站里的记录只能恢复，不能销毁。
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
    put_reward_rule,
)

pytestmark = pytest.mark.asyncio


async def _delete(client, token, task_id):
    return await client.delete(
        f"/api/merchant/tasks/{task_id}", headers=bearer(token)
    )


async def _restore(client, token, task_id):
    return await client.post(
        f"/api/merchant/tasks/{task_id}/restore", headers=bearer(token)
    )


async def _trash(client, token):
    return await client.get("/api/merchant/tasks/trash", headers=bearer(token))


# --------------------------------------------------------------------------- #
# 正常路径
# --------------------------------------------------------------------------- #
async def test_e14_delete_to_trash(client, merchant, db):
    _, token, _ = merchant
    task = await create_task_ok(client, token)

    r = await _delete(client, token, task["id"])
    assert r.status_code == 204, r.text

    deleted_at = await db.fetchval(
        "SELECT deleted_at FROM task WHERE id = $1", task["id"]
    )
    assert deleted_at is not None, "软删必须写 deleted_at"


async def test_e15_trash_list(client, merchant):
    _, token, _ = merchant
    task = await create_task_ok(client, token)
    await _delete(client, token, task["id"])

    r = await _trash(client, token)
    assert r.status_code == 200, r.text
    assert len(r.json()["items"]) == 1


# --------------------------------------------------------------------------- #
# 删除的前置条件
# --------------------------------------------------------------------------- #
async def test_tr01_closed_can_delete(client, merchant):
    _, token, _ = merchant
    task = await published_task(client, token)
    await client.post(f"/api/merchant/tasks/{task['id']}/pause", headers=bearer(token))
    await client.post(f"/api/merchant/tasks/{task['id']}/close", headers=bearer(token))

    r = await _delete(client, token, task["id"])
    assert r.status_code == 204, r.text


async def test_tr02_published_cannot_delete(client, merchant):
    _, token, _ = merchant
    task = await published_task(client, token)
    r = await _delete(client, token, task["id"])
    assert r.status_code == 409, f"published 必须先 close 才可删：{r.status_code} {r.text}"


async def test_tr03_has_claim_cannot_delete(client, merchant, customer, db):
    _, m_token, _ = merchant
    _, c_token, _ = customer
    task = await published_task(client, m_token, quota=5)
    await claim(client, c_token, task["id"])

    await client.post(f"/api/merchant/tasks/{task['id']}/pause", headers=bearer(m_token))
    await client.post(f"/api/merchant/tasks/{task['id']}/close", headers=bearer(m_token))

    r = await _delete(client, m_token, task["id"])
    assert r.status_code == 409, f"有有效领取的任务不得删除：{r.status_code} {r.text}"


# --------------------------------------------------------------------------- #
# 删除后的可见性
# --------------------------------------------------------------------------- #
async def test_tr04_deleted_detail_404(client, merchant):
    assert_route_registered("GET", "/api/tasks/{id}")
    _, m_token, _ = merchant
    task = await published_task(client, m_token)
    await client.post(f"/api/merchant/tasks/{task['id']}/pause", headers=bearer(m_token))
    await client.post(f"/api/merchant/tasks/{task['id']}/close", headers=bearer(m_token))
    await _delete(client, m_token, task["id"])

    # 用**本人**的令牌查：已关闭但没删是 200（本人可见），进了回收站才必须 404。
    # 若换成客户令牌，非 published 就已经是 404，测不到 deleted_at 这一分支。
    r = await client.get(f"/api/tasks/{task['id']}", headers=bearer(m_token))
    assert r.status_code == 404, r.text


async def test_tr05_excluded_from_merchant_list(client, merchant):
    _, token, _ = merchant
    task = await create_task_ok(client, token)
    await _delete(client, token, task["id"])

    r = await client.get("/api/merchant/tasks", headers=bearer(token))
    assert r.status_code == 200, r.text
    ids = [i["id"] for i in r.json()["items"]]
    assert task["id"] not in ids, "已软删的任务不得出现在商户任务列表"


# --------------------------------------------------------------------------- #
# 恢复
# --------------------------------------------------------------------------- #
async def test_tr06_restore(client, merchant, db):
    _, token, _ = merchant
    task = await create_task_ok(client, token)
    await _delete(client, token, task["id"])

    r = await _restore(client, token, task["id"])
    assert r.status_code == 200, r.text

    deleted_at = await db.fetchval(
        "SELECT deleted_at FROM task WHERE id = $1", task["id"]
    )
    assert deleted_at is None, "恢复后 deleted_at 应置回 null"


async def test_tr07_restore_keeps_status(client, merchant, db):
    _, token, _ = merchant
    task = await published_task(client, token)
    await client.post(f"/api/merchant/tasks/{task['id']}/pause", headers=bearer(token))

    await _delete(client, token, task["id"])
    await _restore(client, token, task["id"])

    status = await db.fetchval("SELECT status FROM task WHERE id = $1", task["id"])
    assert status == "paused", f"删除动作不改 status，恢复后应仍是 paused，实际 {status}"


async def test_tr08_restored_expired_not_claimable(client, merchant, customer, db):
    _, m_token, _ = merchant
    _, c_token, _ = customer

    now = datetime.now(timezone.utc)
    task_id = await insert_task(
        db,
        (await client.get("/api/me", headers=bearer(m_token))).json()["user"]["id"],
        title="过期的已发布任务",
        start_at=now - timedelta(days=2),
        end_at=now - timedelta(days=1),
        deleted_at=now - timedelta(hours=1),
    )

    r = await _restore(client, m_token, task_id)
    assert r.status_code == 200, r.text

    c = await claim(client, c_token, task_id)
    assert c.status_code == 422, f"过期任务恢复后仍不可领取（走时间窗）：{c.status_code} {c.text}"


async def test_tr09_restore_not_in_trash_404(client, merchant):
    assert_route_registered("POST", "/api/merchant/tasks/{id}/restore")
    _, token, _ = merchant
    task = await create_task_ok(client, token)

    r = await _restore(client, token, task["id"])
    assert r.status_code == 404, f"没进回收站的任务不能恢复：{r.status_code} {r.text}"


async def test_tr10_double_delete_404(client, merchant):
    assert_route_registered("DELETE", "/api/merchant/tasks/{id}")
    _, token, _ = merchant
    task = await create_task_ok(client, token)
    assert (await _delete(client, token, task["id"])).status_code == 204

    r = await _delete(client, token, task["id"])
    assert r.status_code == 404, r.text


async def test_tr11_restore_others_403(client, merchant, merchant_b):
    _, token_a, _ = merchant
    task = await create_task_ok(client, token_a)
    await _delete(client, token_a, task["id"])

    r = await _restore(client, merchant_b, task["id"])
    assert r.status_code == 403, r.text


async def test_tr12_trash_order_desc(client, merchant, db):
    _, token, _ = merchant
    uid = (await client.get("/api/me", headers=bearer(token))).json()["user"]["id"]

    base = datetime.now(timezone.utc)
    await insert_task(db, uid, title="早删的", deleted_at=base - timedelta(hours=2))
    await insert_task(db, uid, title="晚删的", deleted_at=base - timedelta(hours=1))

    titles = [i["title"] for i in (await _trash(client, token)).json()["items"]]
    assert titles == ["晚删的", "早删的"], f"回收站应按 deleted_at 倒序：{titles}"


async def test_tr13_children_rows_retained(client, merchant, customer, db):
    """软删不级联：子表行必须留下。

    合并领取是「有效领取」，会被 TR-03 挡住删除，所以这里先把它置成
    `closed`（非有效）再删——这样既过了删除的前置条件，又能验证子表确实没被级联清掉。
    """
    _, m_token, _ = merchant
    _, c_token, _ = customer
    task = await published_task(client, m_token, quota=5)
    c = await claim(client, c_token, task["id"])
    assert c.status_code == 201, c.text

    await db.execute(
        "UPDATE task_claim SET status = 'closed' WHERE task_id = $1", task["id"]
    )
    await client.post(f"/api/merchant/tasks/{task['id']}/pause", headers=bearer(m_token))
    await client.post(f"/api/merchant/tasks/{task['id']}/close", headers=bearer(m_token))
    r = await _delete(client, m_token, task["id"])
    assert r.status_code == 204, r.text

    rules = await db.fetchval(
        "SELECT count(*) FROM reward_rule WHERE task_id = $1", task["id"]
    )
    claims = await db.fetchval(
        "SELECT count(*) FROM task_claim WHERE task_id = $1", task["id"]
    )
    assert rules == 1, "软删只作用 task 主表，reward_rule 必须保留"
    assert claims == 1, "软删只作用 task 主表，task_claim 必须保留"
