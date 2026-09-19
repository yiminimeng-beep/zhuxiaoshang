"""测试公共工具。fixture 在 conftest.py，这里只放被多处复用的纯函数与常量。"""

import itertools
import json
import re
import os
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import asyncpg

# --------------------------------------------------------------------------- #
# 环境常量（须与 conftest.py 顶部写入的环境变量一致）
# --------------------------------------------------------------------------- #
TEST_DSN = "postgresql://zxs:zxs_dev_pw@localhost:5433/zhuxiaoshang_test"
ADMIN_DSN = "postgresql://zxs:zxs_dev_pw@localhost:5433/postgres"
TEST_DB_NAME = "zhuxiaoshang_test"

BACKEND_DIR = Path(__file__).resolve().parent.parent

SEED_MERCHANT = {"account": "000000", "password": "000000"}
SEED_CUSTOMER = {"account": "000001", "password": "000001"}
SEED_ADMIN = {"account": "999999", "password": "999999"}


# --------------------------------------------------------------------------- #
# HTTP 辅助
# --------------------------------------------------------------------------- #
def device_headers(fp: str = "fp-default", name: str = "pytest") -> dict:
    return {"device_fingerprint": fp, "device_name": name}


def bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _path_segments(path: str) -> list[str]:
    return [s for s in path.strip("/").split("/") if s]


def assert_route_registered(method: str, path: str) -> None:
    """确认该路由已注册，否则判失败。

    只有 **404** 这一种期望值会与「路由根本没实现」撞车——FastAPI 对不存在的
    路由也返回 404。不加以区分，那些用例就会**因为没实现而变绿**（假绿）。
    其余期望值（401/403/409/422…）缺失路由时会返回 404，天然不会误判。

    走 OpenAPI schema 而不是遍历 `app.routes`：FastAPI 0.141 + Starlette 1.6 起，
    `include_router` 会塞一个惰性的 `_IncludedRouter` 包装对象进去，它既没有
    `.path` 也没有 `.routes`，遍历不到真正的路由。
    """
    from app.main import app

    wanted = _path_segments(path)
    method = method.lower()
    for template, operations in app.openapi().get("paths", {}).items():
        if method not in operations:
            continue
        segments = _path_segments(template)
        if len(segments) != len(wanted):
            continue
        if all(
            seg.startswith("{") or seg == actual
            for seg, actual in zip(segments, wanted)
        ):
            return
    raise AssertionError(
        f"路由未实现：{method.upper()} {path} —— 该用例期望的 404 不得因路由缺失而变绿"
    )


_FRAMEWORK_404 = '{"detail":"Not Found"}'


def _reject_missing_route(r) -> None:
    """注册端点没有「合法 404」，所以拿到 404 只可能是路由没实现。

    不拦的话，调用方会在 `body["access_token"]` 抛 `KeyError`，
    读的人分不清是「端点没写」还是「测试自己写错了」。
    """
    if r.status_code == 404 and r.text.strip() == _FRAMEWORK_404:
        raise AssertionError(
            f"端点未实现（路由缺失，返回框架默认 404）：{r.request.method} "
            f"{r.request.url.path}"
        )


async def register_customer(client, account="cust0001", password="pass1234", **extra):
    r = await client.post(
        "/api/auth/register",
        json={
            "account": account,
            "password": password,
            "role": "customer",
            "nickname": account,
            **extra,
        },
    )
    _reject_missing_route(r)
    return r


async def register_merchant(
    client, account="shop0001", password="pass1234", shop_name="测试店铺", **extra
):
    r = await client.post(
        "/api/auth/register",
        json={
            "account": account,
            "password": password,
            "role": "merchant",
            "nickname": account,
            "shop_name": shop_name,
            "category": "餐饮",
            **extra,
        },
    )
    _reject_missing_route(r)
    return r


async def login(client, identifier: str, password: str, fp: str = "fp-default"):
    return await client.post(
        "/api/auth/login",
        json={
            "identifier": identifier,
            "password": password,
            "device_fingerprint": fp,
            "device_name": "pytest",
        },
    )


async def login_ok(client, identifier: str, password: str, fp: str = "fp-default") -> dict:
    """登录并断言成功，返回整个响应体。"""
    r = await login(client, identifier, password, fp)
    assert r.status_code == 200, f"登录失败：{r.status_code} {r.text}"
    return r.json()


# --------------------------------------------------------------------------- #
# 令牌 / 种子脚本
# --------------------------------------------------------------------------- #
def forge_access_token(user_id: int, expires_delta_seconds: int = 3600) -> str:
    """直造 access_token，用于「过期令牌」这类没法走接口造出来的场景。"""
    from datetime import timedelta

    try:
        from app.core.security import create_access_token
    except ImportError as exc:  # pragma: no cover - 红阶段必然走到
        raise AssertionError(
            "app.core.security.create_access_token 尚未实现"
            "（spec 01-auth 的实现阶段补齐）"
        ) from exc

    return create_access_token(
        user_id=user_id, expires_delta=timedelta(seconds=expires_delta_seconds)
    )


def run_seed(app_env: str) -> subprocess.CompletedProcess:
    """跑种子脚本。契约：`python -m app.seed`，读 `APP_ENV` 决定是否拒绝。

    两端都**钉死 UTF-8**：脚本的拒绝日志是中文，而 `text=True` 默认按 Windows
    本地编码（GBK）解码。若调用方设了 `PYTHONIOENCODING=utf-8`（CI、UTF-8 终端都
    会），子进程写 UTF-8、父进程按 GBK 读，读取线程直接抛 `UnicodeDecodeError`，
    `stderr` 变成 `None`——用例会以一句莫名其妙的断言失败收场。
    """
    return subprocess.run(
        [sys.executable, "-m", "app.seed"],
        cwd=str(BACKEND_DIR),
        env={**os.environ, "APP_ENV": app_env, "PYTHONIOENCODING": "utf-8"},
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )


# --------------------------------------------------------------------------- #
# 02-task 工厂
# --------------------------------------------------------------------------- #
def make_tiers(n: int = 3, step: int = 100, reward: dict | None = None) -> list[dict]:
    """造 n 档**合法**阶梯：首档 min=0、中间无缝无重叠、末档 max=null。"""
    reward = {"points": 50} if reward is None else reward
    tiers: list[dict] = []
    start = 0
    for i in range(n):
        is_last = i == n - 1
        end = None if is_last else start + step - 1
        tiers.append({"min": start, "max": end, "reward": dict(reward)})
        if is_last:
            break
        start = end + 1
    return tiers


def task_payload(**over) -> dict:
    """一份合法的建任务请求体。`start_at` 取「此刻」，既不违反「不得早于创建时间」，
    又能立刻进入领取时间窗（spec 给了 1 秒容差，故毫秒级抖动无害）。"""
    now = datetime.now(timezone.utc)
    payload = {
        "title": "测试任务标题",
        "description": "这是一段用于测试的任务描述内容",
        "category": "餐饮",
        "start_at": now.isoformat(),
        "end_at": (now + timedelta(days=7)).isoformat(),
    }
    payload.update(over)
    return payload


async def create_task(client, token: str, **over):
    """建草稿，返回响应（不自动断言，调用方自行看状态码）。"""
    r = await client.post(
        "/api/merchant/tasks",
        headers=bearer(token),
        json=task_payload(**over),
    )
    _reject_missing_route(r)
    return r


async def create_task_ok(client, token: str, **over) -> dict:
    r = await create_task(client, token, **over)
    assert r.status_code == 201, f"建草稿失败：{r.status_code} {r.text}"
    return r.json()["task"]


async def put_reward_rule(client, token: str, task_id: int, tiers=None, **over):
    body = {"metric": "engagement", "tiers": make_tiers() if tiers is None else tiers}
    body.update(over)
    return await client.put(
        f"/api/merchant/reward-rules/{task_id}", headers=bearer(token), json=body
    )


async def publish_task(client, token: str, task_id: int, **tiers_over) -> dict:
    """配好合法规则并发布，返回 publish 的响应。"""
    rr = await put_reward_rule(client, token, task_id, **tiers_over)
    assert rr.status_code == 200, f"配奖励规则失败：{rr.status_code} {rr.text}"
    return await client.post(
        f"/api/merchant/tasks/{task_id}/publish", headers=bearer(token)
    )


async def published_task(client, token: str, **over) -> dict:
    """建 + 配规则 + 发布，返回已发布任务。前置失败会在这里直接炸，不伪装成被测断言。"""
    task = await create_task_ok(client, token, **over)
    r = await publish_task(client, token, task["id"])
    assert r.status_code == 200, f"发布失败：{r.status_code} {r.text}"
    return task


async def claim(client, token: str, task_id: int):
    return await client.post(f"/api/tasks/{task_id}/claim", headers=bearer(token))


async def seed_customers(db, count: int, prefix: str = "bulk") -> list[int]:
    """直插 `count` 个客户，返回 id 列表。

    并发用例要 100 个身份，走注册接口要跑 100 次 argon2（每次 ~100ms，用例会拖到十几秒）。
    这里哈希只算一次然后复用——这些账号**只用来持有身份**，从不走登录校验。
    """
    from argon2 import PasswordHasher

    digest = PasswordHasher().hash("bulkpass123")
    ids: list[int] = []
    for i in range(count):
        uid = await db.fetchval(
            """
            INSERT INTO "user" (account, password_hash, role, nickname, status)
            VALUES ($1, $2, 'customer', $3, 'active')
            RETURNING id
            """,
            f"{prefix}{i:05d}",
            digest,
            f"{prefix}{i}",
        )
        ids.append(uid)
    return ids


def token_for(user_id: int, device_id: int | None = None) -> str:
    """直造 access_token。并发用例靠它省掉登录往返。"""
    from app.core.security import create_access_token

    return create_access_token(user_id, device_id=device_id)


