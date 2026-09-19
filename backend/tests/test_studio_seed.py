"""03 追加 B · 种子数据（`SN-01` ~ `SN-10`）。

种子不只是「三个账号」了：它要撑起手工验收的第一步——登录客户账号，页面上
**立刻**有两条可选任务、有余额、有「垫付 / 可报 / 池子还剩」可看。
`SN-09` / `SN-10` 守的是这条链路的终点：**账真的会动**。
"""

import pytest

from tests.helpers import (
    as_json,
    assets_of,
    bearer,
    create_job_ok,
    login_ok,
    run_seed,
)

pytestmark = pytest.mark.asyncio

FOUR_LIMIT_COLUMNS = (
    "daily_limit",
    "per_user_daily_limit",
    "per_user_task_limit",
    "user_daily_limit",
)


@pytest.fixture
async def seeded(db):
    from app.seed import seed

    return await seed()


@pytest.fixture
async def customer_token(client, seeded):
    body = await login_ok(client, "000001", "000001", fp="fp-seed-customer")
    return body["access_token"]


# --------------------------------------------------------------------------- #
# SN-01 幂等
# --------------------------------------------------------------------------- #
async def test_sn01_seed_is_idempotent(db, seeded):
    assert seeded > 0, "首次跑种子应当有新建"

    from app.seed import seed

    again = await seed()

    assert again == 0, f"第二次跑种子不得新建任何东西，实际 {again}"
    assert await db.fetchval("SELECT count(*) FROM task") == 2
    assert await db.fetchval("SELECT count(*) FROM model_price") == 5


# --------------------------------------------------------------------------- #
# SN-02 额度账户
# --------------------------------------------------------------------------- #
async def test_sn02_demo_accounts_have_balance(db, seeded):
    rows = await db.fetch(
        """
        SELECT u.account, a.balance, a.reserved, a.debt,
               a.daily_limit, a.per_user_daily_limit,
               a.per_user_task_limit, a.user_daily_limit
        FROM quota_account a
        JOIN "user" u ON u.id = a.user_id
        WHERE u.account IN ('000000', '000001')
        ORDER BY u.account
        """
    )
    by_account = {r["account"]: r for r in rows}
    assert set(by_account) == {"000000", "000001"}, (
        f"两个演示账号都该有额度账户，实际 {sorted(by_account)}"
    )

    assert by_account["000000"]["balance"] == 5000
    assert by_account["000001"]["balance"] == 2000
    assert by_account["000001"]["reserved"] == 0
    assert by_account["000001"]["debt"] == 0

    for account, row in by_account.items():
        for column in FOUR_LIMIT_COLUMNS:
            assert row[column] is None, (
                f"{account} 的 {column} 必须显式写 NULL。"
                f"「helper 静默吞列」这条坑已栽过三次——未传的列要显式写，不靠默认值"
            )


# --------------------------------------------------------------------------- #
# SN-03 ~ SN-05 两条任务与奖励规则
# --------------------------------------------------------------------------- #
async def test_sn03_two_tasks_covering_both_pay_modes(db, seeded):
    rows = await db.fetch(
        "SELECT id, title, pay_mode, status, quota, start_at, end_at, "
        "reimburse_pool, reimburse_per_user_limit, char_length(description) AS dlen "
        "FROM task ORDER BY id"
    )
    assert len(rows) == 2, f"种子应恰好两条任务，实际 {len(rows)}"

    modes = {r["pay_mode"] for r in rows}
    assert modes == {"merchant_pay", "user_pay_reimburse"}, (
        f"两条任务要覆盖两种付费模式，实际 {modes}"
    )

    for row in rows:
        assert row["status"] == "published", f"{row['title']} 未发布，页面上看不到"
        assert row["dlen"] >= 10, (
            f"{row['title']} 的 description 只有 {row['dlen']} 字，低于 10 字下限"
        )
        assert row["start_at"] < row["end_at"], f"{row['title']} 的时间窗反了"


