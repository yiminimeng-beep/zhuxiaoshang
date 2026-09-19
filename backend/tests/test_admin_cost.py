"""06-admin · 成本看板：`CS` / `CM` / `CD` / `CP` / `CB` / `CX` 六组。

对应 test_plan.md 的 `CS-01` ~ `CX-03`。

这一组盯的是**聚合口径**。聚合写错不会报错，只会安静地给出一份对不上的报表，
所以这里有三条**跨端点的一致性校验**（`CM-02` / `CD-02` / `CM-01`），
它们是本模块唯一能自动发现「看板整体偏移」的东西。

`gen_output.created_at` 是唯一的窗口维度，全部造数都靠 `insert_output`
的 `created_at` 覆盖（本轮刚为它加上，之前写死 `now()`）。
"""

from datetime import datetime, timedelta, timezone

import pytest

from tests.helpers import (
    admin_token,
    bearer,
    cost_scene,
    insert_budget_alert,
    insert_output,
    json_of,
)

pytestmark = pytest.mark.asyncio


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _days_ago(n: int) -> str:
    return (datetime.now(timezone.utc).date() - timedelta(days=n)).isoformat()


def _at(days_ago: int, hour: int = 12) -> datetime:
    """`days_ago` 天前的 `hour` 点（UTC）。窗口边界用例靠它精确落点。"""
    day = datetime.now(timezone.utc).date() - timedelta(days=days_ago)
    return datetime.combine(day, datetime.min.time(), timezone.utc) + timedelta(
        hours=hour
    )


async def _summary(client, token, **params):
    return await client.get(
        "/api/admin/cost/summary", headers=bearer(token), params=params or None
    )


# --------------------------------------------------------------------------- #
# CS · 总览
# --------------------------------------------------------------------------- #
async def test_cs01_summary_aggregates(client, seed_accounts, merchant, customer, db):
    """3 份产出各 12 分 → `total_cents=36`、`gen_count=3`、`job_count=1`。"""
    token = await admin_token(client)
    merchant_id, user_id = merchant[0]["id"], customer[0]["id"]
    scene = await cost_scene(db, merchant_id, user_id)
    await insert_output(db, scene.job_id, cost_cents=12)
    await insert_output(db, scene.job_id, cost_cents=12)

    r = await _summary(client, token, **{"from": _today(), "to": _today()})
    assert r.status_code == 200, r.text
    body = json_of(r)
    assert body["total_cents"] == 36
    assert body["gen_count"] == 3
    assert body["job_count"] == 1


async def test_cs02_from_after_to_is_422(client, seed_accounts):
    """`from > to` → `422`。"""
    token = await admin_token(client)
    r = await _summary(client, token, **{"from": _today(), "to": _days_ago(3)})
    assert r.status_code == 422, f"from 晚于 to 应 422：{r.status_code} {r.text}"


async def test_cs03_single_day_only_counts_that_day(client, seed_accounts, merchant, customer, db):
    """`from == to`（今天）→ 只统计今天，昨天的产出不计入。"""
    token = await admin_token(client)
    merchant_id, user_id = merchant[0]["id"], customer[0]["id"]
    scene = await cost_scene(db, merchant_id, user_id, created_at=_at(0))
    # 昨天的那份：同一个 job 下再插一份，只改时间
    await insert_output(db, scene.job_id, cost_cents=99, created_at=_at(1))

    r = await _summary(client, token, **{"from": _today(), "to": _today()})
    assert r.status_code == 200, r.text
    assert json_of(r)["total_cents"] == 12, "昨天那份不得计入今天"


async def test_cs04_span_boundary_366_ok_367_rejected(client, seed_accounts):
    """跨度 367 天 → `422`；**366 天 → `200`**。

    成对断言：只有上界被卡死，才说明比较写的是「> 366」而不是「>= 366」，
    也不是随手写了个更小的数。spec 的「> 366 天」正是 366 合法、367 非法。
    """
    token = await admin_token(client)

    ok = await _summary(client, token, **{"from": _days_ago(365), "to": _today()})
    assert ok.status_code == 200, f"366 天窗口应放行：{ok.status_code} {ok.text}"

    bad = await _summary(client, token, **{"from": _days_ago(366), "to": _today()})
    assert bad.status_code == 422, f"367 天窗口应 422：{bad.status_code} {bad.text}"


