"""03 追加 B · AI 真实现的契约（`AI-01` ~ `AI-18` + `GR-01` / `GR-02`）。

**打桩位置在 transport 层**（`httpx.MockTransport`），不是函数层。
这一趟要测的正是「请求发得对不对」（URL / 头 / 体 / 状态码分类 / 用量怎么读），
把 `ai.py` 的函数整个换掉等于把被测对象一起换掉了——`AiStub` 那套测不了本段。

`conftest._no_real_ai` 是 autouse 护栏，本文件的 `deepseek_stub` 在它之后安装，
后装者胜；万一将来 `ai.py` 绕过 `_TRANSPORT` 直连网络，`GR-01` 会红。
"""

import inspect
import json
import math
from types import SimpleNamespace

import pytest

from app.services import ai as ai_service
from tests.helpers import SSE_BODY, SSE_PIECES

pytestmark = pytest.mark.asyncio

KEY = "sk-test-0123456789"
BASE = "https://api.deepseek.com"
TEXT_MODEL = "deepseek-v4-flash"
VISION_MODEL = "deepseek-v4-flash-vision-exp"

TASK = SimpleNamespace(
    id=1,
    title="门店探店短视频",
    description="拍摄 15 秒探店短视频并发布到抖音",
    category="餐饮",
    requirement="视频不少于 15 秒",
    tags=["探店"],
)
ASSETS = [SimpleNamespace(url="https://x.test/a.jpg", mime="image/jpeg", size_bytes=1024)]
IMAGE_URL = "https://x.test/shot.png"


@pytest.fixture
def configured(settings_override):
    """配好 key 与模型名——绝大多数用例的前置。"""
    return settings_override(
        deepseek_api_key=KEY,
        deepseek_base_url=BASE,
        deepseek_text_model=TEXT_MODEL,
        deepseek_vision_model=VISION_MODEL,
        deepseek_timeout_seconds=60,
    )


async def _drain(agen) -> list:
    return [piece async for piece in agen]


# --------------------------------------------------------------------------- #
# AI-01 签名
# --------------------------------------------------------------------------- #
# (参数名, 参数种类, 是否有默认值)。**种类也要对上**：`provider` 是 KEYWORD_ONLY，
# 谁把它改成位置参数，调用方 `chat_stream(msgs, "deepseek")` 就会静默绑错。
EXPECTED_PARAMS = {
    "guard_assets": [
        ("assets", "POSITIONAL_OR_KEYWORD", True),
        ("task", "POSITIONAL_OR_KEYWORD", True),
    ],
    "chat_stream": [
        ("messages", "POSITIONAL_OR_KEYWORD", True),
        ("provider", "KEYWORD_ONLY", True),
    ],
    "rewrite_prompt": [
        ("raw_prompt", "POSITIONAL_OR_KEYWORD", True),
        ("provider", "KEYWORD_ONLY", True),
    ],
    "generate": [
        ("prompt", "POSITIONAL_OR_KEYWORD", True),
        ("kind", "KEYWORD_ONLY", True),
        ("provider", "KEYWORD_ONLY", True),
        ("task", "KEYWORD_ONLY", True),
    ],
    "judge": [
        ("kind", "KEYWORD_ONLY", True),
        ("content", "KEYWORD_ONLY", True),
        ("url", "KEYWORD_ONLY", True),
        ("provider", "KEYWORD_ONLY", True),
    ],
    "ocr_metrics": [("image_url", "POSITIONAL_OR_KEYWORD", True)],
}


def _params(fn) -> list:
    return [
        (p.name, p.kind.name, p.default is inspect.Parameter.empty)
        for p in inspect.signature(fn).parameters.values()
    ]


async def test_ai01_signatures_unchanged():
    assert set(EXPECTED_PARAMS) == {
        "guard_assets",
        "chat_stream",
        "rewrite_prompt",
        "generate",
        "judge",
        "ocr_metrics",
    }, "六个接缝名本身也是契约，少一个多一个都算违约"
    for name, expected in EXPECTED_PARAMS.items():
        fn = getattr(ai_service, name)
        assert _params(fn) == expected, (
            f"{name} 的签名变了。接缝是钉住的契约（test_plan.md「测试缝」），"
            f"改名/改序必须同步改所有调用点与用例。"
        )