async def insert_task(db, merchant_id: int, **over) -> int:
    """直插一条任务并返回 id。

    列表 / 搜索 / 排序类用例要造十几二十条数据，走接口每条都得
    建草稿 + 配规则 + 发布三趟，纯属浪费。这里直接落库，
    并**按 spec 的列名**写，所以它同时也在钉住表结构。

    报销三列必须显式写：漏掉 `reimburse_per_user_limit` / `reimburse_pool`
    会让任务静默落成「纯用户自费」（= 0），于是断言池子与上限的用例
    拿到的是 0 而不是自己传的数——与 07 `quota_account_for` 吞限额列是同一个坑。
    """
    now = datetime.now(timezone.utc)
    row = {
        "title": "批量任务标题",
        "description": "批量任务描述内容",
        "category": "餐饮",
        "start_at": now - timedelta(hours=1),
        "end_at": now + timedelta(days=7),
        "quota": None,
        "status": "published",
        "pay_mode": "merchant_pay",
        "tags": None,
        "reimburse_pool": None,
        "reimburse_pool_used": 0,
        "reimburse_pool_reserved": 0,
        "reimburse_per_user_limit": None,
        "created_at": now,
        "deleted_at": None,
    }
    row.update(over)

    return await db.fetchval(
        """
        INSERT INTO task (
            merchant_id, title, description, category, start_at, end_at,
            quota, claimed_count, pay_mode, status, tags, created_at,
            updated_at, deleted_at, reimburse_pool, reimburse_pool_used,
            reimburse_pool_reserved, reimburse_per_user_limit
        )
        VALUES (
            $1, $2, $3, $4, $5, $6, $7, 0, $8, $9, $10::jsonb, $11, $11, $12,
            $13, $14, $15, $16
        )
        RETURNING id
        """,
        merchant_id,
        row["title"],
        row["description"],
        row["category"],
        row["start_at"],
        row["end_at"],
        row["quota"],
        row["pay_mode"],
        row["status"],
        json.dumps(row["tags"]) if row["tags"] is not None else None,
        row["created_at"],
        row["deleted_at"],
        row["reimburse_pool"],
        row["reimburse_pool_used"],
        row["reimburse_pool_reserved"],
        row["reimburse_per_user_limit"],
    )


# --------------------------------------------------------------------------- #
# 07-token 工厂
# --------------------------------------------------------------------------- #
SECRET_KEY_SAMPLE = "sk-live-abcdefghijklmnop-a1b2"
# 断言「明文不外泄」时只搜中段：整串去搜会被各式掩码挡住，搜了个寂寞。
SECRET_KEY_PROBE = "abcdefghijklmnop"

_REF_SEQ = itertools.count(1000)


async def quota_account_for(db, user_id: int, **over) -> None:
    """直插 / 覆盖一条 `quota_account`。

    默认只写 `balance` / `reserved` / `debt` / `status`，其余（`total_*`）留给
    DDL 的 `DEFAULT 0`——顺带钉住「这些列真有默认值」，否则 spec 里说的
    「必填，默认 0」就是空话。

    四个 `*_limit` 列**必须能被显式写入**：限额用例（`LM-01`~`LM-08`）全靠
    `daily_limit=1000` 这类传参。曾漏写这四列，于是 `daily_limit` 静默落成
    NULL（= 平台默认），限额用例会因「没设上限」而测了个寂寞。
    """
    row = {"balance": 0, "reserved": 0, "debt": 0, "status": "active"}
    row.update(over)
    limits = (
        "daily_limit",
        "per_user_daily_limit",
        "per_user_task_limit",
        "user_daily_limit",
    )
    # 没显式传的 limit 一律写 NULL：用例之间靠 TRUNCATE 隔离，但同一用例里
    # 二次调用要能「把限额清空」，不能因为 ON CONFLICT 保留了上一轮的值。
    limit_values = [row.get(k) for k in limits]
    await db.execute(
        f"""
        INSERT INTO quota_account (
            user_id, balance, reserved, debt, status, updated_at,
            {", ".join(limits)}
        )
        VALUES ($1, $2, $3, $4, $5, now(), $6, $7, $8, $9)
        ON CONFLICT (user_id) DO UPDATE SET
            balance = EXCLUDED.balance,
            reserved = EXCLUDED.reserved,
            debt = EXCLUDED.debt,
            status = EXCLUDED.status,
            daily_limit = EXCLUDED.daily_limit,
            per_user_daily_limit = EXCLUDED.per_user_daily_limit,
            per_user_task_limit = EXCLUDED.per_user_task_limit,
            user_daily_limit = EXCLUDED.user_daily_limit,
            updated_at = EXCLUDED.updated_at
        """,
        user_id,
        row["balance"],
        row["reserved"],
        row["debt"],
        row["status"],
        *limit_values,
    )


async def grant(db, user_id: int, balance: int, **over) -> None:
    """把余额设成 `balance` 并补一条 `recharge` 流水。

    没有流水的话，「合计 == 余额」的对账用例（`US-05`）会无数据可对，
    等于把 `quota_ledger` 的接缝空着。
    """
    await quota_account_for(db, user_id, balance=balance, **over)
    await db.execute(
        """
        INSERT INTO quota_ledger
            (user_id, change, balance_after, source, ref_type, ref_id, created_at)
        VALUES ($1, $2, $2, 'recharge', 'seed', $3, now())
        """,
        user_id,
        balance,
        next(_REF_SEQ),
    )


async def insert_ledger(
    db,
    user_id: int,
    change: int,
    balance_after: int,
    *,
    source: str = "consume",
    ref_type: str = "seed",
    ref_id: int | None = None,
    job_id: int | None = None,
    task_id: int | None = None,
    spender_id: int | None = None,
    billing_source: str | None = None,
    provider: str | None = None,
    created_at=None,
) -> int:
    """直插一条流水。`ref_id` 默认取自增序号，避开 `(source, ref_type, ref_id)` 唯一约束。"""
    return await db.fetchval(
        """
        INSERT INTO quota_ledger (
            user_id, change, balance_after, source, ref_type, ref_id,
            job_id, task_id, spender_id, billing_source, provider, created_at
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
        RETURNING id
        """,
        user_id,
        change,
        balance_after,
        source,
        ref_type,
        next(_REF_SEQ) if ref_id is None else ref_id,
        job_id,
        task_id,
        spender_id,
        billing_source,
        provider,
        created_at or datetime.now(timezone.utc),
    )


async def ledger_rows(db, user_id: int) -> list:
    return await db.fetch(
        "SELECT * FROM quota_ledger WHERE user_id = $1 ORDER BY id", user_id
    )


async def insert_price(
    db,
    provider: str = "deepseek",
    model: str = "deepseek-chat",
    op: str = "chat",
    **over,
) -> int:
    row = {
        "unit": "token",
        "cost_price_per_unit": 0.000002,
        "price_per_unit": 0.000004,
        "max_price_per_call": 120,
        "markup_rate": 2.00,
        "supports_byok": True,
        "provider_visible": True,
        "effective_from": datetime.now(timezone.utc) - timedelta(days=1),
    }
    row.update(over)
    return await db.fetchval(
        """
        INSERT INTO model_price (
            provider, model, op, unit, cost_price_per_unit, price_per_unit,
            max_price_per_call, markup_rate, supports_byok, provider_visible,
            effective_from
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
        RETURNING id
        """,
        provider,
        model,
        op,
        row["unit"],
        row["cost_price_per_unit"],
        row["price_per_unit"],
        row["max_price_per_call"],
        row["markup_rate"],
        row["supports_byok"],
        row["provider_visible"],
        row["effective_from"],
    )


async def admin_token(client) -> str:
    """用种子管理员（`999999`）登录。

    调用方**必须同时声明 `seed_accounts` fixture**——建行的是它，不是这里。
    注册接口不允许 `role=admin`，所以管理员只能靠种子数据出现。
    """
    body = await login_ok(
        client, SEED_ADMIN["account"], SEED_ADMIN["password"], fp="fp-admin"
    )
    return body["access_token"]


async def post_key(
    client, token: str, provider: str = "deepseek", api_key: str = SECRET_KEY_SAMPLE, **over
):
    body = {"provider": provider, "api_key": api_key}
    body.update(over)
    return await client.post("/api/me/model-keys", headers=bearer(token), json=body)


def patch_verify(monkeypatch, ok: bool = True):
    """把「调外部 provider 校验 Key」换成固定结果。

    打的是**接缝名**（见 test_plan.md「测试缝」）。名字被改掉时这里会抛
    `AttributeError`——这是故意的：接缝是契约，改名必须同步改测试。
    """

    async def _fake(provider: str, api_key: str) -> bool:
        return ok

    monkeypatch.setattr("app.services.model_key.verify_provider_key", _fake)


async def insert_user_key(
    db, user_id: int, provider: str = "deepseek", api_key: str = SECRET_KEY_SAMPLE
) -> None:
    """直接落一条 active BYOK，不走 HTTP 校验。流水线用例要能解开它。"""
    from app.services.model_key import CURRENT_KEY_VERSION, encrypt_key, mask_key

    await db.execute(
        """
        INSERT INTO user_model_key (
            user_id, provider, key_cipher, key_masked, key_version,
            status, fail_count, created_at, updated_at
        )
        VALUES ($1, $2, $3, $4, $5, 'active', 0, now(), now())
        """,
        user_id,
        provider,
        encrypt_key(api_key),
        mask_key(api_key),
        CURRENT_KEY_VERSION,
    )


# --------------------------------------------------------------------------- #
# 03-studio 工厂
# --------------------------------------------------------------------------- #
OK_MIME = "image/jpeg"
MAX_ASSET_BYTES = 20971520  # 20MB 整


def assets_of(n: int = 1, **over) -> list[dict]:
    """造 n 个合法素材。"""
    out = []
    for i in range(n):
        row = {
            "url": f"minio://bucket/asset-{i}.jpg",
            "mime": OK_MIME,
            "size_bytes": 1024,
        }
        row.update(over)
        out.append(row)
    return out


