"""05-reward · `PL` 组：积分账本。

对应 test_plan.md 的 `PL-01` ~ `PL-07`。

这一组盯的是 `balance_after` **怎么算出来的**：它不是「查一下当前余额再加」，
而是「锁住上一行再算」。并发两笔入账时，朴素写法会写出两个一模一样的
`balance_after`，而两行都「看起来对」。
"""

import asyncio
from pathlib import Path

import pytest
from sqlalchemy import text

from tests.helpers import (
    balance_of,
    bearer,
    grant_points_now,
    insert_point_ledger,
    point_ledger_rows,
    points_of,
    reward_scene,
    settle_now,
)

pytestmark = pytest.mark.asyncio


async def test_pl01_balance_after_matches_api(db, client, merchant, customer):
    """发放积分后 `point_ledger.balance_after` == `GET /api/me/points`。"""
    _, c_token, _ = customer
    merchant_id, customer_id = merchant[0]["id"], customer[0]["id"]
    await insert_point_ledger(db, customer_id, 500, 500, source="adjust")
    scene = await reward_scene(db, merchant_id, customer_id)

    await settle_now(
        post_id=scene.post_id,
        user_id=customer_id,
        task_id=scene.task_id,
        engagement=18,
    )

    ledger = await point_ledger_rows(db, customer_id)
    assert ledger[-1]["balance_after"] == 550
    assert await balance_of(client, c_token) == 550
    assert await balance_of(client, c_token) == ledger[-1]["balance_after"]


async def test_pl02_concurrent_grants_serialize_balance(db, merchant, customer):
    """并发 +100 与 +50 → 两笔必须**串行**，合计 150，**不得各读各的**。

    这是行锁的用例。朴素的「SELECT 余额 → 插入」在并发下两个事务读到同一个
    旧值，于是两行各写自己那一笔（50 与 100），合计永远凑不出 150。

    ⚠️ **必须有 barrier**。只 `asyncio.gather` 两个协程是测不出来的：事件循环
    会把第一个协程「一路跑到提交」再切给第二个，两个事务根本没重叠——
    去掉 `FOR UPDATE` 该用例照样绿（实测）。所以这里用 `Barrier(2)` 钉住
    「两个事务都已开启、都还没读余额」这一刻，再把它们同时放出去。

    断言取「最大余额 == 150」而不是「排序后 == [100,150]」：串行之后谁先谁后
    是调度决定的，把顺序写死会让用例变成抛硬币。
    """
    merchant_id, customer_id = merchant[0]["id"], customer[0]["id"]

    from app.db import SessionLocal
    from app.services import reward as reward_service

    barrier = asyncio.Barrier(2)

    async def one(change: int, ref_id: int) -> int:
        session = SessionLocal()
        try:
            # 先摸一下库，把事务真正开起来（否则 barrier 放的还是「没开事务」的协程）
            await session.execute(text("SELECT 1"))
            await barrier.wait()
            balance = await reward_service.grant_points(
                session,
                user_id=customer_id,
                change=change,
                source="adjust",
                ref_type="concurrent",
                ref_id=ref_id,
            )
            await session.commit()
            return balance
        finally:
            await session.close()

    results = await asyncio.gather(
        one(100, 1), one(50, 2), return_exceptions=True
    )
    assert not [r for r in results if isinstance(r, BaseException)], results

    rows = await point_ledger_rows(db, customer_id)
    assert len(rows) == 2
    balances = sorted(r["balance_after"] for r in rows)
    assert balances[-1] == 150, f"并发入账未串行（合计不是 150）：{balances}"
    assert balances[0] in (50, 100), balances


async def test_pl03_duplicate_ref_rejected(db, merchant, customer):
    """同一 `(source, ref_type, ref_id)` 重复入账 → 唯一约束拒绝，表里仍 1 行。"""
    customer_id = customer[0]["id"]

    await grant_points_now(
        customer_id, 100, source="task_reward", ref_type="reward_grant", ref_id=7
    )
    with pytest.raises(Exception) as excinfo:
        await grant_points_now(
            customer_id, 100, source="task_reward", ref_type="reward_grant", ref_id=7
        )
    assert not isinstance(excinfo.value, AssertionError), excinfo.value

    rows = await point_ledger_rows(db, customer_id)
    assert len(rows) == 1
    assert await points_of(db, customer_id) == 100


