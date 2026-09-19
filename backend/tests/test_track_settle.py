"""PS · 结算取值规则（**取峰值**）。

本组钉的是「传给 05 的那个数」，不是 `reward_grant.engagement` 列——05 的表
还没落地（见 test_plan.md「已知取舍」1）。断言接缝参数与断言落库列在语义上
等价：05 要写的就是这个入参。05 落地后 `PS-01` 改为直接查库即可。

取峰值这条规则的理由：用户可以先提交低位数据快速骗过审核，审核后再慢慢涨。
`PS-04` 单独盯反面——**审核之后产生的快照一分钱都不算**。
"""

from datetime import datetime, timedelta, timezone

from tests.helpers import (
    bearer,
    insert_snapshot,
    insert_task,
    post_under_task,
    run_expiry,
)


async def approve(client, token, post_id):
    return await client.post(
        f"/api/merchant/reviews/{post_id}/approve", headers=bearer(token)
    )


async def approve_ok(client, token, post_id):
    """过审并断言 200，再让调用方去读 `patch_reward.calls`。

    不这么包的话，端点未实现时拿到的是 404，`calls` 为空，红成
    `IndexError: list index out of range` 或 `assert 0 == 1`——读的人
    分不清「端点还没写」和「结算接缝没被调用」。
    """
    r = await approve(client, token, post_id)
    assert r.status_code == 200, f"过审失败：{r.status_code} {r.text}"
    return r


async def pending_post(db, merchant_id: int, user_id: int) -> int:
    task_id = await insert_task(db, merchant_id)
    return await post_under_task(db, task_id, user_id)


async def peak_of(post_id: int, *, moment=None) -> int:
    """直接问峰值口径本身。`run_expiry` 已按同样方式耦合 `review` 的接缝名。"""
    from app.db import SessionLocal
    from app.services import review

    session = SessionLocal()
    try:
        return await review.peak_engagement(session, post_id, moment=moment)
    finally:
        await session.close()


async def test_ps01_peak_wins(client, db, merchant, customer, patch_reward):
    """PS-01 3 条快照 `engagement` = 100 / 1800 / 900 → 结算接缝收到的值是 **1800**。"""
    m, m_token, _ = merchant
    c, _, _ = customer
    post_id = await pending_post(db, m["id"], c["id"])
    for likes in (100, 1800, 900):
        await insert_snapshot(db, post_id, likes=likes, collects=0, comments=0, shares=0)

    await approve_ok(client, m_token, post_id)

    assert len(patch_reward.calls) == 1
    assert patch_reward.calls[0]["engagement"] == 1800, (
        "取的是峰值，不是最后一条（900），也不是第一条（100）"
    )


async def test_ps02_peak_never_comes_from_social_post(
    client, db, merchant, customer, patch_reward
):
    """PS-02 峰值只来自 `metric_snapshot`：`social_post` 上不该有任何互动数字列。

    结构断言不是凑数——「把 post 上的字段也读进来当上限」这种写法一旦出现，
    最省事的做法恰好就是给 `social_post` 加一列（比如把最后一次快照冗余上去，
    好让列表页少一次 join）。那样峰值口径就有了第二个来源，从这里开始分家。
    """
    cols = await db.fetch(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = 'social_post'"
    )
    names = {c["column_name"] for c in cols}
    assert not [n for n in names if "engagement" in n or "likes" in n], (
        f"social_post 上出现了互动数字列：{sorted(names)}"
    )

    m, m_token, _ = merchant
    c, _, _ = customer
    post_id = await pending_post(db, m["id"], c["id"])
    await insert_snapshot(db, post_id, likes=700, collects=0, comments=0, shares=0)

    await approve_ok(client, m_token, post_id)
    assert patch_reward.calls[0]["engagement"] == 700


