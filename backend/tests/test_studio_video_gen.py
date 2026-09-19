"""03 追加 D · 平台 Key 配置与视频 generate（CF / VG）。"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services import ai as ai_service
from tests.helpers import DeepSeekStub

TASK = SimpleNamespace(title="探店", description="描述够十个字了", category="餐饮")


def test_cf01_env_example_has_key_lines():
    path = Path(__file__).resolve().parents[1] / ".env.example"
    text = path.read_text(encoding="utf-8")
    assert "DEEPSEEK_API_KEY=" in text
    assert "JIMENG_API_KEY=" in text
    assert "KLING_API_KEY=" in text


def test_cf02_settings_video_fields_default_empty(settings_override):
    s = settings_override(jimeng_api_key="", kling_api_key="")
    assert s.jimeng_api_key == ""
    assert s.kling_api_key == ""
    assert s.jimeng_base_url
    assert s.kling_base_url


@pytest.mark.asyncio
async def test_vg01_jimeng_empty_key_no_http(settings_override, monkeypatch):
    settings_override(jimeng_api_key="", kling_api_key="x")
    stub = DeepSeekStub()
    stub.install(monkeypatch)
    with pytest.raises(ai_service.VideoNotSupportedError, match="视频模型尚未接入"):
        await ai_service.generate("p", kind="video", provider="jimeng", task=TASK)
    assert stub.count == 0


@pytest.mark.asyncio
async def test_vg02_kling_empty_key_no_http(settings_override, monkeypatch):
    settings_override(kling_api_key="", jimeng_api_key="x")
    stub = DeepSeekStub()
    stub.install(monkeypatch)
    with pytest.raises(ai_service.VideoNotSupportedError, match="视频模型尚未接入"):
        await ai_service.generate("p", kind="video", provider="kling", task=TASK)
    assert stub.count == 0


@pytest.mark.asyncio
async def test_vg03_jimeng_success(settings_override, monkeypatch):
    settings_override(
        jimeng_api_key="sk-jimeng",
        jimeng_base_url="https://api.jimeng.example",
    )
    stub = DeepSeekStub()
    stub.push(content=None)  # overridden below via custom push shape

    # DeepSeekStub defaults to chat completions JSON; for video we need url field.
    # push with raw body via content being our JSON string won't work for url —
    # look at DeepSeekStub handler... it wraps content in choices message.
    # So we need to either extend stub or monkeypatch handler.

    import json
    import httpx

    requests: list = []

    async def handler(request):
        requests.append(request)
        return httpx.Response(
            200,
            json={"url": "https://cdn.example/v.mp4", "usage": {"total_tokens": 10}},
        )

    from app.services import ai as mod

    monkeypatch.setattr(mod, "_TRANSPORT", httpx.MockTransport(handler))
    result = await ai_service.generate("提示", kind="video", provider="jimeng", task=TASK)
    assert result.url == "https://cdn.example/v.mp4"
    assert result.content is None
    assert result.units == 10
    assert len(requests) == 1


@pytest.mark.asyncio
async def test_vg04_jimeng_401_key_invalid(settings_override, monkeypatch):
    settings_override(jimeng_api_key="bad", jimeng_base_url="https://api.jimeng.example")
    import httpx
    from app.services import ai as mod

    async def handler(request):
        return httpx.Response(401, json={"error": "no"})

    monkeypatch.setattr(mod, "_TRANSPORT", httpx.MockTransport(handler))
    with pytest.raises(ai_service.KeyInvalidError):
        await ai_service.generate("p", kind="video", provider="jimeng", task=TASK)
