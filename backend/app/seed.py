"""种子数据：三个测试账号 + 一份「开箱可用」的演示数据。

    python -m app.seed

`APP_ENV=production` 时**拒绝执行**并退出——这三个账号的密码等于账号本身，
是公开的弱口令，绝不能出现在生产库里。生产环境的管理员只能由运维手工创建。

## 种子不只是「三个账号」

它要撑起手工验收的第一步：登录客户账号，页面上**立刻**有两条可选任务、
有余额、有「垫付 / 可报 / 池子还剩」可看。少了任务或领取，创作台的
「选任务」弹层就是空的，第一步就卡死。

## 计价表是**投影**，源在代码里

`model_price` 的五行全部来自 `app/services/pricing.py` 的常量。绕开库是行不通的：
`resolve_price` / `GET /api/models` / 预扣额三处都读库，且都有既存用例锁着。
**改价改代码，重跑种子**——所以本脚本对计价行是 upsert，不是「有就跳过」。
"""

import asyncio
import sys
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.config import get_settings
from app.core.security import hash_password
from app.db import Base, SessionLocal, engine
from app.models import (
    MerchantProfile,
    ModelPrice,
    QuotaAccount,
    RewardRule,
    Task,
    TaskClaim,
    User,
)
from app.services import pricing

# (account, password, role, nickname, shop_name)
SEED_ACCOUNTS = (
    ("000000", "000000", "merchant", "测试商户", "测试商户"),
    ("000001", "000001", "customer", "测试用户", None),
    ("999999", "999999", "admin", "测试管理员", None),
)

#: 演示账户的初始额度（点）。商户 5000、客户 2000。
#: 预扣上界是 300（`pricing.MAX_PRICE_PER_CALL`），2000 够跑 6 次——
#: 手工验收时不会第一次就撞余额不足。
MERCHANT_BALANCE = 5000
CUSTOMER_BALANCE = 2000

#: 两条演示任务，**刻意覆盖两种付费模式**：页面上「商户付费」与
#: 「用户垫付 / 可报销」两种口径都得看得见，否则第二种永远是死代码。
DEMO_TASKS = (
    {
        "title": "门店探店短视频征集",
        "description": "到店实拍一条 15 秒探店短视频，突出门店环境与招牌产品。",
        "category": "餐饮",
        "requirement": "竖屏 9:16，时长不少于 10 秒",
        "tags": ["探店", "短视频", "本地生活"],
        "pay_mode": "merchant_pay",
        "quota": 20,
        "reimburse_pool": None,
        "reimburse_per_user_limit": None,
    },
    {
        "title": "新品试吃图文笔记",
        "description": "试吃本店当季新品，写一篇带图的体验笔记并发布到小红书。",
        "category": "餐饮",
        "requirement": "正文不少于 100 字，配图不少于 3 张",
        "tags": ["新品", "图文", "小红书"],
        "pay_mode": "user_pay_reimburse",
        "quota": 20,
        # 池子 1000 / 单人 500：单人上限必须 >= 单次预扣上界（300），
        # 否则演示账号第一次建 job 就被自己的上限挡在门外
        "reimburse_pool": 1000,
        "reimburse_per_user_limit": 500,
    },
)

DEMO_MAX_REWARD = 20


def demo_tiers() -> list[dict]:
    """两档阶梯：0–49 发 5 元，50 以上发 20 元。

    末档必须开区间（`max=None`）——否则高互动量的作品无档可落。
    每次现造一份，免得两行 JSONB 共用同一个可变对象。
    """
    return [
        {"min": 0, "max": 49, "reward": {"cash": 5}},
        {"min": 50, "max": None, "reward": {"cash": 20}},
    ]


async def _quota_account(session, user_id: int, balance: int) -> bool:
    """建额度账户。已存在则不动（余额是会被消耗的，重跑种子不该把它冲回去）。"""
    exists = await session.scalar(
        select(QuotaAccount.user_id).where(QuotaAccount.user_id == user_id)
    )
    if exists is not None:
        return False

    # 四个 `*_limit` 显式留 None（= NULL，= 不设限额）。这一条踩过三次：
    # helper 少写列会让限额静默落成别的值，用例拿到的一直是「没设上限」。
    session.add(
        QuotaAccount(
            user_id=user_id,
            balance=balance,
            reserved=0,
            debt=0,
            status="active",
        )
    )
    await session.flush()
    return True


