"""06-admin 追加 · `FB` / `FL` / `FR` 三组：用户反馈（45 条）。

对应 test_plan.md 的 `FB-01` ~ `FR-11`。

这一批盯三件事：

1. **`role` 由服务端推导**（全局约定 #11 的同款）。`FB-04` / `FB-05`（请求体带
   `role` / `user_id` → `422`）必须与 `FB-02` / `FB-03`（不带 → 按提交者落对角色）
   **成对**——只测前者的话，「实现直接把请求体的 role 存进去」照样绿。
2. **新提交不得是已处理**（`FB-18`）。三个处理态字段都得是干净的初始值。
3. **每次处理恰好一条痕，重复处理不得多写**（`FR-03` / `FR-06` / `FR-07` / `FR-10`）。
   写法一律 `assert 行数 == 期望值`：写成「查得到我刚写的那行」时，
   完全不写日志也会因 `all([])` 恒真而绿。
"""

import asyncio
from datetime import datetime, timezone

import pytest

from tests.helpers import (
    action_logs,
    admin_feedback,
    admin_token,
    app_source,
    assert_route_registered,
    feedback_mutations,
    feedback_payload,
    feedback_row,
    feedback_rows,
    insert_feedback,
    json_of,
    reopen_feedback,
    resolve_feedback,
    submit_feedback,
    submit_feedback_ok,
)

pytestmark = pytest.mark.asyncio

FEEDBACK_POST = "/api/feedback"
FEEDBACK_LIST = "/api/admin/feedback"
RESOLVE_PATH = "/api/admin/feedback/{id}/resolve"
REOPEN_PATH = "/api/admin/feedback/{id}/reopen"

_ADMIN_INDEX = 2  # seed_accounts 的顺序：商户 / 客户 / 管理员


def _admin_id(seed_accounts) -> int:
    return seed_accounts[_ADMIN_INDEX][0]


async def _insert_second_admin(db) -> int:
    """再插一个 admin（注册接口不允许 `role=admin`，只能直插）。

    密码哈希直接复制种子管理员的——并发用例只走令牌，不需要知道密码。
    按 `role` 取种子管理员，不写死 id（种子顺序变了就会拷错人的哈希）。
    """
    return await db.fetchval(
        """
        INSERT INTO "user" (account, password_hash, role, nickname, status)
        SELECT 'admin0002', password_hash, 'admin', '二号管理员', 'active'
        FROM "user" WHERE role = 'admin' ORDER BY id LIMIT 1
        RETURNING id
        """
    )


# --------------------------------------------------------------------------- #
# FB · 提交
# --------------------------------------------------------------------------- #
async def test_fb01_submit_requires_login(client):
    """未登录 → `401`（不是 403，也不是 201）。"""
    r = await client.post(FEEDBACK_POST, json=feedback_payload())
    assert r.status_code == 401, r.text


async def test_fb02_merchant_submit_lands_merchant(client, merchant, db):
    """商户提交 → `201`，落库 `role='merchant'`。"""
    user, token, _ = merchant
    body = await submit_feedback_ok(client, token)

    rows = await feedback_rows(db, user_id=user["id"])
    assert len(rows) == 1, rows
    assert rows[0]["role"] == "merchant", rows[0]
    assert body["role"] == "merchant", body


async def test_fb03_customer_submit_lands_customer(client, customer, db):
    """客户提交 → `201`，落库 `role='customer'`（与 `FB-02` 成对）。"""
    user, token, _ = customer
    body = await submit_feedback_ok(client, token)

    rows = await feedback_rows(db, user_id=user["id"])
    assert len(rows) == 1, rows
    assert rows[0]["role"] == "customer", rows[0]
    assert body["role"] == "customer", body


async def test_fb04_body_role_field_rejected(client, merchant, db):
    """请求体带 `role` → `422`，且**库里不落任何行**。

    后半句是重点：只断 422 的话，「忽略了 role 但还是存了这条」会绿。
    """
    _, token, _ = merchant
    r = await submit_feedback(client, token, role="admin")
    assert r.status_code == 422, r.text
    assert await feedback_rows(db) == [], "带 role 的请求不得落库"


async def test_fb05_body_user_id_field_rejected(client, customer, merchant, db):
    """请求体带 `user_id`（指向别人）→ `422`，且库里不落行。"""
    _, token, _ = customer
    other_id = merchant[0]["id"]
    r = await submit_feedback(client, token, user_id=other_id)
    assert r.status_code == 422, r.text
    assert await feedback_rows(db) == [], "带 user_id 的请求不得落库"