async def claimed_task(client, merchant_token: str, customer_token: str, **over):
    """建 + 发布任务并由客户领取，返回 `(task, claim_id)`。"""
    task = await published_task(client, merchant_token, **over)
    r = await claim(client, customer_token, task["id"])
    assert r.status_code == 201, f"领取失败：{r.status_code} {r.text}"
    return task, r.json()["claim"]["id"]


async def create_job(
    client,
    token: str,
    task_id: int,
    claim_id: int | None = None,
    *,
    kind: str = "copy",
    assets: list[dict] | None = None,
    **over,
):
    """建 job（不自动断言）。

    `claim_id` 是**可选**的：spec 的请求体列了 `task_id`，而边界里又写「用别人的
    `claim_id` 建 job → 403」——两句都成立的前提是该字段可传可不传，服务端
    最终仍以「本人对该任务的领取」为准。
    """
    body = {
        "task_id": task_id,
        "kind": kind,
        "assets": assets_of(1) if assets is None else assets,
    }
    if claim_id is not None:
        body["claim_id"] = claim_id
    body.update(over)
    return await client.post("/api/jobs", headers=bearer(token), json=body)


async def create_job_ok(
    client, token: str, task_id: int, claim_id: int | None = None, **over
) -> dict:
    r = await create_job(client, token, task_id, claim_id, **over)
    assert r.status_code == 201, f"建 job 失败：{r.status_code} {r.text}"
    return r.json()


async def insert_job(db, task_id: int, claim_id: int, user_id: int, **over) -> int:
    """直插 `content_job`。状态机用例靠它把 job 直接摆到目标状态。"""
    now = datetime.now(timezone.utc)
    row = {
        "kind": "copy",
        "status": "created",
        "provider": "deepseek",
        "billing_source": "platform",
        "fail_reason": None,
        "retry_count": 0,
        "created_at": now,
        "deleted_at": None,
    }
    row.update(over)
    return await db.fetchval(
        """
        INSERT INTO content_job (
            task_id, claim_id, user_id, kind, status, provider, billing_source,
            fail_reason, retry_count, created_at, updated_at, deleted_at
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $10, $11)
        RETURNING id
        """,
        task_id,
        claim_id,
        user_id,
        row["kind"],
        row["status"],
        row["provider"],
        row["billing_source"],
        row["fail_reason"],
        row["retry_count"],
        row["created_at"],
        row["deleted_at"],
    )


async def insert_asset(db, job_id: int, sort_order: int = 0, **over) -> int:
    row = {"url": f"minio://b/a{sort_order}.jpg", "mime": OK_MIME, "size_bytes": 1024}
    row.update(over)
    return await db.fetchval(
        """
        INSERT INTO job_input_asset (job_id, url, mime, size_bytes, sort_order)
        VALUES ($1, $2, $3, $4, $5) RETURNING id
        """,
        job_id,
        row["url"],
        row["mime"],
        row["size_bytes"],
        sort_order,
    )


async def insert_reservation(
    db, job_id: int, payer_id: int, spender_id: int, task_id: int, **over
) -> int:
    """直插一张预扣单。结算类用例需要它先存在，否则结算无处落账。"""
    row = {
        "billing_source": "platform",
        "reserved": 120,
        "actual": None,
        # 报销基数 = `model_price.cost_price_per_unit × units`，**只有这一列**
        # 能算出该报多少（BYOK 下 `actual` 恒为 0）。漏写它，所有报销基数
        # 都会算成 0——与 `quota_account_for` 吞限额列是同一个坑。
        "units": 0,
        "reimburse_reserved": 0,
        "status": "reserved",
        "created_at": datetime.now(timezone.utc),
        "settled_at": None,
    }
    row.update(over)
    return await db.fetchval(
        """
        INSERT INTO quota_reservation (
            job_id, payer_id, spender_id, task_id, billing_source, reserved,
            actual, units, reimburse_reserved, status, created_at, settled_at
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
        RETURNING id
        """,
        job_id,
        payer_id,
        spender_id,
        task_id,
        row["billing_source"],
        row["reserved"],
        row["actual"],
        row["units"],
        row["reimburse_reserved"],
        row["status"],
        row["created_at"],
        row["settled_at"],
    )


async def insert_message(db, job_id: int, role: str = "user", content: str = "你好") -> int:
    model = None if role == "user" else "deepseek-chat"
    return await db.fetchval(
        """
        INSERT INTO chat_message (job_id, role, content, model, created_at)
        VALUES ($1, $2, $3, $4, now()) RETURNING id
        """,
        job_id,
        role,
        content,
        model,
    )


async def insert_output(db, job_id: int, **over) -> int:
    row = {
        "type": "copy",
        "content": "一杯好喝的奶茶",
        "url": None,
        "provider": "deepseek",
        "billing_source": "platform",
        "cost_cents": 12,
        "judge_score": 90,
        "judge_detail": None,
        "attempt": 1,
        "is_active": True,
        "created_at": datetime.now(timezone.utc),
    }
    row.update(over)
    return await db.fetchval(
        """
        INSERT INTO gen_output (
            job_id, type, content, url, provider, billing_source, cost_cents,
            judge_score, judge_detail, attempt, is_active, created_at
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb, $10, $11, $12)
        RETURNING id
        """,
        job_id,
        row["type"],
        row["content"],
        row["url"],
        row["provider"],
        row["billing_source"],
        row["cost_cents"],
        row["judge_score"],
        json.dumps(row["judge_detail"]) if row["judge_detail"] is not None else None,
        row["attempt"],
        row["is_active"],
        row["created_at"],
    )


async def insert_template(db, merchant_id: int, **over) -> int:
    row = {
        "name": "夏日奶茶文案模板",
        "content": "写一段温暖的奶茶店小红书文案，突出当季新品与到店体验",
        "category": "餐饮",
        "tags": None,
        "is_public": False,
        "source_template_id": None,
        "usage_count": 0,
        "deleted_at": None,
    }
    row.update(over)
    return await db.fetchval(
        """
        INSERT INTO prompt_template (
            merchant_id, name, content, category, tags, is_public,
            source_template_id, usage_count, deleted_at, created_at, updated_at
        )
        VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7, $8, $9, now(), now())
        RETURNING id
        """,
        merchant_id,
        row["name"],
        row["content"],
        row["category"],
        json.dumps(row["tags"]) if row["tags"] is not None else None,
        row["is_public"],
        row["source_template_id"],
        row["usage_count"],
        row["deleted_at"],
    )


# --------------------------------------------------------------------------- #
# 03-studio 接缝打桩器
# --------------------------------------------------------------------------- #
class AiStub:
    """AI 接缝的打桩器。

    `guard` / `rewrite` / `generate` / `judge` 都是「**队列 + 兜底**」两段式：
    队列非空就先出队，队空则用兜底值。这样才能自然表达「第 1 次 40 分、
    第 2 次 90 分」这类序列——而这些序列恰恰是重试逻辑的全部测点。

    队列元素若是 `Exception` 实例，则**抛出**它（模拟超时 / 5xx / Key 失效）。
    """

    def __init__(self) -> None:
        self.calls: dict[str, list] = {
            k: [] for k in ("guard", "chat", "rewrite", "generate", "judge")
        }
        self._guard: list = []
        self._rewrite: list = []
        self._generate: list = []
        self._judge: list = []
        # 对话是流式的，用「片段序列」表达：元素是 `Exception` 就抛出来，
        # 这样才能测「流到一半挂了」——只写"成功"的字面量测不到这条路径。
        self.chat_pieces: list = ["先聊聊", "你的奶茶店", "最想突出什么？"]

    def q_guard(self, *items): self._guard.extend(items); return self
    def q_rewrite(self, *items): self._rewrite.extend(items); return self
    def q_generate(self, *items): self._generate.extend(items); return self
    def q_judge(self, *items): self._judge.extend(items); return self

    @staticmethod
    def _next(queue: list, default):
        if not queue:
            return default
        item = queue.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    # --- 打桩 ---
    def install(self, monkeypatch) -> None:
        try:
            from app.services import ai as ai_service
        except ImportError as exc:  # pragma: no cover - 红阶段必然走到
            raise AssertionError(
                "app.services.ai 尚未实现（03 的 AI 接缝，见 test_plan.md「测试缝」）"
            ) from exc

        stub = self

        async def guard_assets(asset_list, task):
            stub.calls["guard"].append({"assets": asset_list, "task_id": task.id})
            return stub._next(
                stub._guard, ai_service.GuardVerdict(passed=True, model="qwen3-vl")
            )

        async def chat_stream(messages, *, provider):
            stub.calls["chat"].append({"messages": messages, "provider": provider})
            for piece in stub.chat_pieces:
                if isinstance(piece, BaseException):
                    raise piece
                yield piece

        async def rewrite_prompt(raw_prompt, *, provider):
            stub.calls["rewrite"].append({"raw_prompt": raw_prompt, "provider": provider})
            return stub._next(
                stub._rewrite,
                ai_service.RewriteResult(
                    optimized_prompt=f"优化版：{raw_prompt}",
                    quality_score=88,
                    model="prompt-optimizer",
                ),
            )

        async def generate(prompt, *, kind, provider, task):
            stub.calls["generate"].append(
                {"prompt": prompt, "kind": kind, "provider": provider}
            )
            return stub._next(
                stub._generate,
                ai_service.GenResult(
                    content="一杯好喝的奶茶" if kind == "copy" else None,
                    url=None if kind == "copy" else "minio://bucket/out.mp4",
                    cost_cents=12,
                    # `units` 必须是**非零的定值**：07 的报销基数 =
                    # cost_price_per_unit × units，若这里是 0，所有
                    # `base_points` 都会算成 0，报销组的断言就全成了 0 == 0
                    units=100,
                    model=provider,
                ),
            )

        async def judge(*, kind, content, url, provider):
            stub.calls["judge"].append(
                {"kind": kind, "content": content, "url": url, "provider": provider}
            )
            return stub._next(
                stub._judge,
                ai_service.JudgeResult(
                    score=90, relevance=90, compliance=95, quality=85, reasons=[]
                ),
            )

        monkeypatch.setattr(ai_service, "guard_assets", guard_assets)
        monkeypatch.setattr(ai_service, "chat_stream", chat_stream)
        monkeypatch.setattr(ai_service, "rewrite_prompt", rewrite_prompt)
        monkeypatch.setattr(ai_service, "generate", generate)
        monkeypatch.setattr(ai_service, "judge", judge)


