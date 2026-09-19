"""03 追加 C · 本地上传图转 base64（IR-01…IR-05）。"""

from __future__ import annotations

import base64
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services import ai as ai_service
from tests.helpers import DeepSeekStub

pytestmark = pytest.mark.asyncio

PNG_BYTES = bytes(
    [
        0x89,
        0x50,
        0x4E,
        0x47,
        0x0D,
        0x0A,
        0x1A,
        0x0A,
        0x00,
        0x00,
        0x00,
        0x0D,
        0x49,
        0x48,
        0x44,
        0x52,
    ]
)


@pytest.fixture
def configured(settings_override, tmp_path):
    return settings_override(
        deepseek_api_key="sk-test",
        deepseek_base_url="https://api.deepseek.com",
        deepseek_text_model="deepseek-v4-flash",
        deepseek_vision_model="deepseek-v4-flash-vision-exp",
        upload_dir=str(tmp_path),
        upload_url_prefix="/api/uploads",
    )


@pytest.fixture
def stub(monkeypatch, configured):
    s = DeepSeekStub()
    s.push(content='{"passed": true, "reason": null}')
    s.install(monkeypatch)
    return s


def _write_png(root: Path, name: str = "a.png") -> Path:
    path = root / name
    path.write_bytes(PNG_BYTES)
    return path


async def test_ir01_local_upload_becomes_data_url(configured, stub, tmp_path):
    _write_png(tmp_path, "a.png")
    assets = [SimpleNamespace(url="/api/uploads/a.png", mime="image/png")]
    await ai_service.guard_assets(assets, SimpleNamespace(title="t"))
    assert stub.count == 1
    content = stub.body()["messages"][1]["content"]
    images = [c for c in content if isinstance(c, dict) and c.get("type") == "image_url"]
    assert images, content
    url = images[0]["image_url"]["url"]
    assert url.startswith("data:image/png;base64,")
    raw = base64.b64decode(url.split(",", 1)[1])
    assert raw == PNG_BYTES


async def test_ir02_https_passthrough(configured, stub):
    assets = [SimpleNamespace(url="https://x.test/a.jpg", mime="image/jpeg")]
    await ai_service.guard_assets(assets, SimpleNamespace(title="t"))
    content = stub.body()["messages"][1]["content"]
    images = [c for c in content if isinstance(c, dict) and c.get("type") == "image_url"]
    assert images[0]["image_url"]["url"] == "https://x.test/a.jpg"


async def test_ir03_data_url_not_reencoded(configured, stub):
    data_url = "data:image/png;base64,AAAA"
    assets = [SimpleNamespace(url=data_url, mime="image/png")]
    await ai_service.guard_assets(assets, SimpleNamespace(title="t"))
    content = stub.body()["messages"][1]["content"]
    images = [c for c in content if isinstance(c, dict) and c.get("type") == "image_url"]
    assert images[0]["image_url"]["url"] == data_url


async def test_ir04_missing_file_raises_no_http(configured, stub, tmp_path):
    assets = [SimpleNamespace(url="/api/uploads/missing.png", mime="image/png")]
    with pytest.raises(ValueError, match="不存在|非法"):
        await ai_service.guard_assets(assets, SimpleNamespace(title="t"))
    assert stub.count == 0


async def test_ir05_ocr_same_resolver(configured, stub, tmp_path, monkeypatch):
    _write_png(tmp_path, "shot.png")
    stub.queue.clear()
    stub.push(
        content='{"likes":1,"collects":2,"comments":3,"shares":0,"confidence":0.9}'
    )
    await ai_service.ocr_metrics("/api/uploads/shot.png")
    assert stub.count == 1
    content = stub.body()["messages"][1]["content"]
    images = [c for c in content if isinstance(c, dict) and c.get("type") == "image_url"]
    assert images[0]["image_url"]["url"].startswith("data:image/png;base64,")
