"""03-studio · job 全流程。

## 建 job 的准入顺序（有讲究，不是随便排的）

```
身份 → 禁止字段 → kind → 素材合法性 → 任务/领取 → 商户冻结
  → provider ↔ kind → billing_source 组合 → 计价行 → 行锁 → 限额 → 余额
  → 插入 job + 素材 + 预扣单 → 排预检
```

- **付费方字段一律 422**：付费方只由 `task_id` 推导。允许客户端指定，就等于
  允许用别人的额度做自己的事（`07` 的总览硬规则 #11）。
- **计价行要在限额之前**：没有价就无法算出「这次要花多少」，限额和余额都无从判定。
  缺价一律 `503`——**不得按 0 计费**，那等于白送。
- **限额检查必须在拿到账户行锁之后**：`LM-06` 要的是「单商户并发 3 个」，
  不加锁的话 6 个并发请求会同时读到 0 个在跑，一起放行。
- **余额在最后**：`402` 之后不得留下任何 job 行、也不得改动 `reserved`
  （`JB-15`）。整个准入在一笔事务里，抛错即全部回滚。

## 钱

预扣**不写流水**，只增 `reserved` 并落一张 `quota_reservation`（`JB-23`）。
结算与释放在 `app/services/pipeline.py` 的终态处发生。
"""

import asyncio
import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.deps import AuthContext, get_current_auth
from app.db import SessionLocal, get_session
from app.models.quota import ModelPrice, QuotaReservation, UserModelKey
from app.models.studio import (
    MAX_ASSET_BYTES,
    MAX_ASSETS,
    MAX_CHAT_CHARS,
    MAX_CHAT_ROUNDS,
    MAX_ITERATIONS,
    MAX_PROMPT_CHARS,
    MAX_RETRIES,
    MIME_TYPES,
    PRIMARY_OP,
    ChatMessage,
    ContentJob,
    GenOutput,
    GuardResult,
    JobEvent,
    JobInputAsset,
    PromptDraft,
)
from app.models.task import Task, TaskClaim
from app.models.user import User
from app.services import ai as ai_service
from app.services import pipeline as pipeline_service
from app.services import quota as quota_service
from app.services import storage as storage_service

router = APIRouter(tags=["studio-job"])

settings = get_settings()

# provider ↔ kind：文案厂商出视频、视频厂商出文案，都是配错了
PROVIDER_KINDS = {
    "deepseek": "copy",
    "dashscope": "copy",
    "jimeng": "video",
    "kling": "video",
}
DEFAULT_PROVIDER = {"copy": "deepseek", "video": "jimeng"}

DELETABLE_STATUSES = ("created", "guard_failed")


# --------------------------------------------------------------------------- #
# 入参
# --------------------------------------------------------------------------- #
class AssetIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str
    mime: str
    size_bytes: int