# --------------------------------------------------------------------------- #
# AI-02 未配置 key
# --------------------------------------------------------------------------- #
async def test_ai02_no_key_raises_config_error_without_request(
    settings_override, deepseek_stub
):
    settings_override(deepseek_api_key="")
    err = ai_service.AIConfigError

    with pytest.raises(err):
        await ai_service.guard_assets(ASSETS, TASK)
    with pytest.raises(err):
        await _drain(ai_service.chat_stream([{"role": "user", "content": "hi"}], provider="deepseek"))
    with pytest.raises(err):
        await ai_service.rewrite_prompt("粗糙提示词", provider="deepseek")
    with pytest.raises(err):
        await ai_service.generate("写点什么", kind="copy", provider="deepseek", task=TASK)
    with pytest.raises(err):
        await ai_service.judge(kind="copy", content="文案", url=None, provider="deepseek")
    with pytest.raises(err):
        await ai_service.ocr_metrics(IMAGE_URL)

    assert deepseek_stub.count == 0, (
        "未配置 key 时必须**在发请求之前**就失败，不能打出去了才发现没 key"
    )


# --------------------------------------------------------------------------- #
# AI-03 URL 与鉴权头
# --------------------------------------------------------------------------- #
async def test_ai03_request_url_and_auth_header(configured, deepseek_stub):
    deepseek_stub.push(content='{"passed": true, "reason": null}')
    await ai_service.guard_assets(ASSETS, TASK)

    req = deepseek_stub.last()
    assert req["url"] == f"{BASE}/chat/completions", req["url"]
    assert req["headers"].get("authorization") == f"Bearer {KEY}", req["headers"]


# --------------------------------------------------------------------------- #
# AI-04 / AI-05 / AI-06 流式对话
# --------------------------------------------------------------------------- #
async def test_ai04_chat_stream_body(configured, deepseek_stub):
    await _drain(ai_service.chat_stream([{"role": "user", "content": "hi"}], provider="deepseek"))

    body = deepseek_stub.body()
    assert body["stream"] is True, body
    assert body["model"] == TEXT_MODEL, body
    assert body["messages"] == [{"role": "user", "content": "hi"}], body


async def test_ai05_chat_stream_parses_each_data_line(configured, deepseek_stub):
    pieces = await _drain(
        ai_service.chat_stream([{"role": "user", "content": "hi"}], provider="deepseek")
    )

    assert pieces == list(SSE_PIECES), pieces
    assert "".join(pieces) == "".join(SSE_PIECES)
    assert "".join(pieces) == "你好，世界！"


async def test_ai06_chat_stream_stops_at_done(configured, deepseek_stub):
    # 在 [DONE] 之后再塞一段——它**不得**被吐出来
    deepseek_stub.push(
        content=SSE_BODY + 'data: {"choices":[{"delta":{"content":"不该出现"}}]}\n\n'
    )
    pieces = await _drain(
        ai_service.chat_stream([{"role": "user", "content": "hi"}], provider="deepseek")
    )

    assert pieces == list(SSE_PIECES), pieces
    assert "不该出现" not in "".join(pieces)


# --------------------------------------------------------------------------- #
# AI-07 ~ AI-10 上游错误分类
# --------------------------------------------------------------------------- #
async def test_ai07_401_and_403_are_key_invalid(configured, deepseek_stub):
    deepseek_stub.push(status=401)
    with pytest.raises(ai_service.KeyInvalidError):
        await ai_service.generate("写点什么", kind="copy", provider="deepseek", task=TASK)

    deepseek_stub.push(status=403)
    with pytest.raises(ai_service.KeyInvalidError):
        await ai_service.generate("写点什么", kind="copy", provider="deepseek", task=TASK)


async def test_ai08_429_is_ordinary_error(configured, deepseek_stub):
    deepseek_stub.push(status=429)
    with pytest.raises(Exception) as excinfo:
        await ai_service.generate("写点什么", kind="copy", provider="deepseek", task=TASK)

    assert not isinstance(excinfo.value, ai_service.KeyInvalidError), (
        "限流不是 Key 失效。混成一类会让流水线把「稍后再试」当成「Key 坏了」，"
        "把用户的 BYOK 标记成 invalid"
    )


async def test_ai09_500_is_ordinary_error(configured, deepseek_stub):
    deepseek_stub.push(status=500)
    with pytest.raises(Exception) as excinfo:
        await ai_service.generate("写点什么", kind="copy", provider="deepseek", task=TASK)

    assert not isinstance(excinfo.value, ai_service.KeyInvalidError)


