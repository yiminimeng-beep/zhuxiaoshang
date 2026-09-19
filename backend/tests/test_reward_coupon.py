"""05-reward · `UP` 组（优惠券）与 `MN` 组（券过期）。

对应 test_plan.md 的 `UP-01` ~ `UP-09` 与 `MN-01` ~ `MN-03`，共 12 条。

券有两处「发出去就收不回来」：

- `issued` 与 `user_coupon` 行数必须始终一致——发放是「`issued+1` 与 `insert`
  同事务」，不能一个成功一个失败；
- `total` 改成小于 `issued` 要 `422`——发出去的券追不回来，改小等于自相矛盾。
"""

from datetime import datetime, timedelta, timezone

import pytest

from tests.helpers import (
    another_post,
    as_json,
    bearer,
    expire_coupons_now,
    grant_rows,
    insert_coupon,
    insert_user_coupon,
    make_tiers,
    only_grant,
    points_of,
    reward_scene,
    settle_now,
)

pytestmark = pytest.mark.asyncio


def _coupon_payload(**over) -> dict:
    now = datetime.now(timezone.utc)
    body = {
        "name": "满100减10",
        "type": "cash_off",
        "value": 1000,
        "total": 10,
        "valid_from": (now - timedelta(days=1)).isoformat(),
        "valid_to": (now + timedelta(days=30)).isoformat(),
    }
    body.update(over)
    return body


async def _issue(db, scene) -> None:
    """在既有场景里再挂一篇作品并结算一次——用来把券发出去。"""
    post_id = await another_post(db, scene)
    await settle_now(
        post_id=post_id,
        user_id=scene.user_id,
        task_id=scene.task_id,
        engagement=18,
    )


async def test_up01_merchant_creates_coupon(db, client, merchant):
    """建券 → `201`，`{coupon}` 含 `id` / `issued=0` / `status=active`。"""
    _, token, _ = merchant

    r = await client.post(
        "/api/merchant/coupons", headers=bearer(token), json=_coupon_payload()
    )
    assert r.status_code == 201, r.text
    coupon = r.json()["coupon"]
    assert coupon["id"]
    assert coupon["issued"] == 0
    assert coupon["status"] == "active"
    assert coupon["total"] == 10


async def test_up02_create_coupon_boundaries(db, client, merchant):
    """`valid_to < valid_from` / `total <= 0` / 非法 `type` → `422`。"""
    _, token, _ = merchant
    now = datetime.now(timezone.utc)

    bad_range = await client.post(
        "/api/merchant/coupons",
        headers=bearer(token),
        json=_coupon_payload(
            valid_from=(now + timedelta(days=1)).isoformat(),
            valid_to=(now - timedelta(days=1)).isoformat(),
        ),
    )
    assert bad_range.status_code == 422, bad_range.text

    for total in (0, -1):
        r = await client.post(
            "/api/merchant/coupons",
            headers=bearer(token),
            json=_coupon_payload(total=total),
        )
        assert r.status_code == 422, f"total={total} 应 422，实际 {r.status_code}"

    bad_type = await client.post(
        "/api/merchant/coupons",
        headers=bearer(token),
        json=_coupon_payload(type="bogus"),
    )
    assert bad_type.status_code == 422, bad_type.text


async def test_up03_patch_total_below_issued(db, client, merchant):
    """`total` 改成小于已 `issued` → `422`。"""
    _, token, _ = merchant
    coupon_id = await insert_coupon(db, merchant[0]["id"], total=10, issued=3)

    r = await client.patch(
        f"/api/merchant/coupons/{coupon_id}", headers=bearer(token), json={"total": 2}
    )
    assert r.status_code == 422, r.text
    assert await db.fetchval("SELECT total FROM coupon WHERE id = $1", coupon_id) == 10


async def test_up04_settle_issues_user_coupon(db, merchant, customer):
    """结算发券 → `user_coupon` 一行，`expire_at == coupon.valid_to`。"""
    merchant_id, customer_id = merchant[0]["id"], customer[0]["id"]
    valid_to = datetime.now(timezone.utc) + timedelta(days=7)
    coupon_id = await insert_coupon(db, merchant_id, valid_to=valid_to)
    scene = await reward_scene(
        db,
        merchant_id,
        customer_id,
        tiers=make_tiers(3, reward={"coupon_id": coupon_id}),
    )

    await settle_now(
        post_id=scene.post_id,
        user_id=customer_id,
        task_id=scene.task_id,
        engagement=18,
    )

    row = await db.fetchrow("SELECT * FROM user_coupon WHERE user_id = $1", customer_id)
    assert row is not None
    assert row["status"] == "unused"
    assert row["coupon_id"] == coupon_id
    assert row["obtained_at"] is not None
    assert row["expire_at"] == valid_to
    grant = await only_grant(db, scene.post_id)
    assert as_json(grant["reward_detail"])["coupon_ids"] == [row["id"]]


async def test_up05_exhausted_coupon_stops_at_total(db, merchant, customer):
    """`total=10` 发满 10 张后第 11 张发不出：`user_coupon` 仍 10 行，`issued` 不得变 11。"""
    merchant_id, customer_id = merchant[0]["id"], customer[0]["id"]
    coupon_id = await insert_coupon(db, merchant_id, total=10)
    scene = await reward_scene(
        db,
        merchant_id,
        customer_id,
        tiers=make_tiers(3, reward={"coupon_id": coupon_id}),
    )

    for _ in range(10):
        await _issue(db, scene)
    assert await db.fetchval("SELECT count(*) FROM user_coupon") == 10

    await _issue(db, scene)

    assert await db.fetchval("SELECT count(*) FROM user_coupon") == 10
    assert await db.fetchval("SELECT issued FROM coupon WHERE id = $1", coupon_id) == 10