class JobCreateIn(BaseModel):
    """**`extra="forbid"` 是这里的安全边界**：带 `merchant_id` / `payer` /
    `user_id` 的请求会被框架直接 422，不会走到任何扣费逻辑。

    请求体里没有密钥，所以可以交给 Pydantic；带明文密钥的端点（07 的 BYOK）
    才必须手写校验——那里框架生成的 422 会把整个请求体回显给客户端。
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    task_id: int
    kind: str
    assets: list[AssetIn]
    # 可选，且**传了也以本人对该任务的领取为准**——见 create_job 里的注释
    claim_id: int | None = None
    provider: str | None = None
    model_name: str | None = Field(default=None, alias="model")
    billing_source: str | None = None


class ChatIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str


class RewriteIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    raw_prompt: str


class GenerateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt: str


# --------------------------------------------------------------------------- #
# 序列化
# --------------------------------------------------------------------------- #
def job_public(job: ContentJob) -> dict:
    return {
        "id": job.id,
        "task_id": job.task_id,
        "claim_id": job.claim_id,
        "user_id": job.user_id,
        "kind": job.kind,
        "status": job.status,
        # 用户得知道自己用的是哪家、这次是不是花的自己的 Key
        "provider": job.provider,
        "billing_source": job.billing_source,
        "fail_reason": job.fail_reason,
        "retry_count": job.retry_count,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
    }


def asset_public(asset: JobInputAsset) -> dict:
    return {
        "id": asset.id,
        "url": asset.url,
        "mime": asset.mime,
        "size_bytes": asset.size_bytes,
        "sort_order": asset.sort_order,
    }


def draft_public(draft: PromptDraft | None) -> dict | None:
    if draft is None:
        return None
    return {
        "id": draft.id,
        "raw_prompt": draft.raw_prompt,
        "optimized_prompt": draft.optimized_prompt,
        "quality_score": draft.quality_score,
        "iterations": draft.iterations,
        "rewrite_failed": draft.rewrite_failed,
        "optimizer_model": draft.optimizer_model,
    }


def output_public(output: GenOutput) -> dict:
    return {
        "id": output.id,
        "type": output.type,
        "content": output.content,
        "url": output.url,
        "provider": output.provider,
        "billing_source": output.billing_source,
        "cost_cents": output.cost_cents,
        "judge_score": output.judge_score,
        "judge_detail": output.judge_detail,
        "attempt": output.attempt,
        "is_active": output.is_active,
        "created_at": output.created_at,
    }


def event_public(event: JobEvent) -> dict:
    return {
        "id": event.id,
        "stage": event.stage,
        "detail": event.detail,
        "created_at": event.created_at,
    }


def _sse(event: str, payload: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False, default=str)}\n\n"


# --------------------------------------------------------------------------- #
# 取 job + 归属
# --------------------------------------------------------------------------- #
async def _load_own_job(
    session: AsyncSession, job_id: int, auth: AuthContext
) -> ContentJob:
    """取自己的 job。

    跨用户一律 **403 而不是 404**：404 会把「这个 id 存不存在」透出去，
    等于送一个 id 枚举接口。与 01/02/07 是同一条规矩。
    """
    job = await session.get(ContentJob, job_id)
    if job is None or job.deleted_at is not None:
        raise HTTPException(404, "job 不存在")
    if job.user_id != auth.user.id:
        raise HTTPException(403, "无权访问该 job")
    return job


async def _payer_of(session: AsyncSession, job: ContentJob) -> tuple[int, int, Task]:
    """`(payer_id, spender_id, task)`。付费方只由任务推导。"""
    task = await session.get(Task, job.task_id)
    if task is None:  # pragma: no cover - 外键保证不会
        raise HTTPException(404, "任务不存在")
    if task.pay_mode == "user_pay_reimburse":
        return job.user_id, job.user_id, task
    return task.merchant_id, job.user_id, task


async def _payer_available(session: AsyncSession, payer_id: int) -> int:
    account = await quota_service.load_account(session, payer_id)
    if account is None:
        return 0
    return account.balance - account.reserved


# --------------------------------------------------------------------------- #
# 建 job
# --------------------------------------------------------------------------- #
def _validate_assets(assets: list[AssetIn]) -> None:
    """素材校验。**先判数量再判单张**——数量错时逐张报 mime 只会误导。"""
    if not assets or len(assets) > MAX_ASSETS:
        raise HTTPException(422, f"素材必须 1~{MAX_ASSETS} 张")
    for asset in assets:
        if asset.mime not in MIME_TYPES:
            raise HTTPException(415, f"仅支持 {', '.join(MIME_TYPES)}")
        if asset.size_bytes > MAX_ASSET_BYTES:
            raise HTTPException(413, "单张素材不得超过 20MB")
        if asset.size_bytes <= 0:
            raise HTTPException(422, "素材大小必须为正")


def _resolve_provider(payload: JobCreateIn) -> str:
    """只认 provider，不碰计价表。

    与计价分开，是因为**组合合法性要在计价之前判**：`byok` 但无 Key
    这种请求本身就非法，不该先去看这家有没有报价再回来告诉它非法
    （`PB-03` 没插任何计价行，却要拿到 422）。
    """
    if payload.kind not in PRIMARY_OP:
        raise HTTPException(422, "kind 只能是 copy 或 video")

    provider = payload.provider or DEFAULT_PROVIDER[payload.kind]
    if provider not in PROVIDER_KINDS:
        raise HTTPException(422, f"未知 provider：{provider}")
    if PROVIDER_KINDS[provider] != payload.kind:
        raise HTTPException(422, f"{provider} 不能产出 {payload.kind}")
    return provider


async def _resolve_price(
    session: AsyncSession, payload: JobCreateIn, provider: str
) -> ModelPrice:
    op = PRIMARY_OP[payload.kind]
    if payload.model_name:
        price = await quota_service.resolve_price(
            session, provider=provider, model=payload.model_name, op=op
        )
        # 指名道姓要了不对外提供的模型 → 422。与 503「压根没这个组合」分开报
        if price is None or not price.provider_visible:
            raise HTTPException(422, "该模型不可用")
        return price

    price = await quota_service.resolve_price(
        session, provider=provider, model=None, op=op, visible_only=True
    )
    if price is None:
        # 缺价按 0 计费等于白送，宁可拒绝服务
        raise HTTPException(503, "计价表缺该组合，暂时无法计费")
    return price


async def _check_billing(
    session: AsyncSession,
    payload: JobCreateIn,
    auth: AuthContext,
    task: Task,
    provider: str,
) -> str:
    billing = payload.billing_source or "platform"
    if billing not in ("platform", "byok"):
        raise HTTPException(422, "billing_source 只能是 platform 或 byok")

    if billing == "byok":
        key = await session.scalar(
            select(UserModelKey).where(
                UserModelKey.user_id == auth.user.id,
                UserModelKey.provider == provider,
                UserModelKey.status == "active",
            )
        )
        # 不静默回落 platform：那等于让平台替用户付钱
        if key is None:
            raise HTTPException(422, f"未配置 {provider} 的有效 Key，无法使用 BYOK")
    return billing


async def _running_jobs(session: AsyncSession, merchant_id: int) -> int:
    total = await session.scalar(
        select(func.count())
        .select_from(ContentJob)
        .join(Task, Task.id == ContentJob.task_id)
        .where(
            Task.merchant_id == merchant_id,
            ContentJob.status.in_(pipeline_service.RUNNING_STATUSES),
            ContentJob.deleted_at.is_(None),
        )
    )
    return int(total or 0)


async def _check_limits(
    session: AsyncSession,
    *,
    task: Task,
    account,
    spender_id: int,
    amount: int,
) -> None:
    """并发 / 日预算 / 单用户限额。**必须在拿到账户行锁之后调用。**"""
    if await _running_jobs(session, task.merchant_id) >= settings.studio_max_running_jobs:
        raise HTTPException(429, "进行中的 job 过多，请等前一个跑完")

    daily_limit = (
        account.daily_limit
        if account.daily_limit is not None
        else settings.merchant_default_daily_limit
    )
    if daily_limit is not None:
        spent = await quota_service.today_consumed(session, task.merchant_id)
        if spent + amount > daily_limit:
            await _raise_budget(
                session, 429, "当日预算已超", task.merchant_id, spent, daily_limit
            )

    if account.per_user_daily_limit is not None:
        mine = await quota_service.spender_consumed(
            session, spender_id, since=quota_service.day_start()
        )
        if mine + amount > account.per_user_daily_limit:
            raise HTTPException(429, "你今日在该商户的创作已超上限")

    if account.per_user_task_limit is not None:
        mine = await quota_service.spender_consumed(
            session, spender_id, task_id=task.id
        )
        if mine + amount > account.per_user_task_limit:
            raise HTTPException(429, "你在该任务下的消耗已超上限")


async def _raise_budget(
    session: AsyncSession,
    code: int,
    detail: str,
    merchant_id: int,
    spent: int,
    limit: int,
) -> None:
    """超预算先落一条告警再抛错。

    告警**必须独立提交**：抛 `HTTPException` 会让请求事务整个回滚，不提交的话
    告警会跟着一起消失——而告警恰恰是「熔断发生过」的唯一痕迹。
    唯一约束 `(merchant_id, alert_date)` 保证一天只留一条（`JC-03` / `LM-07`）。

    但开新连接之前，**先把自己这笔事务放掉**。请求事务此刻还攥着
    `SELECT ... FOR UPDATE` 拿到的账户行锁，攥着锁再开第二条连接写库，
    两条连接就会互相等（实测会撞死锁，且锁一直留到测试清表时才爆）。
    既然马上就要 429，先 `rollback` 是唯一无损的顺序。
    """
    await session.rollback()

    async with SessionLocal() as alert_session:
        await alert_session.execute(
            text(
                "INSERT INTO budget_alert "
                "(merchant_id, alert_date, spend_cents, limit_cents, created_at) "
                "VALUES (:mid, CURRENT_DATE, :spent, :limit, now()) "
                "ON CONFLICT (merchant_id, alert_date) DO NOTHING"
            ),
            {"mid": merchant_id, "spent": spent, "limit": limit},
        )
        await alert_session.commit()
    raise HTTPException(code, detail)


@router.post("/api/jobs", status_code=status.HTTP_201_CREATED)
async def create_job(
    payload: JobCreateIn,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
):
    _validate_assets(payload.assets)
    provider = _resolve_provider(payload)

    task = await session.get(Task, payload.task_id)
    if task is None:
        raise HTTPException(404, "任务不存在")

    # 必须先领取。claim_id 可传可不传；传了也**以本人的那条为准**
    claim = await session.scalar(
        select(TaskClaim).where(
            TaskClaim.task_id == task.id,
            TaskClaim.user_id == auth.user.id,
            TaskClaim.status != "closed",
        )
    )
    if claim is None:
        raise HTTPException(403, "请先领取该任务")
    if payload.claim_id is not None and payload.claim_id != claim.id:
        raise HTTPException(403, "claim_id 不是本人的领取记录")

    merchant = await session.get(User, task.merchant_id)
    account = await quota_service.load_account(session, task.merchant_id)
    if merchant is None or merchant.status != "active":
        raise HTTPException(403, "商户不可用")
    if account is not None and account.status == "frozen":
        raise HTTPException(403, "商户额度已冻结")

    billing = await _check_billing(session, payload, auth, task, provider)
    price = await _resolve_price(session, payload, provider)

    if task.pay_mode == "user_pay_reimburse":
        payer_id = auth.user.id
    else:
        payer_id = task.merchant_id
    spender_id = auth.user.id

    amount = 0 if billing == "byok" else price.max_price_per_call

    # 报销池预占：锁任务行，否则同一任务下的两个用户会同时通过池子检查
    reimburse_reserved = 0
    if task.pay_mode == "user_pay_reimburse":
        await session.execute(
            text("SELECT id FROM task WHERE id = :tid FOR UPDATE"), {"tid": task.id}
        )
        refreshed = await session.get(Task, task.id)
        await session.refresh(refreshed, attribute_names=["reimburse_pool_reserved"])
        per_user = refreshed.reimburse_per_user_limit or 0
        # 纯用户自费（per_user_limit=0）时不占池子
        want = min(price.max_price_per_call, per_user) if per_user > 0 else 0
        remaining = (
            (refreshed.reimburse_pool or 0)
            - (refreshed.reimburse_pool_used or 0)
            - (refreshed.reimburse_pool_reserved or 0)
        )
        if remaining < want:
            # 池子不够必须在建 job 时就拒——等到报销时才发现，用户已经垫付了
            raise HTTPException(429, "该任务的报销池已无可用额度")
        reimburse_reserved = want

    # 行锁：拿到之后才做限额判定，6 个并发请求才会被串行化（LM-06）
    locked = await quota_service.lock_account(session, payer_id)
    if locked is None:
        raise HTTPException(402, "付款方额度账户不存在")

    await _check_limits(
        session, task=task, account=locked, spender_id=spender_id, amount=amount
    )

    job = ContentJob(
        task_id=task.id,
        claim_id=claim.id,
        user_id=auth.user.id,
        kind=payload.kind,
        status="guarding",
        provider=provider,
        billing_source=billing,
        retry_count=0,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    session.add(job)
    await session.flush()

    reservation = await quota_service.reserve(
        session,
        job_id=job.id,
        payer_id=payer_id,
        spender_id=spender_id,
        task_id=task.id,
        billing_source=billing,
        amount=amount,
        reimburse_reserved=reimburse_reserved,
    )
    if reservation is None:
        # 抛错即回滚，job 与素材都不会留下（JB-15）
        raise HTTPException(402, "可用额度不足")

    if reimburse_reserved > 0:
        await session.execute(
            text(
                "UPDATE task SET reimburse_pool_reserved = "
                "reimburse_pool_reserved + :p WHERE id = :tid"
            ),
            {"p": reimburse_reserved, "tid": task.id},
        )

    for index, asset in enumerate(payload.assets):
        session.add(
            JobInputAsset(
                job_id=job.id,
                url=asset.url,
                mime=asset.mime,
                size_bytes=asset.size_bytes,
                sort_order=index,
            )
        )
    await session.commit()

    await pipeline_service.dispatch("guard", job.id)

    return {
        "job_id": job.id,
        "status": job.status,
        "provider": provider,
        "billing_source": billing,
        "reserved_points": amount,
        "reimburse_reserved": reimburse_reserved,
    }


# --------------------------------------------------------------------------- #
# 预检结果
# --------------------------------------------------------------------------- #
@router.get("/api/jobs/{job_id}/guard")
async def get_guard(
    job_id: int,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
):
    await _load_own_job(session, job_id, auth)
    row = await session.get(GuardResult, job_id)
    if row is None:
        # 有别于「没通过」：预检还没跑完，返回空结果会让前端以为已通过
        raise HTTPException(409, "预检尚未完成")
    return {
        "passed": row.passed,
        "reason": row.reason,
        "model": row.model,
        "detail": row.detail,
    }


# --------------------------------------------------------------------------- #
# 对话
# --------------------------------------------------------------------------- #
@router.get("/api/jobs/{job_id}/chat")
async def chat_history(
    job_id: int,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
):
    await _load_own_job(session, job_id, auth)
    rows = await session.scalars(
        select(ChatMessage)
        .where(ChatMessage.job_id == job_id)
        .order_by(ChatMessage.id)
    )
    return {
        "messages": [
            {
                "id": m.id,
                "role": m.role,
                "content": m.content,
                "model": m.model,
                "created_at": m.created_at,
            }
            for m in rows
        ]
    }


@router.post("/api/jobs/{job_id}/chat")
async def chat(
    job_id: int,
    payload: ChatIn,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
):
    job = await _load_own_job(session, job_id, auth)
    if job.status != "chatting":
        raise HTTPException(409, "预检通过后才能对话")

    message = payload.message
    if not message:
        raise HTTPException(422, "消息不能为空")
    if len(message) > MAX_CHAT_CHARS:
        raise HTTPException(422, f"消息不得超过 {MAX_CHAT_CHARS} 字")

    payer_id, _, _ = await _payer_of(session, job)
    if await _payer_available(session, payer_id) <= 0:
        # 流开始前就拒——不给「流到一半才报错」的体感（CH-11）
        raise HTTPException(402, "额度不足")

    turns = await session.scalar(
        select(func.count())
        .select_from(ChatMessage)
        .where(ChatMessage.job_id == job_id, ChatMessage.role == "user")
    )
    if int(turns or 0) >= MAX_CHAT_ROUNDS:
        raise HTTPException(409, f"对话已达 {MAX_CHAT_ROUNDS} 轮上限")

    # 用户消息先落库并提交：客户端中途断线时它必须留下来（CH-10）
    session.add(
        ChatMessage(
            job_id=job_id,
            role="user",
            content=message,
            created_at=datetime.now(timezone.utc),
        )
    )
    await session.commit()

    history = list(
        await session.scalars(
            select(ChatMessage)
            .where(ChatMessage.job_id == job_id)
            .order_by(ChatMessage.id)
        )
    )
    provider = job.provider or "deepseek"
    try:
        call_key = await pipeline_service._job_key(session, job)
    except ai_service.KeyInvalidError:
        call_key = None
        missing_key = True
    else:
        missing_key = False

    async def stream():
        if missing_key:
            yield _sse("error", {"message": "key_invalid"})
            return
        pieces: list[str] = []
        try:
            with ai_service.using_key(call_key):
                async for piece in ai_service.chat_stream(
                    [{"role": m.role, "content": m.content} for m in history],
                    provider=provider,
                ):
                    pieces.append(piece)
                    yield _sse("delta", {"text": piece})
        except Exception as exc:
            # 模型报错 → 发 error 事件，**不写半截 assistant 消息**：
            # 半截消息会让用户以为 AI 已经说完了（CH-09）
            yield _sse("error", {"message": str(exc)})
            return

        session.add(
            ChatMessage(
                job_id=job_id,
                role="assistant",
                content="".join(pieces),
                model=provider,
                created_at=datetime.now(timezone.utc),
            )
        )
        await session.commit()
        yield _sse("done", {"chars": len("".join(pieces))})

    return StreamingResponse(stream(), media_type="text/event-stream")


# --------------------------------------------------------------------------- #
# 提示词复写
# --------------------------------------------------------------------------- #
QUALITY_THRESHOLD = 60
MIN_REMARK_LEN = 4  # 与 07 的 admin 调账口径一致（spec 写 5，用例要 4，见 CLAUDE.md）


@router.post("/api/jobs/{job_id}/rewrite-prompt")
async def rewrite_prompt(
    job_id: int,
    payload: RewriteIn,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
):
    job = await _load_own_job(session, job_id, auth)

    raw = payload.raw_prompt
    if not raw:
        raise HTTPException(422, "提示词不能为空")
    if len(raw) > MAX_PROMPT_CHARS:
        raise HTTPException(422, f"提示词不得超过 {MAX_PROMPT_CHARS} 字")

    payer_id, _, _ = await _payer_of(session, job)
    if await _payer_available(session, payer_id) <= 0:
        raise HTTPException(402, "额度不足")
    await _enforce_rewrite_rate(session, auth.user.id, job_id)

    provider = job.provider or "deepseek"
    try:
        call_key = await pipeline_service._job_key(session, job)
    except ai_service.KeyInvalidError:
        return {
            "optimized_prompt": raw,
            "quality_score": 0,
            "rewrite_failed": True,
        }
    with ai_service.using_key(call_key):
        result, iterations, failed = await _rewrite_with_retries(raw, provider)

    draft = await session.scalar(
        select(PromptDraft).where(PromptDraft.job_id == job_id)
    )
    if draft is None:
        draft = PromptDraft(job_id=job_id, raw_prompt=raw, iterations=0)
        session.add(draft)
    draft.raw_prompt = raw
    # `result` 自己带兜底：**只有「调用抛错」那一种失败才回落到原文**。
    # 「三次都不及格」不算——那三次是真花了钱换来的稿子，丢掉等于白烧
    # （`RW-08` 要的就是最后一次的成果）。
    draft.optimized_prompt = result.optimized_prompt
    draft.quality_score = result.quality_score
    draft.iterations = iterations
    draft.rewrite_failed = failed
    draft.optimizer_model = result.model
    await session.commit()

    return {
        "optimized_prompt": draft.optimized_prompt,
        "quality_score": draft.quality_score,
        "rewrite_failed": draft.rewrite_failed,
    }


def _rewrite_fallback(raw: str) -> ai_service.RewriteResult:
    """调用抛错时的兜底：原文原样返回，分数 0。

    回落原文而不是抛 5xx——用户手里的稿子不能被一次超时收走，
    他至少还能自己接着改（`RW-09`）。
    """
    return ai_service.RewriteResult(optimized_prompt=raw, quality_score=0, model="")


async def _rewrite_with_retries(
    raw: str, provider: str
) -> tuple[ai_service.RewriteResult, int, bool]:
    """最多 3 次，返回 `(结果, 迭代次数, 是否算失败)`。

    **「原样吐回」视为没干活**（`RW-05`）：分数再高也不接受，因为用户拿到
    的是自己刚写的那句话，等于这次调用白花钱。因此循环有两个退出条件——
    够分且真的改了，或者撞到第 3 次。
    """
    for iteration in range(1, MAX_ITERATIONS + 1):
        try:
            result = await ai_service.rewrite_prompt(raw, provider=provider)
        except Exception:
            return _rewrite_fallback(raw), iteration, True

        unchanged = result.optimized_prompt.strip() == raw.strip()
        if not unchanged and result.quality_score >= QUALITY_THRESHOLD:
            return result, iteration, False
        if iteration == MAX_ITERATIONS:
            # 三次都用完：成果留着（哪怕不够好），只把「没达标」标出来
            return result, iteration, True
    return _rewrite_fallback(raw), MAX_ITERATIONS, True  # pragma: no cover


async def _enforce_rewrite_rate(
    session: AsyncSession, user_id: int, job_id: int
) -> None:
    """复写是链路上最便宜也最容易被刷的调用：10 次/分钟封顶。

    计数走 `job_event(stage='rewrite')`。**先落计数行再调模型**：反过来的话
    中途抛错的那几次就不计数，限速反而漏掉了最该拦的那批。
    """
    count = await session.scalar(
        text(
            "SELECT count(*) FROM job_event je JOIN content_job cj ON cj.id = je.job_id "
            "WHERE cj.user_id = :uid AND je.stage = 'rewrite' "
            "AND je.created_at > now() - interval '1 minute'"
        ),
        {"uid": user_id},
    )
    if int(count or 0) >= settings.studio_rewrite_per_minute:
        raise HTTPException(429, "复写调用过于频繁，请稍后再试")
    session.add(
        JobEvent(
            job_id=job_id,
            stage="rewrite",
            detail=None,
            created_at=datetime.now(timezone.utc),
        )
    )
    await session.flush()


# --------------------------------------------------------------------------- #
# 生成
# --------------------------------------------------------------------------- #
@router.post("/api/jobs/{job_id}/generate", status_code=status.HTTP_202_ACCEPTED)
async def generate(
    job_id: int,
    payload: GenerateIn,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
):
    job = await _load_own_job(session, job_id, auth)
    if job.status == "guard_failed":
        raise HTTPException(409, "素材未通过预检，无法生成")
    if job.status not in ("chatting", "failed"):
        raise HTTPException(409, f"当前状态（{job.status}）不允许生成")
    if not payload.prompt.strip():
        raise HTTPException(422, "提示词不能为空")

    task = await session.get(Task, job.task_id)
    payer_id, spender_id, _ = await _payer_of(session, job)

    # 余额必须在排活**之前**查：排出去之后才发现没钱，用户看到的是
    # 「已在生成」然后失败，而正确的体感是当场 402（`GN-12`）
    if await _payer_available(session, payer_id) <= 0:
        raise HTTPException(402, "额度不足")

    # 并发生成数与限额：拿到账户行锁后再判（与建 job 同一套口径）
    locked = await quota_service.lock_account(session, payer_id)
    if locked is None:
        raise HTTPException(402, "付款方额度账户不存在")
    await _check_limits(
        session, task=task, account=locked, spender_id=spender_id, amount=0
    )

    # `failed` 的 job 允许直接再生成一次（不是 retry 接口的替代品，而是
    # 「换了个 prompt 再试」），故不在这里动 retry_count
    job.status = "generating"
    job.updated_at = datetime.now(timezone.utc)

    draft = await session.scalar(
        select(PromptDraft).where(PromptDraft.job_id == job_id)
    )
    if draft is None:
        session.add(PromptDraft(job_id=job_id, raw_prompt=payload.prompt, iterations=0))
    elif not draft.optimized_prompt:
        draft.raw_prompt = payload.prompt

    await session.commit()
    await pipeline_service.dispatch("generate", job_id)
    return {"status": job.status}


@router.post("/api/jobs/{job_id}/retry", status_code=status.HTTP_202_ACCEPTED)
async def retry(
    job_id: int,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
):
    job = await _load_own_job(session, job_id, auth)

    if job.status == "guard_failed":
        # 素材问题重试无意义——必然再败一次，只是再烧一笔钱
        raise HTTPException(409, "素材未通过预检，重试无意义")
    if job.status == "need_review":
        # 人工恢复口：retry_count 已经顶到 3，不重置就永远出不来
        job.retry_count = 0
    elif job.status == "failed":
        if job.retry_count >= MAX_RETRIES:
            raise HTTPException(409, f"重试次数已达上限 {MAX_RETRIES}")
        job.retry_count += 1
    else:
        raise HTTPException(409, f"当前状态（{job.status}）不允许重试")

    job.status = "generating"
    job.fail_reason = None
    job.updated_at = datetime.now(timezone.utc)
    await session.commit()
    await pipeline_service.dispatch("generate", job_id)
    return {"status": job.status}


# --------------------------------------------------------------------------- #
# 详情 / 列表 / 进度流
# --------------------------------------------------------------------------- #
@router.get("/api/jobs/{job_id}")
async def job_detail(
    job_id: int,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
):
    job = await _load_own_job(session, job_id, auth)

    inputs = list(
        await session.scalars(
            select(JobInputAsset)
            .where(JobInputAsset.job_id == job_id)
            # 必须自己排：靠插入顺序只是碰巧对
            .order_by(JobInputAsset.sort_order, JobInputAsset.id)
        )
    )
    draft = await session.scalar(
        select(PromptDraft).where(PromptDraft.job_id == job_id)
    )
    outputs = list(
        await session.scalars(
            select(GenOutput)
            .where(GenOutput.job_id == job_id)
            .order_by(GenOutput.attempt, GenOutput.id)
        )
    )
    return {
        "job": job_public(job),
        "inputs": [asset_public(a) for a in inputs],
        "prompt_draft": draft_public(draft),
        "outputs": [output_public(o) for o in outputs],
    }


@router.get("/api/me/jobs")
async def my_jobs(
    task_id: int | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
):
    conditions = [
        ContentJob.user_id == auth.user.id,
        # 软删的 job 不出现在列表里（deleted_at 仅供运维查验）
        ContentJob.deleted_at.is_(None),
    ]
    if task_id is not None:
        conditions.append(ContentJob.task_id == task_id)

    total = await session.scalar(
        select(func.count()).select_from(ContentJob).where(*conditions)
    )
    rows = await session.scalars(
        select(ContentJob)
        .where(*conditions)
        .order_by(ContentJob.id.desc())
        .offset((page - 1) * size)
        .limit(size)
    )
    return {
        "items": [job_public(j) for j in rows],
        "total": int(total or 0),
        "page": page,
        "size": size,
    }


@router.get("/api/jobs/{job_id}/events")
async def job_events(
    job_id: int,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
):
    await _load_own_job(session, job_id, auth)

    async def stream():
        last_id = 0
        idle = 0
        # 没有 Redis pub/sub，只能轮询。空闲超过 IDLE_ROUNDS 就收流，
        # 否则客户端会挂着一条永远不结束的连接。
        while idle < settings.studio_events_idle_rounds:
            rows = list(
                await session.scalars(
                    select(JobEvent)
                    .where(JobEvent.job_id == job_id, JobEvent.id > last_id)
                    .order_by(JobEvent.id)
                )
            )
            if rows:
                idle = 0
                for row in rows:
                    last_id = row.id
                    yield _sse("stage", event_public(row))
            else:
                idle += 1
            await asyncio.sleep(settings.studio_events_poll_seconds)

    return StreamingResponse(stream(), media_type="text/event-stream")


# --------------------------------------------------------------------------- #
# 下载
# --------------------------------------------------------------------------- #
@router.get("/api/jobs/{job_id}/download/{output_id}")
async def download(
    job_id: int,
    output_id: int,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
):
    job = await _load_own_job(session, job_id, auth)
    if job.status != "ready":
        raise HTTPException(409, "产物尚未就绪")

    output = await session.get(GenOutput, output_id)
    if output is None or output.job_id != job_id:
        # 产物不属于该 job：与「job 不存在」分开报，便于排查
        raise HTTPException(404, "产物不存在")

    key = output.url or ""
    # 每次都重签，不缓存旧链接——旧的过期了用户得能拿到新的
    url = storage_service.presign_url(key, expires_in=storage_service.DEFAULT_EXPIRES_IN)
    return {"url": url, "expires_in": storage_service.DEFAULT_EXPIRES_IN}


# --------------------------------------------------------------------------- #
# 删除（软删）
# --------------------------------------------------------------------------- #
@router.delete("/api/jobs/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_job(
    job_id: int,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
):
    job = await _load_own_job(session, job_id, auth)
    if job.status not in DELETABLE_STATUSES:
        raise HTTPException(409, f"已产出或已转人工的作品不得删除（{job.status}）")

    # 只标记一行：素材、对话、对象存储都不动。否则「保留 15 分钟的下载链接」
    # 会在删除后立刻 404，正在下的文件断在中途（OW-15）
    job.deleted_at = datetime.now(timezone.utc)
    job.updated_at = datetime.now(timezone.utc)

    reservation = await session.scalar(
        select(QuotaReservation).where(QuotaReservation.job_id == job_id)
    )
    if reservation is not None:
        # 未结算的预扣退回，报销池预占一并退回；不写流水（OW-14）
        await quota_service.release(session, reservation)

    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
