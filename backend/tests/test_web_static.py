"""全局约定 13 · 前端构建产物的静态挂载（`WS-01` ~ `WS-12`）。

对着 `specs/00-overview/spec.md` 的「13 展开」六条契约写。

三条最容易静默坏掉的，是本文件的重点：
- `WS-03` / `WS-04`：不带扩展名的路径要回落 `index.html`。少了它，
  **开发期看不出来**（Vite 自带回落），只有生产刷新深链才 404。
- `WS-08`：`/api/*` 永不被吞。少了守卫，未注册的 `/api/*` 从 404 变成 200 HTML，
  而 06 那一批「期望 404」的用例**全靠 `/api` 的 404**。
- `WS-07`：带扩展名不回落。回落会让前端拿 HTML 当 JS 解析，
  报 `Unexpected token '<'`，比 404 难查十倍。

产物一律用 `tmp_path` 现造，**不依赖本机有没有构建过**——
否则这套用例会「因环境而绿」，那是最坏的一种绿。
"""

from pathlib import Path

import pytest

from app import web

# 两个前端各造一份**可区分**的 index.html：WS-04 靠它断言两边没串
ADMIN_MARK = "admin-build"
FRONTEND_MARK = "frontend-build"
SECRET = "TOP-SECRET-DO-NOT-SERVE"


def _build_dist(root: Path, mark: str) -> Path:
    """造一份最小可用的构建产物：index.html + 一个 assets/app.js。"""
    root.mkdir(parents=True, exist_ok=True)
    (root / "index.html").write_text(f"<!doctype html><title>{mark}</title>")
    (root / "assets").mkdir(exist_ok=True)
    (root / "assets" / "app.js").write_text("console.log('ok')\n")
    return root


@pytest.fixture
def dist(tmp_path, monkeypatch):
    """把两个产物目录指到 tmp。

    ⚠️ 必须 monkeypatch **模块属性**（`web.ADMIN_DIST`）。若 `main.py` 写成
    `from app.web import ADMIN_DIST` 把它绑成本地名，这个接缝就打不中——
    本项目已经在 07 / 04 踩过三次同样的坑。
    """
    admin = _build_dist(tmp_path / "admin", ADMIN_MARK)
    front = _build_dist(tmp_path / "front", FRONTEND_MARK)
    monkeypatch.setattr(web, "ADMIN_DIST", admin)
    monkeypatch.setattr(web, "FRONTEND_DIST", front)
    return {"admin": admin, "front": front}


@pytest.fixture
def no_dist(tmp_path, monkeypatch):
    """产物**还没构建**的那种状态：目录根本不存在。"""
    monkeypatch.setattr(web, "ADMIN_DIST", tmp_path / "never-built-admin")
    monkeypatch.setattr(web, "FRONTEND_DIST", tmp_path / "never-built-front")


# --------------------------------------------------------------------------- #
# A. 两条 spec 边界
# --------------------------------------------------------------------------- #
async def test_ws_01_admin_html_route_200(client, dist):
    """`GET /admin` → 200（06 spec 边界原文）。"""
    response = await client.get("/admin")

    assert response.status_code == 200
    assert ADMIN_MARK in response.text


async def test_ws_02_root_html_route_200(client, dist):
    """`GET /` → 200（06 spec 边界原文）。"""
    response = await client.get("/")

    assert response.status_code == 200
    assert FRONTEND_MARK in response.text


# --------------------------------------------------------------------------- #
# B. SPA 回落
# --------------------------------------------------------------------------- #
async def test_ws_03_admin_deep_link_falls_back_to_index(client, dist):
    """`/admin/feedback` 刷新不能 404——回落 index.html。"""
    response = await client.get("/admin/feedback")

    assert response.status_code == 200
    assert ADMIN_MARK in response.text


