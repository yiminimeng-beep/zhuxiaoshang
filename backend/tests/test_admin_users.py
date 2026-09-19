"""06-admin · `AU` / `AB` / `AL` 三组：用户列表、封禁解封、操作日志。

对应 test_plan.md 的 `AU-01` ~ `AL-04`。

封禁是 06 里**唯一能改变别的模块行为**的动作（被封的人立刻用不了系统），
所以这一组盯两件事：**下界被卡死**（4 字拒 / 5 字放行，`AB-02`/`AB-03` 成对）
与**每一笔都留痕**（`AB-01` 直接断言日志行数，不写日志就红）。
"""

import pytest

from tests.helpers import (
    action_logs,
    admin_token,
    admin_users,
    assert_route_registered,
    ban_user,
    bearer,
    insert_action_log,
    insert_point_ledger,
    insert_task,
    json_of,
    points_of,
    register_customer,
)

pytestmark = pytest.mark.asyncio


async def _user_detail(client, token, user_id: int):
    return await client.get(f"/api/admin/users/{user_id}", headers=bearer(token))


# --------------------------------------------------------------------------- #
# AU · 列表与详情
# --------------------------------------------------------------------------- #
async def test_au01_user_list_shape(client, seed_accounts):
    """`{items,total,page,size}` 齐全，字段含 `account` / `email` / `role` / `status`。"""
    token = await admin_token(client)
    r = await admin_users(client, token)
    assert r.status_code == 200, r.text
    body = json_of(r)
    assert set(("items", "total", "page", "size")) <= set(body), body
    assert body["total"] >= 3, f"种子有三个账号：{body}"
    first = body["items"][0]
    assert set(("account", "email", "role", "status")) <= set(first), first


async def test_au02_filter_by_role(client, seed_accounts, merchant, customer):
    """`?role=merchant` 只返回商户；`?role=customer` 只返回客户。"""
    token = await admin_token(client)
    merchants = json_of(await admin_users(client, token, role="merchant"))
    assert merchants["items"], "至少应有种子商户与注册商户"
    assert all(i["role"] == "merchant" for i in merchants["items"]), merchants["items"]

    customers = json_of(await admin_users(client, token, role="customer"))
    assert customers["items"], "至少应有种子客户"
    assert all(i["role"] == "customer" for i in customers["items"]), customers["items"]


async def test_au03_filter_by_status(client, seed_accounts, customer, set_user_status):
    """`?status=banned` 只返回被封禁的；`?status=deleted` 能筛出已注销的。"""
    token = await admin_token(client)
    await set_user_status(customer[0]["id"], "banned")

    banned = json_of(await admin_users(client, token, status="banned"))
    assert [i["id"] for i in banned["items"]] == [customer[0]["id"]], banned["items"]

    deleted = json_of(await admin_users(client, token, status="deleted"))
    assert deleted["items"] == [], "此时还没有已注销用户"


async def test_au04_keyword_matches_four_fields(client, seed_accounts, merchant):
    """`keyword` 分别命中 `account` / `email` / `nickname` / `shop_name`。

    四个字段各造一个只出现一次的词：只要有一个字段没被搜到，就能定位到是哪一个。
    """
    token = await admin_token(client)
    await register_customer(
        client,
        account="kwaa0001",
        email="kwbb0001@example.com",
        nickname="风口浪尖",
    )

    for kw in ("kwaa0001", "kwbb0001", "风口浪尖"):
        body = json_of(await admin_users(client, token, keyword=kw))
        assert any("kwaa0001" in (i["account"] or "") for i in body["items"]), (
            f"keyword={kw!r} 应命中 kwaa0001：{body['items']}"
        )

    # shop_name 在 merchant_profile 里，不在 user 表上——漏了 join 就搜不到
    shop = json_of(await admin_users(client, token, keyword="测试店铺"))
    assert any(i["id"] == merchant[0]["id"] for i in shop["items"]), (
        f"keyword 必须能匹配 shop_name（走 merchant_profile）：{shop['items']}"
    )


