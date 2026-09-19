"""01-auth · 注册端点正常路径 + 注册与标识符边界。

对应 test_plan.md：E-01 / E-02 / R-01 ~ R-28
"""

import pytest

from tests.helpers import (
    bearer,
    login,
    register_customer,
    register_merchant,
)

pytestmark = pytest.mark.asyncio


# --------------------------------------------------------------------------- #
# A. 端点正常路径
# --------------------------------------------------------------------------- #
async def test_e01_register_success(client):
    r = await register_customer(client, account="cust0001", password="pass1234")
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["access_token"]
    assert body["refresh_token"]
    assert body["user"]["account"] == "cust0001"
    assert body["user"]["role"] == "customer"


async def test_e02_register_merchant_creates_profile(client):
    r = await register_merchant(
        client, account="shop0001", shop_name="老王奶茶", category="餐饮"
    )
    assert r.status_code == 201, r.text
    user = r.json()["user"]
    assert user["role"] == "merchant"
    assert user["merchant_profile"]["shop_name"] == "老王奶茶"


# --------------------------------------------------------------------------- #
# B. 注册与标识符边界
# --------------------------------------------------------------------------- #
async def test_r01_need_account_or_email(client):
    r = await client.post(
        "/api/auth/register", json={"password": "pass1234", "role": "customer"}
    )
    assert r.status_code == 422, r.text


async def test_r02_account_only(client, db):
    r = await register_customer(client, account="cust0001")
    assert r.status_code == 201, r.text
    assert r.json()["user"]["email"] is None
    assert await db.fetchval('SELECT email FROM "user" WHERE account = $1', "cust0001") is None


async def test_r03_email_only(client, db):
    r = await client.post(
        "/api/auth/register",
        json={
            "email": "only@example.com",
            "password": "pass1234",
            "role": "customer",
        },
    )
    assert r.status_code == 201, r.text
    assert r.json()["user"]["account"] is None
    assert (
        await db.fetchval('SELECT account FROM "user" WHERE email = $1', "only@example.com")
        is None
    )


async def test_r04_both_identifiers(client, db):
    r = await register_customer(client, account="cust0001", email="both@example.com")
    assert r.status_code == 201, r.text
    row = await db.fetchrow(
        'SELECT account, email FROM "user" WHERE account = $1', "cust0001"
    )
    assert row["account"] == "cust0001"
    assert row["email"] == "both@example.com"


@pytest.mark.parametrize(
    "bad_account,label",
    [
        ("abc12", "5 位"),
        ("a" * 21, "21 位"),
        ("abc-def", "含 -"),
        ("账号abc123", "含中文"),
        ("abc 123", "含空格"),
    ],
)
async def test_r05_r09_account_bad_format(client, bad_account, label):
    r = await register_customer(client, account=bad_account)
    assert r.status_code == 422, f"{label} 应被拒：{r.status_code} {r.text}"


async def test_r10_account_pure_digits_allowed(client):
    """种子账号 000000 依赖这条。"""
    r = await register_customer(client, account="000000")
    assert r.status_code == 201, r.text


async def test_r11_account_case_insensitive_login(client, db):
    r = await register_customer(client, account="Abc123")
    assert r.status_code == 201, r.text
    r2 = await login(client, "abc123", "pass1234")
    assert r2.status_code == 200, r2.text


async def test_r12_account_case_insensitive_unique(client, db):
    r1 = await register_customer(client, account="Abc123")
    assert r1.status_code == 201, r1.text
    r2 = await register_customer(client, account="abc123")
    assert r2.status_code == 409, r2.text


@pytest.mark.parametrize("bad_email", ["notanemail", "a@", "@b.com", "a b@c.com"])
async def test_r13_r14_email_invalid(client, bad_email):
    r = await client.post(
        "/api/auth/register",
        json={"email": bad_email, "password": "pass1234", "role": "customer"},
    )
    assert r.status_code == 422, f"{bad_email} 应被拒：{r.status_code} {r.text}"


async def test_r15_email_case_insensitive(client):
    r = await client.post(
        "/api/auth/register",
        json={"email": "Mixed@Example.com", "password": "pass1234", "role": "customer"},
    )
    assert r.status_code == 201, r.text
    r2 = await login(client, "mixed@example.com", "pass1234")
    assert r2.status_code == 200, r2.text


async def test_r16_email_254_ok(client):
    # 254 = local(64) + "@" + domain
    local = "a" * 64
    domain = ("b" * 185) + ".com"  # 189 -> 64+1+189 = 254
    email = f"{local}@{domain}"
    assert len(email) == 254, len(email)
    r = await client.post(
        "/api/auth/register",
        json={"email": email, "password": "pass1234", "role": "customer"},
    )
    assert r.status_code == 201, r.text


async def test_r17_email_255_rejected(client):
    local = "a" * 64
    domain = ("b" * 186) + ".com"
    email = f"{local}@{domain}"
    assert len(email) == 255, len(email)
    r = await client.post(
        "/api/auth/register",
        json={"email": email, "password": "pass1234", "role": "customer"},
    )
    assert r.status_code == 422, r.text


async def test_r18_password_too_short(client):
    r = await register_customer(client, password="a" * 5)
    assert r.status_code == 422, r.text


async def test_r19_password_6_ok(client):
    r = await register_customer(client, password="a" * 6)
    assert r.status_code == 201, r.text


async def test_r20_password_64_ok(client):
    r = await register_customer(client, password="a" * 64)
    assert r.status_code == 201, r.text


async def test_r21_password_65_rejected(client):
    r = await register_customer(client, password="a" * 65)
    assert r.status_code == 422, r.text


async def test_r22_password_all_spaces(client):
    r = await register_customer(client, password=" " * 8)
    assert r.status_code == 422, r.text


async def test_r23_phone_conflict(client):
    r1 = await register_customer(client, account="cust0001", phone="13800000001")
    assert r1.status_code == 201, r1.text
    r2 = await register_customer(client, account="cust0002", phone="13800000001")
    assert r2.status_code == 409, r2.text


async def test_r24_phone_optional(client, db):
    r = await register_customer(client, account="cust0001")
    assert r.status_code == 201, r.text
    assert await db.fetchval(
        'SELECT phone FROM "user" WHERE account = $1', "cust0001"
    ) is None


@pytest.mark.parametrize("bad_phone", ["1380000000", "138000000000", "abcdefghijk", "138 0000 0001"])
async def test_r25_phone_invalid_format(client, bad_phone):
    r = await register_customer(client, phone=bad_phone)
    assert r.status_code == 422, f"{bad_phone} 应被拒：{r.status_code} {r.text}"


async def test_r26_admin_role_rejected(client, db):
    r = await client.post(
        "/api/auth/register",
        json={"account": "hacker", "password": "pass1234", "role": "admin"},
    )
    assert r.status_code == 400, r.text
    assert await db.fetchval("SELECT count(*) FROM \"user\"") == 0


async def test_r27_merchant_missing_shop_name(client):
    r = await client.post(
        "/api/auth/register",
        json={
            "account": "shop0001",
            "password": "pass1234",
            "role": "merchant",
            "nickname": "shop0001",
            "category": "餐饮",
        },
    )
    assert r.status_code == 422, r.text


async def test_r28_password_never_leaks(client, db):
    plain = "pass1234"
    r = await register_customer(client, account="cust0001", password=plain)
    assert r.status_code == 201, r.text

    stored = await db.fetchval(
        'SELECT password_hash FROM "user" WHERE account = $1', "cust0001"
    )
    assert stored != plain
    assert plain not in stored
    assert "password" not in r.text
    assert plain not in r.text
