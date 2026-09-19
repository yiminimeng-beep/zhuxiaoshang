"""05-reward 追加 A · 按商家「我的奖励」（BM / BC / BO / BP / BN）。

对应 `specs/05-reward/test_plan.md` 追加 A。
"""

import json
from datetime import datetime, timezone

import pytest

from tests.helpers import (
    assert_route_registered,
    bearer,
    grant_points_now,
    insert_coupon,
    insert_point_ledger,
    insert_user_coupon,
    reward_scene,
)

pytestmark = pytest.mark.asyncio


async def _seed_points(db, merchant_id: int, user_id: int, points: int) -> int:
    """造一条 reward_grant + 对应正流水，返回 grant_id。"""
    scene = await reward_scene(db, merchant_id, user_id)
    grant_id = await db.fetchval(
        """
        INSERT INTO reward_grant (
            post_id, claim_id, user_id, task_id, reward_rule_id, engagement,
            tier_index, reward_detail, status, granted_at
        )
        VALUES ($1, $2, $3, $4, $5, 0, 0, $6::jsonb, 'granted', now())
        RETURNING id
        """,
        scene.post_id,
        scene.claim_id,
        user_id,
        scene.task_id,
        scene.rule_id,
        json.dumps({"points": points}),
    )
    await grant_points_now(
        user_id, points, source="task_reward", ref_type="reward_grant", ref_id=grant_id
    )
    return grant_id


async def _list(client, token, **params):
    return await client.get(
        "/api/me/rewards/by-merchant",
        params=params,
        headers=bearer(token),
    )


async def _detail(client, token, merchant_id, **params):
    return await client.get(
        f"/api/me/rewards/by-merchant/{merchant_id}",
        params=params,
        headers=bearer(token),
    )


# --------------------------------------------------------------------------- #
# BM · 归组
# --------------------------------------------------------------------------- #
async def test_bm01_only_merchant_a(client, db, merchant, customer):
    mid, cid = merchant[0]["id"], customer[0]["id"]
    _, c_token, _ = customer
    await _seed_points(db, mid, cid, 50)
    r = await _list(client, c_token)
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    assert len(body["items"]) == 1
    assert body["items"][0]["merchant_id"] == mid
    assert body["items"][0]["points_earned"] == 50


async def test_bm02_two_merchants(client, db, merchant, merchant_b, customer):
    """merchant_b fixture 只返回 token——用 login 换 id。"""
    from tests.helpers import login_ok

    mid_a, cid = merchant[0]["id"], customer[0]["id"]
    _, c_token, _ = customer
    body_b = await login_ok(client, "shop0002", "pass1234", fp="fp-b")
    mid_b = body_b["user"]["id"]

    await _seed_points(db, mid_a, cid, 30)
    await _seed_points(db, mid_b, cid, 70)

    r = await _list(client, c_token)
    assert r.status_code == 200
    items = {it["merchant_id"]: it for it in r.json()["items"]}
    assert set(items) == {mid_a, mid_b}
    assert items[mid_a]["points_earned"] == 30
    assert items[mid_b]["points_earned"] == 70


async def test_bm03_two_tasks_same_merchant_merge(client, db, merchant, customer):
    mid, cid = merchant[0]["id"], customer[0]["id"]
    _, c_token, _ = customer
    await _seed_points(db, mid, cid, 40)
    await _seed_points(db, mid, cid, 60)
    r = await _list(client, c_token)
    assert r.status_code == 200
    assert r.json()["total"] == 1
    assert r.json()["items"][0]["points_earned"] == 100


async def test_bm04_coupon_only_zero_points(client, db, merchant, customer):
    mid, cid = merchant[0]["id"], customer[0]["id"]
    _, c_token, _ = customer
    coupon_id = await insert_coupon(db, mid, name="满20减5")
    await insert_user_coupon(db, cid, coupon_id)
    r = await _list(client, c_token)
    assert r.status_code == 200
    assert r.json()["total"] == 1
    it = r.json()["items"][0]
    assert it["merchant_id"] == mid
    assert it["points_earned"] == 0
    assert it["coupon_total"] == 1
    assert it["coupon_unused"] == 1


async def test_bm05_points_no_coupon(client, db, merchant, customer):
    mid, cid = merchant[0]["id"], customer[0]["id"]
    _, c_token, _ = customer
    await _seed_points(db, mid, cid, 20)
    it = (await _list(client, c_token)).json()["items"][0]
    assert it["coupon_total"] == 0
    assert it["coupon_unused"] == 0