async def test_fb06_admin_submit_forbidden(client, seed_accounts):
    """`admin` 提交 → `403`——后台不是反馈者。"""
    token = await admin_token(client)
    r = await submit_feedback(client, token)
    assert r.status_code == 403, r.text


async def test_fb07_deleted_user_forbidden(client, customer, db, set_user_status):
    """已注销用户提交 → `403`（`deps` 对非 `active` 一律拦）。"""
    user, token, _ = customer
    await set_user_status(user["id"], "deleted")

    r = await submit_feedback(client, token)
    assert r.status_code == 403, r.text
    assert await feedback_rows(db) == []


async def test_fb08_banned_user_forbidden(client, merchant, db, set_user_status):
    """已封禁用户提交 → `403`。"""
    user, token, _ = merchant
    await set_user_status(user["id"], "banned")

    r = await submit_feedback(client, token)
    assert r.status_code == 403, r.text
    assert await feedback_rows(db) == []


async def test_fb09_content_four_chars_rejected(client, customer):
    """`content` 4 字 → `422`。"""
    _, token, _ = customer
    r = await submit_feedback(client, token, content="四个字啊")
    assert r.status_code == 422, r.text


async def test_fb10_content_five_chars_accepted(client, customer, db):
    """`content` 5 字 → `201`（与 `FB-09` 成对：下界是 5，不是 4 也不是 6）。"""
    _, token, _ = customer
    await submit_feedback_ok(client, token, content="刚好五个字")

    rows = await feedback_rows(db)
    assert len(rows) == 1, rows
    assert rows[0]["content"] == "刚好五个字", rows[0]


async def test_fb11_content_five_hundred_accepted(client, customer):
    """`content` 恰好 500 字 → `201`。"""
    _, token, _ = customer
    await submit_feedback_ok(client, token, content="字" * 500)


async def test_fb12_content_five_oh_one_rejected(client, customer, db):
    """`content` 501 字 → `422`（与 `FB-11` 成对）。"""
    _, token, _ = customer
    r = await submit_feedback(client, token, content="字" * 501)
    assert r.status_code == 422, r.text
    assert await feedback_rows(db) == []


async def test_fb13_blank_content_rejected(client, customer):
    """`content` 全空白 → `422`（按**去首尾空白后**的字数判）。"""
    _, token, _ = customer
    r = await submit_feedback(client, token, content=" " * 20)
    assert r.status_code == 422, r.text


async def test_fb14_bad_category_rejected(client, customer):
    """`category='foo'` → `422`。"""
    _, token, _ = customer
    r = await submit_feedback(client, token, category="foo")
    assert r.status_code == 422, r.text


async def test_fb15_three_categories_accepted(client, customer, db):
    """`bug` / `suggestion` / `other` 三个合法值各提交成功一次，落库与传入一致。"""
    _, token, _ = customer
    for category in ("bug", "suggestion", "other"):
        body = await submit_feedback_ok(client, token, category=category)
        assert body["category"] == category, body

    rows = await feedback_rows(db)
    assert sorted(r["category"] for r in rows) == ["bug", "other", "suggestion"], rows


async def test_fb16_contact_optional(client, customer, db):
    """`contact` 省略 → `201`，落库 `contact` 为 `NULL`。"""
    _, token, _ = customer
    body = await submit_feedback_ok(client, token)
    assert body["contact"] is None, body

    rows = await feedback_rows(db)
    assert rows[0]["contact"] is None, rows[0]


async def test_fb17_contact_length_bound(client, customer, db):
    """`contact` 128 字 → `201`；129 字 → `422`（成对）。"""
    _, token, _ = customer
    await submit_feedback_ok(client, token, contact="c" * 128)

    r = await submit_feedback(client, token, contact="c" * 129)
    assert r.status_code == 422, r.text
    assert len(await feedback_rows(db)) == 1, "超长的那条不得落库"


async def test_fb18_new_feedback_is_open_and_unresolved(client, customer, db):
    """新提交必须**恰好一行**，且 `status='open'`、两个处理态字段均为 `NULL`。"""
    user, token, _ = customer
    await submit_feedback_ok(client, token)

    rows = await feedback_rows(db, user_id=user["id"])
    assert len(rows) == 1, rows
    row = rows[0]
    assert row["status"] == "open", row
    assert row["resolved_at"] is None, row
    assert row["resolved_by"] is None, row


async def test_fb19_response_keys_exact(client, customer):
    """响应体键**恰好**为 spec 那六个——不含 `user_id`、不含 `status`。"""
    _, token, _ = customer
    body = await submit_feedback_ok(client, token)
    assert set(body) == {
        "id",
        "role",
        "category",
        "content",
        "contact",
        "created_at",
    }, body