class DeepSeekStub:
    """把 DeepSeek 的 HTTP 调用截在 **transport 层**。

    与 `AiStub` 的区别是关键的：`AiStub` 换掉的是**函数**，这个换掉的是
    **transport**——03 追加 B 要测的正是「请求发得对不对」（URL / 头 / 体 /
    状态码分类），把函数整个换掉等于把被测对象一起换掉了。

    默认行为足以跑通 `chat_stream` 与 `generate(kind="copy")`：

    - 体里 `stream` 为真 → 回 SSE 文本（3 段 + `[DONE]`）；
    - 否则 → 回一段 `chat/completions` JSON，`usage.total_tokens = 1000`。

    需要别的形状时 `push(...)`：

        stub.push(status=401)                      # 上游拒绝
        stub.push(content="这不是 JSON")            # 复写拿不到结构
        stub.push(content=json.dumps({...}))        # 指定返回结构
        stub.push(usage={"total_tokens": 1234})     # 指定用量
        stub.push(sleep=2)                          # 模拟超时（配 timeout=0.2）
        stub.push(error=httpx.ConnectError("x"))   # 模拟网络错
    """

    def __init__(self) -> None:
        self.requests: list[dict] = []
        self.queue: list[dict] = []

    def push(self, **over) -> "DeepSeekStub":
        self.queue.append(over)
        return self

    @property
    def count(self) -> int:
        return len(self.requests)

    def body(self, index: int = -1) -> dict:
        return self.requests[index]["json"]

    def last(self) -> dict:
        return self.requests[-1]

    def install(self, monkeypatch) -> None:
        try:
            from app.services import ai as ai_service
        except ImportError as exc:  # pragma: no cover - 红阶段必然走到
            raise AssertionError(
                "app.services.ai 尚未实现（03 的 AI 接缝，见 test_plan.md「测试缝」）"
            ) from exc

        import httpx

        stub = self

        async def handler(request) -> "httpx.Response":
            raw = request.content or b""
            try:
                body = json.loads(raw) if raw else {}
            except ValueError:
                body = {}
            stub.requests.append(
                {
                    "url": str(request.url),
                    "headers": dict(request.headers),
                    "json": body,
                    # 超时是**传输层**的参数，体里看不到它。`MockTransport` 不是
                    # 真 socket，所以 `sleep` 不会按超时中断——要断言「超时取自
                    # 配置」只能看这里（`AI-10`）。
                    "timeout": request.extensions.get("timeout"),
                }
            )

            over = stub.queue.pop(0) if stub.queue else {}
            if over.get("sleep"):
                import asyncio

                await asyncio.sleep(over["sleep"])
            if over.get("error") is not None:
                raise over["error"]

            status = over.get("status", 200)
            if status != 200:
                return httpx.Response(status, json={"error": "upstream"})

            # 是不是流式**只看请求体**：`content` 覆盖的是「回什么」，不是「怎么回」。
            # 之前把它也当成了「非流式」的条件，于是「给流式响应指定内容」这条
            # 路径（AI-06）拿到的是 JSON 体，流直接是空的。
            if body.get("stream"):
                text = over.get("content", SSE_BODY)
                return httpx.Response(
                    200,
                    headers={"content-type": "text/event-stream"},
                    content=text.encode("utf-8"),
                )

            text = over.get("content", "默认回复")
            usage = over.get("usage", {"total_tokens": 1000})
            return httpx.Response(
                200,
                json={
                    "id": "chatcmpl-test",
                    "object": "chat.completion",
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": text},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": usage,
                },
            )

        transport = httpx.MockTransport(handler)
        # `raising=False`：红阶段 `ai.py` 里还没有 `_TRANSPORT`，有它才不至于
        # 把「实现没写」报成「测试自己写错」——那种红看不出在等什么。
        monkeypatch.setattr(ai_service, "_TRANSPORT", transport, raising=False)


# 3 段增量 + 收尾。拼接结果 = "你好，世界！"
SSE_BODY = (
    'data: {"choices":[{"delta":{"content":"你好"}}]}\n\n'
    'data: {"choices":[{"delta":{"content":"，世界"}}]}\n\n'
    'data: {"choices":[{"delta":{"content":"！"}}]}\n\n'
    "data: [DONE]\n\n"
)
SSE_PIECES = ("你好", "，世界", "！")


class DispatchStub:
    """把后台阶段调度换成记录器：只记下排了什么，**不真跑**。

    不这么干，用例断言 `status` 时就是在跟后台任务抢跑，红绿取决于机器快慢。
    真实进度由用例显式 `await run_stage(...)` 驱动。
    """

    def __init__(self) -> None:
        self.records: list[tuple[str, int]] = []

    def install(self, monkeypatch) -> None:
        try:
            from app.services import pipeline
        except ImportError as exc:  # pragma: no cover
            raise AssertionError(
                "app.services.pipeline 尚未实现（03 的流水线接缝）"
            ) from exc

        stub = self

        async def dispatch(stage: str, job_id: int) -> None:
            stub.records.append((stage, job_id))

        monkeypatch.setattr(pipeline, "dispatch", dispatch)

    def stages(self) -> list[str]:
        return [s for s, _ in self.records]


class PresignStub:
    """把预签名换成固定串，并记下每次的 `expires_in`。"""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def install(self, monkeypatch) -> None:
        try:
            from app.services import storage
        except ImportError as exc:  # pragma: no cover
            raise AssertionError("app.services.storage 尚未实现") from exc

        stub = self

        def presign_url(key: str, *, expires_in: int = 900) -> str:
            stub.calls.append({"key": key, "expires_in": expires_in})
            return f"https://minio.test/{key}?X-Amz-Expires={expires_in}"

        monkeypatch.setattr(storage, "presign_url", presign_url)


async def run_stage(stage: str, job_id: int) -> None:
    """显式驱动一个后台阶段跑完并落库。"""
    from app.services import pipeline

    await pipeline.run_stage(stage, job_id)


# --------------------------------------------------------------------------- #
# 04-tracking 工厂
# --------------------------------------------------------------------------- #
# spec 只举了 `xhs` 一个例子，其余四个平台的域名由 test_plan.md「已知取舍」3 定。
# 测试与实现**各自**持有一份（实现那份在 `app/api/post_serializers.py`）：
# 共用一份的话，白名单写错时测试会跟着一起错，等于没测。
PLATFORM_HOSTS = {
    "xhs": ("xiaohongshu.com", "xhslink.com"),
    "douyin": ("douyin.com", "v.douyin.com", "iesdouyin.com"),
    "kuaishou": ("kuaishou.com", "v.kuaishou.com"),
    "bilibili": ("bilibili.com", "b23.tv"),
    "shipinhao": ("channels.weixin.qq.com", "weixin.qq.com"),
}

_POST_SEQ = itertools.count(1000)
_CLAIM_SEQ = itertools.count(1000)

REVIEW_WINDOW_HOURS = 72
# 驳回理由与申诉理由的下限都是 10 字（spec 边界）。
# 两个常量各有一条断言盯着长度（RV-04 `len(REASON_9) == 9` /
# `len(REASON_10) == 10`），且 AP-02 拿 `REASON_10[:9]` 当"差一个字"那一刀。
# 所以长度必须是**精确的** 9 / 10，不能靠"看起来差不多"。
_REASON_PREFIX = "驳回理由"  # 4 字
REASON_9 = _REASON_PREFIX + "欠" * 5  # 4 + 5 = 9
REASON_10 = _REASON_PREFIX + "足" * 6  # 4 + 6 = 10（前 9 字同为 9 字）
OK_IMAGE_MIME = "image/png"
MAX_SCREENSHOT_BYTES = 20971520


def post_url(platform: str = "xhs", slug: str | None = None) -> str:
    """造一个与该平台域名匹配的合法链接。"""
    host = PLATFORM_HOSTS[platform][0]
    tag = f"{next(_POST_SEQ)}" if slug is None else slug
    return f"https://www.{host}/explore/{tag}"


def post_payload(scene, **over) -> dict:
    """一份合法的提交作品请求体。`post_url` 每次现取，天然避开「同链接」唯一约束。"""
    body = {
        "claim_id": scene.claim_id,
        "job_id": scene.job_id,
        "platform": "xhs",
        "post_url": post_url(),
        "source": "plugin",
    }
    body.update(over)
    return body


async def submit_post(client, token: str, scene, **over):
    return await client.post(
        "/api/posts", headers=bearer(token), json=post_payload(scene, **over)
    )


async def submit_post_ok(client, token: str, scene, **over) -> dict:
    r = await submit_post(client, token, scene, **over)
    assert r.status_code == 201, f"提交作品失败：{r.status_code} {r.text}"
    return r.json()


async def insert_claim(db, task_id: int, user_id: int, **over) -> int:
    """直插一条领取记录。批量审核用例要几十条 claim，走接口太贵。

    时间列叫 `claimed_at` 而不是 `created_at`（02 的表），默认 `in_progress`
    ——`claimed_at`/`created_at` 这类名字一个写错，红的是**几十条用例**，
    看起来像实现塌了，其实是这一行 SQL。
    """
    row = {"status": "in_progress", "claimed_at": datetime.now(timezone.utc)}
    row.update(over)
    return await db.fetchval(
        """
        INSERT INTO task_claim (task_id, user_id, status, claimed_at)
        VALUES ($1, $2, $3, $4) RETURNING id
        """,
        task_id,
        user_id,
        row["status"],
        row["claimed_at"],
    )


