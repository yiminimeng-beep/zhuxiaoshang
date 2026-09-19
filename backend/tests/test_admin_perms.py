"""06-admin · `AC` 组：权限与入口。

对应 test_plan.md 的 `AC-01` ~ `AC-05`。

`AC-02` / `AC-03` **遍历全部 14 个端点**，不是抽查一个。spec 写的是
「所有端点均要求 `role=admin`」；只测一个的话，后来新加的端点漏挂
`_admin_auth` 不会被任何用例发现——而那个后果是**任意登录用户都能封禁他人**。
"""

import pytest

from tests.helpers import admin_token, bearer

pytestmark = pytest.mark.asyncio


#: 06 新增的 14 个端点。(方法, 路径)。带 `{id}` 的用常量 1——权限闸在
#: 查库之前，替身 id 一样能测出「非 admin 被拦」。
ADMIN_ENDPOINTS = (
    ("GET", "/api/admin/cost/summary"),
    ("GET", "/api/admin/cost/by-merchant"),
    ("GET", "/api/admin/cost/by-day"),
    ("GET", "/api/admin/cost/by-provider"),
    ("GET", "/api/admin/cost/budget-alerts"),
    ("GET", "/api/admin/cost/merchants/1/detail"),
    ("GET", "/api/admin/users"),
    ("GET", "/api/admin/users/1"),
    ("POST", "/api/admin/users/1/ban"),
    ("POST", "/api/admin/users/1/unban"),
    ("GET", "/api/admin/action-logs"),
    ("GET", "/api/admin/exceptions"),
    ("POST", "/api/admin/exceptions/content/1/resolve"),
    ("POST", "/api/admin/exceptions/ocr/1/resolve"),
)


async def _call(client, method: str, path: str, headers: dict):
    kwargs = {"headers": headers}
    if method == "POST":
        kwargs["json"] = {"reason": "违规内容发布", "action": "approve"}
    return await client.request(method, path, **kwargs)


async def test_ac01_unauthenticated_is_401(client):
    """未登录打 admin 端点 → `401`（不是 403，也不是 200）。

    `401` 与 `403` 的区别是契约的一部分：前者说「你没说你是谁」，
    后者说「我知道你是谁，但你没资格」。
    """
    r = await client.get("/api/admin/cost/summary")
    assert r.status_code == 401, f"未登录应 401：{r.status_code} {r.text}"


async def test_ac02_customer_is_403(client, customer):
    """客户打全部 14 个 admin 端点 → 一律 `403`。"""
    _, c_token, _ = customer
    bad = []
    for method, path in ADMIN_ENDPOINTS:
        r = await _call(client, method, path, bearer(c_token))
        if r.status_code != 403:
            bad.append(f"{method} {path} → {r.status_code}")
    assert not bad, f"客户访问 admin 端点应一律 403：{bad}"


async def test_ac03_merchant_is_403(client, merchant):
    """商户打全部 14 个 admin 端点 → 一律 `403`。"""
    _, m_token, _ = merchant
    bad = []
    for method, path in ADMIN_ENDPOINTS:
        r = await _call(client, method, path, bearer(m_token))
        if r.status_code != 403:
            bad.append(f"{method} {path} → {r.status_code}")
    assert not bad, f"商户访问 admin 端点应一律 403：{bad}"


async def test_ac04_banned_admin_is_403(client, seed_accounts, set_user_status):
    """已封禁的 admin 打 admin 端点 → `403`。

    06 的 `_admin_auth` 只管 `role == "admin"`，账号状态由 01 的 `deps` 拦。
    这条确认 06 没有绕过 01 的状态闸——否则「封了管理员」会变成一个空动作。
    """
    admin_id = next(uid for uid, row, role in seed_accounts if role == "admin")
    token = await admin_token(client)
    await set_user_status(admin_id, "banned")

    r = await client.get("/api/admin/cost/summary", headers=bearer(token))
    assert r.status_code == 403, (
        f"被封禁的 admin 应 403（01 的状态闸必须覆盖 admin）：{r.status_code} {r.text}"
    )


async def test_ac05_admin_smoke_all_groups(client, seed_accounts):
    """冒烟：admin 打 A/B/C 三组各一个端点 → `200`。

    没有这条，`AC-01`~`AC-04` 会在「路由全 404」的世界里**全部通过**：
    404 既不等于 401 也不等于 403 显然不成立……但若实现把权限写成
    「非 admin 一律 403、admin 一律 404」这种荒唐形状，前面四条照样绿。
    这条把「端点对 admin 真的可用」钉死。
    """
    token = await admin_token(client)
    for method, path in (
        ("GET", "/api/admin/cost/summary"),
        ("GET", "/api/admin/users"),
        ("GET", "/api/admin/exceptions"),
    ):
        r = await _call(client, method, path, bearer(token))
        assert r.status_code == 200, (
            f"admin 打 {method} {path} 应 200：{r.status_code} {r.text}"
        )
