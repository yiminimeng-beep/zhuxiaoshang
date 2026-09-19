"""额度账户的读取、预扣、结算与释放。

只有一条不变量值得反复念：**`available == balance - reserved`**。
`reserved` 是「已承诺但未发生」的钱，必须从可用额里扣掉——否则同一笔钱
既能预扣给一个 job，又能在别处被花掉。

**预扣不写流水**，只有结算与释放才写（见 spec「写流水的时机」）。这不是洁癖：
预扣是「打算花」，流水是「真的花了」。混在一起，对账时分不清一笔钱是已花
还是待花，日限额也会把没花的钱算进去。

`reimburse` 的触发点在 04（`social_post` 过审），故它由 04 的审核状态机调用，
但**规则属于 07**，实现留在这里。
"""

from datetime import datetime, timezone

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.quota import (
    ModelPrice,
    QuotaAccount,
    QuotaLedger,
    QuotaReservation,
    ReimburseClaim,
    ReimburseClaimJob,
)
from app.models.studio import PRIMARY_OP
from app.models.task import Task, TaskClaim
from app.models.tracking import APPROVED_STATUSES, SocialPost


def day_start(moment: datetime | None = None) -> datetime:
    """当日零点（**UTC**）。

    统一按 UTC 切日：服务器时区换一次，「今日消耗」就会跳一格，
    对账时最难解释的就是这种「数字自己变了」。取舍见 test_plan.md。
    """
    now = moment or datetime.now(timezone.utc)
    return now.astimezone(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )


async def load_account(session: AsyncSession, user_id: int) -> QuotaAccount | None:
    return await session.get(QuotaAccount, user_id)


async def ensure_account(session: AsyncSession, user_id: int) -> QuotaAccount:
    """取账户行，没有就建一行全 0 的（**不 commit**，交给调用方）。

    新用户不该因为「还没有账户行」而在首屏就看到 404。
    """
    account = await session.get(QuotaAccount, user_id)
    if account is not None:
        return account
    account = QuotaAccount(user_id=user_id, updated_at=datetime.now(timezone.utc))
    session.add(account)
    await session.flush()
    return account


async def today_consumed(session: AsyncSession, user_id: int) -> int:
    """当日 `consume` 流水的绝对值之和。"""
    from app.models.quota import QuotaLedger

    total = await session.scalar(
        select(func.coalesce(func.sum(-QuotaLedger.change), 0)).where(
            QuotaLedger.user_id == user_id,
            QuotaLedger.source == "consume",
            QuotaLedger.created_at >= day_start(),
        )
    )
    return int(total or 0)


async def spender_consumed(
    session: AsyncSession,
    spender_id: int,
    *,
    task_id: int | None = None,
    since: datetime | None = None,
) -> int:
    """按**消耗方**统计流水，供商户的「单用户」限额使用。

    必须按 `spender_id` 而不是 `user_id`：商户付费时 `user_id` 是商户，
    一个商户下所有用户会被算成一笔，限额形同虚设（`LM-04`）。
    """
    query = select(func.coalesce(func.sum(-QuotaLedger.change), 0)).where(
        QuotaLedger.spender_id == spender_id,
        QuotaLedger.source == "consume",
    )
    if task_id is not None:
        query = query.where(QuotaLedger.task_id == task_id)
    if since is not None:
        query = query.where(QuotaLedger.created_at >= since)
    total = await session.scalar(query)
    return int(total or 0)


def account_public(account: QuotaAccount | None, user_id: int) -> dict:
    """账户行的公开表示。缺行时给全 0，而不是 404。"""
    if account is None:
        return {
            "balance": 0,
            "reserved": 0,
            "available": 0,
            "debt": 0,
            "status": "active",
        }
    return {
        "balance": account.balance,
        "reserved": account.reserved,
        # 由后端算，不让前端做减法：前端算错了就会显示成「有钱」实则没有
        "available": account.balance - account.reserved,
        "debt": account.debt,
        "total_recharged": account.total_recharged,
        "total_consumed": account.total_consumed,
        "total_reimburse_in": account.total_reimburse_in,
        "total_reimburse_out": account.total_reimburse_out,
        "daily_limit": account.daily_limit,
        "per_user_daily_limit": account.per_user_daily_limit,
        "per_user_task_limit": account.per_user_task_limit,
        "user_daily_limit": account.user_daily_limit,
        "status": account.status,
    }


