"""06-admin 追加 · `DS` 组：今日使用量（12 条）。

对应 test_plan.md 的 `DS-01` ~ `DS-12`。

这一批盯两件事：

1. **切日是北京时间（UTC+8），不是 UTC**。`DS-05` / `DS-08` / `DS-11` 从三个方向
   钉它：边界前 1 分钟（北京昨天 `23:59` 不计入）、换一天（查昨天只看昨天）、
   以及「UTC 日期与北京日期不是同一天」的那一刻。拿掉这三条，把区间换成
   UTC 整天，其余九条全绿——即它们才是唯一能发现「切日用了 UTC」的用例。
2. **空窗口不得 `500`、不得 `null`**（`DS-04`）。聚合端点最常见的坏法是
   `SUM()` 在零行时返回 `None`，透出去前端就渲染成 `null`。本用例造的正是
   这种局面：`user` 表有行（种子账号），而 `task` / `task_claim` / `social_post` /
   `content_job` 四张业务表**一行都没有**——那四个数落进 SUM 就是 NULL。

⚠️ `active` / `new` 都按时间区间切，而测试里的时间戳默认是「现在」。
不显式指定时刻的话，「昨天 23:59 不计入」这条**根本造不出来**，
`DS-05` 会退化成「今天的登录算今天」（平凡成立）。故一律走
`set_last_login` / `set_created_at` 显式落时刻。
"""

from datetime import datetime, timedelta, timezone

import pytest

from tests.helpers import (
    admin_token,
    assert_route_registered,
    beijing_day_end,
    beijing_midnight,
    beijing_today,
    daily_stats,
    daily_stats_ok,
    insert_claim,
    insert_job,
    insert_post,
    insert_task,
    register_customer,
    set_created_at,
    set_last_login,
)

pytestmark = pytest.mark.asyncio

STATS_PATH = "/api/admin/stats/daily"

_ACTIVITY_KEYS = {"tasks_created", "claims", "posts_submitted", "jobs_created"}
_ROLE_KEYS = {"merchant", "customer"}


def _noon(day) -> "datetime":
    """北京 `day` 当天中午对应的 UTC 时刻——窗口正中，离两端都远。"""
    return beijing_midnight(day) + timedelta(hours=12)


async def _id_of(db, account: str) -> int:
    uid = await db.fetchval('SELECT id FROM "user" WHERE account = $1', account)
    assert uid is not None, f"账号 {account!r} 不存在——fixture 没建？"
    return uid


# --------------------------------------------------------------------------- #
# 权限与入参
# --------------------------------------------------------------------------- #
async def test_ds01_requires_login(client):
    """未登录 → `401`（不是 404、不是 403）。"""
    assert_route_registered("GET", STATS_PATH)
    r = await client.get(STATS_PATH)
    assert r.status_code == 401, r.text


async def test_ds02_merchant_and_customer_forbidden(client, merchant, customer):
    """商户 → `403`；客户 → `403`（两条都要，防「只挂了商户闸」）。"""
    _, merchant_token, _ = merchant
    _, customer_token, _ = customer
    assert (await daily_stats(client, merchant_token)).status_code == 403
    assert (await daily_stats(client, customer_token)).status_code == 403


async def test_ds03_bad_date_rejected(client, seed_accounts):
    """`date` 格式不合法、或格式合法但日期不存在 → `422`。

    `2026-02-30` 是重点：字符串匹配能过，真解析才拦得住。
    """
    token = await admin_token(client)
    for bad in ("2026-13-45", "notadate", "2026-02-30"):
        r = await daily_stats(client, token, bad)
        assert r.status_code == 422, f"{bad!r} 应当 422，实际 {r.status_code}：{r.text}"