async def insert_post(db, claim_id: int, job_id: int, user_id: int, **over) -> int:
    """直插一条 `social_post`。

    状态机用例（RV / BA / AP）不走提交接口造状态——那样每条都得先搭
    「已 ready 的 job」，而它们的测点是**跃迁规则**，不是提交规则。
    """
    now = datetime.now(timezone.utc)
    row = {
        "platform": "xhs",
        "post_url": post_url(),
        "post_title": "探店笔记",
        "post_body": None,
        "source": "plugin",
        "status": "pending",
        "submitted_at": now,
        "reviewed_at": None,
        "reviewer_id": None,
        "reject_reason": None,
        "review_deadline": now + timedelta(hours=REVIEW_WINDOW_HOURS),
    }
    row.update(over)
    return await db.fetchval(
        """
        INSERT INTO social_post (
            claim_id, job_id, user_id, platform, post_url, post_title,
            post_body, source, status, submitted_at, review_deadline,
            reviewed_at, reviewer_id, reject_reason
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14)
        RETURNING id
        """,
        claim_id,
        job_id,
        user_id,
        row["platform"],
        row["post_url"],
        row["post_title"],
        row["post_body"],
        row["source"],
        row["status"],
        row["submitted_at"],
        row["review_deadline"],
        row["reviewed_at"],
        row["reviewer_id"],
        row["reject_reason"],
    )


async def post_under_task(db, task_id: int, user_id: int, **over) -> int:
    """在既有任务下造一条作品：claim + 已 ready 的 job + post。

    状态机用例（TO / RV / BA / AP）只需要「一条挂在本商户任务下的 pending 作品」，
    走提交接口的话每条都要先跑完整条流水线才能得到一个 ready 的 job。
    """
    claim_id = await insert_claim(db, task_id, user_id)
    job_id = await insert_job(db, task_id, claim_id, user_id, status="ready")
    return await insert_post(db, claim_id, job_id, user_id, **over)


async def post_with_task(
    db, merchant_id: int, user_id: int, task_over: dict | None = None, **post_over
) -> tuple[int, int]:
    """造一条独立任务 + 其下一条作品，返回 `(task_id, post_id)`。"""
    task_id = await insert_task(db, merchant_id, **(task_over or {}))
    post_id = await post_under_task(db, task_id, user_id, **post_over)
    return task_id, post_id


async def insert_snapshot(db, post_id: int, **over) -> int:
    """直插一条快照。`engagement` 默认按 spec 的公式由三个数算出来——
    测试自己手写 `engagement` 就等于把「谁算的」这件事验没了。"""
    row = {
        "likes": 10,
        "collects": 5,
        "comments": 3,
        "shares": 0,
        "source": "plugin",
        "raw": None,
        "captured_at": datetime.now(timezone.utc),
    }
    row.update(over)
    engagement = row.get("engagement")
    if engagement is None:
        engagement = row["likes"] + row["collects"] + row["comments"]
    return await db.fetchval(
        """
        INSERT INTO metric_snapshot (
            post_id, likes, collects, comments, shares, engagement, source,
            raw, captured_at
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb, $9) RETURNING id
        """,
        post_id,
        row["likes"],
        row["collects"],
        row["comments"],
        row["shares"],
        engagement,
        row["source"],
        json.dumps(row["raw"]) if row["raw"] is not None else None,
        row["captured_at"],
    )


async def expire_post(db, post_id: int, hours: float = 73) -> None:
    """把 `submitted_at` 往前推 `hours`，`review_deadline` 同步重算。

    **两个字段必须一起推**：spec 说 `review_deadline` 恒等于
    `submitted_at + 72h`，只推 deadline 会造出一个库里不存在的状态，
    `TO-01` 就白写了。
    """
    submitted_at = datetime.now(timezone.utc) - timedelta(hours=hours)
    await db.execute(
        "UPDATE social_post SET submitted_at = $2, review_deadline = $3 "
        "WHERE id = $1",
        post_id,
        submitted_at,
        submitted_at + timedelta(hours=REVIEW_WINDOW_HOURS),
    )


async def run_expiry(*, now=None) -> list[int]:
    """跑一次 72h 扫描并 committed（调度器每次跑在一个独立事务里）。

    `now` 可传：`TO-03` 要造「`review_deadline` 恰好等于 now」，
    只能由调用方把那个时刻喂进来，不能靠 `time.sleep`。
    """
    from app.db import SessionLocal
    from app.services import review

    session = SessionLocal()
    try:
        ids = await review.auto_approve_expired(session, now=now)
        await session.commit()
        return ids
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


async def set_post_status(db, post_id: int, status: str) -> None:
    """把作品直接摆到某个状态。

    状态机用例（RV / BA / AP / RT）不靠接口走到「已通过 / 已驳回」——
    那要先会申诉、先会超时，而它们的测点是**跃迁规则**，不是怎么走到那儿。
    """
    await db.execute(
        "UPDATE social_post SET status = $2, reviewed_at = now() WHERE id = $1",
        post_id,
        status,
    )


async def reimburse_scene(db, merchant_id: int, user_id: int, **over):
    """搭一个「用户垫付、过审后报销」的完整场景，返回各 id。

    `base_points = cost_price_per_unit × units`，默认 `1 × 100 = 100`——
    一个能一眼验算的数。报销组的三重截断全靠调 `units` / `per_user_limit`
    / `pool` 卡出不同的截断点，默认值若是个浮点数，红了也看不出是实现的锅
    还是精度的锅。

    `merchant_reserved` 默认取 `pool − used`：这是发布任务时的口径
    （整池锁进商户的 `reserved`），也是 `reimburse` 里
    `reserved == pool − used` 那条不变式的初值。
    """
    from types import SimpleNamespace

    now = datetime.now(timezone.utc)
    units = over.pop("units", 100)
    cost = over.pop("cost_price_per_unit", 1)
    pool = over.pop("pool", 1000)
    pool_used = over.pop("pool_used", 0)
    per_user_limit = over.pop("per_user_limit", 1000)
    reimburse_reserved = over.pop("reimburse_reserved", 100)
    merchant_balance = over.pop("merchant_balance", 100_000)
    merchant_reserved = over.pop("merchant_reserved", max(pool - pool_used, 0))
    user_balance = over.pop("user_balance", 0)
    task_over = over.pop("task_over", None) or {}

    task_id = await insert_task(
        db,
        merchant_id,
        pay_mode="user_pay_reimburse",
        reimburse_pool=pool,
        reimburse_pool_used=pool_used,
        reimburse_pool_reserved=reimburse_reserved,
        reimburse_per_user_limit=per_user_limit,
        **task_over,
    )
    # `effective_from` 每次唯一：计价行有 (provider, model, op, effective_from)
    # 唯一约束，同一用例里搭两次场景会直接 IntegrityError。
    await insert_price(
        db,
        "deepseek",
        "deepseek-chat",
        "chat",
        cost_price_per_unit=cost,
        price_per_unit=cost,
        max_price_per_call=120,
        effective_from=now - timedelta(days=1, microseconds=next(_REF_SEQ)),
    )
    await quota_account_for(
        db, merchant_id, balance=merchant_balance, reserved=merchant_reserved
    )
    await quota_account_for(db, user_id, balance=user_balance)
    claim_id = await insert_claim(db, task_id, user_id)
    job_id = await insert_job(db, task_id, claim_id, user_id, status="ready")
    await insert_reservation(
        db,
        job_id,
        merchant_id,
        user_id,
        task_id,
        units=units,
        reserved=120,
        actual=120,
        reimburse_reserved=reimburse_reserved,
        status="settled",
        settled_at=now,
    )
    post_id = await insert_post(db, claim_id, job_id, user_id, **over)
    return SimpleNamespace(
        task_id=task_id,
        claim_id=claim_id,
        job_id=job_id,
        post_id=post_id,
        merchant_id=merchant_id,
        user_id=user_id,
    )


async def insert_review_log(db, post_id: int, action: str, **over) -> int:
    row = {"operator_id": None, "reason": None}
    row.update(over)
    return await db.fetchval(
        """
        INSERT INTO review_log (post_id, action, operator_id, reason, created_at)
        VALUES ($1, $2, $3, $4, now()) RETURNING id
        """,
        post_id,
        action,
        row["operator_id"],
        row["reason"],
    )


async def insert_appeal(db, post_id: int, user_id: int, **over) -> int:
    row = {"reason": REASON_10, "status": "pending"}
    row.update(over)
    return await db.fetchval(
        """
        INSERT INTO appeal (
            post_id, user_id, reason, status, admin_id, admin_note,
            created_at, decided_at
        )
        VALUES ($1, $2, $3, $4, $5, $6, now(), $7) RETURNING id
        """,
        post_id,
        user_id,
        row["reason"],
        row["status"],
        row.get("admin_id"),
        row.get("admin_note"),
        row.get("decided_at"),
    )


# --------------------------------------------------------------------------- #
# 04-tracking 接缝打桩器
# --------------------------------------------------------------------------- #
class OcrStub:
    """`ai.ocr_metrics` 与 `storage.sniff_mime` 的打桩器。

    `ocr_metrics` 是「队列 + 兜底」两段式（与 `AiStub` 同款）：默认返回
    「认得出、有把握」的一档。

    `sniff_mime` 必须一起打——它读的是对象的真实字节，本趟还没有 MinIO。
    默认 `image/png`（合法），`sniff_queue` 非空时出队（`None` 表示读不到对象）。
    """

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.queue: list = []
        self.sniffed: list[str] = []
        self.sniff_queue: list = []

    def push(self, *items):
        self.queue.extend(items)
        return self

    def sniff(self, *items):
        self.sniff_queue.extend(items)
        return self

    def install(self, monkeypatch) -> None:
        try:
            from app.services import ai as ai_service
            from app.services import storage as storage_service
        except ImportError as exc:  # pragma: no cover
            raise AssertionError(
                "app.services.ai 尚未实现（04 的识别接缝）"
            ) from exc

        stub = self

        async def ocr_metrics(image_url: str):
            stub.calls.append(image_url)
            if stub.queue:
                item = stub.queue.pop(0)
                if isinstance(item, BaseException):
                    raise item
                return item
            return ai_service.OcrVerdict(
                confidence=0.95,
                model="qwen3-vl",
                parsed={"likes": 100, "collects": 20, "comments": 5, "shares": 1},
                raw_text="赞 100 收藏 20 评论 5",
            )

        def sniff_mime(key: str) -> str | None:
            stub.sniffed.append(key)
            if stub.sniff_queue:
                return stub.sniff_queue.pop(0)
            return OK_IMAGE_MIME

        monkeypatch.setattr(ai_service, "ocr_metrics", ocr_metrics)
        monkeypatch.setattr(storage_service, "sniff_mime", sniff_mime)