# --------------------------------------------------------------------------- #
# 计价：改价只插新行，故取「建 job 时刻生效」的那条
# --------------------------------------------------------------------------- #
async def resolve_price(
    session: AsyncSession,
    *,
    provider: str,
    model: str | None,
    op: str,
    moment: datetime | None = None,
    visible_only: bool = False,
) -> ModelPrice | None:
    """取 `effective_from <= moment` 里最新的一条计价行。

    **按建 job 时刻取**，不是按结算时刻——否则用户下单后运营改价，已建 job
    的预扣额会跟着变，前端展示与实际扣费就分家了（`JC-08` 盯的就是这里）。

    `visible_only` 只影响**自动选模型**这一条路径：`provider_visible=false`
    的行（灰度中、或已停售但仍有个别 key 在用）不该被自动选中。用户
    **指名道姓**要某个模型时不加这个过滤，再由调用方就 `provider_visible`
    单独判否——否则「指定了停售模型」会被报成「模型不存在」，两件事混一起。
    """
    now = moment or datetime.now(timezone.utc)
    query = select(ModelPrice).where(
        ModelPrice.provider == provider,
        ModelPrice.op == op,
        ModelPrice.effective_from <= now,
    )
    if model is not None:
        query = query.where(ModelPrice.model == model)
    if visible_only:
        query = query.where(ModelPrice.provider_visible.is_(True))
    query = query.order_by(ModelPrice.effective_from.desc(), ModelPrice.id.desc())
    return await session.scalar(query.limit(1))


# --------------------------------------------------------------------------- #
# 预扣 / 结算 / 释放
# --------------------------------------------------------------------------- #
async def lock_account(
    session: AsyncSession, user_id: int
) -> QuotaAccount | None:
    """拿账户行的行锁，返回**锁住的那一行**（完整 ORM 对象）；无行时 `None`。

    并发正确性全押在这里：`SELECT ... FOR UPDATE` 之后，同一付款方的建 job
    请求被串行化，后到者看到的是**别人刚提交的 reserved**，于是
    「100 可用、两个 60 并发」才会恰好一个成功（`RS-01`）。
    先查后判而不加锁，两个请求会同时读到 0，双双通过。

    先拿锁再 `get` 一次，而不是只取 `balance, reserved` 两列：调用方要的是
    整行（限额那几列也在上面），而 `populate_existing` 保证读到的是锁内视图
    而非 identity map 里的旧值。
    """
    row = await session.execute(
        text("SELECT balance, reserved FROM quota_account WHERE user_id = :uid FOR UPDATE"),
        {"uid": user_id},
    )
    if row.first() is None:
        return None
    return await session.get(QuotaAccount, user_id, populate_existing=True)


async def reserve(
    session: AsyncSession,
    *,
    job_id: int,
    payer_id: int,
    spender_id: int,
    task_id: int,
    billing_source: str,
    amount: int,
    reimburse_reserved: int = 0,
) -> QuotaReservation | None:
    """预扣：锁行 → 判定可用额 → `reserved += amount` → 落一张预扣单。

    失败返回 `None`（调用方据此决定 402 / 429），**且不留任何痕迹**——
    `JB-15` 断言被拒之后 `reserved` 不得改动、job 不得存在。
    """
    account = await lock_account(session, payer_id)
    if account is None or (account.balance - account.reserved) < amount:
        return None

    if amount > 0:
        await session.execute(
            text(
                "UPDATE quota_account SET reserved = reserved + :r, updated_at = now() "
                "WHERE user_id = :uid"
            ),
            {"r": amount, "uid": payer_id},
        )

    reservation = QuotaReservation(
        job_id=job_id,
        payer_id=payer_id,
        spender_id=spender_id,
        task_id=task_id,
        billing_source=billing_source,
        reserved=amount,
        reimburse_reserved=reimburse_reserved,
        status="reserved",
        created_at=datetime.now(timezone.utc),
    )
    session.add(reservation)
    await session.flush()
    return reservation


