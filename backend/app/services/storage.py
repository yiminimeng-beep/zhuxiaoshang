"""03-studio 的对象存储接缝。

下载走**预签名 URL**（15 分钟），不是永久直链——spec 明确要求「获取下载 URL 后
16 分钟再访问 → 403」。这意味着 URL 的签发权在服务端，客户端拿到的是一次性凭证。

`presign_url` 仍未接 MinIO。`sniff_mime` / `sniff_bytes` 已按追加 A 填真：
本地上传目录按魔数判断；读不到文件时返回 `None`。
"""

from pathlib import Path

from app.config import get_settings

_NOT_IMPLEMENTED = (
    "对象存储接缝尚未接入 MinIO。接入时请保持签名不变（见 test_plan.md「测试缝」）。"
)

DEFAULT_EXPIRES_IN = 900

MIME_EXT = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
}


def sniff_bytes(data: bytes) -> str | None:
    """按文件头魔数判断 mime；不够字节或对不上 → `None`。"""
    if len(data) >= 3 and data[0:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if len(data) >= 8 and data[0:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if (
        len(data) >= 12
        and data[0:4] == b"RIFF"
        and data[8:12] == b"WEBP"
    ):
        return "image/webp"
    return None


def resolve_upload_path(key: str) -> Path | None:
    """把 `/api/uploads/<name>` 或裸文件名解析到本地盘；非法形状 → `None`。"""
    settings = get_settings()
    prefix = settings.upload_url_prefix.rstrip("/") + "/"
    name = key
    if key.startswith(prefix):
        name = key[len(prefix) :]
    elif key.startswith("/"):
        return None
    if not name or "/" in name or "\\" in name or ".." in name:
        return None
    return Path(settings.upload_dir) / name


def sniff_mime(key: str) -> str | None:
    """读对象的**真实**类型（按魔数，不看 key 的后缀）。

    04 的截图上传靠它挡住「把 .txt 改名成 .png」：客户端声明的 `mime` 与
    文件名都只是它的自述，服务端要自己看一眼内容。取不到对象时返回 `None`,
    由调用方决定是 415 还是放行——这里不替业务选。
    """
    path = resolve_upload_path(key)
    if path is None or not path.is_file():
        return None
    try:
        with path.open("rb") as f:
            head = f.read(16)
    except OSError:
        return None
    return sniff_bytes(head)


def presign_url(key: str, *, expires_in: int = DEFAULT_EXPIRES_IN) -> str:
    """给对象存储的 key 签一个限时下载地址。"""
    raise NotImplementedError(_NOT_IMPLEMENTED)
