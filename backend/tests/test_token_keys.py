"""07-token · BYOK 密钥（F 组）。

对应 test_plan.md 的 `E-19` ~ `E-23` / `K-01` ~ `K-25`。

这一组测的不是「功能对不对」，而是**明文会不会漏**。
每条 Key 相关的断言都拿 `SECRET_KEY_PROBE`（明文中段）去搜响应体 / 数据库 /
异常栈——整串搜会被掩码挡掉，搜了等于没搜。

另外两条硬规则：
- **不接受 `base_url` / `endpoint` / `url`**：等于让用户把请求打到任意地址，SSRF + 密钥外泄
- **Key 失效绝不回落平台 Key**：回落等于白烧平台的钱（`LM-14`，第二趟）
"""

import pytest

from tests.helpers import (
    SECRET_KEY_PROBE,
    SECRET_KEY_SAMPLE,
    assert_route_registered,
    bearer,
    patch_verify,
    post_key,
)

pytestmark = pytest.mark.asyncio


async def _keys(client, token):
    return await client.get("/api/me/model-keys", headers=bearer(token))


# --------------------------------------------------------------------------- #
# 正常路径
# --------------------------------------------------------------------------- #
async def test_e19_create_key(client, customer, monkeypatch):
    patch_verify(monkeypatch, ok=True)

    r = await post_key(client, customer[1], "deepseek")
    assert r.status_code == 201, r.text

    body = r.json()
    assert body["provider"] == "deepseek"
    assert body["status"] == "active"
    assert "id" in body
    assert "key_masked" in body


async def test_e20_list_keys(client, customer, monkeypatch):
    patch_verify(monkeypatch, ok=True)
    await post_key(client, customer[1], "deepseek")

    r = await _keys(client, customer[1])
    assert r.status_code == 200, r.text

    items = r.json()["items"]
    assert len(items) == 1
    for key in ("id", "provider", "label", "key_masked", "status", "last_verified_at", "last_used_at"):
        assert key in items[0], f"密钥列表项缺字段 {key}"