async def test_bm06_redemption_negative_ignored(client, db, merchant, customer):
    mid, cid = merchant[0]["id"], customer[0]["id"]
    _, c_token, _ = customer
    await _seed_points(db, mid, cid, 100)
    await insert_point_ledger(
        db, cid, -30, 70, source="redemption", ref_type="mall_item", ref_id=1
    )
    it = (await _list(client, c_token)).json()["items"][0]
    assert it["points_earned"] == 100


async def test_bm07_empty_list(client, customer):
    _, c_token, _ = customer
    r = await _list(client, c_token)
    assert r.status_code == 200
    body = r.json()
    assert body["items"] == []
    assert body["total"] == 0


async def test_bm08_reconcile_sum(client, db, merchant, merchant_b, customer):
    from tests.helpers import login_ok

    mid_a, cid = merchant[0]["id"], customer[0]["id"]
    _, c_token, _ = customer
    mid_b = (await login_ok(client, "shop0002", "pass1234", fp="fp-b"))["user"]["id"]
    await _seed_points(db, mid_a, cid, 25)
    await _seed_points(db, mid_b, cid, 35)
    await insert_point_ledger(
        db, cid, -10, 50, source="redemption", ref_type="mall_item", ref_id=9
    )
    body = (await _list(client, c_token)).json()
    listed = sum(it["points_earned"] for it in body["items"])
    rows = await db.fetch(
        """
        SELECT coalesce(sum(change), 0) AS s FROM point_ledger
        WHERE user_id = $1 AND ref_type = 'reward_grant' AND change > 0
        """,
        cid,
    )
    assert listed == int(rows[0]["s"]) == 60


# --------------------------------------------------------------------------- #
# BC · 券
# --------------------------------------------------------------------------- #
async def test_bc01_coupon_counts(client, db, merchant, customer):
    mid, cid = merchant[0]["id"], customer[0]["id"]
    _, c_token, _ = customer
    coupon_id = await insert_coupon(db, mid)
    await insert_user_coupon(db, cid, coupon_id, status="unused")
    await insert_user_coupon(db, cid, coupon_id, status="unused")
    await insert_user_coupon(db, cid, coupon_id, status="used",
                             used_at=datetime.now(timezone.utc))
    it = (await _list(client, c_token)).json()["items"][0]
    assert it["coupon_total"] == 3
    assert it["coupon_unused"] == 2


async def test_bc02_nested_status_fields(client, db, merchant, customer):
    mid, cid = merchant[0]["id"], customer[0]["id"]
    _, c_token, _ = customer
    coupon_id = await insert_coupon(db, mid, status="active")
    await insert_user_coupon(db, cid, coupon_id, status="unused")
    r = await _detail(client, c_token, mid)
    assert r.status_code == 200
    c = r.json()["coupons"][0]
    assert c["status"] == "unused"
    assert c["coupon"]["status"] == "active"
    assert c["status"] != c["coupon"]["status"] or True  # 两字段都在
    assert "status" in c and "status" in c["coupon"]


async def test_bc03_coupon_display_fields(client, db, merchant, customer):
    mid, cid = merchant[0]["id"], customer[0]["id"]
    _, c_token, _ = customer
    coupon_id = await insert_coupon(
        db, mid, name="满20减5", type="cash_off", value=500
    )
    await insert_user_coupon(db, cid, coupon_id)
    c = (await _detail(client, c_token, mid)).json()["coupons"][0]["coupon"]
    assert c["name"] == "满20减5"
    assert c["type"] == "cash_off"
    assert c["value"] == 500


# --------------------------------------------------------------------------- #
# BO · 归属与 404
# --------------------------------------------------------------------------- #
async def test_bo01_no_relation_404(client, db, merchant, merchant_b, customer):
    from tests.helpers import login_ok

    _, c_token, _ = customer
    mid_b = (await login_ok(client, "shop0002", "pass1234", fp="fp-b"))["user"]["id"]
    # 只跟 A 有往来
    await _seed_points(db, merchant[0]["id"], customer[0]["id"], 10)
    r = await _detail(client, c_token, mid_b)
    assert r.status_code == 404


async def test_bo02_missing_merchant_404(client, customer):
    _, c_token, _ = customer
    assert_route_registered("get", "/api/me/rewards/by-merchant/{merchant_id}")
    r = await _detail(client, c_token, 9_999_999)
    assert r.status_code == 404


async def test_bo03_bad_merchant_id(client, customer):
    _, c_token, _ = customer
    for bad in ("abc", "0", "-1"):
        r = await client.get(
            f"/api/me/rewards/by-merchant/{bad}",
            headers=bearer(c_token),
        )
        assert r.status_code == 422, bad