async def test_ws_04_frontend_deep_link_serves_frontend_build(client, dist):
    """`/merchant` → 200，且拿的是**主站**那份 index.html（两个前端不串）。"""
    response = await client.get("/merchant")

    assert response.status_code == 200
    assert FRONTEND_MARK in response.text
    assert ADMIN_MARK not in response.text


async def test_ws_05_admin_with_trailing_slash_200(client, dist):
    response = await client.get("/admin/")

    assert response.status_code == 200
    assert ADMIN_MARK in response.text


# --------------------------------------------------------------------------- #
# C. 静态文件本身
# --------------------------------------------------------------------------- #
async def test_ws_06_real_asset_served_with_js_content_type(client, dist):
    response = await client.get("/admin/assets/app.js")

    assert response.status_code == 200
    # 关键：不能是 text/html——那正是「回落错了」的表现
    assert "javascript" in response.headers["content-type"]
    assert "console.log" in response.text


async def test_ws_07_missing_asset_with_extension_is_404(client, dist):
    """有扩展名就**不回落**：`gone.js` 必须 404，不能回一段 HTML。

    回落的话浏览器报 `Unexpected token '<'`，比 404 难查得多。
    """
    response = await client.get("/admin/assets/gone.js")

    assert response.status_code == 404
    assert ADMIN_MARK not in response.text


# --------------------------------------------------------------------------- #
# D. 不许越界
# --------------------------------------------------------------------------- #
async def test_ws_08_api_is_never_swallowed(client, dist):
    """未注册的 `/api/*` 必须保持 404，不能伪装成 200 HTML。

    这一条一松，06 那批「期望 404」的用例会一起绿在错的地方。
    """
    response = await client.get("/api/definitely-not-a-route")

    assert response.status_code == 404
    assert ADMIN_MARK not in response.text
    assert FRONTEND_MARK not in response.text


async def test_ws_09_non_get_is_not_swallowed(client, dist):
    """挂载只处理 GET/HEAD——其余方法一律 404，不回 HTML。

    ⚠️ 探针**不能**用 `/api/*`：那条路径已经被 `_API_PREFIX` 守卫挡掉了，
    方法守卫拆掉它也仍回 404——用例会对着写坏的实现继续绿。
    要打的是**不受守卫保护**的路径（`POST /merchant` 这种前端深链）：
    少了方法守卫，它会从 404 变成 200 HTML。
    """
    response = await client.post("/merchant")

    assert response.status_code == 404
    assert FRONTEND_MARK not in response.text


async def test_ws_10_path_traversal_cannot_escape_dist(client, tmp_path, monkeypatch):
    """`/admin/%2e%2e/secret.txt` 拿不到产物目录外的文件。

    用百分号编码而不是 `../`：多数客户端会在发出前把 `../` 规范化掉，
    那样测的是客户端、不是我们的守卫。
    """
    (tmp_path / "secret.txt").write_text(SECRET)
    monkeypatch.setattr(web, "ADMIN_DIST", _build_dist(tmp_path / "dist", ADMIN_MARK))

    response = await client.get("/admin/%2e%2e/secret.txt")

    assert response.status_code == 404
    assert SECRET not in response.text


# --------------------------------------------------------------------------- #
# E. 容忍与不打扰
# --------------------------------------------------------------------------- #
async def test_ws_11_missing_dist_is_404_not_500(client, no_dist):
    """产物还没构建时回 404，**不许 500**，也不许伪造 200。

    `StaticFiles(directory=...)` 在目录缺失时**导入即抛**，会连累整个后端起不来——
    所以这里必须是自己兜底，不是把异常交给 Starlette。
    """
    for path in ("/", "/admin", "/admin/feedback"):
        response = await client.get(path)
        assert response.status_code == 404, path

    # 而且它没连累别的路由
    assert (await client.get("/healthz")).status_code == 200


async def test_ws_12_existing_routes_still_work(client, dist):
    """挂载排在最后，不遮挡已有路由。"""
    assert (await client.get("/healthz")).status_code == 200
    assert (await client.get("/openapi.json")).status_code == 200