async def test_cs05_no_data_is_zero_not_null(client, seed_accounts):
    """库里无任何产出 → `200`，各字段为 0 / 空数组，**不得 500、不得 null**。"""
    token = await admin_token(client)
    r = await _summary(client, token, **{"from": _today(), "to": _today()})
    assert r.status_code == 200, f"空库不得报错：{r.status_code} {r.text}"
    body = json_of(r)
    assert body["total_cents"] == 0
    assert body["gen_count"] == 0
    assert body["job_count"] == 0
    assert body["total_cents"] is not None, "0 而不是 null"


async def test_cs06_avg_cents_zero_not_nan(client, seed_accounts):
    """无数据时 `avg_cents` = `0`，**不是 NaN、不是 null**。

    `AVG()` 在空集上返回 NULL，直接透传会让前端渲染出 `null` 或 `NaN`。
    """
    token = await admin_token(client)
    body = json_of(await _summary(client, token, **{"from": _today(), "to": _today()}))
    assert body["avg_cents"] == 0, f"空集均价应为 0，实际 {body['avg_cents']!r}"


async def test_cs07_failed_job_cost_still_counted(client, seed_accounts, merchant, customer, db):
    """`content_job.status='failed'` 的 job，其产出成本**仍计入**。

    钱已经花出去了。把它排除掉，看板会少算一笔真实账单。
    """
    token = await admin_token(client)
    merchant_id, user_id = merchant[0]["id"], customer[0]["id"]
    scene = await cost_scene(db, merchant_id, user_id, status="failed")

    body = json_of(await _summary(client, token, **{"from": _today(), "to": _today()}))
    assert body["total_cents"] == 12, "失败 job 的成本也是花掉的钱"


async def test_cs08_out_of_window_excluded(client, seed_accounts, merchant, customer, db):
    """窗口外的产出（`created_at` 早于 `from`）不计入。"""
    token = await admin_token(client)
    merchant_id, user_id = merchant[0]["id"], customer[0]["id"]
    await cost_scene(db, merchant_id, user_id, created_at=_at(5))

    body = json_of(await _summary(client, token, **{"from": _today(), "to": _today()}))
    assert body["total_cents"] == 0, "5 天前的产出不该落进今天的窗口"


# --------------------------------------------------------------------------- #
# CM · 按商户
# --------------------------------------------------------------------------- #
async def _by_merchant(client, token, **params):
    return await client.get(
        "/api/admin/cost/by-merchant", headers=bearer(token), params=params or None
    )


async def test_cm01_per_merchant_breakdown(
    client, seed_accounts, merchant, merchant_b, customer, db
):
    """两商户各 2 份产出 → 各自金额正确，`shop_name` 来自 `merchant_profile`。

    `video_count` / `copy_count` 按 `gen_output.type` 分列：商户最想看的就是
    「钱花在视频上还是文案上」，不分开等于这条拆解白做。
    """
    token = await admin_token(client)
    a_id, user_id = merchant[0]["id"], customer[0]["id"]
    b_id = await db.fetchval('SELECT id FROM "user" WHERE account = $1', "shop0002")

    scene_a = await cost_scene(db, a_id, user_id)
    await insert_output(db, scene_a.job_id, cost_cents=10)
    await insert_output(db, scene_a.job_id, type="video", cost_cents=100)
    await cost_scene(db, b_id, user_id)

    r = await _by_merchant(client, token, **{"from": _today(), "to": _today()})
    assert r.status_code == 200, r.text
    items = {i["merchant_id"]: i for i in json_of(r)["items"]}

    assert items[a_id]["total_cents"] == 122, items[a_id]
    assert items[a_id]["video_count"] == 1, items[a_id]
    assert items[a_id]["copy_count"] == 2, items[a_id]
    assert items[a_id]["shop_name"], f"shop_name 应取自 merchant_profile：{items[a_id]}"
    assert items[b_id]["total_cents"] == 12, items[b_id]


