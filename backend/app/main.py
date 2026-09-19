from fastapi import FastAPI

from app.api import (
    admin_cost,
    admin_exception,
    admin_feedback,
    admin_quota,
    admin_stats,
    admin_user,
    appeal,
    auth,
    feedback,
    follow,
    mall,
    me,
    me_quota,
    merchant_mall,
    merchant_quota,
    merchant_task,
    model_keys,
    models,
    post,
    reimburse,
    review,
    reward,
    studio_job,
    studio_template,
    task,
    uploads,
)
from app import web
from app.config import get_settings

settings = get_settings()

app = FastAPI(
    title="助小商 API",
    version="0.1.0",
    docs_url="/docs" if settings.app_env != "production" else None,
)

app.include_router(auth.router)
app.include_router(me.router)
app.include_router(follow.router)
app.include_router(merchant_task.router)
app.include_router(task.router)
app.include_router(studio_job.router)
app.include_router(studio_template.router)
app.include_router(uploads.router)
app.include_router(models.router)
app.include_router(model_keys.router)
app.include_router(me_quota.router)
app.include_router(reimburse.router)
app.include_router(merchant_quota.router)
app.include_router(admin_quota.router)
app.include_router(admin_cost.router)
app.include_router(admin_user.router)
app.include_router(admin_exception.router)
app.include_router(admin_feedback.router)
app.include_router(admin_stats.router)
app.include_router(feedback.router)
app.include_router(post.router)
app.include_router(review.router)
app.include_router(appeal.router)
app.include_router(reward.router)
app.include_router(mall.router)
app.include_router(merchant_mall.router)


@app.get("/healthz", tags=["internal"])
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


# ⚠️ 必须在**所有 router 与 /healthz 之后**：匹配顺序 = 注册顺序。
# `/` 那个挂载会匹配一切，排在前面就把 `/api/*` 与 `/healthz` 全吞了。
web.mount_frontends(app)