async def test_au05_keyword_wildcards_are_literal(client, seed_accounts, db):
    """⚠️ `keyword` 里的 `_` 按**字面量**处理，不得当通配符。

    自然写法是 `ilike(f"%{kw}%")`，于是搜 `a_b` 会命中 `axb`。这在功能上
    像是「模糊搜索更聪明」，在安全上是逃逸的种子。造两个账号成对验证。
    """
    token = await admin_token(client)
    await register_customer(client, account="wca_bbb1")
    await register_customer(client, account="wcaxbbb1")

    body = json_of(await admin_users(client, token, keyword="wca_bbb1"))
    accounts = {i["account"] for i in body["items"]}
    assert accounts == {"wca_bbb1"}, (
        f"`_` 必须按字面量匹配，不得命中 wcaxbbb1：{accounts}"
    )

    # `%` 同理：搜 `%` 不该命中所有人
    pct = json_of(await admin_users(client, token, keyword="%"))
    assert pct["items"] == [], f"`%` 应按字面量匹配（无人含 %）：{pct['items']}"


async def test_au06_bad_params_are_422(client, seed_accounts):
    """`?role=foo` → `422`；`?size=101` → `422`。"""
    token = await admin_token(client)
    assert (await admin_users(client, token, role="foo")).status_code == 422
    assert (await admin_users(client, token, size=101)).status_code == 422


async def test_au07_user_detail_stats(client, seed_accounts, customer, merchant, db):
    """详情返回 `{user, stats:{task_count, claim_count, job_count, points_balance}}`。

    造数：商户建 1 个任务，客户领 1 次，有 1 个额度流水（不是余额）。
    """
    from tests.helpers import insert_claim

    token = await admin_token(client)
    task_id = await insert_task(db, merchant[0]["id"])
    await insert_claim(db, task_id, customer[0]["id"])
    await insert_point_ledger(db, customer[0]["id"], 70, 70, source="adjust")

    r = await _user_detail(client, token, customer[0]["id"])
    assert r.status_code == 200, r.text
    body = json_of(r)
    assert "user" in body and "stats" in body, body
    stats = body["stats"]
    assert set(("task_count", "claim_count", "job_count", "points_balance")) <= set(stats), stats
    assert stats["claim_count"] == 1, stats
    assert stats["points_balance"] == 70, stats


async def test_au08_merchant_detail_has_profile(client, seed_accounts, merchant, customer):
    """商户详情额外返回 `merchant_profile`；客户详情**不含**该键。"""
    token = await admin_token(client)

    m = json_of(await _user_detail(client, token, merchant[0]["id"]))
    assert "merchant_profile" in m, f"商户详情应含 merchant_profile：{m}"
    assert m["merchant_profile"], "商户档案不该是空的"

    c = json_of(await _user_detail(client, token, customer[0]["id"]))
    assert "merchant_profile" not in c or c["merchant_profile"] is None, (
        f"客户不该有商户档案：{c}"
    )


async def test_au09_unknown_user_is_404(client, seed_accounts):
    """不存在的 id → `404`。"""
    assert_route_registered("GET", "/api/admin/users/{user_id}")
    token = await admin_token(client)
    r = await _user_detail(client, token, 999999)
    assert r.status_code == 404, f"用户不存在应 404：{r.status_code} {r.text}"


async def test_au10_points_balance_matches_ledger(
    client, seed_accounts, customer, db
):
    """`stats.points_balance` == `points_of(db, uid)`——与 05 的账本一致。

    这里防的是「另算一遍」：余额若从别的表（比如 `reward_grant`）推，
    和 `point_ledger` 的合计迟早会漂——而用户看到的余额是账本那一本。
    """
    token = await admin_token(client)
    uid = customer[0]["id"]
    await insert_point_ledger(db, uid, 500, 500, source="adjust")
    await insert_point_ledger(db, uid, -120, 380, source="redemption", ref_type="r")

    body = json_of(await _user_detail(client, token, uid))
    assert body["stats"]["points_balance"] == 380
    assert body["stats"]["points_balance"] == await points_of(db, uid)


# --------------------------------------------------------------------------- #
# AB · 封禁与解封
# --------------------------------------------------------------------------- #
async def test_ab01_ban_succeeds_and_logs(client, seed_accounts, customer, db):
    """封禁成功 → `200`、`status='banned'`，**并写一条 `ban_user` 日志含 reason**。

    ⚠️ 断言写成 `len(logs) == 1` 而不是「查得到刚写的那行」：
    后者在实现完全不写日志时会因为查询返回空而侥幸通过（`all([])` 恒真）。
    """
    token = await admin_token(client)
    uid = customer[0]["id"]

    r = await ban_user(client, token, uid, reason="违规内容发布")
    assert r.status_code == 200, r.text
    assert await db.fetchval('SELECT status FROM "user" WHERE id = $1', uid) == "banned"

    logs = await action_logs(db, target_type="user", target_id=uid)
    assert len(logs) == 1, f"封禁必须恰好写一条日志，实际 {len(logs)} 条：{logs}"
    assert logs[0]["action"] == "ban_user", logs[0]
    detail = logs[0]["detail"] or {}
    assert "违规内容发布" in str(detail.get("reason", detail)), (
        f"detail 必须含 reason：{detail}"
    )