async def test_ai10_timeout_comes_from_settings_and_is_not_swallowed(
    settings_override, deepseek_stub
):
    """超时取自配置，且超时**不会被当成 Key 失效**吞掉。

    这里不靠「睡 2 秒看它挂不挂」：`MockTransport` 不是真 socket，httpx 的超时
    是靠传输层实现的，桩上睡多久都不会中断——那样写只会得到一个**永远绿**的
    用例（还平白慢 2 秒）。改成两段可证的断言：

    1. 配置里的秒数**真的传到了传输层**（体里看不到超时，只有 `extensions` 有）；
    2. 传输层抛 `ReadTimeout` 时它原样上抛，不被 `_raise_for_status` 归成 Key 失效。
    """
    settings_override(
        deepseek_api_key=KEY, deepseek_base_url=BASE, deepseek_timeout_seconds=0.2
    )

    deepseek_stub.push(content="文案", usage={"total_tokens": 1})
    await ai_service.generate("写点什么", kind="copy", provider="deepseek", task=TASK)

    timeout = deepseek_stub.last()["timeout"]
    assert timeout, (
        f"请求没带超时配置——`_make_client` 必须把 deepseek_timeout_seconds 传下去，"
        f"实际 extensions.timeout={timeout!r}"
    )
    assert max(timeout.values()) <= 0.2, (
        f"超时必须取自配置（0.2s），实际 {timeout}——写死成默认值的话，"
        f"上游卡住时会一直挂着"
    )

    import httpx

    deepseek_stub.push(error=httpx.ReadTimeout("read timeout"))
    with pytest.raises(httpx.ReadTimeout) as excinfo:
        await ai_service.generate("写点什么", kind="copy", provider="deepseek", task=TASK)

    assert not isinstance(excinfo.value, ai_service.KeyInvalidError), (
        "超时是「稍后再试」，不是「Key 坏了」——混成一类会让用户的 BYOK 被误标 invalid"
    )


# --------------------------------------------------------------------------- #
# AI-11 / AI-12 复写
# --------------------------------------------------------------------------- #
async def test_ai11_rewrite_returns_parsed_result(configured, deepseek_stub):
    deepseek_stub.push(
        content='{"optimized_prompt": "优化后的提示词内容", "quality_score": 88}'
    )
    result = await ai_service.rewrite_prompt("粗糙提示词", provider="deepseek")

    assert result.optimized_prompt == "优化后的提示词内容"
    assert result.quality_score == 88
    assert result.model, "model 不得为空——它要落到 prompt_draft.optimizer_model"


async def test_ai12_rewrite_non_json_raises(configured, deepseek_stub):
    deepseek_stub.push(content="这是一段人话，不是 JSON")

    with pytest.raises(Exception) as excinfo:
        await ai_service.rewrite_prompt("粗糙提示词", provider="deepseek")

    assert not isinstance(excinfo.value, (ai_service.KeyInvalidError,)), (
        "拿不到结构就是失败。**回落原文是 api 层 3 次重试的活**"
        "（_rewrite_with_retries），不是接缝这里兜的"
    )


# --------------------------------------------------------------------------- #
# AI-13 / AI-14 / AI-15 生成
# --------------------------------------------------------------------------- #
async def test_ai13_generate_copy_reads_usage(configured, deepseek_stub):
    deepseek_stub.push(content="一杯好喝的奶茶", usage={"total_tokens": 1234})
    result = await ai_service.generate(
        "写点什么", kind="copy", provider="deepseek", task=TASK
    )

    assert result.content == "一杯好喝的奶茶"
    assert result.url is None, "文案没有文件产物"
    assert result.units == 1234, (
        "units 必须取上游真实用量——07 的报销基数 = cost_price_per_unit × units，"
        "写成常量的话 BYOK 报销会按同一个数走"
    )


async def test_ai14_generate_video_raises_without_request(configured, deepseek_stub):
    with pytest.raises(ai_service.VideoNotSupportedError):
        await ai_service.generate(
            "拍个视频", kind="video", provider="jimeng", task=TASK
        )

    assert deepseek_stub.count == 0, "视频直接拒，不该把请求打到 DeepSeek"


