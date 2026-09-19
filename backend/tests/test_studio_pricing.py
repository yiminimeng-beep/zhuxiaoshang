"""03 追加 B · 计价常量与它的库投影（`PR-01` ~ `PR-08`）。

「价写死在代码里」的**实际含义是「常量作源、种子作投影」**，不是「不写库」：
`resolve_price` / `GET /api/models` / 预扣额三处都读 `model_price`，
且都有既存用例锁着；绕开库等于打翻它们。`PR-04` 就是这条设计的验收——
改常量，库里跟着变。
"""

from decimal import Decimal

import pytest

from tests.helpers import (
    assets_of,
    bearer,
    create_job,
    login_ok,
)

pytestmark = pytest.mark.asyncio

ALL_FIELDS = (
    "provider",
    "model",
    "op",
    "unit",
    "cost_price_per_unit",
    "price_per_unit",
    "max_price_per_call",
    "markup_rate",
)


@pytest.fixture
async def seeded(db):
    """跑一遍种子。返回新建数量。"""
    from app.seed import seed

    return await seed()


@pytest.fixture
async def customer_token(client, seeded):
    body = await login_ok(client, "000001", "000001", fp="fp-seed-customer")
    return body["access_token"]


def _pricing():
    from app.services import pricing

    return pricing


# --------------------------------------------------------------------------- #
# PR-01 ~ PR-02 常量表本身
# --------------------------------------------------------------------------- #
async def test_pr01_exactly_five_ops():
    prices = _pricing().PRICES

    assert set(prices) == {"chat", "rewrite", "generate", "judge", "guard"}, (
        f"常量表必须恰好覆盖五个 op，实际 {sorted(prices)}"
    )


async def test_pr02_provider_and_model_per_op(settings_override):
    settings = settings_override(
        deepseek_text_model="deepseek-v4-flash",
        deepseek_vision_model="deepseek-v4-flash-vision-exp",
    )
    prices = _pricing().PRICES

    for op, row in prices.items():
        assert row["provider"] == "deepseek", f"{op} 行的 provider 不对：{row}"

    assert prices["guard"]["model"] == settings.deepseek_vision_model, (
        "预检读的是图，必须走视觉模型"
    )
    for op in ("chat", "rewrite", "generate", "judge"):
        assert prices[op]["model"] == settings.deepseek_text_model, (
            f"{op} 走文本模型——模型 ID 一律取自配置，不硬编码"
        )


# --------------------------------------------------------------------------- #
# PR-03 ~ PR-06 投影：常量 → model_price 表
# --------------------------------------------------------------------------- #
async def test_pr03_db_rows_match_constants(db, seeded):
    prices = _pricing().PRICES

    rows = await db.fetch(
        """
        SELECT provider, model, op, unit, cost_price_per_unit, price_per_unit,
               max_price_per_call, markup_rate
        FROM model_price
        """
    )
    assert len(rows) == len(prices), (
        f"库里应有 {len(prices)} 行，实际 {len(rows)} 行：{[r['op'] for r in rows]}"
    )

    by_op = {r["op"]: r for r in rows}
    for op, want in prices.items():
        got = by_op[op]
        for field in ALL_FIELDS:
            expected = want[field]
            actual = got[field]
            if isinstance(expected, (Decimal, int)) and not isinstance(expected, bool):
                actual = Decimal(str(actual))
                expected = Decimal(str(expected))
            assert actual == expected, (
                f"{op} 行的 {field} 与常量不一致：库里 {got[field]!r} != 常量 {want[field]!r}"
            )


async def test_pr04_changing_constant_flows_into_db(db, seeded, monkeypatch):
    """**常量是源**：改常量 → 重跑种子 → 库里那行跟着变。"""
    from app.services import pricing

    patched = dict(pricing.PRICES)
    patched["chat"] = {**patched["chat"], "price_per_unit": Decimal("9.000000")}
    monkeypatch.setattr(pricing, "PRICES", patched)

    from app.seed import seed

    await seed()

    value = await db.fetchval("SELECT price_per_unit FROM model_price WHERE op = 'chat'")
    assert Decimal(str(value)) == Decimal("9.000000"), (
        f"改常量后库里应变成 9.000000，实际 {value}——种子没有把常量当源"
    )


async def test_pr05_seeding_twice_does_not_duplicate(db):
    from app.seed import seed

    await seed()
    first = await db.fetchval("SELECT count(*) FROM model_price")
    await seed()
    second = await db.fetchval("SELECT count(*) FROM model_price")

    assert first == second == 5, (
        f"重跑种子不得插新行（effective_from 若用 now() 会反复插），"
        f"实际 {first} → {second}"
    )


async def test_pr06_unit_is_token_and_markup_holds():
    prices = _pricing().PRICES

    for op, row in prices.items():
        assert row["unit"] == "token", f"{op} 的 unit 必须是 token，实际 {row['unit']}"

        cost = Decimal(str(row["cost_price_per_unit"]))
        price = Decimal(str(row["price_per_unit"]))
        markup = Decimal(str(row["markup_rate"]))
        assert price == cost * markup, (
            f"{op}：price_per_unit 必须等于 cost_price_per_unit × markup_rate，"
            f"实际 {price} != {cost} × {markup}"
        )


# --------------------------------------------------------------------------- #
# PR-07 ~ PR-08 投影被真的读到
# --------------------------------------------------------------------------- #
async def test_pr07_models_endpoint_exposes_the_row(client, customer_token, seeded):
    r = await client.get(
        "/api/models", params={"op": "chat"}, headers=bearer(customer_token)
    )
    assert r.status_code == 200, f"{r.status_code} {r.text}"

    items = r.json()["items"]
    chat_rows = [i for i in items if i["op"] == "chat" and i["provider"] == "deepseek"]
    assert chat_rows, f"op=chat 应当有 deepseek 的行，实际 {items}"

    row = chat_rows[0]
    assert row["est_price_per_call"] == 300, (
        f"est_price_per_call 必须等于后端预扣的依据 max_price_per_call，实际 {row}"
    )
    assert row["est_price_per_call"] == _pricing().PRICES["chat"]["max_price_per_call"], (
        "前端展示的预估价必须与常量同源，否则就是「展示一个数、扣另一个数」"
    )


async def test_pr08_job_creation_no_longer_503(
    client, customer_token, seeded, db, patch_dispatch
):
    task_id = await db.fetchval(
        "SELECT id FROM task WHERE pay_mode = 'user_pay_reimburse'"
    )
    assert task_id is not None, "种子必须有一条 user_pay_reimburse 任务"

    r = await create_job(client, customer_token, task_id, assets=assets_of(1))

    assert r.status_code == 201, (
        f"种完计价表后建 copy job 应当 201，实际 {r.status_code} {r.text}——"
        f"仍是 503 说明计价行没落库（POST /api/jobs 缺价一律 503，不按 0 计费）"
    )
