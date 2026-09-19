"""计价常量表——**唯一真相**。

## 为什么单价写在代码里，而不是运营在后台改

2026-09-16 用户改口径：「先不做 token 计费功能，目前测试阶段，先做好页面，
要有这个功能展示，目前的 token 费用直接写在代码里面」。

意思是不建计费**管理**功能（充值 / 台账界面 / 在线改价），但**展示要有**。
于是单价从「运营可配的 `model_price` 表」降级成这里的常量。

## 为什么仍然要写进 `model_price` 表

**常量作源、种子作投影**：`app/seed.py` 把这张表投影进库。绕开库是行不通的，
因为读库的有三处，且都有既存用例锁着：

- `quota_service.resolve_price` —— 预扣额的来源（缺价一律 `503`，不按 0 计费）
- `GET /api/models` —— 用户决策前看的价目表
- 07 的一整组计价用例

所以改价改**这里**，再跑种子；`PR-04` 盯的就是这条链路。

## 口径：这是演示口径，不是 DeepSeek 账单的等比换算

真实 DeepSeek 约 1.5 元 / 百万 token ≈ 每 token 0.00000015 元。而「点」是整数
（1 点 = 1 分），照实换算每次调用都是 0 点、余额永远不动，界面上的计费展示
就成了一件死的装饰。故取 `0.003 点/token`：一次 ~1000 token 的调用 ≈ **3 点**，
肉眼看得见。**这是演示参数，不是汇率。**

`cost_price_per_unit` 是报销基数口径（不含 markup，见 07 spec），
`price_per_unit` 是实收。两者之比恒等于 `markup_rate`。
"""

from collections import OrderedDict
from datetime import datetime, timezone
from decimal import Decimal

from app.config import get_settings

#: `effective_from` 写**固定过去时刻**。`uq_model_price_row` 含这一列，
#: 用 `now()` 的话每跑一次种子就插一批新行——改价靠插新行没错，但**重跑种子**
#: 不是改价，它得是幂等的（`PR-05`）。
EFFECTIVE_FROM = datetime(2026, 1, 1, tzinfo=timezone.utc)

#: 单次调用的预扣上界（点）。预扣按它扣，结算按实际用量退差。
#: 客户演示账号 2000 点，够跑 6 次——手工验收时不会第一次就撞余额不足。
MAX_PRICE_PER_CALL = 300

COST_PER_UNIT = Decimal("0.002")
PRICE_PER_UNIT = Decimal("0.003")
MARKUP_RATE = Decimal("1.50")


def _row(op: str, model: str) -> dict:
    return {
        "provider": "deepseek",
        "model": model,
        "op": op,
        "unit": "token",
        "cost_price_per_unit": COST_PER_UNIT,
        "price_per_unit": PRICE_PER_UNIT,
        "max_price_per_call": MAX_PRICE_PER_CALL,
        "markup_rate": MARKUP_RATE,
        "supports_byok": True,
        "provider_visible": True,
        "effective_from": EFFECTIVE_FROM,
    }


def _build() -> "OrderedDict[str, dict]":
    settings = get_settings()
    text, vision = settings.deepseek_text_model, settings.deepseek_vision_model
    return OrderedDict(
        [
            ("chat", _row("chat", text)),
            ("rewrite", _row("rewrite", text)),
            ("generate", _row("generate", text)),
            ("judge", _row("judge", text)),
            # 预检读的是图，只有这一行走视觉模型
            ("guard", _row("guard", vision)),
        ]
    )


#: `op → 那一行`。恰好五行，覆盖 `models/quota.py` 的 `OPS`。
PRICES = _build()

#: 视频建 job 用的计价行（03 追加 D）。不进 `PRICES`，避免打翻 PR-01/PR-02。
VIDEO_PRICES = OrderedDict(
    [
        (
            "jimeng_generate",
            {
                "provider": "jimeng",
                "model": "jimeng-video",
                "op": "generate",
                "unit": "token",
                "cost_price_per_unit": COST_PER_UNIT,
                "price_per_unit": PRICE_PER_UNIT,
                "max_price_per_call": MAX_PRICE_PER_CALL,
                "markup_rate": MARKUP_RATE,
                "supports_byok": True,
                "provider_visible": True,
                "effective_from": EFFECTIVE_FROM,
            },
        ),
        (
            "kling_generate",
            {
                "provider": "kling",
                "model": "kling-video",
                "op": "generate",
                "unit": "token",
                "cost_price_per_unit": COST_PER_UNIT,
                "price_per_unit": PRICE_PER_UNIT,
                "max_price_per_call": MAX_PRICE_PER_CALL,
                "markup_rate": MARKUP_RATE,
                "supports_byok": True,
                "provider_visible": True,
                "effective_from": EFFECTIVE_FROM,
            },
        ),
    ]
)


def seed_price_rows() -> list[dict]:
    """种子要投影的全部计价行 = 文案五行 + 视频两行。"""
    return list(PRICES.values()) + list(VIDEO_PRICES.values())


def cost_cents_for(op: str, units: int) -> int:
    """本次调用该收多少点。

    **向上取整**，不是四舍五入：点是整数，截断等于系统性少收。口径由 `AI-15` 钉住
    （0.003 × 1001 = 3.003 → 4，不是 3）。
    """
    from math import ceil

    row = PRICES[op]
    amount = Decimal(str(row["price_per_unit"])) * max(int(units), 0)
    return ceil(amount)