class RewardStub:
    """`reward.settle`（05 的接缝）的打桩器：只记参数，不做结算。

    04 的 `PS-*` 组断言的是**传给 05 的那个数**——`reward_grant` 表还没落地，
    落库列无从断言（见 test_plan.md「已知取舍」1）。
    """

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.raises: BaseException | None = None

    def install(self, monkeypatch) -> None:
        try:
            from app.services import reward
        except ImportError as exc:  # pragma: no cover
            raise AssertionError("app.services.reward 尚未实现") from exc

        stub = self

        async def settle(session, *, post_id, user_id, task_id, engagement, rule_id=None):
            stub.calls.append(
                {
                    "post_id": post_id,
                    "user_id": user_id,
                    "task_id": task_id,
                    "engagement": engagement,
                    "rule_id": rule_id,
                }
            )
            if stub.raises is not None:
                raise stub.raises
            return None

        monkeypatch.setattr(reward, "settle", settle)


# --------------------------------------------------------------------------- #
# 05-reward 工厂
# --------------------------------------------------------------------------- #
_COUPON_SEQ = itertools.count(1)


def as_json(value):
    """读 jsonb 回来的是 `str`（asyncpg 默认不挂 json codec），断言前统一解一下。

    不挂 codec 是刻意的：挂上去就看不见「写进去的到底是 jsonb 还是字符串」，
    而那正是 `jsonb` 列最容易出的错。
    """
    if isinstance(value, (dict, list)) or value is None:
        return value
    return json.loads(value)


async def insert_reward_rule(db, task_id: int, tiers=None, **over) -> int:
    """直插一条奖励规则并返回 id。

    默认 `make_tiers(3)`：`[0,99] → [100,199] → [200,∞)`，每档发 50 积分。
    要测「阶梯有缺口」这种**02 的校验不让出现**的形状，只能从这里进
    （`TI-06`）——那条测的是「脏数据进来了会不会 500」，不是 02 的校验对不对。
    """
    row = {"metric": "engagement", "max_reward_per_user": None}
    row.update(over)
    return await db.fetchval(
        """
        INSERT INTO reward_rule (task_id, metric, tiers, max_reward_per_user)
        VALUES ($1, $2, $3::jsonb, $4) RETURNING id
        """,
        task_id,
        row["metric"],
        json.dumps(make_tiers(3) if tiers is None else tiers),
        row["max_reward_per_user"],
    )


async def reward_scene(
    db,
    merchant_id: int,
    user_id: int,
    *,
    tiers=None,
    rule: bool = True,
    max_reward_per_user: int | None = None,
    snapshots: list[dict] | None = None,
    task_over: dict | None = None,
    **post_over,
):
    """一整套可结算的场景：task + reward_rule + claim + ready job + pending post。

    默认带**一条**快照（`likes=10 / collects=5 / comments=3` → `engagement=18`
    → 命中第 0 档 → 发 50 积分）。断言里的数都能一眼验算。

    `rule=False` 造「任务没配规则」——那是 04 真实会遇到的形状（任务可以不配
    规则就发布），此时「不发奖」是正确结果而不是错误（`TI-07`）。
    """
    from types import SimpleNamespace

    task_kwargs = {"status": "published"}
    task_kwargs.update(task_over or {})
    task_id = await insert_task(db, merchant_id, **task_kwargs)
    rule_id = (
        await insert_reward_rule(
            db, task_id, tiers=tiers, max_reward_per_user=max_reward_per_user
        )
        if rule
        else None
    )
    claim_id = await insert_claim(db, task_id, user_id)
    job_id = await insert_job(db, task_id, claim_id, user_id, status="ready")
    post_id = await insert_post(db, claim_id, job_id, user_id, **post_over)
    for snap in snapshots if snapshots is not None else [{}]:
        await insert_snapshot(db, post_id, **snap)

    return SimpleNamespace(
        task_id=task_id,
        rule_id=rule_id,
        claim_id=claim_id,
        job_id=job_id,
        post_id=post_id,
        merchant_id=merchant_id,
        user_id=user_id,
    )


async def another_post(db, scene, **over) -> int:
    """在**同一个 claim / 同一个任务**下再挂一篇作品。

    这是「同一用户在同一任务的第二篇作品」的唯一合法形状：一条 claim 只算
    一个作品，而 `uq_task_claim_active` 又不让同一 (task, user) 有第二条 claim。
    要造「这个用户在这个任务上已经发过 8000 分」，就得走这里。
    """
    job_id = await insert_job(
        db, scene.task_id, scene.claim_id, scene.user_id, status="ready"
    )
    return await insert_post(db, scene.claim_id, job_id, scene.user_id, **over)


async def seed_cash_grant(db, scene, post_id: int, amount: int) -> int:
    """造一条**已经发出去**的现金奖励（审计行 + 台账行），把累计额抬起来。

    真跑八次结算来凑 8000 会让上限用例又慢又难读，而累计口径是从
    `reward_grant.reward_detail.cash`（或 `cash_payout.amount`）加出来的，
    这里两处**同时**写一致的数——与真结算落下的形状完全相同。
    """
    grant_id = await db.fetchval(
        """
        INSERT INTO reward_grant (
            post_id, claim_id, user_id, task_id, reward_rule_id, engagement,
            tier_index, reward_detail, status, granted_at
        )
        VALUES ($1, $2, $3, $4, $5, 0, 0, $6::jsonb, 'granted', now())
        RETURNING id
        """,
        post_id,
        scene.claim_id,
        scene.user_id,
        scene.task_id,
        scene.rule_id,
        json.dumps({"cash": amount}),
    )
    await db.execute(
        """
        INSERT INTO cash_payout (user_id, reward_grant_id, amount, status)
        VALUES ($1, $2, $3, 'pending')
        """,
        scene.user_id,
        grant_id,
        amount,
    )
    return grant_id


async def grant_rows(db, post_id: int) -> list:
    return await db.fetch(
        "SELECT * FROM reward_grant WHERE post_id = $1 ORDER BY id", post_id
    )


async def grant_row(db, post_id: int):
    return await db.fetchrow(
        "SELECT * FROM reward_grant WHERE post_id = $1 ORDER BY id", post_id
    )


async def only_grant(db, post_id: int):
    """断言该 post **恰好**一行 grant 并返回它。

    直接 `grant_rows(...)[0]` 在「一行都没有」时抛的是 `IndexError`，
    读的人分不清是「结算根本没跑」还是「测试自己写错了」。这里的失败信息
    能直接指认。
    """
    rows = await grant_rows(db, post_id)
    assert len(rows) == 1, f"post {post_id} 的 reward_grant 应恰好 1 行，实际 {len(rows)} 行"
    return rows[0]


async def insert_point_ledger(
    db,
    user_id: int,
    change: int,
    balance_after: int,
    *,
    source: str = "adjust",
    ref_type: str = "seed",
    ref_id: int | None = None,
    remark: str | None = None,
    created_at=None,
) -> int:
    """直插一条积分流水。`ref_id` 默认取自增序号，避开 `(source, ref_type, ref_id)` 唯一。"""
    return await db.fetchval(
        """
        INSERT INTO point_ledger (
            user_id, change, balance_after, source, ref_type, ref_id, remark,
            created_at
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8) RETURNING id
        """,
        user_id,
        change,
        balance_after,
        source,
        ref_type,
        next(_REF_SEQ) if ref_id is None else ref_id,
        remark,
        created_at or datetime.now(timezone.utc),
    )


async def point_ledger_rows(db, user_id: int) -> list:
    return await db.fetch(
        "SELECT * FROM point_ledger WHERE user_id = $1 ORDER BY id", user_id
    )


async def points_of(db, user_id: int) -> int:
    """余额 = 全部 `change` 之和。不读缓存、不读快照列——只认账本。"""
    value = await db.fetchval(
        "SELECT coalesce(sum(change), 0) FROM point_ledger WHERE user_id = $1",
        user_id,
    )
    return int(value or 0)


async def settle_now(
    *, post_id: int, user_id: int, task_id: int, engagement: int
) -> dict | None:
    """脱离 HTTP 直接跑一次结算并提交。

    纯逻辑组（`TI` / `CP`）用它：它们的测点是**匹配与截断算法**，
    走一遍「建 job → 提交作品 → 商户过审」只会让红的定位成本变高。
    """
    from app.db import SessionLocal
    from app.services import reward as reward_service

    session = SessionLocal()
    try:
        out = await reward_service.settle(
            session,
            post_id=post_id,
            user_id=user_id,
            task_id=task_id,
            engagement=engagement,
        )
        await session.commit()
        return out
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