async def test_ai15_cost_is_ceil_of_unit_price_times_units(configured, deepseek_stub):
    from decimal import Decimal

    deepseek_stub.push(content="文案", usage={"total_tokens": 1000})
    result = await ai_service.generate(
        "写点什么", kind="copy", provider="deepseek", task=TASK
    )

    from app.services import pricing

    row = pricing.PRICES["generate"]
    unit_price = Decimal(str(row["price_per_unit"]))
    expected = math.ceil(unit_price * 1000)
    assert expected == 3, f"0.003 × 1000 应得 3 点，实际 {expected}——常量被改过了？"
    assert result.cost_cents == expected, (
        f"cost_cents 必须与 pricing.PRICES 同源，实际 {result.cost_cents} != {expected}"
    )

    # 不低估：多一个 token 也要进位，不能截断成 3
    deepseek_stub.push(content="文案", usage={"total_tokens": 1001})
    bumped = await ai_service.generate(
        "写点什么", kind="copy", provider="deepseek", task=TASK
    )
    assert bumped.cost_cents == math.ceil(unit_price * 1001) == 4


# --------------------------------------------------------------------------- #
# AI-16 / AI-17 / AI-18 视觉与配置
# --------------------------------------------------------------------------- #
async def test_ai16_vision_functions_use_vision_model(configured, deepseek_stub):
    deepseek_stub.push(content='{"passed": true, "reason": null}')
    await ai_service.guard_assets(ASSETS, TASK)

    body = deepseek_stub.body()
    assert body["model"] == VISION_MODEL, f"预检必须走视觉模型，实际 {body['model']}"
    assert "image_url" in json.dumps(body["messages"], ensure_ascii=False), (
        "素材图得以 image_url 传进去，否则模型看不到图"
    )

    deepseek_stub.push(
        content='{"likes": 100, "collects": 20, "comments": 5, "shares": 1, "confidence": 0.93}'
    )
    await ai_service.ocr_metrics(IMAGE_URL)

    body = deepseek_stub.body()
    assert body["model"] == VISION_MODEL, f"OCR 也必须走视觉模型，实际 {body['model']}"
    assert IMAGE_URL in json.dumps(body["messages"], ensure_ascii=False)


async def test_ai17_model_ids_come_from_settings(
    settings_override, deepseek_stub
):
    settings_override(
        deepseek_api_key=KEY,
        deepseek_base_url=BASE,
        deepseek_text_model="some-other-text-model",
    )
    deepseek_stub.push(content="文案", usage={"total_tokens": 10})
    await ai_service.generate("写点什么", kind="copy", provider="deepseek", task=TASK)

    assert deepseek_stub.body()["model"] == "some-other-text-model", (
        "模型 ID 一律走配置项——vision-exp 是实验模型，ID 随时会变，硬编码进函数体"
        "就等于写死了一个会过期的字符串"
    )


async def test_ai18_ocr_unrecognized_returns_none_not_zero(configured, deepseek_stub):
    deepseek_stub.push(
        content='{"likes": null, "collects": null, "comments": null, "shares": null,'
        ' "confidence": 0.05}'
    )
    verdict = await ai_service.ocr_metrics(IMAGE_URL)

    assert verdict.parsed is None, (
        "一个数字都没认出来必须是 None。用 0 兜底会让商户把一张糊图当真的批过去"
        "——0 是一个合法的互动量（spec 04 的硬规则）"
    )
    assert 0.0 <= verdict.confidence <= 1.0, verdict.confidence


# --------------------------------------------------------------------------- #
# GR-01 / GR-02 测试护栏
# --------------------------------------------------------------------------- #
async def test_gr01_guard_blocks_unstubbed_call():
    """不装任何桩 → 护栏当场炸，且信息指向该用哪个 fixture。"""
    with pytest.raises(AssertionError) as excinfo:
        await ai_service.guard_assets(ASSETS, TASK)

    assert "patch_ai" in str(excinfo.value), (
        f"护栏的错误信息必须说清该怎么办，实际：{excinfo.value}"
    )


async def test_gr02_stub_overrides_guard(configured, patch_ai):
    """装了 patch_ai 之后护栏让路——既有的 03 用例全绿就是这条的规模证明。"""
    verdict = await ai_service.guard_assets(ASSETS, TASK)
    assert verdict.passed is True
    assert len(patch_ai.calls["guard"]) == 1