async def test_ab02_reason_four_chars_rejected(client, seed_accounts, customer, db):
    """`reason` **4 字** → `422`（spec 明写「< 5 字」）。"""
    token = await admin_token(client)
    r = await ban_user(client, token, customer[0]["id"], reason="内容违规")
    assert r.status_code == 422, f"4 字理由应 422：{r.status_code} {r.text}"
    assert await db.fetchval("SELECT count(*) FROM admin_action_log") == 0, (
        "被拒的封禁不得留痕"
    )


async def test_ab03_reason_five_chars_accepted(client, seed_accounts, customer):
    """`reason` **5 字** → `200`。

    与 `AB-02` 成对：只有下界被卡死，才说明比较写的是 `< 5`，
    而不是 `<= 5`（那样 5 字也会被拒）或 `< 10`（那样 4 字也会放行）。
    """
    token = await admin_token(client)
    r = await ban_user(client, token, customer[0]["id"], reason="内容违规了")
    assert r.status_code == 200, f"5 字理由应放行：{r.status_code} {r.text}"


async def test_ab04_double_ban_is_409(client, seed_accounts, customer):
    """重复封禁 → `409`。"""
    token = await admin_token(client)
    uid = customer[0]["id"]
    assert (await ban_user(client, token, uid)).status_code == 200
    r = await ban_user(client, token, uid)
    assert r.status_code == 409, f"已是 banned 再封应 409：{r.status_code} {r.text}"


async def test_ab05_ban_admin_is_409(client, seed_accounts, db):
    """⚠️ **封禁 admin → `409`**。

    允许互封 = 任何一个管理员都能锁死整个后台，且**没有恢复入口**
    （spec 明确不做「新增管理员界面」，admin 只由种子脚本写入）。
    """
    token = await admin_token(client)
    admin_id = next(uid for uid, _, role in seed_accounts if role == "admin")

    r = await ban_user(client, token, admin_id, reason="管理员互封测试")
    assert r.status_code == 409, f"封禁 admin 应 409：{r.status_code} {r.text}"
    assert await db.fetchval('SELECT status FROM "user" WHERE id = $1', admin_id) == "active"


async def test_ab06_ban_deleted_user_is_409(
    client, seed_accounts, customer, set_user_status
):
    """封禁已注销用户（`status='deleted'`）→ `409`。

    注销是不可逆的（01 把标识符置 null 并写墓碑表），封一个已经不可用的
    账号不会产生任何效果，只会往审计里塞一条假的「执法记录」。
    """
    token = await admin_token(client)
    uid = customer[0]["id"]
    await set_user_status(uid, "deleted")

    r = await ban_user(client, token, uid, reason="注销后补封测试")
    assert r.status_code == 409, f"封禁已注销用户应 409：{r.status_code} {r.text}"


async def test_ab07_ban_takes_effect_immediately(client, seed_accounts, customer):
    """封禁后该用户的原 token 下次请求 → `403`（立即生效，不等 token 过期）。"""
    token = await admin_token(client)
    _, c_token, _ = customer
    assert (
        await client.get("/api/me", headers=bearer(c_token))
    ).status_code == 200, "封禁前应当能正常访问"

    assert (await ban_user(client, token, customer[0]["id"])).status_code == 200

    r = await client.get("/api/me", headers=bearer(c_token))
    assert r.status_code == 403, (
        f"封禁必须立刻生效，不必等 token 过期：{r.status_code} {r.text}"
    )