async def insert_coupon(db, merchant_id: int, **over) -> int:
    """直插券模板。默认 `total=100`、有效期一年——要造「发完了 / 已停用」
    这类前置就用它；建券本身的边界走 `POST /api/merchant/coupons`。"""
    now = datetime.now(timezone.utc)
    row = {
        "name": "满100减10",
        "type": "cash_off",
        "value": 1000,
        "min_amount": 10000,
        "total": 100,
        "issued": 0,
        "valid_from": now - timedelta(days=1),
        "valid_to": now + timedelta(days=365),
        "status": "active",
    }
    row.update(over)
    return await db.fetchval(
        """
        INSERT INTO coupon (
            merchant_id, name, type, value, min_amount, total, issued,
            valid_from, valid_to, status
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10) RETURNING id
        """,
        merchant_id,
        row["name"],
        row["type"],
        row["value"],
        row["min_amount"],
        row["total"],
        row["issued"],
        row["valid_from"],
        row["valid_to"],
        row["status"],
    )


async def insert_user_coupon(db, user_id: int, coupon_id: int, **over) -> int:
    now = datetime.now(timezone.utc)
    row = {
        "code": f"UC{next(_COUPON_SEQ):010d}",
        "status": "unused",
        "obtained_at": now,
        "expire_at": now + timedelta(days=30),
        "used_at": None,
    }
    row.update(over)
    return await db.fetchval(
        """
        INSERT INTO user_coupon (
            user_id, coupon_id, code, status, obtained_at, expire_at, used_at
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7) RETURNING id
        """,
        user_id,
        coupon_id,
        row["code"],
        row["status"],
        row["obtained_at"],
        row["expire_at"],
        row["used_at"],
    )


async def insert_mall_item(db, merchant_id: int, **over) -> int:
    row = {
        "name": "9.9 元咖啡券",
        "cover_url": None,
        "description": "到店核销",
        "points_cost": 100,
        "stock": 10,
        "sold": 0,
        "per_user_limit": None,
        "status": "on",
    }
    row.update(over)
    return await db.fetchval(
        """
        INSERT INTO mall_item (
            merchant_id, name, cover_url, description, points_cost, stock,
            sold, per_user_limit, status
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9) RETURNING id
        """,
        merchant_id,
        row["name"],
        row["cover_url"],
        row["description"],
        row["points_cost"],
        row["stock"],
        row["sold"],
        row["per_user_limit"],
        row["status"],
    )


async def insert_redemption(db, user_id: int, mall_item_id: int, **over) -> int:
    row = {
        "points_spent": 100,
        "quantity": 1,
        "status": "pending",
        "redeem_code": f"RC{next(_COUPON_SEQ):010d}",
        "address": None,
        "created_at": datetime.now(timezone.utc),
    }
    row.update(over)
    return await db.fetchval(
        """
        INSERT INTO redemption (
            user_id, mall_item_id, points_spent, quantity, status, redeem_code,
            address, created_at
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, $8) RETURNING id
        """,
        user_id,
        mall_item_id,
        row["points_spent"],
        row["quantity"],
        row["status"],
        row["redeem_code"],
        json.dumps(row["address"]) if row["address"] is not None else None,
        row["created_at"],
    )


async def approve_post(client, token: str, post_id: int):
    """商户过审——**结算的真实触发点**。除纯逻辑组外，一律走它而不是直调 `settle`。"""
    return await client.post(
        f"/api/merchant/reviews/{post_id}/approve", headers=bearer(token)
    )


async def approve_ok(client, token: str, post_id: int) -> dict:
    r = await approve_post(client, token, post_id)
    assert r.status_code == 200, f"过审失败：{r.status_code} {r.text}"
    return r.json()


async def redeem(client, token: str, item_id: int, **over):
    body = {"quantity": 1}
    body.update(over)
    return await client.post(
        f"/api/mall/items/{item_id}/redeem", headers=bearer(token), json=body
    )


async def redeem_ok(client, token: str, item_id: int, **over) -> dict:
    r = await redeem(client, token, item_id, **over)
    assert r.status_code == 201, f"兑换失败：{r.status_code} {r.text}"
    return r.json()


async def balance_of(client, token: str) -> int:
    r = await client.get("/api/me/points", headers=bearer(token))
    assert r.status_code == 200, f"查积分失败：{r.status_code} {r.text}"
    return r.json()["balance"]


async def grant_points_now(
    user_id: int, change: int, *, source: str, ref_type: str, ref_id: int
) -> int:
    """脱离 HTTP 直接调 `reward.grant_points`（`PL` 组用它测行锁串行）。"""
    from app.db import SessionLocal
    from app.services import reward as reward_service

    session = SessionLocal()
    try:
        balance = await reward_service.grant_points(
            session,
            user_id=user_id,
            change=change,
            source=source,
            ref_type=ref_type,
            ref_id=ref_id,
        )
        await session.commit()
        return balance
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


async def expire_coupons_now(*, now=None) -> int:
    """跑一次券过期扫描并提交。与 04 的 `run_expiry` 同款：不起真调度器，
    「扫的动作」才是被测对象。"""
    from app.db import SessionLocal
    from app.services import reward as reward_service

    session = SessionLocal()
    try:
        count = await reward_service.expire_coupons(session, now=now)
        await session.commit()
        return count
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


# --------------------------------------------------------------------------- #
# 06-admin 工厂
# --------------------------------------------------------------------------- #
def json_of(r) -> dict:
    """先断状态码，再取 body。

    端点未实现时 FastAPI 对不存在的路由也回 404，于是 `r.json()["items"]`
    会红成 `KeyError: 'items'`——读的人分不清是「端点没实现」还是
    「响应形状写错了」。先断 200 把这两件事分开。

    与 04 的 `appeal_ok` / `body_of`、07 的断言包装同款。
    """
    assert r.status_code == 200, f"期望 200，实际 {r.status_code}：{r.text}"
    return r.json()



async def insert_action_log(
    db,
    admin_id: int,
    action: str = "ban_user",
    target_type: str = "user",
    target_id: int = 1,
    **over,
) -> int:
    """直插一条审计日志（06 的形状：`admin_id` + `target_type`/`target_id` + jsonb `detail`）。

    写入端只有 `_log_action` 一处，本工厂只用于造「已有历史操作」的前置，
    以及 `AL` 组的排序断言——**不用它冒充被测端点的输出**。
    """
    row = {
        "detail": {"reason": "违规内容发布"},
        "created_at": datetime.now(timezone.utc),
    }
    row.update(over)
    return await db.fetchval(
        """
        INSERT INTO admin_action_log (
            admin_id, action, target_type, target_id, detail, created_at
        )
        VALUES ($1, $2, $3, $4, $5::jsonb, $6) RETURNING id
        """,
        admin_id,
        action,
        target_type,
        target_id,
        json.dumps(row["detail"]) if row["detail"] is not None else None,
        row["created_at"],
    )


async def action_logs(db, *, target_type=None, target_id=None) -> list:
    """按 spec 形状查审计日志，**倒序**（与端点的排序口径一致）。

    `detail` 已经 `as_json` 过，调用方可以直接 `row["detail"]["reason"]`——
    asyncpg 把 jsonb 当字符串返回，不解析的话断言会得到一串转义文本。
    """
    sql = "SELECT * FROM admin_action_log"
    args: list = []
    if target_type is not None:
        args.append(target_type)
        sql += f" WHERE target_type = ${len(args)}"
        if target_id is not None:
            args.append(target_id)
            sql += f" AND target_id = ${len(args)}"
    elif target_id is not None:
        args.append(target_id)
        sql += f" WHERE target_id = ${len(args)}"
    sql += " ORDER BY id DESC"
    rows = [dict(r) for r in await db.fetch(sql, *args)]
    for row in rows:
        row["detail"] = as_json(row["detail"])
    return rows


async def insert_budget_alert(db, merchant_id: int, **over) -> int:
    """直插一条熔断告警。写入端在 03 的 `_raise_budget`，06 只读。"""
    today = datetime.now(timezone.utc).date()
    row = {
        "alert_date": today,
        "spend_cents": 3000,
        "limit_cents": 5000,
        "created_at": datetime.now(timezone.utc),
    }
    row.update(over)
    return await db.fetchval(
        """
        INSERT INTO budget_alert (
            merchant_id, alert_date, spend_cents, limit_cents, created_at
        )
        VALUES ($1, $2, $3, $4, $5) RETURNING id
        """,
        merchant_id,
        row["alert_date"],
        row["spend_cents"],
        row["limit_cents"],
        row["created_at"],
    )


async def cost_scene(db, merchant_id: int, user_id: int, **over):
    """一整套成本场景：`task` + `claim` + `job` + `gen_output`。

    `gen_output.created_at` 是成本看板**唯一**的窗口维度，故必须可覆盖——
    默认今天，要造「窗口外」就传一个更早的时刻。
    """
    from types import SimpleNamespace

    task_id = await insert_task(db, merchant_id)
    claim_id = await insert_claim(db, task_id, user_id)
    job_id = await insert_job(db, task_id, claim_id, user_id, **over)
    output_id = await insert_output(db, job_id, **over)
    return SimpleNamespace(
        task_id=task_id,
        claim_id=claim_id,
        job_id=job_id,
        output_id=output_id,
        merchant_id=merchant_id,
        user_id=user_id,
    )


async def admin_users(client, token: str, **params):
    """打用户列表端点（不自动断言，调用方看状态码）。"""
    return await client.get(
        "/api/admin/users", headers=bearer(token), params=params or None
    )


async def ban_user(client, token: str, user_id: int, reason: str = "违规内容发布"):
    return await client.post(
        f"/api/admin/users/{user_id}/ban",
        headers=bearer(token),
        json={"reason": reason},
    )


async def resolve_content(client, token: str, job_id: int, action: str = "approve", **over):
    body = {"action": action}
    body.update(over)
    return await client.post(
        f"/api/admin/exceptions/content/{job_id}/resolve",
        headers=bearer(token),
        json=body,
    )


async def resolve_ocr(client, token: str, ocr_id: int, action: str = "accept", **over):
    body = {"action": action}
    body.update(over)
    return await client.post(
        f"/api/admin/exceptions/ocr/{ocr_id}/resolve",
        headers=bearer(token),
        json=body,
    )


async def decide_appeal(client, token: str, appeal_id: int, action: str = "accept", **over):
    body = {"action": action}
    body.update(over)
    return await client.post(
        f"/api/admin/appeals/{appeal_id}/decide",
        headers=bearer(token),
        json=body,
    )


