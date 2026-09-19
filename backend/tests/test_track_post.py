"""SP · 提交作品（`POST /api/posts`）+ OW-01（我的作品只含本人）。

一律走接口造数据——本组的测点就是**提交**这道闸，用直插绕过它等于什么都没测。
"""

from tests.helpers import (
    bearer,
    insert_claim,
    insert_job,
    insert_post,
    post_url,
    submit_post,
    submit_post_ok,
)


async def test_sp01_submit_ok(client, db, make_scene):
    """SP-01 正常提交 → 201，响应含 post 与 review_deadline。"""
    scene = await make_scene()
    body = await submit_post_ok(client, scene.customer_token, scene)

    assert body["post"]["status"] == "pending"
    assert body["post"]["source"] == "plugin"
    assert body["review_deadline"]

    row = await db.fetchrow(
        "SELECT claim_id, job_id, user_id, platform, status FROM social_post WHERE id = $1",
        body["post"]["id"],
    )
    assert row["claim_id"] == scene.claim_id
    assert row["job_id"] == scene.job_id
    assert row["user_id"] == scene.customer["id"]
    assert row["platform"] == "xhs"


async def test_sp02_deadline_is_submitted_plus_72h(client, db, make_scene):
    """SP-02 `review_deadline == submitted_at + 72h`（容差 1 秒，只差写入往返）。"""
    scene = await make_scene()
    body = await submit_post_ok(client, scene.customer_token, scene)

    row = await db.fetchrow(
        "SELECT submitted_at, review_deadline FROM social_post WHERE id = $1",
        body["post"]["id"],
    )
    delta = (row["review_deadline"] - row["submitted_at"]).total_seconds()
    assert abs(delta - 72 * 3600) <= 1, f"应恰好 72h，实际 {delta}s"


async def test_sp03_same_claim_same_url_twice_conflicts(client, db, make_scene):
    """SP-03 同 claim 同链接提交两次 → 409。"""
    scene = await make_scene()
    url = post_url()
    await submit_post_ok(client, scene.customer_token, scene, post_url=url)

    r = await submit_post(client, scene.customer_token, scene, post_url=url)
    assert r.status_code == 409, r.text


async def test_sp04_same_claim_different_url_rejected(client, db, make_scene):
    """SP-04 同 claim 换一个不同 URL → 422（一个领取只算一个作品）。

    与 SP-03 不是同一道闸：这条拦的是「刷奖励」，不是「重复提交」。
    """
    scene = await make_scene()
    await submit_post_ok(client, scene.customer_token, scene)

    r = await submit_post(client, scene.customer_token, scene, post_url=post_url())
    assert r.status_code == 422, r.text


async def test_sp05_not_a_url(client, db, make_scene):
    """SP-05 `not-a-url` → 422。"""
    scene = await make_scene()
    r = await submit_post(client, scene.customer_token, scene, post_url="not-a-url")
    assert r.status_code == 422, r.text


async def test_sp06_javascript_scheme_rejected(client, db, make_scene):
    """SP-06 `javascript:alert(1)` → 422（防 XSS）。

    只校验「以 http 开头」是不够的——`javascript:` 不是 http，但一个只用
    `urlparse` 取 host 的实现会把它当成「host 为空」而放行。
    """
    scene = await make_scene()
    r = await submit_post(
        client, scene.customer_token, scene, post_url="javascript:alert(1)"
    )
    assert r.status_code == 422, r.text


async def test_sp07_platform_domain_mismatch(client, db, make_scene):
    """SP-07 `platform=xhs` 但域名是 `douyin.com` → 422。"""
    scene = await make_scene()
    r = await submit_post(
        client,
        scene.customer_token,
        scene,
        platform="xhs",
        post_url="https://www.douyin.com/video/123",
    )
    assert r.status_code == 422, r.text