async def settle(
    session: AsyncSession,
    reservation: QuotaReservation,
    actual: int | None = None,
    *,
    units: int = 0,
) -> bool:
    """结算：`balance -= actual`、`reserved -= reserved`，写一条 `consume` 流水。

    幂等由两道闸把着：`reservation.status != "reserved"` 直接返回，
    以及 `quota_ledger` 的 `(source, ref_type, ref_id)` 唯一约束
    （`JC-09`：同一 job 重复投递 3 次，流水只多 1 条）。

    `units` 是本次调用的计量单位数，**结算时冻结**：报销基数要按成本价重算，
    而 BYOK 下 `actual` 是 0，只有 units 能还原「这台机器干了多少活」。
    """
    if reservation.status != "reserved":
        return False

    cost = reservation.reserved if actual is None else max(actual, 0)
    account = await lock_account(session, reservation.payer_id)
    balance = account.balance if account is not None else 0
    reserved = account.reserved if account is not None else 0

    # 结算超出预扣的部分进欠款，而不是把 balance 压成负数——
    # `ck_quota_account_balance` 会直接拒掉负数，届时整条链路一起回滚，
    # 用户看到的是「生成完了但账没结」，比欠款更难解释。
    payable = min(cost, balance)
    debt = cost - payable
    new_balance = balance - payable

    await session.execute(
        text(
            "UPDATE quota_account SET balance = :b, reserved = :r, debt = debt + :d, "
            "total_consumed = total_consumed + :c, updated_at = now() "
            "WHERE user_id = :uid"
        ),
        {
            "b": new_balance,
            "r": max(reserved - reservation.reserved, 0),
            "d": debt,
            "c": payable,
            "uid": reservation.payer_id,
        },
    )

    # 结算 0 元不写流水：0 元的行是噪声，且 BYOK 本就不该在平台账上留痕（JC-05）
    if payable > 0:
        session.add(
            QuotaLedger(
                user_id=reservation.payer_id,
                change=-payable,
                balance_after=new_balance,
                source="consume",
                ref_type="job",
                ref_id=reservation.job_id,
                job_id=reservation.job_id,
                task_id=reservation.task_id,
                spender_id=reservation.spender_id,
                billing_source=reservation.billing_source,
                created_at=datetime.now(timezone.utc),
            )
        )

    reservation.status = "settled"
    reservation.actual = cost
    reservation.units = max(units, 0)
    reservation.settled_at = datetime.now(timezone.utc)
    await session.flush()
    return True


async def release(session: AsyncSession, reservation: QuotaReservation) -> bool:
    """释放：把 `reserved` 原样退回，**不写流水**。

    释放是「这笔钱从没花过」，写一条 `change=0` 的流水只会让对账多一行噪声。
    审计靠 `quota_reservation.status` 就够（`JC-06` / `OW-14`）。
    """
    if reservation.status != "reserved":
        return False

    account = await lock_account(session, reservation.payer_id)
    if account is not None and reservation.reserved > 0:
        await session.execute(
            text(
                "UPDATE quota_account SET reserved = GREATEST(reserved - :r, 0), "
                "updated_at = now() WHERE user_id = :uid"
            ),
            {"r": reservation.reserved, "uid": reservation.payer_id},
        )

    if reservation.reimburse_reserved > 0:
        # 报销池的预占退回到任务上（池子总额由发布时锁的那笔兜底）
        await session.execute(
            text(
                "UPDATE task SET reimburse_pool_reserved = "
                "GREATEST(reimburse_pool_reserved - :p, 0) WHERE id = :tid"
            ),
            {"p": reservation.reimburse_reserved, "tid": reservation.task_id},
        )

    reservation.status = "released"
    await session.flush()
    return True


# --------------------------------------------------------------------------- #
# 报销：商户额度 → 用户额度（过审触发，只退平台额度，不退现金）
# --------------------------------------------------------------------------- #
async def release_reimburse(session: AsyncSession, *, job_id: int) -> bool:
    """把某个 job 认领的报销池份额还回去（驳回时用）。

    **只动 `task.reimburse_pool_reserved`，不动商户的 `reserved`。** 池子是
    「商户愿意为这个任务掏的上限」，驳回只是「这笔钱不欠这个用户了」，池子
    总额本身没变——所以商户侧那道整池锁定要留到任务关闭时才还
    （`merchant_task._release_pool`）。把两者搞混，一次驳回就会凭空放出额度。

    幂等靠把 `reimburse_reserved` 置 0：第二次调用直接返回 `False`。
    """
    reservation = await session.scalar(
        select(QuotaReservation).where(QuotaReservation.job_id == job_id)
    )
    if reservation is None or reservation.reimburse_reserved <= 0:
        return False

    await session.execute(
        text(
            "UPDATE task SET reimburse_pool_reserved = "
            "GREATEST(reimburse_pool_reserved - :p, 0) WHERE id = :tid"
        ),
        {"p": reservation.reimburse_reserved, "tid": reservation.task_id},
    )
    reservation.reimburse_reserved = 0
    await session.flush()
    return True


