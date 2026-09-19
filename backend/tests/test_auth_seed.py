"""01-auth · 种子数据安全。

对应 test_plan.md：SD-01 / SD-02 / SD-03

⚠️ 注意：`app.seed` 尚未实现时子进程会以非 0 退出（ModuleNotFoundError），
这会让「production 拒绝种入」**假绿**。故 SD-01 额外断言 stderr 里
不能是"模块不存在"，必须是明确的拒绝信息。
"""

import pytest

from tests.helpers import (
    SEED_ADMIN,
    SEED_CUSTOMER,
    SEED_MERCHANT,
    login,
    run_seed,
)

pytestmark = pytest.mark.asyncio

_MISSING_MODULE_MARKERS = ("No module named", "ModuleNotFoundError", "ImportError")


async def test_sd01_seed_refuses_in_production(db):
    result = run_seed("production")

    assert result.returncode != 0, (
        "APP_ENV=production 时种子脚本必须拒绝执行并退出，"
        f"实际退出码 {result.returncode}\nstdout={result.stdout}\nstderr={result.stderr}"
    )

    combined = f"{result.stdout}\n{result.stderr}"
    assert not any(m in combined for m in _MISSING_MODULE_MARKERS), (
        "失败原因必须是「生产环境拒绝种入默认密码账号」，"
        f"而不是脚本不存在或依赖缺失：\n{combined}"
    )
    assert any(
        kw in combined.lower() for kw in ("production", "生产", "refuse", "拒绝")
    ), f"必须打出明确的拒绝日志，实际输出：\n{combined}"

    assert await db.fetchval('SELECT count(*) FROM "user"') == 0, (
        "production 下不得写入任何账号"
    )


async def test_sd02_seed_creates_three_accounts(db):
    result = run_seed("development")
    assert result.returncode == 0, (
        f"development 下种子脚本应成功\nstdout={result.stdout}\nstderr={result.stderr}"
    )

    rows = await db.fetch(
        'SELECT account, password_hash, role FROM "user" ORDER BY account'
    )
    accounts = {r["account"]: r for r in rows}
    assert set(accounts) == {"000000", "000001", "999999"}, accounts.keys()
    assert accounts["000000"]["role"] == "merchant"
    assert accounts["000001"]["role"] == "customer"
    assert accounts["999999"]["role"] == "admin"

    for r in rows:
        assert r["password_hash"] not in ("000000", "000001", "999999")
        assert not r["password_hash"].startswith(
            ("000000", "000001", "999999")
        ), "密码哈希不得等于明文"


async def test_sd03_seed_accounts_can_login(client):
    result = run_seed("development")
    assert result.returncode == 0, result.stderr

    for spec_row, expected_role in (
        (SEED_MERCHANT, "merchant"),
        (SEED_CUSTOMER, "customer"),
        (SEED_ADMIN, "admin"),
    ):
        r = await login(
            client,
            spec_row["account"],
            spec_row["password"],
            fp=f"fp-{spec_row['account']}",
        )
        assert r.status_code == 200, (
            f"{spec_row['account']} 应能登录：{r.status_code} {r.text}"
        )
        assert r.json()["user"]["role"] == expected_role