# --------------------------------------------------------------------------- #
# 空窗口
# --------------------------------------------------------------------------- #
async def test_ds04_empty_window_all_zeros(client, seed_accounts):
    """窗口内无任何事件 → `200`，九个数**全为 `0`**，且**没有一个是 `None`**。

    这里 `task` / `task_claim` / `social_post` / `content_job` 四张表是**真空的**
    （种子只建了用户），故 `activity` 那四个数走的正是「零行聚合」——
    写成 `SUM(...)` 就会拿到 `None`。
    """
    token = await admin_token(client)
    body = await daily_stats_ok(client, token, "2000-01-01")

    assert body["active"] == {"merchant": 0, "customer": 0}, body
    assert body["new"] == {"merchant": 0, "customer": 0}, body
    assert body["activity"] == {
        "tasks_created": 0,
        "claims": 0,
        "posts_submitted": 0,
        "jobs_created": 0,
    }, body

    for group in ("active", "new", "activity"):
        for key, value in body[group].items():
            assert value is not None, f"{group}.{key} 透出了 null：{body}"


# --------------------------------------------------------------------------- #
# active · 登录
# --------------------------------------------------------------------------- #
async def test_ds05_beijing_midnight_counts_yesterday_2359_does_not(
    client, seed_accounts, merchant, db
):
    """北京今天 `00:00` 整的登录**计入**；北京昨天 `23:59:59` 的**不计入**。

    成对断言，钉死下界是 `>=` 且区间左闭。写成 UTC 整天，两边都会错位。
    """
    token = await admin_token(client)
    today = beijing_today()
    yesterday = today - timedelta(days=1)

    at_midnight = seed_accounts[0][0]  # 种子商户
    before_midnight = merchant[0]["id"]  # shop0001

    await set_last_login(db, at_midnight, beijing_midnight(today))
    await set_last_login(db, before_midnight, beijing_day_end(yesterday))

    body = await daily_stats_ok(client, token, today)
    assert body["active"]["merchant"] == 1, (
        f"只有踩在 00:00 整的那一个该计入：{body['active']}"
    )


async def test_ds06_admin_login_not_counted(client, seed_accounts, merchant, db):
    """`role='admin'` 的登录**不计入** `active` 任何一类。

    同时把商户也摆进窗口：若「admin 被算成 merchant」，计数会变成 2；
    不摆商户的话，「admin 被算进 merchant」和「根本没算」都得到 1，分不清。
    """
    token = await admin_token(client)
    today = beijing_today()
    at = beijing_midnight(today) + timedelta(minutes=30)

    admin_id = seed_accounts[2][0]
    await set_last_login(db, admin_id, at)
    await set_last_login(db, merchant[0]["id"], at)

    body = await daily_stats_ok(client, token, today)
    assert body["active"] == {"merchant": 1, "customer": 0}, body


async def test_ds07_deleted_user_not_in_active_nor_new(
    client, seed_accounts, customer, db, set_user_status
):
    """`status='deleted'` 的用户既不算 `active` 也不算 `new`。

    摆一个**活着**的客户作对照，否则「全 0」与「注销被正确排除」不可区分。
    """
    token = await admin_token(client)
    today = beijing_today()
    at = beijing_midnight(today) + timedelta(minutes=30)

    live_id = customer[0]["id"]
    dead_id = seed_accounts[1][0]  # 种子客户 000001

    await set_last_login(db, live_id, at)
    await set_created_at(db, live_id, at)
    await set_last_login(db, dead_id, at)
    await set_created_at(db, dead_id, at)
    await set_user_status(dead_id, "deleted")

    body = await daily_stats_ok(client, token, today)
    assert body["active"]["customer"] == 1, body["active"]
    assert body["new"]["customer"] == 1, body["new"]


async def test_ds08_query_yesterday_only_counts_yesterday(
    client, seed_accounts, merchant, db
):
    """`?date=<昨天>` → 只统计昨天：今天的登录不计入、昨天的登录计入。"""
    token = await admin_token(client)
    today = beijing_today()
    yesterday = today - timedelta(days=1)

    logged_today = seed_accounts[0][0]
    logged_yesterday = merchant[0]["id"]
    await set_last_login(db, logged_today, beijing_midnight(today))
    await set_last_login(db, logged_yesterday, beijing_midnight(yesterday))

    past = await daily_stats_ok(client, token, yesterday)
    assert past["active"]["merchant"] == 1, f"昨天的登录该算进昨天：{past['active']}"

    current = await daily_stats_ok(client, token, today)
    assert current["active"]["merchant"] == 1, f"今天的登录该算进今天：{current['active']}"