async def test_sn04_reward_rule_per_task(db, seeded):
    tasks = await db.fetch("SELECT id, title FROM task ORDER BY id")
    assert len(tasks) == 2

    for task in tasks:
        rows = await db.fetch(
            "SELECT metric, tiers, max_reward_per_user FROM reward_rule WHERE task_id = $1",
            task["id"],
        )
        assert len(rows) == 1, (
            f"{task['title']} 应恰好一条奖励规则，实际 {len(rows)} 条"
            "（没有规则的任务发布不了，也没有奖励可算）"
        )

        rule = rows[0]
        assert rule["metric"] == "engagement", rule["metric"]
        assert rule["max_reward_per_user"] == 20

        # asyncpg 不挂 json codec，jsonb 读回来是**字符串**——不 decode 的话
        # `len(tiers)` 量的是字符数，`tiers[-1]["max"]` 直接抛 TypeError，
        # 红得像实现塌了，其实是这一行少了转换
        tiers = as_json(rule["tiers"])
        assert len(tiers) == 2, f"两档阶梯，实际 {tiers}"
        assert tiers[-1]["max"] is None, (
            f"末档必须是开区间（max=null），否则高互动量的作品无档可落：{tiers}"
        )
        assert tiers[0]["min"] == 0 and tiers[0]["max"] == 49
        assert tiers[0]["reward"] == {"cash": 5}, tiers[0]["reward"]
        assert tiers[1]["reward"] == {"cash": 20}, tiers[1]["reward"]


async def test_sn05_reimburse_pool_and_per_user_limit(db, seeded):
    row = await db.fetchrow(
        "SELECT id, title, reimburse_pool, reimburse_per_user_limit, "
        "reimburse_pool_used, reimburse_pool_reserved "
        "FROM task WHERE pay_mode = 'user_pay_reimburse'"
    )
    assert row is not None, "必须有一条 user_pay_reimburse 任务，否则界面上看不到垫付口径"

    assert row["reimburse_pool"] == 1000, row["reimburse_pool"]
    assert row["reimburse_per_user_limit"] == 500, row["reimburse_per_user_limit"]
    assert row["reimburse_pool_used"] == 0
    assert row["reimburse_pool_reserved"] == 0

    from app.services import pricing

    ceiling = pricing.PRICES["generate"]["max_price_per_call"]
    assert row["reimburse_per_user_limit"] >= ceiling, (
        f"单人上限（{row['reimburse_per_user_limit']}）必须 >= 单次预扣上界（{ceiling}），"
        "否则演示账号第一次建 job 就会被自己的上限挡在门外"
    )


# --------------------------------------------------------------------------- #
# SN-06 ~ SN-07 手工验收的第一步：选任务弹层不能是空的
# --------------------------------------------------------------------------- #
async def test_sn06_customer_has_claims_on_both_tasks(
    client, customer_token, db, seeded
):
    r = await client.get("/api/me/claims", headers=bearer(customer_token))
    assert r.status_code == 200, f"{r.status_code} {r.text}"

    body = r.json()
    assert body["total"] == 2, (
        f"测试用户应对两条任务各有一条领取，实际 {body['total']}——"
        "没有这一步，创作台的「选任务」弹层是空的，手工验收第一步就卡死"
    )

    statuses = {item["status"] for item in body["items"]}
    assert statuses == {"in_progress"}, statuses


async def test_sn07_both_tasks_are_listed(client, db, seeded):
    r = await client.get("/api/tasks")
    assert r.status_code == 200, f"{r.status_code} {r.text}"

    rows = await db.fetch("SELECT id, title FROM task ORDER BY id")
    # 先断「库里真有两条」再断「两条都列出来了」。少了这一句，库是空的时候
    # `missing` 也是空的，用例会**平凡成立**——「查得到」不等于「查得全」。
    assert len(rows) == 2, f"前置：库里应有 2 条演示任务，实际 {len(rows)}"

    listed = {item["id"] for item in r.json()["items"]}
    missing = [row["title"] for row in rows if row["id"] not in listed]
    assert not missing, f"这些演示任务没出现在列表里：{missing}（列出的 id：{sorted(listed)}）"


