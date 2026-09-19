"""07-token · 额度流水与用量报表（D 组）。

对应 test_plan.md 的 `E-09` ~ `E-11` / `LG-01` ~ `LG-08` / `US-01` ~ `US-05`。

`quota_ledger` 是账本：**只追加**，且必须能对账——
`前一笔.balance_after + 本笔.change == 本笔.balance_after`。
报表的三个 `group_by` 之间也必须自洽（同一批流水，换个维度切，合计不能变）。
"""

from datetime import datetime, timedelta, timezone

import pytest

from tests.helpers import (
    assert_route_registered,
    bearer,
    grant,
    insert_ledger,
    insert_task,
    ledger_rows,
    quota_account_for,
)

pytestmark = pytest.mark.asyncio


async def _ledger(client, token, path="/api/merchant/quota/ledger", **params):
    return await client.get(path, headers=bearer(token), params=params)


async def _usage(client, token, group_by):
    return await client.get(
        "/api/merchant/quota/usage",
        headers=bearer(token),
        params={"group_by": group_by},
    )


# --------------------------------------------------------------------------- #
# 正常路径
# --------------------------------------------------------------------------- #
async def test_e09_my_ledger(client, customer, db):
    uid = customer[0]["id"]
    now = datetime.now(timezone.utc)
    await insert_ledger(db, uid, 100, 100, source="recharge", created_at=now - timedelta(minutes=2))
    await insert_ledger(db, uid, -30, 70, source="consume", created_at=now - timedelta(minutes=1))

    r = await _ledger(client, customer[1], path="/api/me/quota/ledger")
    assert r.status_code == 200, r.text

    items = r.json()["items"]
    assert len(items) == 2
    assert items[0]["source"] == "consume", "流水按时间倒序，最新在前"


async def test_e10_merchant_ledger(client, merchant, db):
    uid = merchant[0]["id"]
    await insert_ledger(db, uid, 100, 100, source="recharge")

    r = await _ledger(client, merchant[1])
    assert r.status_code == 200, r.text

    body = r.json()
    for key in ("items", "total", "page", "size"):
        assert key in body, f"分页响应缺字段 {key}"
    assert body["total"] == 1


# --------------------------------------------------------------------------- #
# 过滤与参数
# --------------------------------------------------------------------------- #
async def test_lg01_source_filter(client, merchant, db):
    uid = merchant[0]["id"]
    await insert_ledger(db, uid, 100, 100, source="recharge")
    await insert_ledger(db, uid, -30, 70, source="consume")

    r = await _ledger(client, merchant[1], source="consume")
    assert r.status_code == 200, r.text

    items = r.json()["items"]
    assert len(items) == 1
    assert items[0]["source"] == "consume"


async def test_lg02_from_gt_to_422(client, merchant):
    r = await _ledger(
        client,
        merchant[1],
        **{"from": "2026-03-01", "to": "2026-01-01"},
    )
    assert r.status_code == 422, r.text


async def test_lg03_span_366_ok(client, merchant):
    r = await _ledger(
        client,
        merchant[1],
        **{"from": "2026-01-01", "to": "2027-01-01"},
    )
    assert r.status_code == 200, f"366 天跨度应为上限内：{r.status_code} {r.text}"


async def test_lg04_span_367_422(client, merchant):
    r = await _ledger(
        client,
        merchant[1],
        **{"from": "2026-01-01", "to": "2027-01-02"},
    )
    assert r.status_code == 422, r.text


async def test_lg05_size_cap(client, merchant):
    r = await _ledger(client, merchant[1], size=101)
    assert r.status_code == 422, r.text


async def test_lg06_no_cross_account(client, merchant, customer, db):
    await insert_ledger(db, merchant[0]["id"], 100, 100, source="recharge")
    await insert_ledger(db, customer[0]["id"], 50, 50, source="recharge")

    r = await _ledger(client, merchant[1])
    assert r.status_code == 200, r.text

    user_ids = {i["user_id"] for i in r.json()["items"]}
    assert user_ids == {merchant[0]["id"]}, "商户查流水只该看到自己账户的"


# --------------------------------------------------------------------------- #
# 用量报表
# --------------------------------------------------------------------------- #
async def _seed_consumes(db, merchant_id, task_id, rows):
    """rows: [(points, spender_id, day_offset)]，落成负的 consume 流水。"""
    balance = 1_000_000
    for points, spender_id, day in rows:
        balance -= points
        await insert_ledger(
            db,
            merchant_id,
            -points,
            balance,
            source="consume",
            spender_id=spender_id,
            task_id=task_id,
            created_at=datetime.now(timezone.utc) - timedelta(days=day),
        )