# --------------------------------------------------------------------------- #
# activity · 四类业务量
# --------------------------------------------------------------------------- #
async def test_ds09_activity_four_kinds_each_plus_one(client, seed_accounts, db):
    """四类各造一条**当日**数据 → 四个数**各自 `+1`**。

    骨架任务的 `created_at` 摆在窗口外——否则它自己也算一条 `tasks_created`，
    「新建任务 +1」就与「领取 / job / 作品都挂在它下面」纠缠不清。
    """
    token = await admin_token(client)
    today = beijing_today()
    inside = _noon(today)
    long_ago = beijing_midnight(today) - timedelta(days=10)

    merchant_id = seed_accounts[0][0]
    customer_id = seed_accounts[1][0]

    harness = await insert_task(db, merchant_id, created_at=long_ago)
    claim_id = await insert_claim(db, harness, customer_id, claimed_at=inside)
    job_id = await insert_job(db, harness, claim_id, customer_id, created_at=inside)
    await insert_post(db, claim_id, job_id, customer_id, submitted_at=inside)
    await insert_task(db, merchant_id, created_at=inside)  # 这才是「当日新建的任务」

    body = await daily_stats_ok(client, token, today)
    assert body["activity"] == {
        "tasks_created": 1,
        "claims": 1,
        "posts_submitted": 1,
        "jobs_created": 1,
    }, body


# --------------------------------------------------------------------------- #
# new · 新增用户
# --------------------------------------------------------------------------- #
async def test_ds10_new_grouped_by_role(client, seed_accounts, merchant, customer, db):
    """今天建 1 个商户 + 2 个客户 → `{merchant: 1, customer: 2}`。

    种子三账号的 `created_at` 先挪出窗口——它们默认是「现在」，不移就会被算进来。
    """
    token = await admin_token(client)
    today = beijing_today()
    inside = _noon(today)
    long_ago = beijing_midnight(today) - timedelta(days=30)

    for uid, _, _ in seed_accounts:
        await set_created_at(db, uid, long_ago)

    await set_created_at(db, merchant[0]["id"], inside)
    await set_created_at(db, customer[0]["id"], inside)
    second = await register_customer(client, account="cust0002")
    assert second.status_code == 201, second.text
    await set_created_at(db, second.json()["user"]["id"], inside)

    body = await daily_stats_ok(client, token, today)
    assert body["new"] == {"merchant": 1, "customer": 2}, body


# --------------------------------------------------------------------------- #
# 切日 · 省略 date 取北京今天
# --------------------------------------------------------------------------- #
async def test_ds11_omitted_date_uses_beijing_today(client, seed_accounts, merchant, db):
    """`date` 省略 → 取**北京时间今天**，而不是 UTC 的今天。

    构造：北京今天 `00:01`——这一刻的 **UTC 日期还是昨天**（UTC+8 推回来
    就是昨天 `16:01`）。若实现按 UTC 切日，这条登录就落在「昨天」，
    今天这一侧会读到 0。

    先断言这个前提成立，否则「构造失败」会伪装成「实现错了」。
    """
    token = await admin_token(client)
    today = beijing_today()

    just_after_midnight = beijing_midnight(today) + timedelta(minutes=1)
    assert just_after_midnight.astimezone(timezone.utc).date() == today - timedelta(
        days=1
    ), "构造失败：北京今天 00:01 的 UTC 日期应当还是昨天"

    await set_last_login(db, merchant[0]["id"], just_after_midnight)

    body = await daily_stats_ok(client, token)  # 不传 date
    assert body["active"]["merchant"] == 1, (
        f"北京 00:01 的登录必须算进北京今天：{body}"
    )


async def test_ds12_response_shape(client, seed_accounts):
    """响应形状齐全，且 `date` 回显查询的那一天。"""
    token = await admin_token(client)
    today = beijing_today()

    body = await daily_stats_ok(client, token, today)
    assert set(body) == {"date", "active", "new", "activity"}, body
    assert body["date"] == today.isoformat(), body
    assert set(body["active"]) == _ROLE_KEYS, body["active"]
    assert set(body["new"]) == _ROLE_KEYS, body["new"]
    assert set(body["activity"]) == _ACTIVITY_KEYS, body["activity"]