async def test_cm02_per_merchant_sum_equals_summary(
    client, seed_accounts, merchant, merchant_b, customer, db
):
    """⚠️ **一致性**：`sum(by-merchant.total_cents) == summary.total_cents`。

    这是本模块唯一能自动发现「聚合口径漂移」的断言。两处若用了不同的窗口
    或不同的过滤条件，各自看都很「合理」，合计却对不上。

    **必须有一条窗口外的行**：若数据全落在窗口内，「漏掉窗口过滤」这种漂移
    照样算得相等，这条用例就成了纯查库。`_OUT_OF_WINDOW` 那一笔是它的靶子。
    """
    token = await admin_token(client)
    a_id, user_id = merchant[0]["id"], customer[0]["id"]
    b_id = await db.fetchval('SELECT id FROM "user" WHERE account = $1', "shop0002")

    await cost_scene(db, a_id, user_id)
    await cost_scene(db, b_id, user_id)
    scene = await cost_scene(db, b_id, user_id)
    await insert_output(db, scene.job_id, cost_cents=30)
    # 窗口外的那一笔：唯一会让「两边窗口不一致」显形的数据
    await cost_scene(db, a_id, user_id, created_at=_at(2))

    params = {"from": _today(), "to": _today()}
    summary = json_of(await _summary(client, token, **params))
    items = json_of(await _by_merchant(client, token, **params))["items"]

    total = sum(i["total_cents"] for i in items)
    assert total == summary["total_cents"], (
        f"按商户合计 {total} != 总览 {summary['total_cents']}——两处口径漂了"
    )


async def test_cm03_deleted_merchant_history_still_counted(
    client, seed_accounts, merchant, customer, db, set_user_status
):
    """已注销商户的历史花费**仍要计入**（按 `user_id` 聚合，软删不影响）。

    注销是「这个人不用了」，不是「这笔钱没花过」。看板少算等于平台的账对不上。
    """
    token = await admin_token(client)
    merchant_id, user_id = merchant[0]["id"], customer[0]["id"]
    await cost_scene(db, merchant_id, user_id)
    await set_user_status(merchant_id, "deleted")

    r = await _by_merchant(client, token, **{"from": _today(), "to": _today()})
    items = json_of(r)["items"]
    assert any(i["merchant_id"] == merchant_id for i in items), (
        f"已注销商户的历史花费应仍在清单里：{items}"
    )


async def test_cm04_size_over_limit_is_422(client, seed_accounts):
    """`size=101` → `422`（全局分页上界 100）。"""
    token = await admin_token(client)
    r = await _by_merchant(client, token, size=101)
    assert r.status_code == 422, f"size 上限 100：{r.status_code} {r.text}"


async def test_cm05_pagination_does_not_overlap(
    client, seed_accounts, merchant, customer, db
):
    """`page=2` 与 `page=1` 的 id 集合不重叠（全局约定 #4）。"""
    token = await admin_token(client)
    merchant_id, user_id = merchant[0]["id"], customer[0]["id"]
    for _ in range(3):
        await cost_scene(db, merchant_id, user_id)

    page1 = json_of(
        await _by_merchant(
            client, token, page=1, size=2, **{"from": _today(), "to": _today()}
        )
    )
    page2 = json_of(
        await _by_merchant(
            client, token, page=2, size=2, **{"from": _today(), "to": _today()}
        )
    )
    ids1 = {i["merchant_id"] for i in page1["items"]}
    ids2 = {i["merchant_id"] for i in page2["items"]}
    assert not (ids1 & ids2), f"分页重叠了：{ids1} ∩ {ids2}"
    assert page1["total"] == 1, page1


# --------------------------------------------------------------------------- #
# CD · 按日趋势
# --------------------------------------------------------------------------- #
async def _by_day(client, token, **params):
    return await client.get(
        "/api/admin/cost/by-day", headers=bearer(token), params=params or None
    )


async def test_cd01_missing_days_filled_with_zero(
    client, seed_accounts, merchant, customer, db
):
    """3 天窗口只在第 1、3 天有数据 → 返回**恰好 3 条**，升序，中间那天为 0。

    不补 0 的前端折线图会在中间断开——「没有数据」和「花费为 0」是两件事，
    而这个页面上它们必须是同一个点。
    """
    token = await admin_token(client)
    merchant_id, user_id = merchant[0]["id"], customer[0]["id"]
    await cost_scene(db, merchant_id, user_id, created_at=_at(2))
    await cost_scene(db, merchant_id, user_id, created_at=_at(0))

    r = await _by_day(client, token, **{"from": _days_ago(2), "to": _today()})
    assert r.status_code == 200, r.text
    items = json_of(r)["items"]
    assert len(items) == 3, f"3 天窗口必须返回 3 条：{items}"
    assert [i["date"] for i in items] == [_days_ago(2), _days_ago(1), _today()], (
        "必须按日期升序"
    )
    assert items[1]["total_cents"] == 0, f"中间那天必须补 0，不是跳过：{items}"


