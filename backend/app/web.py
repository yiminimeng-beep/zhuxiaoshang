"""前端构建产物的静态挂载（全局约定 13，见 `specs/00-overview/spec.md`）。

`/` 服务 8001 的产物，`/admin` 服务 8002 后台的产物。两者都是 **SPA**，
所以「文件找不到」不等于「路径不存在」——六条契约写在 spec 里，实现就在下面。

**为什么不用 `StaticFiles`**：它在目录缺失时**导入即抛**（`check_dir=True`），
而「还没构建」是正常状态（只跑后端的机器、忘跑 `npm run build` 的部署），
一抛就整个后端起不来。这里自己兜底成 404。

**为什么目录是 callable**：请求时才去取模块属性，测试才能 monkeypatch
`web.ADMIN_DIST` 打中接缝。若 `main.py` 写 `from app.web import ADMIN_DIST`
把它绑成本地名，接缝就打不中——这个坑本项目踩过三次（07 / 04 根因表）。

**为什么只挂一个 `/`、在内部按前缀分派**：`Mount("/admin")` 编译出的正则
是 `^/admin/(?P<path>.*)$`——**它不匹配裸的 `/admin`**。裸 `/admin` 会掉到
`/` 那个挂载上，于是返回**主站**的 `index.html`（两个前端串了）。
在 `Mount` 上没法表达「前缀本身也算命中」，所以在自己这层分派：
`admin` → 后台产物，其余 → 主站产物。
"""

import os
from collections.abc import Callable
from pathlib import Path

from fastapi import FastAPI
from starlette.responses import FileResponse, JSONResponse
from starlette.routing import get_route_path
from starlette.types import Receive, Scope, Send

REPO_ROOT = Path(__file__).resolve().parents[2]

# 挂载只处理这两个方法：否则未注册路径的 `POST` 会从 404 变 405，
# 把既有的错误码契约改掉（spec 规则 4）
_ALLOWED_METHODS = ("GET", "HEAD")

# 永不被吞的前缀。`/api/*` 里**未注册**的那些必须保持 404——
# 被吞成 index.html 会让「端点没实现」伪装成 200（spec 规则 3）
_API_PREFIX = ("api/",)

# 后台产物所在的 URL 前缀。命中它的走 ADMIN_DIST，其余走 FRONTEND_DIST
_ADMIN_PREFIX = "admin"


def _dist_from_env(var: str, *default_parts: str) -> Path:
    raw = os.environ.get(var)
    return Path(raw) if raw else REPO_ROOT.joinpath(*default_parts)


FRONTEND_DIST = _dist_from_env("FRONTEND_DIST_DIR", "frontend", "dist")
ADMIN_DIST = _dist_from_env("ADMIN_DIST_DIR", "frontend-admin", "dist")


def _has_extension(subpath: str) -> bool:
    """末段带不带「.」——决定找不到时回不回落到 `index.html`。"""
    return "." in Path(subpath).name


def find_asset(directory: Path, subpath: str) -> Path | None:
    """把 URL 子路径解析成目录内的真实文件。越界、不存在、不是文件都回 `None`。

    `resolve()` 会吃掉 `..`，于是「解出来的路径不在目录内」就是穿越——
    直接拒绝，不去修它。
    """
    try:
        root = directory.resolve()
    except OSError:
        return None
    if not root.is_dir():
        return None
    if not subpath:
        return None
    candidate = (root / subpath).resolve()
    if candidate != root and root not in candidate.parents:
        return None
    return candidate if candidate.is_file() else None


async def _not_found(scope: Scope, receive: Receive, send: Send) -> None:
    await JSONResponse({"detail": "Not Found"}, status_code=404)(scope, receive, send)


class SpaFiles:
    """一个 SPA 挂载点的 ASGI 应用。

    `roots` 是「URL 前缀 → 产物目录」的有序表，`default` 是不命中任何前缀时
    的那份产物。两者都收 **callable**：目录在**请求时**才解析，测试才能
    monkeypatch 模块属性。
    """

    def __init__(
        self,
        roots: tuple[tuple[str, Callable[[], Path]], ...],
        *,
        default: Callable[[], Path],
        reserved: tuple[str, ...] = (),
    ) -> None:
        self._roots = roots
        self._default = default
        self._reserved = reserved

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":  # pragma: no cover - 没有 websocket 挂载
            raise RuntimeError("SpaFiles 只处理 http")
        # 挂载在 `/`，这里拿到的就是完整路径（`root_path` 为空）
        subpath = get_route_path(scope).lstrip("/")
        if scope["method"] not in _ALLOWED_METHODS or self._is_reserved(subpath):
            await _not_found(scope, receive, send)
            return
        directory, relative = self._target(subpath)
        await self._serve(scope, receive, send, directory, relative)

    def _is_reserved(self, subpath: str) -> bool:
        return any(
            subpath == prefix.rstrip("/") or subpath.startswith(prefix)
            for prefix in self._reserved
        )

    def _target(self, subpath: str) -> tuple[Path, str]:
        """前缀分派。`admin` 与 `admin/...` 都归后台产物，且后者去掉前缀。

        只认「等于前缀」或「前缀 + `/`」——`/administrator` 不该命中后台。
        """
        for prefix, directory in self._roots:
            if subpath == prefix:
                return directory(), ""
            if subpath.startswith(prefix + "/"):
                return directory(), subpath[len(prefix) + 1 :]
        return self._default(), subpath

    async def _serve(
        self, scope: Scope, receive: Receive, send: Send, directory: Path, subpath: str
    ) -> None:
        target = find_asset(directory, subpath)
        if target is None:
            # 不带扩展名 = 前端路由（`/admin/feedback`）→ 回落 `index.html`；
            # 带扩展名 = 真在找文件（`assets/gone.js`）→ 老实 404。
            # 后者要是也回落，前端会拿 HTML 当 JS 解析，报 `Unexpected token '<'`，
            # 比 404 难查得多（spec 规则 1 / 2）
            if _has_extension(subpath):
                await _not_found(scope, receive, send)
                return
            target = find_asset(directory, "index.html")
        if target is None:
            # 产物还没构建：404，不 500（spec 规则 5）
            await _not_found(scope, receive, send)
            return
        await FileResponse(target)(scope, receive, send)


def mount_frontends(app: FastAPI) -> None:
    """把两个前端挂上去。

    ⚠️ 必须**在所有 router 之后**调用：匹配顺序就是注册顺序，先注册的赢。
    `/` 那个是兜底，它会匹配一切——排在前面就会把 `/api/*` 全吞了。
    """
    app.mount(
        "/",
        SpaFiles(
            roots=((_ADMIN_PREFIX, lambda: ADMIN_DIST),),
            default=lambda: FRONTEND_DIST,
            reserved=_API_PREFIX,
        ),
        name="spa",
    )
