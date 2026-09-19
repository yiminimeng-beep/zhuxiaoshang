"""03-studio 的流水线：把 job 从「排出去」推到「跑完」。

分成两层，是这模块唯一值得解释的设计：

- `dispatch(stage, job_id)` —— **只负责排队**，不做事。HTTP 请求回了 202 之后
  真正的工作跑在别处，所以它不能是同步的。
- `run_stage(stage, job_id)` —— **把一件事跑完并落库**，可以 `await`。

分开的好处全在测试上：并发用例若真要跟 `asyncio.create_task` 抢跑，
绿不绿取决于机器快慢。用例打桩 `dispatch`（只记下排了什么），再显式
`await run_stage(...)` 驱动状态机——这样「状态机对不对」与「异步接得对不对」
就是两条独立的断言了。

## 结算口径（钱）

- **terminal 状态**（`guard_failed` / `ready` / `need_review`）→ **结算**。
  预检不过、把关 3 次不过都算「策略性失败」，钱是真花了的，不退。
- **`failed`（系统故障）** → **释放**预占，不写流水。用户不该为平台的故障付钱。
"""

import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import SessionLocal
from app.models.quota import QuotaReservation
from app.models.studio import (
    ContentJob,
    GenOutput,
    GuardResult,
    JobEvent,
    JobInputAsset,
    PromptDraft,
)
from app.models.task import Task
from app.services import ai as ai_service
from app.services import model_key as key_service
from app.services import quota as quota_service

logger = logging.getLogger(__name__)

STAGES = ("guard", "generate")

# 已经出结果的 job 再投递一次阶段任务是空操作（`JC-09` 靠它做幂等）
TERMINAL_STATUSES = ("guard_failed", "ready", "need_review", "failed")
RUNNING_STATUSES = ("created", "guarding", "chatting", "generating", "judging")

# 把关及格线。低于它自动重试，重试上限到了转人工（spec「生成与把关」）
JUDGE_THRESHOLD = 60
MAX_RETRIES = 3

GUARD_FALLBACK_REASON = "素材未通过预检"


async def _job_key(session: AsyncSession, job: ContentJob) -> str | None:
    """BYOK 返回客户明文 Key；平台计费返回 None（调用方继续读 `.env`）。"""
    if (job.billing_source or "platform") != "byok":
        return None
    provider = job.provider or "deepseek"
    try:
        return await key_service.load_active_plaintext(session, job.user_id, provider)
    except key_service.NoActiveKey as exc:
        raise ai_service.KeyInvalidError("key_invalid") from exc

# 后台任务集合：持强引用，否则事件循环只持弱引用，任务可能被提前回收
_BACKGROUND: set[asyncio.Task] = set()


async def dispatch(stage: str, job_id: int) -> None:
    """把阶段排进后台。**不 await 它的结果**——HTTP 已经回过了。"""
    task = asyncio.create_task(run_stage(stage, job_id))
    _BACKGROUND.add(task)
    task.add_done_callback(_BACKGROUND.discard)


async def run_stage(stage: str, job_id: int) -> None:
    if stage == "guard":
        runner = _run_guard
    elif stage == "generate":
        runner = _run_generate
    else:
        raise ValueError(f"未知阶段：{stage}")

    session = SessionLocal()
    try:
        job = await session.get(ContentJob, job_id)
        if job is None or job.deleted_at is not None:
            return
        await runner(session, job)
        await session.commit()
    except Exception:
        await session.rollback()
        logger.exception("阶段 %s 在 job %s 上崩了", stage, job_id)
        raise
    finally:
        await session.close()


# --------------------------------------------------------------------------- #
# ① 素材预检
# --------------------------------------------------------------------------- #
async def _run_guard(session: AsyncSession, job: ContentJob) -> None:
    if job.status in TERMINAL_STATUSES or job.status not in ("created", "guarding"):
        return

    job.status = "guarding"
    job.updated_at = _now()
    await session.flush()

    assets = list(
        await session.scalars(
            select(JobInputAsset)
            .where(JobInputAsset.job_id == job.id)
            .order_by(JobInputAsset.sort_order)
        )
    )
    task = await session.get(Task, job.task_id)
    await _event(session, job.id, "guard", {"assets": len(assets)})

    try:
        with ai_service.using_key(await _job_key(session, job)):
            verdict = await ai_service.guard_assets(assets, task)
    except ai_service.KeyInvalidError:
        await _fail(session, job, "key_invalid")
        return
    except Exception as exc:
        # 预检自己挂了 → fail closed。默认放行等于把这条产品底线交给运气
        await _fail(session, job, f"预检失败：{exc}")
        return

    session.add(
        GuardResult(
            job_id=job.id,
            passed=verdict.passed,
            # 判定「不通过」却不给理由，前端只能显示空白——兜一句
            reason=None if verdict.passed else (verdict.reason or GUARD_FALLBACK_REASON),
            model=verdict.model,
            detail=verdict.detail,
        )
    )

    if not verdict.passed:
        job.status = "guard_failed"
        job.updated_at = _now()
        await _event(session, job.id, "guard_failed", {"reason": verdict.reason})
        # 策略性失败：钱照扣。预检真的调过模型，成本已经发生
        await _settle(session, job.id, 0)
        return

    job.status = "chatting"
    job.updated_at = _now()
    await _event(session, job.id, "guard_passed", None)