async def test_cd02_by_day_sum_equals_summary(client, seed_accounts, merchant, customer, db):
    """逐日 `total_cents` 之和 == `summary.total_cents`（同窗口）。

    与 `CM-02` 同款的一致性校验。⚠️ **它兜得住的和兜不住的不一样，别把它当成
    万能的一致性闸**：

    - 兜得住：**窗口最后一天被漏掉**（`cursor < end`）、逐日与总览用了**不同的窗口**、
      补 0 时把某天的数算重。这些都会让合计当场对不上。
    - **兜不住**：`by-day` 若把窗口**外**的行也算进来——补 0 的循环只吐
      `[start, end]` 之内的日期，窗口外的行被分到没被遍历的桶里，静默丢弃，
      合计照样相等。这条只能靠 `CS-08`（窗口外排除）从**总览**那一侧钉住。
    """
    token = await admin_token(client)
    merchant_id, user_id = merchant[0]["id"], customer[0]["id"]
    await cost_scene(db, merchant_id, user_id, created_at=_at(1))
    scene = await cost_scene(db, merchant_id, user_id, created_at=_at(0))
    await insert_output(db, scene.job_id, cost_cents=8, created_at=_at(0))

    params = {"from": _days_ago(2), "to": _today()}
    summary = json_of(await _summary(client, token, **params))
    items = json_of(await _by_day(client, token, **params))["items"]

    total = sum(i["total_cents"] for i in items)
    assert total == summary["total_cents"], (
        f"逐日合计 {total} != 总览 {summary['total_cents']}"
    )


async def test_cd03_all_empty_still_returns_every_day(client, seed_accounts):
    """全窗口无数据 → **每一天都在**且为 0（不得返回空数组）。"""
    token = await admin_token(client)
    r = await _by_day(client, token, **{"from": _days_ago(2), "to": _today()})
    assert r.status_code == 200, r.text
    items = json_of(r)["items"]
    assert len(items) == 3, f"无数据也必须补齐 3 天：{items}"
    assert all(i["total_cents"] == 0 for i in items), items


async def test_cd04_span_over_366_is_422(client, seed_accounts):
    """跨度 367 天 → `422`（窗口上界与 `summary` 同一口径）。"""
    token = await admin_token(client)
    r = await _by_day(client, token, **{"from": _days_ago(366), "to": _today()})
    assert r.status_code == 422, f"367 天窗口应 422：{r.status_code} {r.text}"


# --------------------------------------------------------------------------- #
# CP · 按模型
# --------------------------------------------------------------------------- #
async def _by_provider(client, token, **params):
    return await client.get(
        "/api/admin/cost/by-provider", headers=bearer(token), params=params or None
    )


async def test_cp01_per_provider_breakdown(client, seed_accounts, merchant, customer, db):
    """两个 provider 各 2 份产出 → 各自 `total_cents` / `gen_count` / `avg_cents` 正确。"""
    token = await admin_token(client)
    merchant_id, user_id = merchant[0]["id"], customer[0]["id"]
    scene = await cost_scene(db, merchant_id, user_id)
    await insert_output(db, scene.job_id, provider="deepseek", cost_cents=10)
    await insert_output(db, scene.job_id, provider="jimeng", cost_cents=100)
    await insert_output(db, scene.job_id, provider="jimeng", cost_cents=200)

    r = await _by_provider(client, token, **{"from": _today(), "to": _today()})
    assert r.status_code == 200, r.text
    items = {i["provider"]: i for i in json_of(r)["items"]}

    assert items["deepseek"]["total_cents"] == 22, items["deepseek"]
    assert items["deepseek"]["gen_count"] == 2, items["deepseek"]
    assert items["deepseek"]["avg_cents"] == 11, items["deepseek"]
    assert items["jimeng"]["total_cents"] == 300, items["jimeng"]
    assert items["jimeng"]["avg_cents"] == 150, items["jimeng"]


async def test_cp02_success_rate_formula(client, seed_accounts, merchant, customer, db):
    """`success_rate` = 该 provider 成功产出数 / 总调用数。

    造 3 份 `deepseek` 产出、其中 1 份的 job 是 `failed` → 成功率 2/3。

    ⚠️ `cost_scene` **自带一份产出**。第一版各补了两条，实际造出 5 份，
    期望值 2/3 对不上（实测 3/5）——数据里多出来的那一份不在用例的意图里。
    """
    token = await admin_token(client)
    merchant_id, user_id = merchant[0]["id"], customer[0]["id"]
    scene = await cost_scene(db, merchant_id, user_id)  # 1 份
    await insert_output(db, scene.job_id, provider="deepseek")  # 第 2 份
    await cost_scene(db, merchant_id, user_id, status="failed")  # 第 3 份，job 失败

    items = json_of(
        await _by_provider(client, token, **{"from": _today(), "to": _today()})
    )["items"]
    deepseek = next(i for i in items if i["provider"] == "deepseek")
    assert abs(float(deepseek["success_rate"]) - 2 / 3) < 1e-6, deepseek


