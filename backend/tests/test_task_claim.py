"""02-task · 领取 / 并发 / 放弃。

对应 test_plan.md：E-09 ~ E-11 / CC-01 ~ CC-10 / AB-01 ~ AB-04

CC-03 / CC-05 是本模块的核心：只有真行锁或原子条件更新才过得去。
应用层「先查再写」在并发下必然超额，这两条会直接把它照出来。
"""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from tests.helpers import (
    bearer,
    claim,
    published_task,
    seed_customers,
    token_for,
)

pytestmark = pytest.mark.asyncio

CONCURRENCY = 100


async def _claimed(client, token, task_id) -> dict:
    r = await claim(client, token, task_id)
    assert r.status_code == 201, f"领取失败：{r.status_code} {r.text}"
    return r.json()["claim"]


# --------------------------------------------------------------------------- #
# 正常路径
# --------------------------------------------------------------------------- #
async def test_e09_claim_201(client, merchant, customer):
    _, m_token, _ = merchant
    _, c_token, _ = customer
    task = await published_task(client, m_token, quota=5)

    c = await _claimed(client, c_token, task["id"])
    assert c["status"] == "in_progress"


async def test_e10_my_claims(client, merchant, customer):
    _, m_token, _ = merchant
    _, c_token, _ = customer
    task = await published_task(client, m_token, quota=5)
    await _claimed(client, c_token, task["id"])

    r = await client.get("/api/me/claims", headers=bearer(c_token))
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert len(items) == 1
    assert items[0]["task_id"] == task["id"]


async def test_e11_abandon_204(client, merchant, customer):
    _, m_token, _ = merchant
    _, c_token, _ = customer
    task = await published_task(client, m_token, quota=5)
    c = await _claimed(client, c_token, task["id"])

    r = await client.delete(f"/api/me/claims/{c['id']}", headers=bearer(c_token))
    assert r.status_code == 204, r.text


# --------------------------------------------------------------------------- #
# 并发与名额
# --------------------------------------------------------------------------- #
async def test_cc01_double_claim_409(client, merchant, customer):
    _, m_token, _ = merchant
    _, c_token, _ = customer
    task = await published_task(client, m_token, quota=5)

    assert (await claim(client, c_token, task["id"])).status_code == 201
    second = await claim(client, c_token, task["id"])
    assert second.status_code == 409, second.text


async def test_cc02_quota_exhausted(client, merchant, db):
    _, m_token, _ = merchant
    task = await published_task(client, m_token, quota=10)

    ids = await seed_customers(db, 10)
    for uid in ids:
        assert (await claim(client, token_for(uid), task["id"])).status_code == 201

    r = await claim(client, token_for((await seed_customers(db, 1, prefix="extra"))[0]), task["id"])
    assert r.status_code == 409, f"名额已满应 409：{r.status_code} {r.text}"


async def test_cc03_concurrent_quota_one(client, merchant, db):
    _, m_token, _ = merchant
    task = await published_task(client, m_token, quota=1)

    ids = await seed_customers(db, CONCURRENCY)
    tokens = [token_for(uid) for uid in ids]

    responses = await asyncio.gather(
        *(claim(client, t, task["id"]) for t in tokens)
    )
    codes = [r.status_code for r in responses]

    assert codes.count(201) == 1, (
        f"quota=1 且 {CONCURRENCY} 并发，必须恰好 1 个成功；"
        f"实际 201×{codes.count(201)}，全部码={sorted(set(codes))}"
    )
    assert codes.count(409) == CONCURRENCY - 1, f"其余应为 409，实际码={sorted(set(codes))}"


async def test_cc04_claimed_count_exact(client, merchant, db):
    _, m_token, _ = merchant
    task = await published_task(client, m_token, quota=1)

    ids = await seed_customers(db, CONCURRENCY)
    await asyncio.gather(*(claim(client, token_for(uid), task["id"]) for uid in ids))

    count = await db.fetchval(
        "SELECT claimed_count FROM task WHERE id = $1", task["id"]
    )
    assert count == 1, f"并发下 claimed_count 不得多增，实际 {count}"


