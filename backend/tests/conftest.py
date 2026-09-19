"""01-auth 测试基础设施。

设计要点：
1. **独立测试库** —— 环境变量在 `import app` 之前设置，绝不碰开发库。
2. **真 PostgreSQL（Docker，5433）** —— 行锁、唯一约束、CHECK 约束必须真测。
3. **裸 SQL 断言** —— `db` fixture 按 spec 的表名/列名查库，把 spec 变成可执行契约。
4. **每个用例后 TRUNCATE** —— 用例之间零耦合，顺序无关。
"""

import os

# --- 必须在 import app 之前完成（config.Settings 在 import 时读取）------------
os.environ.setdefault("APP_ENV", "test")
os.environ["DATABASE_URL"] = (
    "postgresql+asyncpg://zxs:zxs_dev_pw@localhost:5433/zhuxiaoshang_test"
)
os.environ["REDIS_URL"] = "redis://localhost:6380/15"
os.environ["JWT_SECRET"] = "test-only-secret-0123456789abcdef-not-for-production"
# 显式清空：开发者本地 `.env` 里若放了真 DeepSeek key，会被 Settings 读进来，
# 于是「未配置 key」的用例（AI-02）与整条测试护栏都会失效。
# 需要 key 的用例走 `settings_override` 显式给。
os.environ["DEEPSEEK_API_KEY"] = ""

import asyncpg  # noqa: E402
import httpx  # noqa: E402
import pytest  # noqa: E402
from argon2 import PasswordHasher  # noqa: E402
from sqlalchemy import text  # noqa: E402

import app.models  # noqa: E402,F401  让 Base.metadata 收集到已实现的模型
from app.db import Base, engine  # noqa: E402
from app.main import app as fastapi_app  # noqa: E402
from tests.helpers import (  # noqa: E402
    ADMIN_DSN,
    SEED_ADMIN,
    SEED_CUSTOMER,
    SEED_MERCHANT,
    TEST_DB_NAME,
    TEST_DSN,
    Db,
    login_ok,
    register_customer,
    register_merchant,
)

_hasher = PasswordHasher()


# --------------------------------------------------------------------------- #
# 会话级：库与表结构
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="session", autouse=True)
async def _prepare_database():
    """确保测试库存在并按模型建表。模型未实现时建出 0 张表（用例即红）。"""
    try:
        conn = await asyncpg.connect(ADMIN_DSN)
    except OSError as exc:
        pytest.fail(
            "连不上测试数据库（localhost:5433）。测试依赖 Docker Compose 里的真 PostgreSQL，"
            "请先启动 Docker Desktop，再执行：\n"
            "    docker compose up -d\n"
            f"原始错误：{exc}",
            pytrace=False,
        )
    try:
        exists = await conn.fetchval(
            "SELECT 1 FROM pg_database WHERE datname = $1", TEST_DB_NAME
        )
        if not exists:
            await conn.execute(f'CREATE DATABASE "{TEST_DB_NAME}"')
    finally:
        await conn.close()

    async with engine.begin() as sa_conn:
        # 先删后建：`create_all` 只建缺失的表，**不会**改动已存在表上的约束，
        # 改约束后旧库会一直用着旧定义，用例红得莫名其妙。测试库是纯再生的
        # （每个用例都 TRUNCATE），按模型重建才能保证「库结构 == 模型」。
        await sa_conn.run_sync(Base.metadata.drop_all)
        await sa_conn.run_sync(Base.metadata.create_all)

    yield

    await engine.dispose()


@pytest.fixture(autouse=True)
async def _clean_tables(_prepare_database):
    """每个用例前后清空所有表。"""
    await _truncate_all()
    yield
    await _truncate_all()


async def _truncate_all() -> None:
    async with engine.begin() as conn:
        rows = await conn.execute(
            text(
                "SELECT tablename FROM pg_tables "
                "WHERE schemaname = 'public' AND tablename <> 'spatial_ref_sys'"
            )
        )
        names = [r[0] for r in rows]
        if names:
            joined = ", ".join(f'"{n}"' for n in names)
            await conn.execute(text(f"TRUNCATE {joined} RESTART IDENTITY CASCADE"))