# --------------------------------------------------------------------------- #
# FL · 列表
# --------------------------------------------------------------------------- #
async def test_fl01_list_shape(client, seed_accounts, merchant, customer, db):
    """`{items,total,page,size}` 与 item 的字段齐全。"""
    token = await admin_token(client)
    await insert_feedback(db, merchant[0]["id"])
    await insert_feedback(db, customer[0]["id"], category="bug")

    body = json_of(await admin_feedback(client, token))
    assert set(("items", "total", "page", "size")) <= set(body), body
    assert body["total"] == 2, body

    expected = {
        "id", "user_id", "account", "nickname", "role", "category",
        "content", "contact", "status", "resolved_at", "resolved_by", "created_at",
    }
    assert expected <= set(body["items"][0]), body["items"][0]


async def test_fl02_sorted_desc(client, seed_accounts, merchant, db):
    """倒序 `created_at DESC, id DESC`：同一时刻的两条，后插的 id 在前。"""
    token = await admin_token(client)
    when = datetime.now(timezone.utc)
    older = await insert_feedback(db, merchant[0]["id"], created_at=when)
    newer = await insert_feedback(db, merchant[0]["id"], created_at=when)

    body = json_of(await admin_feedback(client, token))
    assert [i["id"] for i in body["items"]] == [newer, older], body["items"]


async def test_fl03_filter_by_merchant_role(client, seed_accounts, merchant, customer, db):
    """`?role=merchant` 只返回商家反馈。"""
    token = await admin_token(client)
    merchant_fb = await insert_feedback(db, merchant[0]["id"])
    await insert_feedback(db, customer[0]["id"])

    body = json_of(await admin_feedback(client, token, role="merchant"))
    assert [i["id"] for i in body["items"]] == [merchant_fb], body["items"]


async def test_fl04_filter_by_customer_role(client, seed_accounts, merchant, customer, db):
    """`?role=customer` 只返回用户反馈——**这是「分类」的核心断言**。"""
    token = await admin_token(client)
    await insert_feedback(db, merchant[0]["id"])
    customer_fb = await insert_feedback(db, customer[0]["id"])

    body = json_of(await admin_feedback(client, token, role="customer"))
    assert [i["id"] for i in body["items"]] == [customer_fb], body["items"]


async def test_fl05_filter_by_open_status(client, seed_accounts, merchant, db):
    """`?status=open` 只返回未处理的。"""
    token = await admin_token(client)
    admin_id = _admin_id(seed_accounts)
    open_fb = await insert_feedback(db, merchant[0]["id"])
    await insert_feedback(
        db, merchant[0]["id"], status="resolved",
        resolved_at=datetime.now(timezone.utc), resolved_by=admin_id,
    )

    body = json_of(await admin_feedback(client, token, status="open"))
    assert [i["id"] for i in body["items"]] == [open_fb], body["items"]


async def test_fl06_filter_by_resolved_status(client, seed_accounts, merchant, db):
    """`?status=resolved` 只返回已处理的。"""
    token = await admin_token(client)
    admin_id = _admin_id(seed_accounts)
    await insert_feedback(db, merchant[0]["id"])
    resolved_fb = await insert_feedback(
        db, merchant[0]["id"], status="resolved",
        resolved_at=datetime.now(timezone.utc), resolved_by=admin_id,
    )

    body = json_of(await admin_feedback(client, token, status="resolved"))
    assert [i["id"] for i in body["items"]] == [resolved_fb], body["items"]


async def test_fl07_filter_by_category(client, seed_accounts, merchant, db):
    """`?category=bug` 只返回该类型。"""
    token = await admin_token(client)
    bug = await insert_feedback(db, merchant[0]["id"], category="bug")
    await insert_feedback(db, merchant[0]["id"], category="suggestion")

    body = json_of(await admin_feedback(client, token, category="bug"))
    assert [i["id"] for i in body["items"]] == [bug], body["items"]


async def test_fl08_bad_role_filter(client, seed_accounts):
    """`?role=foo` → `422`（不是空列表）。"""
    token = await admin_token(client)
    r = await admin_feedback(client, token, role="foo")
    assert r.status_code == 422, r.text


async def test_fl09_bad_category_filter(client, seed_accounts):
    """`?category=foo` → `422`。"""
    token = await admin_token(client)
    r = await admin_feedback(client, token, category="foo")
    assert r.status_code == 422, r.text


async def test_fl10_bad_status_filter(client, seed_accounts):
    """`?status=foo` → `422`。"""
    token = await admin_token(client)
    r = await admin_feedback(client, token, status="foo")
    assert r.status_code == 422, r.text


