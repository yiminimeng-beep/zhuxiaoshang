r"""BYOK 密钥的加解密、掩码与 provider 校验。

**密钥明文只在请求里出现一次**：落库的是 AES-256-GCM 密文（含 nonce + tag），
列表接口只返回 `key_masked`。任何一条把它回显出去的路都是拖库即失守。

三个对外契约（`test_plan.md` 的「测试缝」钉的就是这三个名字）：

| 名字 | 签名 |
|---|---|
| `encrypt_key` | `(plaintext: str, key_version: int \| None = None) -> str` |
| `decrypt_key` | `(cipher: str, key_version: int \| None = None) -> str` |
| `verify_provider_key` | `async (provider: str, api_key: str) -> bool` |

**主密钥轮换**：`MASTER_KEYS` 是「版本号 → 密钥」的表。每条密钥行都记下
`key_version`，轮换时加一个新版本即可——旧行仍能用旧版本解开，不必一次性重写全表。
版本号对不上就抛错，绝不返回垃圾串（`K-24`）。
"""

import base64
import hashlib
import os

import httpx
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.config import get_settings

# 版本号 → 主密钥。生产环境从环境变量 / KMS 注入；这里是开发默认值。
# 轮换时**只增不改**：删掉旧版本会让所有已落库的密文变成废字节。
MASTER_KEYS: dict[int, bytes] = {
    1: hashlib.sha256(b"dev-only-byok-master-key-v1").digest(),
}
CURRENT_KEY_VERSION = 1

# AES-GCM 标准 nonce 长度；96 位是 GCM 的原生尺寸，别的长度会走 GHASH 慢路径。
_NONCE_LEN = 12

PROVIDERS = ("deepseek", "dashscope", "jimeng", "kling")

# 字段名带这些词的，一律拒绝。接受 `base_url` 等于让用户把请求（连密钥）
# 打到任意地址——SSRF + 密钥外泄，两样都占。
FORBIDDEN_BODY_KEYS = ("base_url", "endpoint", "url", "host", "proxy")


class KeyDecryptionError(Exception):
    """密文解不开：版本号缺失、密钥不对或密文被改过。"""


def _master_key(key_version: int | None) -> bytes:
    version = CURRENT_KEY_VERSION if key_version is None else key_version
    key = MASTER_KEYS.get(version)
    if key is None:
        raise KeyDecryptionError(f"没有版本 {version} 的加密主密钥")
    return key


def encrypt_key(plaintext: str, key_version: int | None = None) -> str:
    """返回 `base64(nonce || ciphertext+tag)`。

    nonce 每次随机，所以同一把密钥两次加密得到的密文不同——想靠「密文相等」
    反推「两行是不是同一把 Key」也不行。
    """
    version = CURRENT_KEY_VERSION if key_version is None else key_version
    key = _master_key(version)
    nonce = os.urandom(_NONCE_LEN)
    blob = nonce + AESGCM(key).encrypt(nonce, plaintext.encode("utf-8"), None)
    return base64.urlsafe_b64encode(blob).decode("ascii")


def decrypt_key(cipher: str, key_version: int | None = None) -> str:
    key = _master_key(key_version)
    try:
        blob = base64.urlsafe_b64decode(cipher.encode("ascii"))
    except Exception as exc:  # 非法 base64 也是「解不开」，不该冒泡成 500
        raise KeyDecryptionError("密文不是合法的 base64") from exc
    if len(blob) <= _NONCE_LEN:
        raise KeyDecryptionError("密文长度不足，缺少 nonce 或 tag")
    nonce, body = blob[:_NONCE_LEN], blob[_NONCE_LEN:]
    try:
        return AESGCM(key).decrypt(nonce, body, None).decode("utf-8")
    except (InvalidTag, ValueError) as exc:
        # GCM 的 tag 校验失败：密钥版本不对或密文被改过。两种都必须抛，
        # 绝不能吞掉错误返回半截垃圾——那会变成一次静默的错误调用。
        raise KeyDecryptionError("密文校验失败") from exc


class NoActiveKey(Exception):
    """BYOK 调用时，该用户没有这个 provider 的有效 Key。"""


async def load_active_plaintext(session, user_id: int, provider: str) -> str:
    """解开该用户该 provider 的 active Key。没有就抛 `NoActiveKey`，不回落平台。"""
    from sqlalchemy import select

    from app.models.quota import UserModelKey

    row = await session.scalar(
        select(UserModelKey).where(
            UserModelKey.user_id == user_id,
            UserModelKey.provider == provider,
            UserModelKey.status == "active",
        )
    )
    if row is None:
        raise NoActiveKey(provider)
    return decrypt_key(row.key_cipher, row.key_version)


def mask_key(plaintext: str) -> str:
    """`sk-live-abcdefghijklmnop-a1b2` → `sk-****a1b2`。**展示只用这一份**。"""
    tail = plaintext[-4:] if len(plaintext) > 4 else plaintext
    head = "sk-" if plaintext.startswith("sk-") else plaintext[:2]
    return f"{head}****{tail}"


async def verify_provider_key(provider: str, api_key: str) -> bool:
    """调 provider 的轻量接口验证 Key 是否可用。

    **测试缝**：用例通过 `tests.helpers.patch_verify` 把本函数换掉，避免真的
    打外部网络。名字被改时测试会抛 `AttributeError`——这是故意的，接缝即契约。

    DeepSeek 打 `GET {base}/models`。401/403 或网络失败 → `False`（端点回 422）。
    即梦 / 可灵同样打各自 base 的 `/models`；厂商路径不同时会得到 False，
    **不抛**——抛了页面上就是 500，用户分不清是 Key 错还是我们没接。
    """
    settings = get_settings()
    bases = {
        "deepseek": settings.deepseek_base_url,
        "jimeng": settings.jimeng_base_url,
        "kling": settings.kling_base_url,
        "dashscope": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    }
    base = bases.get(provider)
    if not base or not api_key.strip():
        return False
    url = base.rstrip("/") + "/models"
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(
                url,
                headers={"Authorization": f"Bearer {api_key}"},
            )
    except httpx.HTTPError:
        return False
    return response.status_code == 200