# --------------------------------------------------------------------------- #
# AI 测试护栏
# --------------------------------------------------------------------------- #
@pytest.fixture(autouse=True)
def _no_real_ai(monkeypatch):
    """不给真网络留缝：把 AI 的 HTTP 出口换成「一调用就炸」。

    03 追加 B 之后 `ai.py` 的六个函数是真的会打 DeepSeek 的。用例若忘了装桩，
    就会**真发请求、真花钱、结果看上上游心情**——红绿取决于网络，是最难查的
    那种绿。`_TRANSPORT` 是 `ai.py` **唯一**的网络出口，换掉它六个函数一起封死。

    装了 `patch_ai` / `patch_ocr`（函数级打桩）或 `deepseek_stub`（transport 级，
    在本 fixture 之后安装，后装者胜）的用例照常工作；**忘了装的用例当场炸**。

    `_TRANSPORT` 用 `raising=False`：红阶段 `ai.py` 里还没有这个名字，
    不加的话会把「实现没写」报成「测试自己写错」，那种红看不出在等什么。
    """
    import httpx

    from app.config import get_settings
    from app.services import ai as ai_service

    def _boom(request):  # pragma: no cover - 命中即失败
        raise AssertionError(
            "测试不得调用真实 AI，请使用 patch_ai / patch_ocr / deepseek_stub"
        )

    monkeypatch.setattr(
        ai_service, "_TRANSPORT", httpx.MockTransport(_boom), raising=False
    )

    # 给个占位 key：否则先撞 AIConfigError，护栏就报不出「打了网络」这件事，
    # 用例拿到的错误信息会指向「未配置」而不是「你不该真调」。
    #
    # 红阶段没有这个字段——pydantic v2 对不存在的字段直接抛 `ValueError`，
    # 连 `raising=False` 都拦不住（那不是 AttributeError）。那时六个函数本来就
    # 只会 `raise NotImplementedError`，打不到网络，跳过即可。
    settings = get_settings()
    if "deepseek_api_key" in type(settings).model_fields:
        monkeypatch.setattr(settings, "deepseek_api_key", "sk-guard-not-a-real-key")


@pytest.fixture
def deepseek_stub(monkeypatch):
    """把 DeepSeek 截在 transport 层。见 helpers.DeepSeekStub。"""
    from tests.helpers import DeepSeekStub

    stub = DeepSeekStub()
    stub.install(monkeypatch)
    return stub


@pytest.fixture
def settings_override(monkeypatch):
    """临时改运行配置。`get_settings()` 是单例，改完由 monkeypatch 还原。

    字段不存在时**明确报出缺哪一项**，而不是让 pydantic 抛一句
    `"Settings" object has no field "..."`——后者分不清是配置没实现还是名字写错。
    """
    from app.config import get_settings

    settings = get_settings()

    def _override(**over):
        for key, value in over.items():
            if key not in type(settings).model_fields:
                raise AssertionError(
                    f"Settings 里没有 {key!r} 这一项——"
                    f"03 追加 B 要求补上它（见 spec.md 的「新增配置」表）"
                )
            monkeypatch.setattr(settings, key, value)
        return settings

    return _override


# --------------------------------------------------------------------------- #
# 裸 SQL / HTTP
# --------------------------------------------------------------------------- #
@pytest.fixture
async def db():
    """按 spec 直接查库。"""
    conn = await asyncpg.connect(TEST_DSN)
    try:
        yield Db(conn)
    finally:
        await conn.close()


@pytest.fixture
async def client():
    transport = httpx.ASGITransport(app=fastapi_app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test", timeout=30.0
    ) as c:
        yield c


# --------------------------------------------------------------------------- #
# 用户工厂
# --------------------------------------------------------------------------- #
@pytest.fixture
async def customer(client):
    """注册并登录一个客户，返回 (user_json, access_token, refresh_token)。"""
    r = await register_customer(client)
    assert r.status_code == 201, f"注册客户失败：{r.status_code} {r.text}"
    body = r.json()
    return body["user"], body["access_token"], body["refresh_token"]


@pytest.fixture
async def merchant(client):
    r = await register_merchant(client)
    assert r.status_code == 201, f"注册商户失败：{r.status_code} {r.text}"
    body = r.json()
    return body["user"], body["access_token"], body["refresh_token"]


@pytest.fixture
async def merchant_b(client):
    """第二个商户的 access_token——跨租户用例要一个「别人」。"""
    await register_merchant(client, account="shop0002", shop_name="别家店铺")
    body = await login_ok(client, "shop0002", "pass1234", fp="fp-b")
    return body["access_token"]


@pytest.fixture
async def seed_accounts(db):
    """按 spec 的种子数据写三个账号。

    注册接口不允许 `role=admin`，故直插；`password_hash` 用 spec 允许的 argon2id，
    这意味着**实现必须支持 argon2id 校验**（见 test_plan.md「已知取舍」#1）。
    """
    rows = []
    for spec_row, role, nickname in (
        (SEED_MERCHANT, "merchant", "测试商户"),
        (SEED_CUSTOMER, "customer", "测试用户"),
        (SEED_ADMIN, "admin", "测试管理员"),
    ):
        uid = await db.fetchval(
            """
            INSERT INTO "user" (account, password_hash, role, nickname, status)
            VALUES ($1, $2, $3, $4, 'active')
            RETURNING id
            """,
            spec_row["account"],
            _hasher.hash(spec_row["password"]),
            role,
            nickname,
        )
        rows.append((uid, spec_row, role))
    return rows