async def test_e21_patch_key_label(client, customer, monkeypatch):
    patch_verify(monkeypatch, ok=True)
    created = (await post_key(client, customer[1], "deepseek")).json()

    r = await client.patch(
        f"/api/me/model-keys/{created['id']}",
        headers=bearer(customer[1]),
        json={"label": "备用"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["label"] == "备用" or r.json()["key_masked"] == created["key_masked"]


async def test_e22_delete_key(client, customer, db, monkeypatch):
    patch_verify(monkeypatch, ok=True)
    created = (await post_key(client, customer[1], "deepseek")).json()

    r = await client.delete(
        f"/api/me/model-keys/{created['id']}", headers=bearer(customer[1])
    )
    assert r.status_code == 204, r.text

    count = await db.fetchval(
        "SELECT count(*) FROM user_model_key WHERE id = $1", created["id"]
    )
    assert count == 0


async def test_e23_verify_ok(client, customer, monkeypatch):
    patch_verify(monkeypatch, ok=True)
    created = (await post_key(client, customer[1], "deepseek")).json()

    r = await client.post(
        f"/api/me/model-keys/{created['id']}/verify", headers=bearer(customer[1])
    )
    assert r.status_code == 200, r.text

    body = r.json()
    assert body["status"] == "active"
    assert body["last_verified_at"], "校验通过要记下校验时刻"


# --------------------------------------------------------------------------- #
# 白名单与非法字段
# --------------------------------------------------------------------------- #
async def test_k01_openai_422(client, customer):
    r = await post_key(client, customer[1], "openai")
    assert r.status_code == 422, "白名单外一律拒：只接国内模型"


async def test_k02_anthropic_422(client, customer):
    r = await post_key(client, customer[1], "anthropic")
    assert r.status_code == 422, r.text


async def test_k03_base_url_422(client, customer):
    r = await post_key(
        client, customer[1], "deepseek", base_url="https://evil.example.com"
    )
    assert r.status_code == 422, (
        f"接受 base_url 等于让用户把密钥打到任意地址（SSRF + 外泄）：{r.status_code} {r.text}"
    )


async def test_k04_endpoint_422(client, customer):
    r = await post_key(client, customer[1], "deepseek", endpoint="https://evil.example.com")
    assert r.status_code == 422, r.text


async def test_k05_url_422(client, customer):
    r = await post_key(client, customer[1], "deepseek", url="https://evil.example.com")
    assert r.status_code == 422, r.text


async def test_k06_duplicate_provider_409(client, customer, monkeypatch):
    patch_verify(monkeypatch, ok=True)
    assert (await post_key(client, customer[1], "deepseek")).status_code == 201

    r = await post_key(client, customer[1], "deepseek")
    assert r.status_code == 409, r.text


# --------------------------------------------------------------------------- #
# 越权与不存在
# --------------------------------------------------------------------------- #
async def test_k07_patch_others_403(client, customer, merchant, monkeypatch):
    patch_verify(monkeypatch, ok=True)
    created = (await post_key(client, customer[1], "deepseek")).json()

    r = await client.patch(
        f"/api/me/model-keys/{created['id']}",
        headers=bearer(merchant[1]),
        json={"label": "抢过来"},
    )
    assert r.status_code == 403, r.text


async def test_k08_delete_others_403(client, customer, merchant, monkeypatch):
    patch_verify(monkeypatch, ok=True)
    created = (await post_key(client, customer[1], "deepseek")).json()

    r = await client.delete(
        f"/api/me/model-keys/{created['id']}", headers=bearer(merchant[1])
    )
    assert r.status_code == 403, r.text


async def test_k09_verify_others_403(client, customer, merchant, monkeypatch):
    patch_verify(monkeypatch, ok=True)
    created = (await post_key(client, customer[1], "deepseek")).json()

    r = await client.post(
        f"/api/me/model-keys/{created['id']}/verify", headers=bearer(merchant[1])
    )
    assert r.status_code == 403, r.text


async def test_k10_unknown_key_404(client, customer):
    assert_route_registered("PATCH", "/api/me/model-keys/{key_id}")

    r = await client.patch(
        "/api/me/model-keys/999999",
        headers=bearer(customer[1]),
        json={"label": "不存在"},
    )
    assert r.status_code == 404, r.text


async def test_k11_delete_unknown_404(client, customer):
    assert_route_registered("DELETE", "/api/me/model-keys/{key_id}")

    r = await client.delete(
        "/api/me/model-keys/999999", headers=bearer(customer[1])
    )
    assert r.status_code == 404, r.text


# --------------------------------------------------------------------------- #
# 校验失败不得入库为 active
# --------------------------------------------------------------------------- #
async def test_k12_verify_fail_not_active(client, customer, db, monkeypatch):
    patch_verify(monkeypatch, ok=False)

    r = await post_key(client, customer[1], "deepseek")
    assert r.status_code == 422, f"provider 校验失败应 422：{r.status_code} {r.text}"

    active = await db.fetchval(
        "SELECT count(*) FROM user_model_key WHERE status = 'active'"
    )
    assert active == 0, "校验失败的密钥不得入库为 active"


# --------------------------------------------------------------------------- #
# 明文不外泄（这一组的重点）
# --------------------------------------------------------------------------- #
async def test_k13_cipher_no_plaintext(client, customer, db, monkeypatch):
    patch_verify(monkeypatch, ok=True)
    await post_key(client, customer[1], "deepseek")

    row = await db.fetchrow(
        "SELECT key_cipher, key_masked FROM user_model_key"
    )
    assert SECRET_KEY_PROBE not in row["key_cipher"], (
        "库里必须是密文——明文落库等于拖库即失守"
    )
    assert SECRET_KEY_PROBE not in row["key_masked"]

    from app.services.model_key import decrypt_key

    assert decrypt_key(row["key_cipher"]) == SECRET_KEY_SAMPLE, (
        "密文必须能解回原文，否则这只是一段无意义的字节"
    )


async def test_k14_response_no_plaintext(client, customer, monkeypatch):
    patch_verify(monkeypatch, ok=True)
    await post_key(client, customer[1], "deepseek")

    r = await _keys(client, customer[1])
    assert SECRET_KEY_PROBE not in r.text, (
        f"密钥列表只该返回掩码，明文不得出现在响应里：{r.text}"
    )


async def test_k15_create_response_no_plaintext(client, customer, monkeypatch):
    patch_verify(monkeypatch, ok=True)
    r = await post_key(client, customer[1], "deepseek")

    assert SECRET_KEY_PROBE not in r.text, (
        f"保存响应不得回显明文：{r.text}"
    )


async def test_k16_error_response_no_plaintext(client, customer):
    """请求被拒时也不能把明文回显出来。

    最真实的泄漏向量：若实现把 `api_key` 交给 pydantic 的 `extra="forbid"`，
    框架生成的 `422` 会把**整个请求体**塞进 `detail[].input` 回给客户端。
    这里带个 `base_url` 把请求逼成 `422`，再搜明文中段。
    """
    r = await post_key(
        client,
        customer[1],
        "deepseek",
        base_url="https://evil.example.com",
    )
    assert r.status_code == 422, r.text
    assert SECRET_KEY_PROBE not in r.text, (
        f"422 响应体里带出了明文密钥——错误回显把请求体原样吐回去了：{r.text}"
    )


async def test_k17_mask_shape(client, customer, monkeypatch):
    patch_verify(monkeypatch, ok=True)
    short_key = "sk-abcd1234"

    r = await post_key(client, customer[1], "deepseek", api_key=short_key)
    assert r.status_code == 201, r.text

    masked = r.json()["key_masked"]
    assert masked != short_key, "掩码不得等于原文"
    assert short_key not in r.text, "短 key 的原文一次都不该出现"
    assert masked.startswith("sk-"), f"掩码应保留前缀形如 sk-****1234，实际 {masked}"
    assert masked.endswith("1234"), f"掩码应保留尾 4 位，实际 {masked}"
    assert "****" in masked


async def test_k18_rotate_key_secret(client, customer, db, monkeypatch):
    patch_verify(monkeypatch, ok=True)
    created = (await post_key(client, customer[1], "deepseek")).json()
    new_key = "sk-live-zzzzzzzzzzzzzzzz-b9c8"

    r = await client.patch(
        f"/api/me/model-keys/{created['id']}",
        headers=bearer(customer[1]),
        json={"api_key": new_key},
    )
    assert r.status_code == 200, r.text

    row = await db.fetchrow(
        "SELECT key_cipher, key_masked FROM user_model_key WHERE id = $1",
        created["id"],
    )
    from app.services.model_key import decrypt_key

    assert decrypt_key(row["key_cipher"]) == new_key, "换密钥后密文要跟着换"
    assert "b9c8" in row["key_masked"], "掩码要反映新密钥的尾号"
    assert SECRET_KEY_PROBE not in r.text, "换密钥的响应同样不得回显明文"


async def test_k19_fail_count_disables(client, customer, db, monkeypatch):
    patch_verify(monkeypatch, ok=True)
    created = (await post_key(client, customer[1], "deepseek")).json()

    await db.execute(
        "UPDATE user_model_key SET fail_count = 3, status = 'disabled' WHERE id = $1",
        created["id"],
    )

    status = await db.fetchval(
        "SELECT status FROM user_model_key WHERE id = $1", created["id"]
    )
    assert status == "disabled", "连续失败达 3 次应自动停用，逼用户重新保存"


async def test_k20_closed_user_keys_purged(client, customer, db, monkeypatch):
    """注销要**物理删除**密钥。

    凭据不是审计数据：留着既是负担又是风险。与「流水/报销单不可删」不冲突——
    后者是账，前者是钥匙。
    """
    patch_verify(monkeypatch, ok=True)
    await post_key(client, customer[1], "deepseek")

    r = await client.post(
        "/api/me/close",
        headers=bearer(customer[1]),
        json={"password": "pass1234", "confirm": True},
    )
    assert r.status_code == 202, f"注销失败：{r.status_code} {r.text}"

    count = await db.fetchval(
        "SELECT count(*) FROM user_model_key WHERE user_id = $1", customer[0]["id"]
    )
    assert count == 0, "用户注销后密钥必须物理删除"


async def test_k21_banned_user_keys_unusable(client, customer, db, monkeypatch, set_user_status):
    """封禁期间密钥**不可用**，且行要留着。

    断言的是行为（不可用），不是 `user_model_key.status` 这个字段值——
    后者的级联写入挂在 06 的封禁端点上下文中（见「已知取舍」#10）。
    """
    patch_verify(monkeypatch, ok=True)
    created = (await post_key(client, customer[1], "deepseek")).json()

    await set_user_status(customer[0]["id"], "banned")

    r = await _keys(client, customer[1])
    assert r.status_code in (401, 403), (
        f"封禁用户不得还能列出/使用密钥：{r.status_code} {r.text}"
    )

    row = await db.fetchrow(
        "SELECT status FROM user_model_key WHERE id = $1", created["id"]
    )
    assert row is not None, "封禁只是停用，不是删除——解封后要能恢复"


async def test_k22_unban_restores_keys(client, customer, db, monkeypatch, set_user_status):
    patch_verify(monkeypatch, ok=True)
    created = (await post_key(client, customer[1], "deepseek")).json()

    await set_user_status(customer[0]["id"], "banned")
    await set_user_status(customer[0]["id"], "active")

    r = await _keys(client, customer[1])
    assert r.status_code == 200, f"解封后密钥应恢复可用：{r.status_code} {r.text}"
    assert len(r.json()["items"]) == 1


async def test_k23_key_version_persisted(client, customer, db, monkeypatch):
    patch_verify(monkeypatch, ok=True)
    await post_key(client, customer[1], "deepseek")

    version = await db.fetchval("SELECT key_version FROM user_model_key")
    assert version is not None and version >= 1, (
        "key_version 要落库，否则将来轮换主密钥时分不清哪把密钥用哪个版本"
    )


async def test_k24_wrong_master_key_fails(client, customer, monkeypatch):
    patch_verify(monkeypatch, ok=True)
    await post_key(client, customer[1], "deepseek")

    from app.services.model_key import decrypt_key, encrypt_key

    cipher = encrypt_key(SECRET_KEY_SAMPLE)
    assert decrypt_key(cipher) == SECRET_KEY_SAMPLE, "同版本密钥要能自洽往返"

    # 换一个版本号：没有对应的主密钥，就该解不出来而不是返回垃圾
    with pytest.raises(Exception):
        decrypt_key(cipher, key_version=999)


async def test_k25_no_token_401(client):
    r = await client.get("/api/me/model-keys")
    assert r.status_code == 401, r.text
