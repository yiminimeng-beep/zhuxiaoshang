"""06-admin · `CC` 组：两个管理员同时操作同一个对象。

对应 test_plan.md 的 `CC-01` ~ `CC-03`。

## 为什么这里不需要 `asyncio.Barrier`

05 的 `PL-02` 打的是服务层纯函数（`grant_points`），裸 `gather` 下事件循环会把
第一个协程一路跑到 `commit()` 再切给第二个，两个事务根本不重叠——去掉行锁
照样绿，所以它必须用 `Barrier` 钉住那一刻。

06 的封禁 / 异常处理 / 裁决**没有可直调的服务函数**：判定与写入都在端点里，
事务从依赖注入起就已经是开的，两个请求天然重叠。这不是推断——去掉
`resolve_content` 的状态闸、去掉 appeal 的行锁，都稳定复现 `[200, 200]`。

⚠️ **诚实说明能力边界**：端点级并发能抓住「完全无锁」的实现，抓不住「锁加错了
列」这种微妙情形。所以：

- `CC-01`~`CC-03` 断言的是**与交错顺序无关的不变量**（成功数 == 1、
  日志行数 == 1），不写死谁先谁后；
- 真正的原子性由**实现**用条件 UPDATE / 行锁保证，那由 `EC-04` / `AB-04` /
  `AP-05` 这些**确定的**顺序用例兜底；
- 本组是**补充**，不是唯一防线。不要因为这一组绿了就认为并发是安全的。
"""

import asyncio

import pytest

from tests.helpers import (
    action_logs,
    admin_token,
    ban_user,
    bearer,
    decide_appeal,
    insert_appeal,
    insert_claim,
    insert_job,
    insert_post,
    insert_reward_rule,
    insert_task,
    make_tiers,
    resolve_content,
)

pytestmark = pytest.mark.asyncio


async def test_cc01_concurrent_resolve_one_wins(
    client, seed_accounts, merchant, customer, db
):
    """两个 admin 同时 `approve` 同一个 job → **恰好 1 个成功，另 1 个 `409`**。

    且 `admin_action_log` 只有 **1** 条 `resolve_content`——两次都记的话，
    审计会显示「管理员改了两次」，而实际上只有一次生效。
    """
    token = await admin_token(client)
    m_id, u_id = merchant[0]["id"], customer[0]["id"]
    task_id = await insert_task(db, m_id)
    claim_id = await insert_claim(db, task_id, u_id)
    job_id = await insert_job(db, task_id, claim_id, u_id, status="need_review")

    r1, r2 = await asyncio.gather(
        resolve_content(client, token, job_id, action="approve"),
        resolve_content(client, token, job_id, action="approve"),
    )
    codes = sorted([r1.status_code, r2.status_code])
    assert codes == [200, 409], (
        f"并发 approve 必须恰好 1 成功 1 冲突，实际 {codes}：{r1.text} / {r2.text}"
    )

    logs = await action_logs(db, target_type="content_job", target_id=job_id)
    assert len(logs) == 1, f"只有生效的那次该留痕，实际 {len(logs)} 条：{logs}"


async def test_cc02_concurrent_ban_one_wins(client, seed_accounts, customer, db):
    """两个 admin 同时 `ban` 同一用户 → 恰好 1 个 `200`、1 个 `409`。

    且 `admin_action_log` 恰好 **1** 条 `ban_user`（不是 2 条，也不是 0 条）。
    「2 条」说明幂等检查被并发穿过，「0 条」说明日志写在检查之前的分支里。
    """
    token = await admin_token(client)
    uid = customer[0]["id"]

    r1, r2 = await asyncio.gather(
        ban_user(client, token, uid),
        ban_user(client, token, uid),
    )
    codes = sorted([r1.status_code, r2.status_code])
    assert codes == [200, 409], (
        f"并发封禁必须恰好 1 成功 1 冲突，实际 {codes}：{r1.text} / {r2.text}"
    )

    logs = await action_logs(db, target_type="user", target_id=uid)
    assert len(logs) == 1, f"只有生效的那次该留痕，实际 {len(logs)} 条：{logs}"
    assert logs[0]["action"] == "ban_user", logs


async def test_cc03_concurrent_decide_one_wins(
    client, seed_accounts, merchant, customer, db
):
    """两个 admin 同时裁决同一条申诉 → **恰好 1 个成功，另 1 个 `409`**。

    裁决走的 `services.review.approve` **只看内存里的 `post.status`**（不查库、
    不加锁），所以拦住第二次的既可以是 appeal 行锁，也可以是 post 状态的
    二次校验——**两者都行，但必须有一个在**。两边都没有时两次都会读到
    `pending` / `appealed`，走两遍跃迁，两张日志表各多出一条。

    断言「与交错顺序无关」的量：成功数 == 1、`admin_action_log` == 1。
    """
    token = await admin_token(client)
    m_id, u_id = merchant[0]["id"], customer[0]["id"]
    task_id = await insert_task(db, m_id)
    await insert_reward_rule(db, task_id, tiers=make_tiers(3, reward={"points": 50}))
    claim_id = await insert_claim(db, task_id, u_id)
    job_id = await insert_job(db, task_id, claim_id, u_id, status="ready")
    post_id = await insert_post(
        db, claim_id, job_id, u_id, status="appealed", reject_reason="内容不符"
    )
    appeal_id = await insert_appeal(db, post_id, u_id)

    r1, r2 = await asyncio.gather(
        decide_appeal(client, token, appeal_id, action="accept"),
        decide_appeal(client, token, appeal_id, action="accept"),
    )
    codes = sorted([r1.status_code, r2.status_code])
    assert codes == [200, 409], (
        f"并发裁决必须恰好 1 成功 1 冲突，实际 {codes}：{r1.text} / {r2.text}"
    )

    logs = await action_logs(db, target_type="appeal", target_id=appeal_id)
    assert len(logs) == 1, f"只有生效的那次该留痕，实际 {len(logs)} 条：{logs}"