async def _prices(session) -> int:
    """把计价常量投影进 `model_price`，返回**新建**行数。

    upsert 而不是「有就跳过」：`PR-04` 要的就是「改常量 → 重跑种子 → 库里跟着变」。
    `effective_from` 是固定值（不是 `now()`），否则每次重跑都会插一批新行。
    """
    created = 0
    for row in pricing.seed_price_rows():
        existing = await session.scalar(
            select(ModelPrice).where(
                ModelPrice.provider == row["provider"],
                ModelPrice.model == row["model"],
                ModelPrice.op == row["op"],
                ModelPrice.effective_from == row["effective_from"],
            )
        )
        if existing is None:
            session.add(ModelPrice(**row))
            created += 1
        else:
            for key, value in row.items():
                setattr(existing, key, value)
    await session.flush()
    return created


async def _demo_tasks(session, merchant_id: int, customer_id: int) -> int:
    """写两条演示任务 + 各自的奖励规则 + 客户的领取。返回新建任务数。"""
    created = 0
    now = datetime.now(timezone.utc)

    for spec in DEMO_TASKS:
        exists = await session.scalar(select(Task.id).where(Task.title == spec["title"]))
        if exists is not None:
            continue

        task = Task(
            merchant_id=merchant_id,
            title=spec["title"],
            description=spec["description"],
            category=spec["category"],
            requirement=spec["requirement"],
            tags=spec["tags"],
            # 窗口挂在「跑种子那一刻」的 ±：既不早于创建时间，又立刻可领取
            start_at=now - timedelta(days=1),
            end_at=now + timedelta(days=30),
            quota=spec["quota"],
            claimed_count=1,
            pay_mode=spec["pay_mode"],
            reimburse_pool=spec["reimburse_pool"],
            reimburse_pool_used=0,
            reimburse_pool_reserved=0,
            reimburse_per_user_limit=spec["reimburse_per_user_limit"],
            status="published",
        )
        session.add(task)
        await session.flush()

        # 没有规则的任务发布不了，也没有奖励可算
        session.add(
            RewardRule(
                task_id=task.id,
                metric="engagement",
                tiers=demo_tiers(),
                max_reward_per_user=DEMO_MAX_REWARD,
            )
        )
        # 客户已领取：否则创作台的「选任务」弹层是空的，手工验收第一步就卡死
        session.add(
            TaskClaim(task_id=task.id, user_id=customer_id, status="in_progress")
        )
        created += 1

    await session.flush()
    return created


async def seed() -> int:
    """写种子数据。返回新建的数量。已存在的一律跳过，可重复执行（幂等）。"""
    created = 0
    async with SessionLocal() as session:
        by_account: dict[str, User] = {}
        for account, password, role, nickname, shop_name in SEED_ACCOUNTS:
            user = await session.scalar(select(User).where(User.account == account))
            if user is None:
                user = User(
                    account=account,
                    password_hash=hash_password(password),
                    role=role,
                    nickname=nickname,
                    status="active",
                )
                session.add(user)
                await session.flush()

                # spec：role=merchant 时 merchant_profile 必填
                if shop_name is not None:
                    session.add(
                        MerchantProfile(
                            user_id=user.id,
                            shop_name=shop_name,
                            category="餐饮",
                        )
                    )
                created += 1
            by_account[account] = user

        merchant_id = by_account["000000"].id
        customer_id = by_account["000001"].id

        for account, balance in (
            ("000000", MERCHANT_BALANCE),
            ("000001", CUSTOMER_BALANCE),
        ):
            if await _quota_account(session, by_account[account].id, balance):
                created += 1

        created += await _prices(session)
        created += await _demo_tasks(session, merchant_id, customer_id)

        await session.commit()
    return created


async def main() -> None:
    settings = get_settings()

    if settings.app_env == "production":
        print(
            "拒绝执行：APP_ENV=production 时不得写入种子账号"
            "（refuse to seed default-password accounts in production）。",
            file=sys.stderr,
        )
        raise SystemExit(1)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    created = await seed()
    print(f"种子完成：新建 {created} 条演示数据（APP_ENV={settings.app_env}）")
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