@pytest.fixture
async def studio(client, merchant, customer, db):
    """「开箱可用」的内容工坊场景：商户已充值 + 计价表齐备 + 客户已领取任务。

    计价表一次备四条（预检 / 对话与复写 / 视频），刻意让**每一条 op 都有行**：
    spec 把「计价表缺该组合 → 503」写成了边界（`JB-14`），若默认场景就缺行，
    那条用例会与一堆用例一起红，分不清是「缺行探针生效」还是「场景没搭好」。
    """
    from types import SimpleNamespace

    from tests.helpers import claimed_task, grant, insert_price

    merchant_user, merchant_token, _ = merchant
    customer_user, customer_token, _ = customer

    await grant(db, merchant_user["id"], 100_000)
    await insert_price(db, "dashscope", "qwen3-vl", "guard", unit="call",
                       cost_price_per_unit=0, price_per_unit=0,
                       max_price_per_call=10, supports_byok=False)
    await insert_price(db, "deepseek", "deepseek-chat", "chat")
    await insert_price(db, "deepseek", "prompt-optimizer", "rewrite")
    await insert_price(db, "jimeng", "jimeng-video", "generate", unit="call",
                       cost_price_per_unit=0, price_per_unit=0,
                       max_price_per_call=200)
    await insert_price(db, "kling", "kling-video", "generate", unit="call",
                       cost_price_per_unit=0, price_per_unit=0,
                       max_price_per_call=200)

    task, claim_id = await claimed_task(client, merchant_token, customer_token)

    return SimpleNamespace(
        merchant=merchant_user,
        merchant_token=merchant_token,
        customer=customer_user,
        customer_token=customer_token,
        task=task,
        task_id=task["id"],
        claim_id=claim_id,
    )


@pytest.fixture
def patch_dispatch(monkeypatch):
    """把后台阶段调度换成记录器（不真跑）。见 helpers.DispatchStub。"""
    from tests.helpers import DispatchStub

    stub = DispatchStub()
    stub.install(monkeypatch)
    return stub


@pytest.fixture
def patch_ai(monkeypatch):
    """把 AI 能力全部换成桩。见 helpers.AiStub。"""
    from tests.helpers import AiStub

    stub = AiStub()
    stub.install(monkeypatch)
    return stub


@pytest.fixture
def patch_presign(monkeypatch):
    """把预签名 URL 换成固定串。见 helpers.PresignStub。"""
    from tests.helpers import PresignStub

    stub = PresignStub()
    stub.install(monkeypatch)
    return stub


@pytest.fixture
async def make_scene(client, merchant, customer, db, patch_dispatch, patch_ai):
    """04 的「开箱可用」场景工厂：一个已 ready 的 job。

    返回的是**工厂**而不是现成对象，因为报销组的任务要 `user_pay_reimburse`
    并带池子，而 fixture 没法接收参数。一个用例调一次（第二次会撞注册的唯一约束）。

    计价表把 `cost_price_per_unit` 定成 **1 分/单位**、桩里 `units=100`，
    于是 `base_points = 100`——一个能一眼验算的数。若用默认的
    `0.000002`，断言会变成浮点数比较，红了也看不出是实现错还是精度错。
    """
    from types import SimpleNamespace

    from tests.helpers import (
        create_job_ok,
        grant,
        insert_price,
        claimed_task,
        run_stage,
    )

    merchant_user, merchant_token, _ = merchant
    customer_user, customer_token, _ = customer

    async def _build(**over):
        await grant(db, merchant_user["id"], 100_000)
        await grant(db, customer_user["id"], 100_000)
        await insert_price(
            db, "dashscope", "qwen3-vl", "guard", unit="call",
            cost_price_per_unit=0, price_per_unit=0, max_price_per_call=10,
        )
        await insert_price(
            db, "deepseek", "deepseek-chat", "chat",
            cost_price_per_unit=1, price_per_unit=2, max_price_per_call=120,
        )
        await insert_price(db, "deepseek", "prompt-optimizer", "rewrite")
        await insert_price(
            db, "jimeng", "jimeng-video", "generate", unit="call",
            cost_price_per_unit=0, price_per_unit=0, max_price_per_call=200,
        )

        task, claim_id = await claimed_task(
            client, merchant_token, customer_token, **over
        )
        created = await create_job_ok(client, customer_token, task["id"], claim_id)
        job_id = created["job_id"]
        # 两段都显式驱动：`dispatch` 被打桩，后台任务不会自己跑
        await run_stage("guard", job_id)
        await run_stage("generate", job_id)

        return SimpleNamespace(
            merchant=merchant_user,
            merchant_token=merchant_token,
            customer=customer_user,
            customer_token=customer_token,
            task=task,
            task_id=task["id"],
            claim_id=claim_id,
            job_id=job_id,
            post_url=None,
        )

    return _build


@pytest.fixture
def patch_ocr(monkeypatch):
    """把视觉识别换成桩。见 helpers.OcrStub。"""
    from tests.helpers import OcrStub

    stub = OcrStub()
    stub.install(monkeypatch)
    return stub


@pytest.fixture
def patch_reward(monkeypatch):
    """把 05 的奖励接缝换成记录器。见 helpers.RewardStub。"""
    from tests.helpers import RewardStub

    stub = RewardStub()
    stub.install(monkeypatch)
    return stub


@pytest.fixture
def set_user_status(db):
    """把用户改成 banned / deleted，用于权限与登录用例。"""

    async def _set(user_id: int, status: str) -> None:
        await db.execute(
            'UPDATE "user" SET status = $2 WHERE id = $1', user_id, status
        )

    return _set