async def test_fl11_page_size_bound(client, seed_accounts, merchant, db):
    """`size=100` → `200`；`size=101` → `422`（成对）。"""
    token = await admin_token(client)
    await insert_feedback(db, merchant[0]["id"])

    assert (await admin_feedback(client, token, size=100)).status_code == 200
    assert (await admin_feedback(client, token, size=101)).status_code == 422


async def test_fl12_merchant_forbidden(client, merchant):
    """商户访问反馈列表 → `403`。"""
    _, token, _ = merchant
    r = await admin_feedback(client, token)
    assert r.status_code == 403, r.text


async def test_fl13_customer_forbidden(client, customer):
    """客户访问反馈列表 → `403`。"""
    _, token, _ = customer
    r = await admin_feedback(client, token)
    assert r.status_code == 403, r.text


async def test_fl14_list_requires_login(client):
    """未登录 → `401`。"""
    r = await client.get(FEEDBACK_LIST)
    assert r.status_code == 401, r.text


async def test_fl15_submitter_traceable_after_close(
    client, seed_accounts, customer, db, set_user_status
):
    """`account` / `nickname` 取自 `user`；提交人被注销后该条**仍在列表里**。"""
    token = await admin_token(client)
    user, _, _ = customer
    fb_id = await insert_feedback(db, user["id"])

    body = json_of(await admin_feedback(client, token))
    item = body["items"][0]
    assert item["account"] == user["account"], item
    assert item["nickname"] == user["nickname"], item

    await set_user_status(user["id"], "deleted")
    after = json_of(await admin_feedback(client, token))
    assert [i["id"] for i in after["items"]] == [fb_id], (
        "外键无 CASCADE，留痕不随人消失"
    )


# --------------------------------------------------------------------------- #
# FR · 处理状态
# --------------------------------------------------------------------------- #
async def test_fr01_resolve_missing_id(client, seed_accounts):
    """`resolve` 不存在的 id → `404`。"""
    assert_route_registered("POST", RESOLVE_PATH)
    token = await admin_token(client)
    r = await resolve_feedback(client, token, 999999)
    assert r.status_code == 404, r.text


async def test_fr02_resolve_marks_resolved(client, seed_accounts, customer, db):
    """`resolve` 成功 → `status='resolved'`、`resolved_at` 非空、`resolved_by` **是该 admin**。"""
    token = await admin_token(client)
    admin_id = _admin_id(seed_accounts)
    fb_id = await insert_feedback(db, customer[0]["id"])

    body = await resolve_feedback(client, token, fb_id)
    assert body.status_code == 200, body.text
    payload = body.json()
    assert payload["status"] == "resolved", payload
    assert payload["resolved_by"] == admin_id, payload
    assert payload["resolved_at"], payload

    row = await feedback_row(db, fb_id)
    assert row["status"] == "resolved", dict(row)
    assert row["resolved_by"] == admin_id, dict(row)
    assert row["resolved_at"] is not None, dict(row)


async def test_fr03_second_resolve_conflicts_and_writes_nothing(
    client, seed_accounts, customer, db
):
    """重复 `resolve` → `409`，且审计**仍只有 1 条**。"""
    token = await admin_token(client)
    fb_id = await insert_feedback(db, customer[0]["id"])
    assert (await resolve_feedback(client, token, fb_id)).status_code == 200

    again = await resolve_feedback(client, token, fb_id)
    assert again.status_code == 409, again.text
    assert len(await action_logs(db, target_type="feedback")) == 1, "不得多写一条痕"


async def test_fr04_reopen_open_row_conflicts(client, seed_accounts, customer, db):
    """`reopen` 一条本就没处理的 → `409`。"""
    assert_route_registered("POST", REOPEN_PATH)
    token = await admin_token(client)
    fb_id = await insert_feedback(db, customer[0]["id"])

    r = await reopen_feedback(client, token, fb_id)
    assert r.status_code == 409, r.text


async def test_fr05_reopen_clears_resolution_fields(client, seed_accounts, customer, db):
    """`reopen` 后 → `status='open'`，且 `resolved_at` / `resolved_by` **均置回 NULL**。"""
    token = await admin_token(client)
    fb_id = await insert_feedback(db, customer[0]["id"])
    assert (await resolve_feedback(client, token, fb_id)).status_code == 200

    body = await reopen_feedback(client, token, fb_id)
    assert body.status_code == 200, body.text
    payload = body.json()
    assert payload["status"] == "open", payload
    assert payload["resolved_at"] is None, payload
    assert payload["resolved_by"] is None, payload

    row = await feedback_row(db, fb_id)
    assert row["status"] == "open", dict(row)
    assert row["resolved_at"] is None, "只改 status 会留下脏字段"
    assert row["resolved_by"] is None, "只改 status 会留下脏字段"


