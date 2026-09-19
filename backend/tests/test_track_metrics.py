"""MS · 数据快照（`POST /api/posts/{id}/metrics`）+ OW-04 + AP-09。

`engagement` 由**后端**算这条是整组的重心：客户端能传 `engagement` 就等于
能自己挑奖励档位。
"""

from tests.helpers import bearer, submit_post_ok


async def metrics(client, token, post_id, **over):
    body = {"likes": 10, "collects": 5, "comments": 3, "shares": 0, "source": "plugin"}
    body.update(over)
    return await client.post(
        f"/api/posts/{post_id}/metrics", headers=bearer(token), json=body
    )


async def test_ms01_append_ok(client, db, make_scene):
    """MS-01 正常追加 → 201，响应含 snapshot，engagement 由后端算。"""
    scene = await make_scene()
    post_id = (await submit_post_ok(client, scene.customer_token, scene))["post"]["id"]

    r = await metrics(client, scene.customer_token, post_id)
    assert r.status_code == 201, r.text
    snap = r.json()["snapshot"]
    assert snap["likes"] == 10
    assert snap["engagement"] == 18


async def test_ms02_engagement_excludes_shares(client, db, make_scene):
    """MS-02 `10 + 5 + 3 = 18`——**`shares` 不计入**。

    实现若顺手把 shares 也加上，这条立刻红。spec 的公式只有三项。
    """
    scene = await make_scene()
    post_id = (await submit_post_ok(client, scene.customer_token, scene))["post"]["id"]

    r = await metrics(
        client, scene.customer_token, post_id, likes=10, collects=5, comments=3, shares=999
    )
    assert r.json()["snapshot"]["engagement"] == 18

    stored = await db.fetchval(
        "SELECT engagement FROM metric_snapshot WHERE post_id = $1", post_id
    )
    assert stored == 18


async def test_ms03_negative_likes(client, db, make_scene):
    """MS-03 负数 → 422。"""
    scene = await make_scene()
    post_id = (await submit_post_ok(client, scene.customer_token, scene))["post"]["id"]

    r = await metrics(client, scene.customer_token, post_id, likes=-1)
    assert r.status_code == 422, r.text


async def test_ms04_fraction_rejected(client, db, make_scene):
    """MS-04 小数 → 422（互动数是整数，1.5 个赞不存在）。"""
    scene = await make_scene()
    post_id = (await submit_post_ok(client, scene.customer_token, scene))["post"]["id"]

    r = await metrics(client, scene.customer_token, post_id, likes=1.5)
    assert r.status_code == 422, r.text


async def test_ms05_client_supplied_engagement_rejected(client, db, make_scene):
    """MS-05 请求体带 `engagement` → 422。

    这是**必须**拒而不是忽略的一条：静默忽略会让插件以为自己算的数生效了。
    """
    scene = await make_scene()
    post_id = (await submit_post_ok(client, scene.customer_token, scene))["post"]["id"]

    r = await metrics(client, scene.customer_token, post_id, engagement=99999)
    assert r.status_code == 422, r.text


async def test_ms08_client_supplied_captured_at_rejected(client, db, make_scene):
    """MS-08 请求体带 `captured_at` → 422（列由服务端写）。

    与 `engagement` 同一类问题：让客户端声称「我是昨天采的」就能绕过
    「审核之后才产生的快照不参与结算」这条。
    对额外字段一律 422 而不是静默忽略：插件是受控客户端，它多传一个字段
    说明协议理解有偏差，静默接受会让它错下去。
    """
    scene = await make_scene()
    post_id = (await submit_post_ok(client, scene.customer_token, scene))["post"]["id"]

    r = await metrics(
        client, scene.customer_token, post_id, captured_at="2020-01-01T00:00:00Z"
    )
    assert r.status_code == 422, r.text


async def test_ms06_second_snapshot_does_not_overwrite(client, db, make_scene):
    """MS-06 追加第 2 次 → 第 1 次**仍在库中**（只追加，不覆盖）。

    覆盖式写入会让「先报低位博通过、再涨上来」无从发现，峰值结算也就
    失去了前提。
    """
    scene = await make_scene()
    post_id = (await submit_post_ok(client, scene.customer_token, scene))["post"]["id"]

    await metrics(client, scene.customer_token, post_id, likes=10)
    await metrics(client, scene.customer_token, post_id, likes=200)

    rows = await db.fetch(
        "SELECT likes FROM metric_snapshot WHERE post_id = $1 ORDER BY id", post_id
    )
    assert [r["likes"] for r in rows] == [10, 200]


async def test_ms07_after_review_completed(client, db, make_scene):
    """MS-07 已审核完成后追加 → 409（审核后不允许再涨档）。"""
    scene = await make_scene()
    post_id = (await submit_post_ok(client, scene.customer_token, scene))["post"]["id"]
    await db.execute(
        "UPDATE social_post SET status = 'approved', reviewed_at = now() WHERE id = $1",
        post_id,
    )

    r = await metrics(client, scene.customer_token, post_id)
    assert r.status_code == 409, r.text


async def test_ow04_someone_elses_post(client, db, make_scene):
    """OW-04 对**别人**的作品追加快照 → 403。"""
    scene = await make_scene()
    post_id = (await submit_post_ok(client, scene.customer_token, scene))["post"]["id"]

    uid = await db.fetchval(
        "INSERT INTO \"user\" (account, password_hash, role, nickname, status) "
        "VALUES ('ow04other', 'x', 'customer', 'ow04other', 'active') RETURNING id"
    )
    from tests.helpers import token_for

    r = await metrics(client, token_for(uid), post_id)
    assert r.status_code == 403, r.text


async def test_ap09_metrics_allowed_while_appealed(client, db, make_scene):
    """AP-09 申诉期间追加快照 → **允许**（用户还在涨互动，裁决时取峰值）。"""
    scene = await make_scene()
    post_id = (await submit_post_ok(client, scene.customer_token, scene))["post"]["id"]
    await db.execute(
        "UPDATE social_post SET status = 'appealed' WHERE id = $1", post_id
    )

    r = await metrics(client, scene.customer_token, post_id, likes=500)
    assert r.status_code == 201, r.text