async def _base_points(session: AsyncSession, claim_id: int) -> list[tuple]:
    """该 claim 下**已结算且尚未报销**的 job 各自的基数。

    基数 = `cost_price_per_unit × units`（**成本价**，不含 markup）：
    与该用户实际付了多少、平台向商户收了多少都无关。这不只是洁癖——
    BYOK 下用户自己付了 provider，平台侧 `actual` 恒为 0，若按 `actual` 算，
    自带 Key 的用户永远报不了，而他明明垫了钱。

    计价行按 **job 创建时刻**取，与预扣同源（`resolve_price` 的 `moment`）。
    ⚠️ `content_job` 只记 `provider` 不记 `model`，故这里取该 provider+op 下
    当时生效的最后一条计价行——同一 provider 同一 op 若同时挂了多个模型，
    基数可能取的不是实际用的那个。落地时只有一个模型，暂不成问题。
    """
    rows = (
        await session.execute(
            text(
                "SELECT j.id, j.provider, j.kind, j.created_at, r.units, "
                "       r.reimburse_reserved "
                "FROM content_job j "
                "JOIN quota_reservation r ON r.job_id = j.id "
                "WHERE j.claim_id = :cid AND r.status = 'settled' "
                "AND NOT EXISTS ("
                "  SELECT 1 FROM reimburse_claim_job cj WHERE cj.job_id = j.id"
                ")"
            ),
            {"cid": claim_id},
        )
    ).all()

    priced: list[tuple[int, int, int]] = []
    for job_id, provider, kind, created_at, units, reserved in rows:
        price = await resolve_price(
            session,
            provider=provider,
            model=None,
            op=PRIMARY_OP.get(kind, "chat"),
            moment=created_at,
        )
        cost = price.cost_price_per_unit if price is not None else 0
        points = int(round(cost * int(units or 0)))
        priced.append((job_id, points, int(reserved or 0)))
    return priced