async def test_ps03_snapshot_before_review_participates(
    client, db, merchant, customer, patch_reward
):
    """PS-03 审核发生在某条快照之后 → 该快照**仍参与**（`<= reviewed_at` 的全部）。

    两条都在审核之前，较早的那条（峰值 1800）必须是结果。只看「最新一条」
    的实现会给出 100。
    """
    m, m_token, _ = merchant
    c, _, _ = customer
    post_id = await pending_post(db, m["id"], c["id"])
    now = datetime.now(timezone.utc)
    await insert_snapshot(
        db, post_id, likes=1800, collects=0, comments=0, shares=0,
        captured_at=now - timedelta(minutes=30),
    )
    await insert_snapshot(
        db, post_id, likes=100, collects=0, comments=0, shares=0,
        captured_at=now - timedelta(minutes=10),
    )

    await approve_ok(client, m_token, post_id)
    assert patch_reward.calls[0]["engagement"] == 1800


async def test_ps04_snapshot_after_review_ignored(
    client, db, merchant, customer, patch_reward
):
    """PS-04 审核**之后**追加的快照 → 结算接缝**未被二次调用**，且峰值不含它。

    「审核后不允许再涨档」有两层：接口那一层由 `MS-06` 挡住（追加 → 409），
    口径这一层在这里——定时任务或别的东西再来问峰值时，审核后的快照不参与。
    """
    m, m_token, _ = merchant
    c, _, _ = customer
    post_id = await pending_post(db, m["id"], c["id"])
    await insert_snapshot(db, post_id, likes=900, collects=0, comments=0, shares=0)

    await approve_ok(client, m_token, post_id)
    assert len(patch_reward.calls) == 1
    reviewed_at = await db.fetchval(
        "SELECT reviewed_at FROM social_post WHERE id = $1", post_id
    )

    # 审核之后用户还在涨互动。直插快照（接口那一侧已 409，由 MS-06 盯）
    await insert_snapshot(
        db, post_id, likes=5000, collects=0, comments=0, shares=0,
        captured_at=reviewed_at + timedelta(minutes=1),
    )
    assert await run_expiry() == []
    assert len(patch_reward.calls) == 1, "已通过的 post 不该被再结算一次"

    assert await peak_of(post_id, moment=reviewed_at) == 900, (
        "审核后的快照不得进入峰值"
    )


async def test_ps05_adopted_snapshot_recomputes_peak(client, db, merchant, customer):
    """PS-05 `ocr_result` 被 admin 采纳写成 `metric_snapshot` 后，峰值**重算**。

    采纳走的是「插一条 `captured_at` 更晚的快照」，故重算就是再问一次峰值——
    这条测的是「更晚的那条真的会被算进去」，即口径按 `captured_at` 排序而不是
    按插入顺序（`id`）。
    """
    m, _, _ = merchant
    c, _, _ = customer
    post_id = await pending_post(db, m["id"], c["id"])
    now = datetime.now(timezone.utc)
    await insert_snapshot(
        db, post_id, likes=400, collects=0, comments=0, shares=0,
        captured_at=now - timedelta(minutes=30),
    )
    before = await peak_of(post_id)
    assert before == 400

    # admin 采纳识别结果 → 落一条更晚的插件快照
    await insert_snapshot(
        db, post_id, likes=1200, collects=0, comments=0, shares=0,
        captured_at=now - timedelta(minutes=1), source="plugin",
    )
    assert await peak_of(post_id) == 1200


async def test_ps06_equal_peaks_same_value(client, db, merchant, customer, patch_reward):
    """PS-06 峰值恰有两条相同 → 取任意一条，结算值相同。

    不该因为实现选 `max(id)` 还是 `max(captured_at)` 而变。这一条同时也在钉
    「不要返回两条」——聚合写成 `ORDER BY ... LIMIT 1` 却忘了聚合函数，会拿到
    一行而不是一个数，比较仍然成立但类型已经不对了。
    """
    m, m_token, _ = merchant
    c, _, _ = customer
    post_id = await pending_post(db, m["id"], c["id"])
    now = datetime.now(timezone.utc)
    await insert_snapshot(
        db, post_id, likes=1800, collects=0, comments=0, shares=0,
        captured_at=now - timedelta(minutes=20),
    )
    await insert_snapshot(
        db, post_id, likes=900, collects=0, comments=900, shares=0,
        captured_at=now - timedelta(minutes=10),
    )

    await approve_ok(client, m_token, post_id)
    assert patch_reward.calls[0]["engagement"] == 1800
    assert await peak_of(post_id) == 1800