async def test_pl04_negative_change_below_balance_rejected(db, merchant, customer):
    """`change` 为负且超过余额 → 拒绝，且 `balance_after` 不得为负。

    积分是「已发出去的债」，负余额等于平台倒欠用户——数据库的
    `ck_point_ledger_balance_after` 是最后一道闸，应用层必须先拦。
    """
    customer_id = customer[0]["id"]
    await insert_point_ledger(db, customer_id, 30, 30, source="adjust")

    with pytest.raises(Exception) as excinfo:
        await grant_points_now(
            customer_id, -50, source="redemption", ref_type="redemption", ref_id=1
        )
    # 不拦 `AttributeError`：`grant_points` 没实现时它也会「抛异常」，
    # 于是 `raised=True`，这条用例在红相里假绿。
    assert not isinstance(
        excinfo.value, AttributeError
    ), f"grant_points 尚未落地，AttributeError 不是「拒绝」：{excinfo.value!r}"

    assert [r["change"] for r in await point_ledger_rows(db, customer_id)] == [30]
    assert await points_of(db, customer_id) == 30


async def test_pl05_ledger_endpoint_shape_and_order(db, client, merchant, customer):
    """`/api/me/points/ledger` 返回 `{items,total,page,size}`，倒序，字段齐。"""
    _, c_token, _ = customer
    customer_id = customer[0]["id"]
    await insert_point_ledger(db, customer_id, 10, 10, source="adjust", ref_type="a")
    await insert_point_ledger(db, customer_id, 20, 30, source="adjust", ref_type="b")

    r = await client.get("/api/me/points/ledger", headers=bearer(c_token))
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(("items", "total", "page", "size")) <= set(body)
    assert body["total"] == 2
    assert [i["change"] for i in body["items"]] == [20, 10]
    first = body["items"][0]
    assert first["source"] == "adjust"
    assert first["ref_type"] == "b"
    assert first["balance_after"] == 30


async def test_pl06_ledger_pagination_bounds(db, client, merchant, customer):
    """`size` 上限 100；`page=2` 不重叠（全局约定 #4）。"""
    _, c_token, _ = customer
    customer_id = customer[0]["id"]
    for i in range(3):
        await insert_point_ledger(
            db, customer_id, i + 1, i + 1, source="adjust", ref_type=f"r{i}"
        )

    big = await client.get(
        "/api/me/points/ledger?size=101", headers=bearer(c_token)
    )
    assert big.status_code in (200, 422), big.text
    if big.status_code == 200:
        assert big.json()["size"] <= 100

    page1 = (
        await client.get(
            "/api/me/points/ledger?page=1&size=2", headers=bearer(c_token)
        )
    ).json()
    page2 = (
        await client.get(
            "/api/me/points/ledger?page=2&size=2", headers=bearer(c_token)
        )
    ).json()
    assert len(page1["items"]) == 2
    assert len(page2["items"]) == 1
    ids1 = {i["id"] for i in page1["items"]}
    ids2 = {i["id"] for i in page2["items"]}
    assert not (ids1 & ids2)


async def test_pl07_point_ledger_is_append_only():
    """本模块任何端点都不对 `point_ledger` 产生 `UPDATE` / `DELETE`（只追加）。

    与 07 的 `LG-07` 同款：扫源码比读代码可靠——审计表的「不可变」是
    spec 用一句话写的，而一句话很容易在某个端点里被破。
    """
    app_dir = Path(__file__).resolve().parent.parent / "app"
    sources = {p: p.read_text("utf-8") for p in app_dir.rglob("*.py")}
    # 光「提到过 point_ledger」不算数：模型定义一落地就满足，端点全没写时这条
    # 也会绿。必须确认真正读写账本的端点已注册，否则「没有可改写的代码」
    # 会让下面的断言平凡成立。
    from app.main import app

    paths = set(app.openapi().get("paths", {}))
    assert "/api/me/points" in paths and "/api/me/points/ledger" in paths, (
        f"积分读端点还没注册，这条检查等于没做：{sorted(paths)}"
    )

    bad: list[str] = []
    for path, src in sources.items():
        for needle in (
            "UPDATE point_ledger",
            "DELETE FROM point_ledger",
            "update(PointLedger)",
            "delete(PointLedger)",
        ):
            if needle in src:
                bad.append(f"{path.name}: {needle}")
    assert not bad, f"审计表被改写了：{bad}"
