"""07-token · 计价表与模型选择。

对应 test_plan.md 的 A 组（`E-01` / `MC-01` ~ `MC-12`）。

`GET /api/models` 是**用户决策前**看到的价目表。它最要紧的一条不是「列出模型」，
而是 `est_price_per_call` 必须与后端预扣用的 `max_price_per_call` **同源**——
前端展示一个数、后端扣另一个数，是最典型的账单纠纷来源。
"""

import pytest

from tests.helpers import (
    bearer,
    insert_price,
    patch_verify,
    post_key,
)

pytestmark = pytest.mark.asyncio


async def _models(client, token, **params):
    return await client.get("/api/models", headers=bearer(token), params=params)


async def test_e01_models_list(client, customer, db):
    await insert_price(db, "deepseek", "deepseek-chat", "chat")

    r = await _models(client, customer[1], op="chat")
    assert r.status_code == 200, r.text

    items = r.json()["items"]
    assert len(items) == 1
    item = items[0]
    for key in (
        "provider",
        "model",
        "op",
        "unit",
        "price_per_unit",
        "cost_price_per_unit",
        "supports_byok",
        "has_my_key",
        "est_price_per_call",
    ):
        assert key in item, f"模型项缺字段 {key}"


async def test_mc01_no_token_401(client, db):
    await insert_price(db)
    r = await client.get("/api/models")
    assert r.status_code == 401, r.text


async def test_mc02_bad_op_422(client, customer, db):
    await insert_price(db)
    r = await _models(client, customer[1], op="foo")
    assert r.status_code == 422, r.text


async def test_mc03_unknown_merchant_404(client, customer, db):
    await insert_price(db)
    r = await _models(client, customer[1], merchant_id=999999)
    assert r.status_code == 404, r.text


async def test_mc04_hidden_model_excluded(client, customer, db):
    await insert_price(db, "deepseek", "deepseek-chat", "chat")
    await insert_price(db, "dashscope", "qwen-max", "chat", provider_visible=False)

    r = await _models(client, customer[1], op="chat")
    assert r.status_code == 200, r.text

    models = [i["model"] for i in r.json()["items"]]
    assert "deepseek-chat" in models
    assert "qwen-max" not in models, "provider_visible=false 的模型不得出现在可选列表里"


async def test_mc05_latest_effective_wins(client, customer, db):
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    await insert_price(
        db, "deepseek", "deepseek-chat", "chat",
        price_per_unit=0.000004, effective_from=now - timedelta(days=10),
    )
    await insert_price(
        db, "deepseek", "deepseek-chat", "chat",
        price_per_unit=0.000009, effective_from=now - timedelta(hours=1),
    )

    r = await _models(client, customer[1], op="chat")
    assert r.status_code == 200, r.text

    items = r.json()["items"]
    assert len(items) == 1, "同一个 (provider, model, op) 只能出一行，出的是生效最新那行"
    assert float(items[0]["price_per_unit"]) == pytest.approx(0.000009)


async def test_mc06_est_equals_max_price(client, customer, db):
    await insert_price(db, "deepseek", "deepseek-chat", "chat", max_price_per_call=120)

    r = await _models(client, customer[1], op="chat")
    item = r.json()["items"][0]
    assert item["est_price_per_call"] == 120, (
        "预估价必须取 max_price_per_call——与后端预扣同源，否则展示与扣费对不上"
    )


async def test_mc07_has_my_key_true(client, customer, db, monkeypatch):
    await insert_price(db, "deepseek", "deepseek-chat", "chat")
    # 没有 provider 可打，建 Key 必须先打桩——漏了这步 POST 会走不到落库
    patch_verify(monkeypatch, ok=True)
    await post_key(client, customer[1], "deepseek")

    r = await _models(client, customer[1], op="chat")
    assert r.json()["items"][0]["has_my_key"] is True


async def test_mc08_has_my_key_false(client, customer, db):
    await insert_price(db, "deepseek", "deepseek-chat", "chat")

    r = await _models(client, customer[1], op="chat")
    assert r.json()["items"][0]["has_my_key"] is False


async def test_mc09_byok_unsupported(client, customer, db):
    await insert_price(db, "jimeng", "jimeng-xl", "generate", supports_byok=False)

    r = await _models(client, customer[1], op="generate")
    assert r.json()["items"][0]["supports_byok"] is False


async def test_mc10_op_mismatch_excluded(client, customer, db):
    await insert_price(db, "deepseek", "deepseek-chat", "generate")

    r = await _models(client, customer[1], op="chat")
    assert r.status_code == 200, r.text
    assert r.json()["items"] == [], "op 不匹配的行不得跨界回退进列表"


async def test_mc11_price_row_immutable(client, customer, db):
    from datetime import datetime, timedelta, timezone

    old_from = datetime.now(timezone.utc) - timedelta(days=10)
    old_id = await insert_price(
        db, "deepseek", "deepseek-chat", "chat",
        price_per_unit=0.000004, effective_from=old_from,
    )
    await insert_price(
        db, "deepseek", "deepseek-chat", "chat",
        price_per_unit=0.000009, effective_from=datetime.now(timezone.utc),
    )

    row = await db.fetchrow("SELECT price_per_unit, effective_from FROM model_price WHERE id = $1", old_id)
    assert row is not None, "改价只插新行，旧行不得被删"
    assert float(row["price_per_unit"]) == pytest.approx(0.000004)
    assert row["effective_from"] == old_from, "旧行的 effective_from 不得被改写"


async def test_mc12_pricing_not_per_merchant(client, customer, merchant, db):
    await insert_price(db, "deepseek", "deepseek-chat", "chat", price_per_unit=0.000004)
    merchant_id = merchant[0]["id"]

    without = await _models(client, customer[1], op="chat")
    with_mid = await _models(client, customer[1], op="chat", merchant_id=merchant_id)

    assert without.status_code == 200, without.text
    assert with_mid.status_code == 200, with_mid.text
    assert (
        without.json()["items"][0]["price_per_unit"]
        == with_mid.json()["items"][0]["price_per_unit"]
    ), "spec 明确不做按商户的个性化定价，传 merchant_id 不得改变价格"