async def test_up06_issued_matches_row_count(db, merchant, customer):
    """`coupon.issued` 与 `user_coupon` 行数始终一致。

    两个数若会分开，对账时「还剩几张」永远说不清。发放必须同事务：
    `issued+1` 成功而行没落，或者反过来，都会让这两个数永久错开。
    """
    merchant_id, customer_id = merchant[0]["id"], customer[0]["id"]
    coupon_id = await insert_coupon(db, merchant_id, total=5)
    scene = await reward_scene(
        db,
        merchant_id,
        customer_id,
        tiers=make_tiers(3, reward={"coupon_id": coupon_id}),
    )

    for _ in range(5):
        await _issue(db, scene)

    issued = await db.fetchval("SELECT issued FROM coupon WHERE id = $1", coupon_id)
    rows = await db.fetchval(
        "SELECT count(*) FROM user_coupon WHERE coupon_id = $1", coupon_id
    )
    assert issued == rows == 5


async def test_up07_user_coupon_code_unique(db, merchant, customer):
    """`code` 全局唯一：同一 code 重复插入 → 唯一约束拒绝。"""
    merchant_id, customer_id = merchant[0]["id"], customer[0]["id"]
    coupon_id = await insert_coupon(db, merchant_id)
    await insert_user_coupon(db, customer_id, coupon_id, code="DUP123")

    with pytest.raises(Exception) as excinfo:
        await insert_user_coupon(db, customer_id, coupon_id, code="DUP123")
    assert not isinstance(excinfo.value, AssertionError), excinfo.value
    assert await db.fetchval("SELECT count(*) FROM user_coupon") == 1


async def test_up08_my_coupons_filter(db, client, merchant, customer):
    """`?status=unused` 只返回 `unused`；不带参数返回全部；非法值 → `422`。"""
    _, c_token, _ = customer
    merchant_id, customer_id = merchant[0]["id"], customer[0]["id"]
    coupon_id = await insert_coupon(db, merchant_id)
    await insert_user_coupon(db, customer_id, coupon_id, code="A1")
    await insert_user_coupon(db, customer_id, coupon_id, code="A2", status="used")
    await insert_user_coupon(db, customer_id, coupon_id, code="A3", status="expired")

    all_rows = await client.get("/api/me/coupons", headers=bearer(c_token))
    assert all_rows.status_code == 200, all_rows.text
    assert all_rows.json()["total"] == 3

    unused = await client.get("/api/me/coupons?status=unused", headers=bearer(c_token))
    assert unused.status_code == 200, unused.text
    codes = [i["code"] for i in unused.json()["items"]]
    assert codes == ["A1"]

    bogus = await client.get("/api/me/coupons?status=bogus", headers=bearer(c_token))
    assert bogus.status_code == 422, bogus.text


async def test_up09_customer_cannot_manage_coupons(db, client, merchant, customer):
    """客户建券 / 改券 → `403`。"""
    _, c_token, _ = customer
    coupon_id = await insert_coupon(db, merchant[0]["id"])

    create = await client.post(
        "/api/merchant/coupons", headers=bearer(c_token), json=_coupon_payload()
    )
    assert create.status_code == 403, create.text

    patch = await client.patch(
        f"/api/merchant/coupons/{coupon_id}", headers=bearer(c_token), json={"total": 20}
    )
    assert patch.status_code == 403, patch.text


# --------------------------------------------------------------------------- #
# MN · 券过期
# --------------------------------------------------------------------------- #
async def test_mn01_expire_sweeps_due_coupons(db, merchant, customer):
    """`expire_at <= now` 的券 → 过期扫描后 `status=expired`。"""
    merchant_id, customer_id = merchant[0]["id"], customer[0]["id"]
    coupon_id = await insert_coupon(db, merchant_id)
    stale = await insert_user_coupon(
        db,
        customer_id,
        coupon_id,
        code="OLD1",
        expire_at=datetime.now(timezone.utc) - timedelta(hours=1),
    )

    count = await expire_coupons_now()

    assert count == 1
    assert await db.fetchval(
        "SELECT status FROM user_coupon WHERE id = $1", stale
    ) == "expired"


async def test_mn02_expire_is_idempotent(db, merchant, customer):
    """连跑 3 次 → 第 2、3 次返回 0，状态不变，**已发积分不受影响**。"""
    merchant_id, customer_id = merchant[0]["id"], customer[0]["id"]
    coupon_id = await insert_coupon(db, merchant_id)
    await insert_user_coupon(
        db,
        customer_id,
        coupon_id,
        code="OLD2",
        expire_at=datetime.now(timezone.utc) - timedelta(hours=1),
    )
    scene = await reward_scene(db, merchant_id, customer_id)
    await settle_now(
        post_id=scene.post_id,
        user_id=customer_id,
        task_id=scene.task_id,
        engagement=18,
    )

    first = await expire_coupons_now()
    rest = [await expire_coupons_now() for _ in range(2)]

    assert first == 1
    assert rest == [0, 0]
    # 券过期不得碰积分账本（spec：「不影响已发积分」）
    assert await points_of(db, customer_id) == 50


async def test_mn03_not_yet_expired_untouched(db, merchant, customer):
    """未过期的券**不得**被改（不能把「扫得到」写成「扫一切」）。"""
    merchant_id, customer_id = merchant[0]["id"], customer[0]["id"]
    coupon_id = await insert_coupon(db, merchant_id)
    fresh = await insert_user_coupon(
        db,
        customer_id,
        coupon_id,
        code="NEW1",
        expire_at=datetime.now(timezone.utc) + timedelta(days=3),
    )

    count = await expire_coupons_now()

    assert count == 0
    assert await db.fetchval(
        "SELECT status FROM user_coupon WHERE id = $1", fresh
    ) == "unused"
