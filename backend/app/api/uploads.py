"""03-studio 追加 A · 本地素材上传。

不建表：文件名 = 不透明 token + 嗅探出的扩展名；`GET` 免鉴权（token 即凭据）。
"""

from __future__ import annotations

import re
import secrets
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse

from app.config import get_settings
from app.core.deps import AuthContext, get_current_auth
from app.models.studio import MIME_TYPES
from app.services import storage as storage_service

router = APIRouter(tags=["uploads"])

# token_urlsafe 字符集 + 白名单扩展名
_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+\.(jpg|png|webp)$")


def _validate_name(name: str) -> None:
    if (
        not name
        or "/" in name
        or "\\" in name
        or ".." in name
        or not _NAME_RE.fullmatch(name)
    ):
        raise HTTPException(status_code=400, detail="文件名非法")


def _upload_root() -> Path:
    root = Path(get_settings().upload_dir)
    root.mkdir(parents=True, exist_ok=True)
    return root


@router.post("/api/uploads", status_code=201)
async def upload_file(
    auth: AuthContext = Depends(get_current_auth),
    file: UploadFile = File(...),
) -> dict:
    del auth  # 只要登录；本段不做归属登记
    settings = get_settings()
    max_bytes = settings.upload_max_bytes

    # 多读一字节：恰好上限放行，超一字节 413
    data = await file.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise HTTPException(status_code=413, detail="文件不得超过 20MB")

    mime = storage_service.sniff_bytes(data)
    if mime is None or mime not in MIME_TYPES:
        raise HTTPException(status_code=415, detail="只接受 jpeg / png / webp 图片")

    ext = storage_service.MIME_EXT[mime]
    name = f"{secrets.token_urlsafe(24)}.{ext}"
    root = _upload_root()
    dest = root / name
    tmp = root / f".{name}.partial"
    try:
        tmp.write_bytes(data)
        tmp.replace(dest)
    except OSError:
        if tmp.exists():
            tmp.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail="落盘失败") from None

    prefix = settings.upload_url_prefix.rstrip("/")
    return {
        "url": f"{prefix}/{name}",
        "mime": mime,
        "size_bytes": len(data),
    }


@router.get("/api/uploads/{name:path}")
async def get_upload(name: str):
    _validate_name(name)
    path = _upload_root() / name
    # 二次兜底：解析后必须仍在上传根目录内
    try:
        path.resolve().relative_to(_upload_root().resolve())
    except ValueError:
        raise HTTPException(status_code=400, detail="文件名非法") from None
    if not path.is_file():
        raise HTTPException(status_code=404, detail="文件不存在")
    mime = storage_service.sniff_bytes(path.read_bytes()[:16]) or "application/octet-stream"
    return FileResponse(path, media_type=mime)