# --------------------------------------------------------------------------- #
# ④⑤ 生成与产物把关
# --------------------------------------------------------------------------- #
async def _run_generate(session: AsyncSession, job: ContentJob) -> None:
    if job.status in TERMINAL_STATUSES:
        return
    if job.status not in ("chatting", "generating"):
        return

    job.status = "generating"
    job.updated_at = _now()
    await session.flush()

    prompt = await _prompt_for(session, job)
    task = await session.get(Task, job.task_id)
    spend = 0
    units = 0
    try:
        call_key = await _job_key(session, job)
    except ai_service.KeyInvalidError:
        await _fail(session, job, "key_invalid")
        return

    attempt = 1
    while True:
        await _event(session, job.id, "generating", {"attempt": attempt})
        try:
            with ai_service.using_key(call_key):
                result = await ai_service.generate(
                    prompt, kind=job.kind, provider=job.provider, task=task
                )
        except ai_service.KeyInvalidError:
            # 绝不回落平台 Key——那等于平台替用户付钱，且用户毫不知情
            await _fail(session, job, "key_invalid")
            return
        except ai_service.VideoNotSupportedError as exc:
            # 异常消息**本身就是 fail_reason**，原样落库（08 追加 C 的按钮虽已禁用，
            # 但 API 层不挡，绕过界面仍会走到这里）
            await _fail(session, job, str(exc))
            return
        except Exception as exc:
            await _fail(session, job, f"生成失败：{exc}")
            return

        spend += result.cost_cents
        units += result.units
        # 旧的置 inactive：同一时刻「有效产物」有且仅有 1 条
        await session.execute(
            text("UPDATE gen_output SET is_active = false WHERE job_id = :jid"),
            {"jid": job.id},
        )
        output = GenOutput(
            job_id=job.id,
            type=job.kind,
            content=result.content,
            url=result.url,
            provider=result.model or job.provider,
            billing_source=job.billing_source or "platform",
            cost_cents=result.cost_cents,
            attempt=attempt,
            is_active=True,
            created_at=_now(),
        )
        session.add(output)
        await session.flush()

        try:
            with ai_service.using_key(call_key):
                verdict = await ai_service.judge(
                    kind=job.kind, content=result.content, url=result.url,
                    provider=job.provider,
                )
        except Exception as exc:
            await _fail(session, job, f"把关失败：{exc}")
            return

        output.judge_score = verdict.score
        output.judge_detail = {
            "relevance": verdict.relevance,
            "compliance": verdict.compliance,
            "quality": verdict.quality,
            "reasons": list(verdict.reasons),
        }
        await session.flush()

        if verdict.score >= JUDGE_THRESHOLD:
            job.status = "ready"
            job.updated_at = _now()
            await _event(session, job.id, "ready", {"score": verdict.score})
            await _settle(session, job.id, spend, units)
            return

        if attempt > MAX_RETRIES:
            # 3 次仍不过 → 转人工。产物一条都不删，供 06 的异常列表页审
            job.retry_count = MAX_RETRIES
            job.status = "need_review"
            job.updated_at = _now()
            await _event(session, job.id, "need_review", {"score": verdict.score})
            await _settle(session, job.id, spend, units)
            return

        job.retry_count = attempt
        job.updated_at = _now()
        attempt += 1


async def _prompt_for(session: AsyncSession, job: ContentJob) -> str:
    """优先用复写后的提示词——那是用户与 AI 商量的结果。"""
    draft = await session.scalar(
        select(PromptDraft).where(PromptDraft.job_id == job.id)
    )
    if draft is None:
        return ""
    return draft.optimized_prompt or draft.raw_prompt


# --------------------------------------------------------------------------- #
# 收尾：失败 / 结算 / 事件
# --------------------------------------------------------------------------- #
async def _fail(session: AsyncSession, job: ContentJob, reason: str) -> None:
    """系统故障（模型 5xx / 超时 / 队列丢弃）→ 释放预占，不写流水。"""
    job.status = "failed"
    job.fail_reason = reason
    job.updated_at = _now()
    reservation = await _reservation(session, job.id)
    if reservation is not None:
        await quota_service.release(session, reservation)
    await _event(session, job.id, "failed", {"reason": reason})


async def _settle(
    session: AsyncSession, job_id: int, actual: int, units: int = 0
) -> None:
    reservation = await _reservation(session, job_id)
    if reservation is not None:
        await quota_service.settle(session, reservation, actual, units=units)


async def _reservation(
    session: AsyncSession, job_id: int
) -> QuotaReservation | None:
    return await session.scalar(
        select(QuotaReservation).where(QuotaReservation.job_id == job_id)
    )


async def _event(
    session: AsyncSession, job_id: int, stage: str, detail: dict | None
) -> None:
    session.add(
        JobEvent(
            job_id=job_id, stage=stage, detail=detail, created_at=_now()
        )
    )
    await session.flush()


def _now() -> datetime:
    return datetime.now(timezone.utc)