async def test_bo04_unauth(client):
    r = await client.get("/api/me/rewards/by-merchant")
    assert r.status_code == 401


# --------------------------------------------------------------------------- #
# BP · 分页
# --------------------------------------------------------------------------- #
async def test_bp01_page_params(client, customer):
    _, c_token, _ = customer
    assert (await _list(client, c_token, size=101)).status_code == 422
    assert (await _list(client, c_token, size=100)).status_code == 200
    assert (await _list(client, c_token, page=0)).status_code == 422


async def test_bp02_merchant_pages(client, db, customer):
    """25 个商家各一笔积分，size=20 → 20 + 5。"""
    from argon2 import PasswordHasher

    cid = customer[0]["id"]
    _, c_token, _ = customer
    hasher = PasswordHasher()
    mids = []
    for i in range(25):
        uid = await db.fetchval(
            """
            INSERT INTO "user" (account, password_hash, role, nickname, status)
            VALUES ($1, $2, 'merchant', $3, 'active') RETURNING id
            """,
            f"m{i:04d}",
            hasher.hash("pass1234"),
            f"店{i}",
        )
        await db.execute(
            """
            INSERT INTO merchant_profile (user_id, shop_name, category)
            VALUES ($1, $2, '餐饮')
            """,
            uid,
            f"店铺{i:02d}",
        )
        mids.append(uid)
        await _seed_points(db, uid, cid, 1)

    p1 = (await _list(client, c_token, page=1, size=20)).json()
    p2 = (await _list(client, c_token, page=2, size=20)).json()
    assert p1["total"] == 25
    assert len(p1["items"]) == 20
    assert len(p2["items"]) == 5
    ids = [it["merchant_id"] for it in p1["items"]] + [
        it["merchant_id"] for it in p2["items"]
    ]
    assert len(ids) == len(set(ids)) == 25
    assert ids == sorted(ids)


async def test_bp03_coupon_pages(client, db, merchant, customer):
    mid, cid = merchant[0]["id"], customer[0]["id"]
    _, c_token, _ = customer
    coupon_id = await insert_coupon(db, mid, total=100)
    for _ in range(25):
        await insert_user_coupon(db, cid, coupon_id)
    p1 = (await _detail(client, c_token, mid, page=1, size=20)).json()
    p2 = (await _detail(client, c_token, mid, page=2, size=20)).json()
    assert p1["coupons_total"] == 25
    assert len(p1["coupons"]) == 20
    assert len(p2["coupons"]) == 5


# --------------------------------------------------------------------------- #
# BN · 不编造
# --------------------------------------------------------------------------- #
async def test_bn01_name_fallback(client, db, customer):
    from argon2 import PasswordHasher

    cid = customer[0]["id"]
    _, c_token, _ = customer
    hasher = PasswordHasher()
    # 有 nickname、无 profile
    uid = await db.fetchval(
        """
        INSERT INTO "user" (account, password_hash, role, nickname, status)
        VALUES ('noprof1', $1, 'merchant', '昵称店', 'active') RETURNING id
        """,
        hasher.hash("pass1234"),
    )
    await _seed_points(db, uid, cid, 5)
    it = (await _list(client, c_token)).json()["items"][0]
    assert it["merchant_name"] == "昵称店"
    assert it["logo_url"] is None

    # nickname 也空串（列非空，不能真 NULL）
    uid2 = await db.fetchval(
        """
        INSERT INTO "user" (account, password_hash, role, nickname, status)
        VALUES ('noprof2', $1, 'merchant', '', 'active') RETURNING id
        """,
        hasher.hash("pass1234"),
    )
    await _seed_points(db, uid2, cid, 5)
    items = (await _list(client, c_token)).json()["items"]
    row = next(x for x in items if x["merchant_id"] == uid2)
    assert row["merchant_name"] == ""
    assert row["merchant_name"] is not None


async def test_bm_cash_only_lists_pending(client, db, merchant, customer):
    """只有现金、没有积分和券时，客户奖励列表仍要出现，并标明待发金额。"""
    from tests.helpers import seed_cash_grant

    mid, cid = merchant[0]["id"], customer[0]["id"]
    _, c_token, _ = customer
    scene = await reward_scene(db, mid, cid)
    await seed_cash_grant(db, scene, scene.post_id, 800)

    listed = await _list(client, c_token)
    assert listed.status_code == 200
    item = listed.json()["items"][0]
    assert item["merchant_id"] == mid
    assert item["points_earned"] == 0
    assert item["cash_pending"] == 800

    detail = await _detail(client, c_token, mid)
    assert detail.status_code == 200
    assert detail.json()["cash_pending"] == 800