async def reimburse(session: AsyncSession, *, post_id: int) -> ReimburseClaim | None:
    """过审后的报销结算。返回落下的报销单；`None` = 这次不该报。

    `None` 有四种情况，调用方**不区分**（04 只负责把「这篇过审了」交出去）：
    没有这个 post / 状态不是过审 / 已经报过 / 任务不是 `user_pay_reimburse`。

    三重截断（spec「报销流程」）：

        covered = min(base, 单用户剩余额度, 池子可用额, 商户余额)

    前两项是产品规则，第三项是物理约束。**商户余额那一道不是多余的**：
    池子在发布时锁进了 `reserved`，但商户的 `balance` 可能因别的事被花掉，
    真扣到负数会被 `ck_quota_account_balance` 拦下，整条链路一起回滚——
    用户看到的是「过审了但钱没到」，比少报一点更难查。

    池子可用额取 `pool − used`（**不减去 `reserved`**）：这个 job 自己的预占
    就算在 `reserved` 里，减掉等于把自己的额度也排除在外，那笔钱就永远报不
    出来了。发布时已保证 `pool − used ≥ 所有未结预占之和`，故不会超发。
    """
    post = await session.get(SocialPost, post_id)
    if post is None or post.status not in APPROVED_STATUSES:
        return None

    already = await session.scalar(
        select(ReimburseClaim.id).where(ReimburseClaim.post_id == post_id)
    )
    if already is not None:
        return None

    claim = await session.get(TaskClaim, post.claim_id)
    task = await session.get(Task, claim.task_id) if claim is not None else None
    if task is None or task.pay_mode != "user_pay_reimburse":
        return None

    merchant_id = task.merchant_id
    user_id = post.user_id

    merchant = await lock_account(session, merchant_id)
    if merchant is None:
        return None
    user_account = await lock_account(session, user_id)
    if user_account is None:
        user_account = await ensure_account(session, user_id)
        user_account = await lock_account(session, user_id)

    priced = await _base_points(session, claim.id)
    base = sum(points for _, points, _ in priced)

    per_user_limit = task.reimburse_per_user_limit or 0
    already_reimbursed = int(
        await session.scalar(
            select(func.coalesce(func.sum(ReimburseClaim.covered_points), 0)).where(
                ReimburseClaim.user_id == user_id,
                ReimburseClaim.task_id == task.id,
                ReimburseClaim.status != "released",
            )
        )
        or 0
    )
    per_user_remaining = max(per_user_limit - already_reimbursed, 0)
    pool_available = max(
        (task.reimburse_pool or 0) - (task.reimburse_pool_used or 0), 0
    )

    covered = max(min(base, per_user_remaining, pool_available, merchant.balance), 0)
    if covered == base:
        status, reason = "settled", None
    elif pool_available <= 0:
        status, reason = "pool_exhausted", "报销池已用尽"
    else:
        status, reason = "capped", (
            f"基数 {base}，实报 {covered}"
            f"（单用户剩余 {per_user_remaining}、池子可用 {pool_available}、"
            f"商户余额 {merchant.balance}）"
        )

    now = datetime.now(timezone.utc)
    row = ReimburseClaim(
        post_id=post.id,
        claim_id=claim.id,
        task_id=task.id,
        merchant_id=merchant_id,
        user_id=user_id,
        base_points=base,
        covered_points=covered,
        status=status,
        reason=reason,
        created_at=now,
        settled_at=now,
    )
    session.add(row)
    await session.flush()

    for job_id, points, _ in priced:
        session.add(
            ReimburseClaimJob(
                reimburse_claim_id=row.id, job_id=job_id, points=points
            )
        )

    if covered > 0:
        merchant_after = merchant.balance - covered
        user_after = user_account.balance + covered
        await session.execute(
            text(
                "UPDATE quota_account SET balance = :b, "
                "reserved = GREATEST(reserved - :c, 0), "
                "total_reimburse_out = total_reimburse_out + :c, updated_at = now() "
                "WHERE user_id = :uid"
            ),
            {"b": merchant_after, "c": covered, "uid": merchant_id},
        )
        await session.execute(
            text(
                "UPDATE quota_account SET balance = :b, "
                "total_reimburse_in = total_reimburse_in + :c, updated_at = now() "
                "WHERE user_id = :uid"
            ),
            {"b": user_after, "c": covered, "uid": user_id},
        )
        # 两条流水互为 counterparty：对账时要能从任一端正查到另一端，
        # 靠 (source, ref_type, ref_id) 唯一约束兜住重放
        session.add(
            QuotaLedger(
                user_id=merchant_id,
                change=-covered,
                balance_after=merchant_after,
                source="reimburse_out",
                ref_type="reimburse_claim",
                ref_id=row.id,
                task_id=task.id,
                spender_id=user_id,
                counterparty_id=user_id,
                created_at=now,
            )
        )
        session.add(
            QuotaLedger(
                user_id=user_id,
                change=covered,
                balance_after=user_after,
                source="reimburse_in",
                ref_type="reimburse_claim",
                ref_id=row.id,
                task_id=task.id,
                spender_id=user_id,
                counterparty_id=merchant_id,
                created_at=now,
            )
        )

    # 池子口径：已用 +covered，已认领 −该 claim 吃掉的预占。
    # 商户侧 `reserved` 同步减 covered，使 `reserved == pool − used` 始终成立，
    # 任务关闭时 `_release_pool` 归还 `pool − used` 正好把锁放干净。
    consumed_reserved = sum(reserved for _, _, reserved in priced)
    await session.execute(
        text(
            "UPDATE task SET reimburse_pool_used = reimburse_pool_used + :c, "
            "reimburse_pool_reserved = "
            "GREATEST(reimburse_pool_reserved - :r, 0) WHERE id = :tid"
        ),
        {"c": covered, "r": consumed_reserved, "tid": task.id},
    )
    await session.flush()
    return row