async def test_cp03_success_rate_zero_not_nan(client, seed_accounts):
    """总调用数为 0 → `success_rate = 0`（**不是 NaN**）。

    NaN 会让前端渲染成 `NaN%`，而它本该是「这个 provider 今天没被用」。
    无数据时压根不会有这个 provider 的行——所以这条测的是**空清单**时
    端点不炸、且不凭空造出 NaN 行。
    """
    token = await admin_token(client)
    r = await _by_provider(client, token, **{"from": _today(), "to": _today()})
    assert r.status_code == 200, r.text
    items = json_of(r)["items"]
    assert items == [], f"无调用时不该有 provider 行：{items}"


async def test_cp04_byok_zero_cost_not_counted_as_failure(
    client, seed_accounts, merchant, customer, db
):
    """⚠️ **BYOK**：`billing_source='byok'` 且 `cost_cents=0` 的调用**不得判为失败**。

    零成本是「用户自己付了钱」，不是「调用炸了」。分子把它排除掉的话，
    看板会显示一个稳定走低、永远到不了 100% 的成功率——而运营会去查一个
    根本不存在的故障。
    """
    token = await admin_token(client)
    merchant_id, user_id = merchant[0]["id"], customer[0]["id"]
    scene = await cost_scene(db, merchant_id, user_id)
    await insert_output(db, scene.job_id, provider="deepseek", cost_cents=10)
    await insert_output(
        db, scene.job_id, provider="deepseek", billing_source="byok", cost_cents=0
    )

    items = json_of(await _by_provider(client, token, **{"from": _today(), "to": _today()}))[
        "items"
    ]
    deepseek = next(i for i in items if i["provider"] == "deepseek")
    assert float(deepseek["success_rate"]) == 1.0, (
        f"BYOK 零成本是成功调用，不该拉低成功率：{deepseek}"
    )


async def test_cp05_byok_in_gen_count_not_in_total(client, seed_accounts, merchant, customer, db):
    """BYOK 调用**进** `gen_count` 但**不进** `total_cents`。

    `cost_cents=0` 的直接推论：平台没花钱，但确实调了一次。
    只看金额会以为这个 provider 没被使用。
    """
    token = await admin_token(client)
    merchant_id, user_id = merchant[0]["id"], customer[0]["id"]
    # 只造**这一份** BYOK 调用：`cost_scene` 自带的默认产出是平台付费的，
    # 留着它 total_cents 就不是 0 了（第一版正是这么写的，实测 12 != 0）
    await cost_scene(db, merchant_id, user_id, billing_source="byok", cost_cents=0)

    items = json_of(await _by_provider(client, token, **{"from": _today(), "to": _today()}))[
        "items"
    ]
    deepseek = next(i for i in items if i["provider"] == "deepseek")
    assert deepseek["gen_count"] == 1, f"BYOK 也是一次调用：{deepseek}"
    assert deepseek["total_cents"] == 0, f"BYOK 平台不花钱：{deepseek}"


# --------------------------------------------------------------------------- #
# CB · 熔断告警
# --------------------------------------------------------------------------- #
async def _alerts(client, token, **params):
    return await client.get(
        "/api/admin/cost/budget-alerts", headers=bearer(token), params=params or None
    )


async def test_cb01_alert_list_shape(client, seed_accounts, merchant, db):
    """清单字段齐全，且带 `shop_name`（管理员看 id 认不出是谁）。"""
    token = await admin_token(client)
    merchant_id = merchant[0]["id"]
    await insert_budget_alert(db, merchant_id, spend_cents=8000, limit_cents=5000)

    r = await _alerts(client, token, **{"from": _days_ago(1), "to": _today()})
    assert r.status_code == 200, r.text
    items = json_of(r)["items"]
    assert len(items) == 1, items
    row = items[0]
    assert set(("merchant_id", "shop_name", "alert_date", "spend_cents", "limit_cents")) <= set(
        row
    ), row
    assert row["merchant_id"] == merchant_id
    assert row["spend_cents"] == 8000
    assert row["limit_cents"] == 5000