async def insert_ocr(db, post_id: int, **over) -> int:
    """直插一条截图识别结果。`confidence` 是 `Numeric(3,2)`——传 float 即可。

    默认 `confidence=0.55`（低于 0.70 的阈）、`is_active=True`，
    正好落在 `ocr_low_confidence` 的名单里。
    """
    row = {
        "image_url": "minio://bucket/shot.jpg",
        "raw_text": "点赞 120 收藏 30 评论 8",
        "parsed": {"likes": 120, "collects": 30, "comments": 8},
        "model": "qwen-vl-max",
        "confidence": 0.55,
        "mismatch_flag": False,
        "is_active": True,
        "created_at": datetime.now(timezone.utc),
    }
    row.update(over)
    return await db.fetchval(
        """
        INSERT INTO ocr_result (
            post_id, image_url, raw_text, parsed, model, confidence,
            mismatch_flag, is_active, created_at
        )
        VALUES ($1, $2, $3, $4::jsonb, $5, $6, $7, $8, $9) RETURNING id
        """,
        post_id,
        row["image_url"],
        row["raw_text"],
        json.dumps(row["parsed"]) if row["parsed"] is not None else None,
        row["model"],
        row["confidence"],
        row["mismatch_flag"],
        row["is_active"],
        row["created_at"],
    )


# --------------------------------------------------------------------------- #
# 06 追加：用户反馈 + 今日使用量
# --------------------------------------------------------------------------- #
#: 12 字，稳稳落在 5 ~ 500 之间——用它当默认值，长度用例才好一眼验算
DEFAULT_FEEDBACK_CONTENT = "这是一条足够长的反馈内容"

BEIJING = timezone(timedelta(hours=8))

FEEDBACK_CATEGORIES = ("bug", "suggestion", "other")


def beijing_now() -> datetime:
    return datetime.now(BEIJING)


def beijing_today() -> date:
    return beijing_now().date()


def beijing_midnight(d: date) -> datetime:
    """北京时间某日 `00:00` 对应的 **UTC 时刻**。

    `DS-05` / `DS-11` 靠它构造「UTC 与北京不在同一天」的那一刻：
    北京 `09-16 00:00` == UTC `09-15 16:00`。
    """
    return datetime(d.year, d.month, d.day, tzinfo=BEIJING).astimezone(timezone.utc)


def beijing_day_end(d: date) -> datetime:
    """北京时间某日 `23:59:59` 对应的 UTC 时刻——「差一分钟就到下一天」的那一刀。"""
    return datetime(d.year, d.month, d.day, 23, 59, 59, tzinfo=BEIJING).astimezone(
        timezone.utc
    )


def feedback_payload(**over) -> dict:
    payload = {"category": "suggestion", "content": DEFAULT_FEEDBACK_CONTENT}
    payload.update(over)
    return payload


async def submit_feedback(client, token: str, **over):
    """打提交端点（不自动断言，调用方看状态码）。

    `over` 直接铺进请求体——`FB-04` / `FB-05` 正是靠它塞进非法的 `role` / `user_id`。
    """
    return await client.post(
        "/api/feedback", headers=bearer(token), json=feedback_payload(**over)
    )


async def submit_feedback_ok(client, token: str, **over) -> dict:
    r = await submit_feedback(client, token, **over)
    assert r.status_code == 201, f"期望 201，实际 {r.status_code}：{r.text}"
    return r.json()


async def insert_feedback(db, user_id: int, **over) -> int:
    """直插一条反馈，**显式写全所有列**。

    `role` 未指定时按 `user.role` 推导（端点也是这么干的）——所以调用方
    只需给 `user_id`。没传的可空列一律写 NULL，不让上一轮的值残留。
    """
    row = {
        "role": None,
        "category": "suggestion",
        "content": DEFAULT_FEEDBACK_CONTENT,
        "contact": None,
        "status": "open",
        "resolved_at": None,
        "resolved_by": None,
        "created_at": datetime.now(timezone.utc),
    }
    row.update(over)
    if row["role"] is None:
        row["role"] = await db.fetchval(
            'SELECT role FROM "user" WHERE id = $1', user_id
        )
    return await db.fetchval(
        """
        INSERT INTO user_feedback (
            user_id, role, category, content, contact,
            status, resolved_at, resolved_by, created_at
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9) RETURNING id
        """,
        user_id,
        row["role"],
        row["category"],
        row["content"],
        row["contact"],
        row["status"],
        row["resolved_at"],
        row["resolved_by"],
        row["created_at"],
    )


async def feedback_rows(db, **filters) -> list:
    """按 spec 形状查反馈，**倒序**（与端点的排序口径一致）。"""
    sql = "SELECT * FROM user_feedback"
    args: list = []
    for column, value in filters.items():
        args.append(value)
        sql += f"{' WHERE' if len(args) == 1 else ' AND'} {column} = ${len(args)}"
    sql += " ORDER BY created_at DESC, id DESC"
    return [dict(r) for r in await db.fetch(sql, *args)]


async def feedback_row(db, feedback_id: int):
    return await db.fetchrow("SELECT * FROM user_feedback WHERE id = $1", feedback_id)


async def admin_feedback(client, token: str, **params):
    return await client.get(
        "/api/admin/feedback", headers=bearer(token), params=params or None
    )


async def resolve_feedback(client, token: str, feedback_id: int, path: str | None = None):
    return await client.post(
        path or f"/api/admin/feedback/{feedback_id}/resolve", headers=bearer(token)
    )


async def reopen_feedback(client, token: str, feedback_id: int, path: str | None = None):
    return await client.post(
        path or f"/api/admin/feedback/{feedback_id}/reopen", headers=bearer(token)
    )


def decide_ok(r, key: str) -> dict:
    """处理类端点的断言包装：先断 200 再取键（同 `appeal_ok` / `redeem_ok` 款）。"""
    assert r.status_code == 200, f"期望 200，实际 {r.status_code}：{r.text}"
    return r.json()[key]


async def daily_stats(client, token: str, date_value=None):
    params = {"date": date_value} if date_value is not None else None
    return await client.get(
        "/api/admin/stats/daily", headers=bearer(token), params=params
    )


async def daily_stats_ok(client, token: str, date_value=None) -> dict:
    r = await daily_stats(client, token, date_value)
    assert r.status_code == 200, f"期望 200，实际 {r.status_code}：{r.text}"
    return r.json()


async def set_last_login(db, user_id: int, when: datetime) -> None:
    """改 `user.last_login_at`——造 `active` 场景的唯一手段。

    不改它就没法构造「北京时间昨天 23:59」这种时刻，`DS-05` 会退化成
    「今天的登录算今天」（平凡成立）。
    """
    await db.execute(
        'UPDATE "user" SET last_login_at = $2 WHERE id = $1', user_id, when
    )


async def set_created_at(db, user_id: int, when: datetime) -> None:
    """改 `user.created_at`——造 `new` 场景用。"""
    await db.execute('UPDATE "user" SET created_at = $2 WHERE id = $1', user_id, when)


def app_source() -> str:
    """`app/` 下全部 Python 源码拼成的一份文本，供契约扫描用。

    扫**整个 `app/`** 而不只是 `app/api/`：`FR-11` 盯的是「有人加了个改反馈的
    入口」，把 SQL 挪进 `services/` 同样算数。
    """
    root = BACKEND_DIR / "app"
    return "\n".join(
        path.read_text(encoding="utf-8") for path in sorted(root.rglob("*.py"))
    )


#: `DELETE FROM user_feedback`——反馈只能标处理状态，不能删
RE_FEEDBACK_DELETE = re.compile(r"(?is)\bdelete\s+from\s+user_feedback\b")

#: `UPDATE user_feedback SET ... content/role/user_id ...`——改这三列即违约
RE_FEEDBACK_FORBIDDEN_SET = re.compile(
    r"(?is)update\s+user_feedback\s+set[^;]*?\b(?:content|role|user_id)\b"
)


def feedback_mutations(src: str) -> list[str]:
    """源码里所有**不该存在**的 `user_feedback` 写操作。

    只认 `DELETE` 与「改 `content` / `role` / `user_id` 的 `UPDATE`」——
    `UPDATE ... SET status, resolved_at, resolved_by` 是**合法**的（处理状态），
    不能跟着一起扫掉，否则 `FR-11` 会对着正确实现常红。
    """
    return RE_FEEDBACK_DELETE.findall(src) + RE_FEEDBACK_FORBIDDEN_SET.findall(src)


# --------------------------------------------------------------------------- #
# 裸 SQL 断言层：spec 的表名/列名即契约
# --------------------------------------------------------------------------- #
class SchemaMissing(AssertionError):
    """表/列还没实现——把 asyncpg 的报错翻译成一句人话。"""


class Db:
    """按 spec 直接查库。schema 缺失时给出明确失败信息，而不是抛裸异常。"""

    def __init__(self, conn: asyncpg.Connection) -> None:
        self._conn = conn

    async def _call(self, method: str, sql: str, *args):
        try:
            return await getattr(self._conn, method)(sql, *args)
        except asyncpg.UndefinedTableError as exc:
            raise SchemaMissing(f"表不存在（该 spec 尚未实现）：{exc}\nSQL: {sql}") from exc
        except asyncpg.UndefinedColumnError as exc:
            raise SchemaMissing(f"列不存在（与 spec 不一致）：{exc}\nSQL: {sql}") from exc

    async def fetch(self, sql: str, *args) -> list[asyncpg.Record]:
        return await self._call("fetch", sql, *args)

    async def fetchrow(self, sql: str, *args) -> asyncpg.Record | None:
        return await self._call("fetchrow", sql, *args)

    async def fetchval(self, sql: str, *args):
        return await self._call("fetchval", sql, *args)

    async def execute(self, sql: str, *args) -> str:
        return await self._call("execute", sql, *args)
