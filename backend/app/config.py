from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """运行配置。测试通过 .env.test 覆盖，见 tests/conftest.py。"""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "development"

    database_url: str = "postgresql+asyncpg://zxs:zxs_dev_pw@localhost:5433/zhuxiaoshang"
    redis_url: str = "redis://localhost:6380/0"

    jwt_secret: str = "dev-only-secret-change-me"
    jwt_algorithm: str = "HS256"
    access_token_ttl_seconds: int = 2 * 60 * 60
    refresh_token_ttl_seconds: int = 30 * 24 * 60 * 60

    max_devices_per_user: int = 10
    login_lock_seconds: int = 15 * 60

    # --- 03 内容工坊 ---
    # 单商户同时在跑的 job 上限。超了 429——并发放开了会把额度检查穿成先到先得
    studio_max_running_jobs: int = 3
    # 商户没显式设 daily_limit 时的兜底日预算（分）
    merchant_default_daily_limit: int = 10000
    # 提示词复写限速：链路上最便宜、也最容易被刷的一环
    studio_rewrite_per_minute: int = 10
    # 进度流空闲多少轮就收流。没有 Redis pub/sub，只能轮询；
    # 不收流的话客户端会挂着一条永远不结束的连接
    studio_events_idle_rounds: int = 5
    studio_events_poll_seconds: float = 0.2

    # --- DeepSeek（03 追加 B）---
    # 一个 key 全覆盖六个能力：`...-vision-exp` 既收文本也收图，且与 flash 同价。
    # 空 = 未配置，六个 AI 函数一律 `AIConfigError` → 流水线 `failed`，**绝不假装成功**。
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_text_model: str = "deepseek-v4-flash"
    # ⚠️ 实验模型，ID 随时可能变，故一律走配置项，不硬编码进函数体
    deepseek_vision_model: str = "deepseek-v4-flash-vision-exp"
    deepseek_timeout_seconds: float = 60.0
    deepseek_max_tokens: int = 2048

    # --- 视频厂商（03 追加 D；空 = 未接入，generate 抛 VideoNotSupportedError）---
    jimeng_api_key: str = ""
    jimeng_base_url: str = "https://api.jimeng.example"
    kling_api_key: str = ""
    kling_base_url: str = "https://api.kling.example"

    # --- 03 追加 A：本地素材上传 ---
    upload_dir: str = "var/uploads"
    upload_max_bytes: int = 20 * 1024 * 1024
    upload_url_prefix: str = "/api/uploads"


@lru_cache
def get_settings() -> Settings:
    return Settings()