async def test_cb02_same_day_single_row(client, seed_accounts, merchant, db):
    """同商户同日重复触发 → `budget_alert` **仍只有 1 行**（唯一约束兜底）。

    写入端在 03 的 `_raise_budget`，06 只读。这条在这里的作用是让清单端点
    在「有告警」的真实场景下被走到，同时确认读侧不会因为多插而重复计数。
    """
    token = await admin_token(client)
    merchant_id = merchant[0]["id"]
    await insert_budget_alert(db, merchant_id)

    with pytest.raises(Exception) as excinfo:
        await insert_budget_alert(db, merchant_id)
    assert "uq_budget_alert_day" in str(excinfo.value), (
        f"应由 (merchant_id, alert_date) 唯一约束兜住，实际：{excinfo.value!r}"
    )

    assert await db.fetchval("SELECT count(*) FROM budget_alert") == 1
    items = json_of(await _alerts(client, token, **{"from": _days_ago(1), "to": _today()}))[
        "items"
    ]
    assert len(items) == 1, f"同商户同日只该有一条告警：{items}"


async def test_cb03_out_of_window_and_bad_range(client, seed_accounts, merchant, db):
    """窗口外的告警不计入；`from > to` → `422`。"""
    token = await admin_token(client)
    await insert_budget_alert(db, merchant[0]["id"], alert_date=(datetime.now(timezone.utc).date() - timedelta(days=10)))

    in_window = await _alerts(client, token, **{"from": _days_ago(1), "to": _today()})
    assert in_window.status_code == 200, in_window.text
    assert json_of(in_window)["items"] == [], "10 天前的告警不该落进昨天的窗口"

    bad = await _alerts(client, token, **{"from": _today(), "to": _days_ago(3)})
    assert bad.status_code == 422, f"from > to 应 422：{bad.status_code} {bad.text}"


# --------------------------------------------------------------------------- #
# CX · 单商户明细
# --------------------------------------------------------------------------- #
async def _detail(client, token, merchant_id: int, **params):
    return await client.get(
        f"/api/admin/cost/merchants/{merchant_id}/detail",
        headers=bearer(token),
        params=params or None,
    )


async def test_cx01_detail_only_this_merchant(
    client, seed_accounts, merchant, merchant_b, customer, db
):
    """返回 `by_day[]` + `by_provider[]` + `recent_jobs[]`，三者都只含该商户的数据。"""
    token = await admin_token(client)
    a_id, user_id = merchant[0]["id"], customer[0]["id"]
    b_id = await db.fetchval('SELECT id FROM "user" WHERE account = $1', "shop0002")

    await cost_scene(db, a_id, user_id)
    await cost_scene(db, b_id, user_id, provider="jimeng")

    r = await _detail(client, token, a_id, **{"from": _days_ago(1), "to": _today()})
    assert r.status_code == 200, r.text
    body = json_of(r)
    assert set(("by_day", "by_provider", "recent_jobs")) <= set(body), body
    assert [i["provider"] for i in body["by_provider"]] == ["deepseek"], (
        f"不该看到别家的 provider：{body['by_provider']}"
    )
    assert len(body["recent_jobs"]) == 1, body["recent_jobs"]


async def test_cx02_unknown_merchant_is_404(client, seed_accounts):
    """不存在的 id → `404`。"""
    from tests.helpers import assert_route_registered

    assert_route_registered("GET", "/api/admin/cost/merchants/{id}/detail")
    token = await admin_token(client)
    r = await _detail(client, token, 999999)
    assert r.status_code == 404, f"商户不存在应 404：{r.status_code} {r.text}"


async def test_cx03_customer_id_is_404(client, seed_accounts, customer):
    """传一个 `customer` 的 id → `404`（它不是商户，等价于不存在）。

    **不得 200 返回空报表**：那会让「这家店没有数据」和「这个 id 是个客户」
    长得一模一样，运营会去查一个不存在的问题。
    """
    from tests.helpers import assert_route_registered

    # 端点没实现时 FastAPI 对不存在的路由也回 404——这条用例会假绿
    assert_route_registered("GET", "/api/admin/cost/merchants/{id}/detail")
    token = await admin_token(client)
    r = await _detail(client, token, customer[0]["id"])
    assert r.status_code == 404, (
        f"非商户 id 应 404，不得返回空报表：{r.status_code} {r.text}"
    )
