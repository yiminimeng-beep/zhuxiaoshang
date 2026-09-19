"""BYOK 调用必须用客户页面里保存的 Key，不读平台 .env。"""

from __future__ import annotations

import pytest

from tests.helpers import (
    create_job_ok,
    patch_verify,
    post_key,
    run_stage,
)

pytestmark = pytest.mark.asyncio

USER_KEY = "sk-customer-page-key-zzzz"
PLATFORM_KEY = "sk-platform-should-not-be-used"
ASSET = [{"url": "https://x.test/a.jpg", "mime": "image/jpeg", "size_bytes": 1024}]


def _authorization(stub) -> str:
    headers = {k.lower(): v for k, v in stub.requests[-1]["headers"].items()}
    return headers.get("authorization", "")


async def test_ky01_guard_uses_user_key_when_platform_empty(
    client, studio, db, patch_dispatch, deepseek_stub, settings_override, monkeypatch
):
    settings_override(deepseek_api_key="")
    patch_verify(monkeypatch, ok=True)
    saved = await post_key(client, studio.customer_token, api_key=USER_KEY)
    assert saved.status_code == 201, saved.text

    deepseek_stub.push(content='{"passed": true, "reason": null}')
    body = await create_job_ok(
        client,
        studio.customer_token,
        studio.task_id,
        studio.claim_id,
        billing_source="byok",
        assets=ASSET,
    )
    await run_stage("guard", body["job_id"])

    status = await db.fetchval(
        "SELECT status FROM content_job WHERE id = $1", body["job_id"]
    )
    assert status == "chatting", f"平台 Key 为空时应用客户 Key 跑完预检：{status}"
    assert deepseek_stub.count == 1
    assert _authorization(deepseek_stub) == f"Bearer {USER_KEY}"


async def test_ky02_byok_ignores_platform_key(
    client, studio, db, patch_dispatch, deepseek_stub, settings_override, monkeypatch
):
    settings_override(deepseek_api_key=PLATFORM_KEY)
    patch_verify(monkeypatch, ok=True)
    saved = await post_key(client, studio.customer_token, api_key=USER_KEY)
    assert saved.status_code == 201, saved.text

    deepseek_stub.push(content='{"passed": true, "reason": null}')
    body = await create_job_ok(
        client,
        studio.customer_token,
        studio.task_id,
        studio.claim_id,
        billing_source="byok",
        assets=ASSET,
    )
    await run_stage("guard", body["job_id"])

    assert deepseek_stub.count == 1
    auth = _authorization(deepseek_stub)
    assert auth == f"Bearer {USER_KEY}"
    assert PLATFORM_KEY not in auth