async def test_e11_usage_by_task(client, merchant, db):
    uid = merchant[0]["id"]
    task_id = await insert_task(db, uid)
    await _seed_consumes(db, uid, task_id, [(100, None, 0), (50, None, 0)])

    r = await _usage(client, merchant[1], "task")
    assert r.status_code == 200, r.text

    items = r.json()["items"]
    assert len(items) >= 1
    for key in ("key", "points", "call_count"):
        assert key in items[0], f"用量项缺字段 {key}"


async def test_us01_bad_group_by_422(client, merchant):
    r = await _usage(client, merchant[1], "banana")
    assert r.status_code == 422, r.text


async def test_us02_group_by_sums_equal(client, merchant, db):
    uid = merchant[0]["id"]
    task_id = await insert_task(db, uid)
    await _seed_consumes(db, uid, task_id, [(100, None, 0), (50, None, 1)])

    by_task = await _usage(client, merchant[1], "task")
    by_day = await _usage(client, merchant[1], "day")

    assert by_task.status_code == 200, by_task.text
    assert by_day.status_code == 200, by_day.text

    def total(resp):
        return sum(i["points"] for i in resp.json()["items"])

    assert total(by_task) == total(by_day) == 150, (
        "换个维度切同一批流水，合计必须相等——不等就是聚合口径不一致"
    )


async def test_us03_group_by_user_uses_spender(client, merchant, customer, db):
    uid = merchant[0]["id"]
    spender = customer[0]["id"]
    task_id = await insert_task(db, uid)
    await _seed_consumes(db, uid, task_id, [(70, spender, 0)])

    r = await _usage(client, merchant[1], "user")
    assert r.status_code == 200, r.text

    keys = {str(i["key"]) for i in r.json()["items"]}
    assert str(spender) in keys, (
        "商户付费时花钱的是用户，group_by=user 要按 spender_id 聚合，否则报表定位不到人"
    )


async def test_us04_day_gap_zero_filled(client, merchant, db):
    uid = merchant[0]["id"]
    task_id = await insert_task(db, uid)
    # 只造「3 天前」和「今天」，中间那天故意空着
    await _seed_consumes(db, uid, task_id, [(100, None, 3), (50, None, 0)])

    r = await _usage(client, merchant[1], "day")
    assert r.status_code == 200, r.text

    items = r.json()["items"]
    assert len(items) >= 4, f"跨度内每天都要有一行（含 0），不得跳空：拿到 {len(items)} 行"

    zeros = [i for i in items if i["points"] == 0]
    assert zeros, "中间没有消耗的那天必须补一条 0"


async def test_us05_reconcile(client, merchant, db):
    uid = merchant[0]["id"]
    await grant(db, uid, 1000)
    await insert_ledger(db, uid, -200, 800, source="consume")
    await insert_ledger(db, uid, -50, 750, source="consume")

    rows = await ledger_rows(db, uid)
    assert len(rows) >= 3

    prev = 0
    for row in rows:
        assert prev + row["change"] == row["balance_after"], (
            f"对账失败：前一笔 {prev} + 本笔 {row['change']} "
            f"!= {row['balance_after']}（流水 id={row['id']}）"
        )
        prev = row["balance_after"]


# --------------------------------------------------------------------------- #
# 不可变
# --------------------------------------------------------------------------- #
async def test_lg07_ledger_no_mutation_route():
    """账本只追加：不存在任何改 / 删流水的路由。

    查的是运行时 OpenAPI，不是扫源码——后者会被注释与死代码骗过。
    但「一条 ledger 路由都没有」也会让断言平凡成立（假绿），故先钉死
    至少存在两条读路由，再断言没有任何写方法。
    """
    from app.main import app

    assert_route_registered("GET", "/api/merchant/quota/ledger")
    assert_route_registered("GET", "/api/me/quota/ledger")

    paths = app.openapi()["paths"]
    seen = 0
    for path, ops in paths.items():
        if "ledger" not in path:
            continue
        seen += 1
        for method in ("put", "patch", "delete"):
            assert method not in ops, f"{method.upper()} {path} 不该存在：账本不可改"
    assert seen >= 2, f"只扫到 {seen} 条 ledger 路由，断言等于没做"


async def test_lg08_closed_user_ledger_kept(client, customer, db):
    """注销不得级联删流水——账是审计数据，人走了账得在。

    打的是真注销接口，不是直接改 `status`：注销那条路径上挂着
    `user_model_key` 的**物理删除**与 `quota_ledger` 的**保留**，
    两者必须在同一个事务里各做各的（`K-20` 测前者，本条测后者）。
    """
    uid = customer[0]["id"]
    await insert_ledger(db, uid, 100, 100, source="recharge")

    r = await client.post(
        "/api/me/close",
        headers=bearer(customer[1]),
        json={"password": "pass1234", "confirm": True},
    )
    assert r.status_code == 202, f"注销失败：{r.status_code} {r.text}"

    rows = await ledger_rows(db, uid)
    assert len(rows) == 1, "用户注销不得删流水——审计数据要留着"