async def test_cc05_unlimited_concurrent(client, merchant, db):
    _, m_token, _ = merchant
    task = await published_task(client, m_token, quota=None)

    ids = await seed_customers(db, CONCURRENCY)
    responses = await asyncio.gather(
        *(claim(client, token_for(uid), task["id"]) for uid in ids)
    )
    codes = [r.status_code for r in responses]
    assert codes.count(201) == CONCURRENCY, (
        f"不限名额时全部应 201，实际 201×{codes.count(201)}，码={sorted(set(codes))}"
    )


async def test_cc06_before_start_422(client, merchant, customer, db):
    _, m_token, _ = merchant
    _, c_token, _ = customer
    task = await published_task(client, m_token)

    now = datetime.now(timezone.utc)
    await db.execute(
        "UPDATE task SET start_at = $2, end_at = $3 WHERE id = $1",
        task["id"],
        now + timedelta(hours=1),
        now + timedelta(hours=2),
    )

    r = await claim(client, c_token, task["id"])
    assert r.status_code == 422, r.text


async def test_cc07_end_at_closed(client, merchant, customer, db):
    _, m_token, _ = merchant
    _, c_token, _ = customer
    task = await published_task(client, m_token)

    now = datetime.now(timezone.utc)
    await db.execute(
        "UPDATE task SET start_at = $2, end_at = $3 WHERE id = $1",
        task["id"],
        now - timedelta(hours=1),
        now,
    )

    r = await claim(client, c_token, task["id"])
    assert r.status_code == 422, f"end_at 视为已结束：{r.status_code} {r.text}"


async def test_cc08_paused_task_422(client, merchant, customer):
    _, m_token, _ = merchant
    _, c_token, _ = customer
    task = await published_task(client, m_token)
    await client.post(f"/api/merchant/tasks/{task['id']}/pause", headers=bearer(m_token))

    r = await claim(client, c_token, task["id"])
    assert r.status_code == 422, r.text


async def test_cc09_self_claim_403(client, merchant):
    _, m_token, _ = merchant
    task = await published_task(client, m_token)
    r = await claim(client, m_token, task["id"])
    assert r.status_code == 403, r.text


async def test_cc10_claim_without_token(client, merchant):
    _, m_token, _ = merchant
    task = await published_task(client, m_token)
    r = await client.post(f"/api/tasks/{task['id']}/claim")
    assert r.status_code == 401, r.text


# --------------------------------------------------------------------------- #
# 放弃
# --------------------------------------------------------------------------- #
async def test_ab01_abandon_decrements(client, merchant, customer, db):
    _, m_token, _ = merchant
    _, c_token, _ = customer
    task = await published_task(client, m_token, quota=5)
    c = await _claimed(client, c_token, task["id"])

    r = await client.delete(f"/api/me/claims/{c['id']}", headers=bearer(c_token))
    assert r.status_code == 204, r.text

    count = await db.fetchval(
        "SELECT claimed_count FROM task WHERE id = $1", task["id"]
    )
    assert count == 0, f"放弃后 claimed_count 应 -1，实际 {count}"


async def test_ab02_submitted_cannot_abandon(client, merchant, customer, db):
    _, m_token, _ = merchant
    _, c_token, _ = customer
    task = await published_task(client, m_token, quota=5)
    c = await _claimed(client, c_token, task["id"])

    await db.execute(
        "UPDATE task_claim SET status = 'submitted' WHERE id = $1", c["id"]
    )

    r = await client.delete(f"/api/me/claims/{c['id']}", headers=bearer(c_token))
    assert r.status_code == 409, r.text


async def test_ab03_can_reclaim_after_abandon(client, merchant, customer):
    _, m_token, _ = merchant
    _, c_token, _ = customer
    task = await published_task(client, m_token, quota=5)
    c = await _claimed(client, c_token, task["id"])

    assert (
        await client.delete(f"/api/me/claims/{c['id']}", headers=bearer(c_token))
    ).status_code == 204

    r = await claim(client, c_token, task["id"])
    assert r.status_code == 201, f"放弃后应可重新领取：{r.status_code} {r.text}"


async def test_ab04_others_claim_403(client, merchant, customer, db):
    _, m_token, _ = merchant
    _, c_token, _ = customer
    task = await published_task(client, m_token, quota=5)
    c = await _claimed(client, c_token, task["id"])

    other_id = (await seed_customers(db, 1, prefix="other"))[0]
    r = await client.delete(
        f"/api/me/claims/{c['id']}", headers=bearer(token_for(other_id))
    )
    assert r.status_code == 403, r.text