async def test_fr06_resolve_writes_exactly_one_log(client, seed_accounts, customer, db):
    """`resolve` 写**恰好 1 条** `admin_action_log`，四个字段都对得上。"""
    token = await admin_token(client)
    admin_id = _admin_id(seed_accounts)
    fb_id = await insert_feedback(db, customer[0]["id"])
    assert (await resolve_feedback(client, token, fb_id)).status_code == 200

    logs = await action_logs(db, target_type="feedback")
    assert len(logs) == 1, logs
    assert logs[0]["action"] == "resolve_feedback", logs[0]
    assert logs[0]["target_id"] == fb_id, logs[0]
    assert logs[0]["admin_id"] == admin_id, logs[0]


async def test_fr07_reopen_writes_exactly_one_log(client, seed_accounts, customer, db):
    """`reopen` 写**恰好 1 条** `reopen_feedback`。"""
    token = await admin_token(client)
    fb_id = await insert_feedback(db, customer[0]["id"])
    await resolve_feedback(client, token, fb_id)
    assert (await reopen_feedback(client, token, fb_id)).status_code == 200

    logs = await action_logs(db, target_type="feedback")
    reopened = [row for row in logs if row["action"] == "reopen_feedback"]
    assert len(reopened) == 1, logs
    assert reopened[0]["target_id"] == fb_id, reopened[0]


async def test_fr08_merchant_and_customer_cannot_resolve(
    client, seed_accounts, merchant, customer, db
):
    """商户调 `resolve` → `403`；**客户**调 `resolve` → `403`（防「只挂了商户闸」）。"""
    fb_id = await insert_feedback(db, customer[0]["id"])

    _, merchant_token, _ = merchant
    _, customer_token, _ = customer
    assert (await resolve_feedback(client, merchant_token, fb_id)).status_code == 403
    assert (await resolve_feedback(client, customer_token, fb_id)).status_code == 403


async def test_fr09_merchant_and_customer_cannot_reopen(
    client, seed_accounts, merchant, customer, db
):
    """商户 / 客户调 `reopen` → `403`。"""
    fb_id = await insert_feedback(db, customer[0]["id"])

    _, merchant_token, _ = merchant
    _, customer_token, _ = customer
    assert (await reopen_feedback(client, merchant_token, fb_id)).status_code == 403
    assert (await reopen_feedback(client, customer_token, fb_id)).status_code == 403


async def test_fr10_concurrent_resolve_yields_one_winner(
    client, seed_accounts, customer, db
):
    """两个 admin 同时 `resolve` 同一条 → 恰好 1 个 `200`、1 个 `409`，日志 1 条。

    断言取**与顺序无关**的量（成功数、日志行数），不写死谁先谁后。
    """
    await _insert_second_admin(db)
    first_token = await admin_token(client)
    second_token = await _token_of_second_admin(client, db)

    fb_id = await insert_feedback(db, customer[0]["id"])
    r1, r2 = await asyncio.gather(
        resolve_feedback(client, first_token, fb_id),
        resolve_feedback(client, second_token, fb_id),
    )

    assert sorted([r1.status_code, r2.status_code]) == [200, 409], (
        f"应当恰好一个赢：{[r1.status_code, r2.status_code]}"
    )
    assert len(await action_logs(db, target_type="feedback")) == 1, "恰好一条痕"


async def _token_of_second_admin(client, db) -> str:
    """给刚直插的 `admin0002` 签一个令牌。"""
    from tests.helpers import token_for

    second_id = await db.fetchval(
        'SELECT id FROM "user" WHERE account = \'admin0002\''
    )
    assert second_id is not None, "`_insert_second_admin` 没跑"
    return token_for(second_id)


async def test_fr11_no_mutating_endpoints(client, seed_accounts):
    """反馈只能标处理状态：没有删它的端点，也没有改 `content` / `role` / `user_id` 的语句。

    ⚠️ 开头四条 `assert_route_registered` 是**必需**的：端点一个都没注册时，
    扫描会平凡成立（同 05 的 `PL-07` / 07 的 `LG-07` 被订正过的那个坑）。
    """
    assert_route_registered("POST", FEEDBACK_POST)
    assert_route_registered("GET", FEEDBACK_LIST)
    assert_route_registered("POST", RESOLVE_PATH)
    assert_route_registered("POST", REOPEN_PATH)

    hits = feedback_mutations(app_source())
    assert hits == [], f"源码里出现了不该有的 user_feedback 写操作：{hits}"