async def test_ab08_unban_restores_access(client, seed_accounts, customer, db):
    """解封 → `200`，`status` 回到 `active`，用户能再次正常使用。

    只断言状态码不够：`status` 写错了下一个请求照样 403，
    而封禁的功能就是在「能不能用」上。
    """
    token = await admin_token(client)
    _, c_token, _ = customer
    uid = customer[0]["id"]
    await ban_user(client, token, uid)

    r = await client.post(f"/api/admin/users/{uid}/unban", headers=bearer(token))
    assert r.status_code == 200, r.text
    assert await db.fetchval('SELECT status FROM "user" WHERE id = $1', uid) == "active"
    assert (
        await client.get("/api/me", headers=bearer(c_token))
    ).status_code == 200, "解封后必须能再用"

    logs = await action_logs(db, target_type="user", target_id=uid)
    actions = [row["action"] for row in logs]
    assert "unban_user" in actions, f"解封也要留痕：{actions}"


async def test_ab09_unban_active_user_is_409(client, seed_accounts, customer):
    """解封一个非 `banned` 的用户 → `409`。"""
    token = await admin_token(client)
    r = await client.post(
        f"/api/admin/users/{customer[0]['id']}/unban", headers=bearer(token)
    )
    assert r.status_code == 409, f"解封未封禁用户应 409：{r.status_code} {r.text}"


async def test_ab10_ban_unban_unknown_is_404(client, seed_accounts):
    """`ban` / `unban` 不存在的 id → `404`。

    ⚠️ **两条路由各查一次**：本用例断言了 **两个** 404，而 FastAPI 对不存在的
    路由也返回 404。只查 `ban` 的话，`unban` 没注册时下面那行照样绿——
    与 07 的 `LG-07`（要求至少扫到 2 条 ledger 读路由）是同一类加固。
    """
    assert_route_registered("POST", "/api/admin/users/{user_id}/ban")
    assert_route_registered("POST", "/api/admin/users/{user_id}/unban")
    token = await admin_token(client)
    assert (await ban_user(client, token, 999999)).status_code == 404
    r = await client.post("/api/admin/users/999999/unban", headers=bearer(token))
    assert r.status_code == 404


# --------------------------------------------------------------------------- #
# AL · 操作日志
# --------------------------------------------------------------------------- #
async def _logs(client, token, **params):
    return await client.get(
        "/api/admin/action-logs", headers=bearer(token), params=params or None
    )


async def test_al01_filter_by_target(client, seed_accounts, customer, db):
    """`?target_type=user&target_id=X` 只返回该 target 的日志，倒序。"""
    token = await admin_token(client)
    uid = customer[0]["id"]

    await insert_action_log(db, 1, action="ban_user", target_id=uid)
    await insert_action_log(db, 1, action="unban_user", target_id=uid)
    await insert_action_log(db, 1, action="ban_user", target_id=uid + 1000)

    r = await _logs(client, token, target_type="user", target_id=uid)
    assert r.status_code == 200, r.text
    items = json_of(r)["items"]
    assert len(items) == 2, f"只该返回该 target 的日志：{items}"
    assert [i["action"] for i in items] == ["unban_user", "ban_user"], "应按 id 倒序"


async def test_al02_detail_is_object(client, seed_accounts, customer, db):
    """`detail` 是**对象**，不是字符串——`as_json` 后可直接取 `["reason"]`。

    spec 的字段表写 `{reason, before, after}`。若列类型写成 text，
    这里取到的会是一串 `'{"reason": ...}'`，前端要自己 parse。
    """
    token = await admin_token(client)
    uid = customer[0]["id"]
    await insert_action_log(
        db, 1, action="ban_user", target_id=uid, detail={"reason": "违规内容发布"}
    )

    items = json_of(await _logs(client, token, target_type="user", target_id=uid))["items"]
    assert isinstance(items[0]["detail"], dict), (
        f"detail 必须是对象（jsonb），实际 {type(items[0]['detail'])}"
    )
    assert items[0]["detail"]["reason"] == "违规内容发布"


async def test_al03_bad_target_type_is_422(client, seed_accounts):
    """`?target_type=foo` → `422`。"""
    token = await admin_token(client)
    r = await _logs(client, token, target_type="foo")
    assert r.status_code == 422, f"target_type 非法应 422：{r.status_code} {r.text}"


async def test_al04_non_admin_403_and_size_limit(client, seed_accounts, customer):
    """非 admin → `403`；`size=101` → `422`。"""
    _, c_token, _ = customer
    assert (await _logs(client, c_token)).status_code == 403

    token = await admin_token(client)
    assert (await _logs(client, token, size=101)).status_code == 422