# --------------------------------------------------------------------------- #
# SN-08 生产环境拒绝
# --------------------------------------------------------------------------- #
async def test_sn08_refuses_in_production(db):
    result = run_seed("production")

    assert result.returncode == 1, (
        f"APP_ENV=production 时必须以退出码 1 拒绝，实际 {result.returncode}\n"
        f"stdout={result.stdout}\nstderr={result.stderr}"
    )
    # 退出码 1 也可能是脚本自己崩了（模块缺失、语法错）。要看到**拒绝日志**才算数，
    # 否则「拒绝执行」会因为「根本没跑起来」而假绿。
    combined = f"{result.stdout}\n{result.stderr}".lower()
    assert any(kw in combined for kw in ("production", "生产", "拒绝", "refuse")), (
        f"必须打出明确的拒绝日志，实际输出：\n{combined}"
    )

    assert await db.fetchval('SELECT count(*) FROM "user"') == 0
    assert await db.fetchval("SELECT count(*) FROM task") == 0
    assert await db.fetchval("SELECT count(*) FROM model_price") == 0
    assert await db.fetchval("SELECT count(*) FROM quota_account") == 0
    assert await db.fetchval("SELECT count(*) FROM task_claim") == 0


# --------------------------------------------------------------------------- #
# SN-09 ~ SN-10 账真的会动
# --------------------------------------------------------------------------- #
@pytest.fixture
async def finished_copy_job(
    client, customer_token, db, seeded, patch_dispatch, patch_ai
):
    """在种子数据上走完一个 copy job（用户垫付那条）。"""
    from tests.helpers import run_stage

    task_id = await db.fetchval(
        "SELECT id FROM task WHERE pay_mode = 'user_pay_reimburse'"
    )
    created = await create_job_ok(client, customer_token, task_id, assets=assets_of(1))
    job_id = created["job_id"]

    await run_stage("guard", job_id)
    await run_stage("generate", job_id)

    # 桩里定的 cost_cents=12；用例从库里读，不从桩里读——断言要对的是落库的事实
    return {
        "job_id": job_id,
        "task_id": task_id,
        "reserved_points": created["reserved_points"],
    }


async def test_sn09_balance_actually_drops(db, finished_copy_job):
    job_id = finished_copy_job["job_id"]

    status = await db.fetchval("SELECT status FROM content_job WHERE id = $1", job_id)
    assert status == "ready", f"job 应跑完到 ready，实际 {status}"

    cost = await db.fetchval(
        "SELECT cost_cents FROM gen_output WHERE job_id = $1 AND is_active", job_id
    )
    assert cost == 12, f"产物成本取桩里的 12，实际 {cost}"

    account = await db.fetchrow(
        """
        SELECT a.balance, a.reserved, a.debt
        FROM quota_account a JOIN "user" u ON u.id = a.user_id
        WHERE u.account = '000001'
        """
    )
    assert account["balance"] == 2000 - cost, (
        f"用户余额应恰好少 cost_cents（2000 → {2000 - cost}），实际 {account['balance']}。"
        "这条是整个界面上计费展示的地基——余额不动的话，「预扣 / 消耗」就成了装饰"
    )
    assert account["reserved"] == 0, "结算后预占必须归零"
    assert account["debt"] == 0, "余额够，不该欠账"


async def test_sn10_one_consume_ledger_row_and_settled_reservation(
    db, finished_copy_job
):
    job_id = finished_copy_job["job_id"]
    user_id = await db.fetchval('SELECT id FROM "user" WHERE account = \'000001\'')

    ledger = await db.fetch(
        "SELECT change, source FROM quota_ledger WHERE job_id = $1 ORDER BY id", job_id
    )
    assert len(ledger) == 1, (
        f"一个 job 恰好一条流水，实际 {len(ledger)} 条：{ledger}。"
        "预扣不写流水、结算才写；多出来的通常是预扣那一步写错了"
    )
    assert ledger[0]["source"] == "consume", ledger[0]["source"]
    assert ledger[0]["change"] == -12, ledger[0]["change"]

    reservation = await db.fetchrow(
        "SELECT status, actual, units FROM quota_reservation WHERE job_id = $1", job_id
    )
    assert reservation["status"] == "settled", reservation["status"]
    assert reservation["actual"] == 12, reservation["actual"]
    assert reservation["units"] == 100, "units 要冻结下来，它是报销基数的唯一来源"

    # 流水的余额快照要对得上——对不上的话 07 的对账用例迟早会红
    balance_after = await db.fetchval(
        "SELECT balance_after FROM quota_ledger WHERE job_id = $1", job_id
    )
    balance = await db.fetchval(
        "SELECT balance FROM quota_account WHERE user_id = $1", user_id
    )
    assert balance_after == balance