async def test_sp08_xiaohongshu_domain_ok(client, db, make_scene):
    """SP-08 `platform=xhs` + `xiaohongshu.com` → 201。"""
    scene = await make_scene()
    body = await submit_post_ok(
        client,
        scene.customer_token,
        scene,
        post_url="https://www.xiaohongshu.com/explore/abc123",
    )
    assert body["post"]["platform"] == "xhs"


async def test_sp09_xhslink_short_url_ok(client, db, make_scene):
    """SP-09 `platform=xhs` + `xhslink.com` → 201（短链）。"""
    scene = await make_scene()
    await submit_post_ok(
        client, scene.customer_token, scene, post_url="https://xhslink.com/a1b2c3"
    )


async def test_sp10_someone_elses_claim(client, db, make_scene, merchant_b):
    """SP-10 引用**别人**的 claim_id → 403。

    直插一条挂在别家用户名下的 claim，比再注册一个客户便宜，且更贴近
    「拿着别人的 claim_id 来提交」这个真实攻击形态。
    """
    scene = await make_scene()
    other_uid = await db.fetchval(
        'SELECT id FROM "user" WHERE account = $1', "shop0002"
    )
    other_claim = await insert_claim(db, scene.task_id, other_uid)
    r = await submit_post(client, scene.customer_token, scene, claim_id=other_claim)
    assert r.status_code == 403, r.text


async def test_sp11_job_not_ready(client, db, make_scene):
    """SP-11 引用的 job 状态非 ready → 409。

    直接改库把 job 打回 `generating`：这条测的是**提交时的状态检查**，
    不是流水线能不能跑。
    """
    scene = await make_scene()
    await db.execute(
        "UPDATE content_job SET status = 'generating' WHERE id = $1", scene.job_id
    )
    r = await submit_post(client, scene.customer_token, scene)
    assert r.status_code == 409, r.text


async def test_sp12_already_approved_post(client, db, make_scene):
    """SP-12 该 claim 已有 approved 作品后再提交（换个 URL）→ 422。

    与 SP-04 叠加存在：SP-04 是「一个领取一个作品」，这条是「已通过就别再交」。
    缺了它，「先交 A 被驳回、再交 B」能从 SP-04 的口子漏过去。
    """
    scene = await make_scene()
    url = post_url()
    first = await submit_post_ok(client, scene.customer_token, scene, post_url=url)
    await db.execute(
        "UPDATE social_post SET status = 'approved', reviewed_at = now() WHERE id = $1",
        first["post"]["id"],
    )

    r = await submit_post(client, scene.customer_token, scene, post_url=url)
    assert r.status_code == 422, r.text


# --------------------------------------------------------------------------- #
# OW-01
# --------------------------------------------------------------------------- #
async def test_ow01_my_posts_only_mine(client, db, make_scene):
    """OW-01 `GET /api/me/posts` 只返回本人的作品，含最新快照与倒计时。"""
    scene = await make_scene()
    mine = await submit_post_ok(client, scene.customer_token, scene)

    # 另一个人也交一篇，挂到同一个任务下
    other_uid = await db.fetchval(
        "INSERT INTO \"user\" (account, password_hash, role, nickname, status) "
        "VALUES ('ow01other', 'x', 'customer', 'ow01other', 'active') RETURNING id"
    )
    other_claim = await insert_claim(db, scene.task_id, other_uid)
    other_job = await insert_job(db, scene.task_id, other_claim, other_uid, status="ready")
    await insert_post(db, other_claim, other_job, other_uid)

    r = await client.get("/api/me/posts", headers=bearer(scene.customer_token))
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert [i["id"] for i in items] == [mine["post"]["id"]]

    item = items[0]
    # 前端只展示数字与倒计时（spec「明确不做」：不做趋势图），这两项必须在
    assert item["review_deadline"]
    assert isinstance(item["countdown_seconds"], int)
    assert item["latest_snapshot"] is None  # 还没追过快照

