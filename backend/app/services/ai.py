"""03-studio 的 AI 接缝——**真接 DeepSeek**（03 追加 B，2026-09-16）。

## 一个 key 全覆盖六个能力

DeepSeek 的 `...-vision-exp` 既收文本也收图，且与 `deepseek-v4-flash` 同价，
故原先写在这里的通义 `qwen3-vl` 不再需要。文本四件套（对话 / 复写 / 文案 / 把关）
走 `deepseek_text_model`，读图两件（素材预检 / 截图 OCR）走 `deepseek_vision_model`，
两个名字都取自 `config.Settings`，**不硬编码**——`vision-exp` 是实验模型，ID 会变。

## 唯一网络出口是 `_TRANSPORT`

六个函数**一个都不许**自建 `httpx.AsyncClient()` 绕过 `_make_client()`。
测试把 `_TRANSPORT` 换成 `MockTransport` 就能把整层封死（`conftest._no_real_ai`
就是这么装护栏的，`GR-01` 盯着它）。绕过它 = 测试真打网络 = 真花钱 = 红绿看上游心情。

## 错误分类是契约（`pipeline` 按类型分流）

| 上游情况 | 抛什么 | 流水线结果 |
|---|---|---|
| 401 / 403 | **`KeyInvalidError`** | `failed(fail_reason="key_invalid")`，**绝不回落平台 Key** |
| 429 / 5xx / 网络错 / 超时 | `httpx.HTTPStatusError` / `httpx.RequestError` | `failed` + **释放预占** |
| 未配置 key | `AIConfigError` | 同上 |
| 拿不到 JSON 结构 | `ValueError`（`guard_assets` 尤其**不许默认放行**） | 同上 |
| `kind="video"` | `VideoNotSupportedError` | `failed("视频模型尚未接入")` |

回落在异常类型上分不出来，就会在 `except` 里被一起兜住，然后白烧平台的钱——
所以「Key 失效」必须自成一类。

## 已知限制（本趟不解决）

素材 `url`：公网 `https://` 与已有 `data:` **原样透传**；形如 `/api/uploads/...`
的本地相对路径在发上游前**读盘转 `data:` base64**（03 追加 C）。

返回值都是 frozen dataclass：它们是「模型说了什么」的搬运工，不该被就地改。
"""

import base64
import contextlib
import contextvars
import json
import logging
from dataclasses import dataclass, field

import httpx

from app.config import get_settings
from app.services import pricing
from app.services import storage as storage_service

logger = logging.getLogger(__name__)

#: 测试的打桩点。`None` = 用 httpx 默认传输（真打网络）。
_TRANSPORT: httpx.AsyncBaseTransport | None = None

# 非空时，这一次调用只用这把 Key，不读平台 `.env`。
_BOUND_KEY: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "ai_bound_key", default=None
)


@contextlib.contextmanager
def using_key(api_key: str | None):
    """客户 BYOK。`None` / 空串 = 不覆盖，继续走平台 Key。"""
    if not api_key:
        yield
        return
    token = _BOUND_KEY.set(api_key)
    try:
        yield
    finally:
        _BOUND_KEY.reset(token)

_VIDEO_NOT_SUPPORTED = "视频模型尚未接入"


class AIConfigError(Exception):
    """没配 key。**绝不静默假装成功**——那会让页面上出现「AI 生成」的假产物。"""


class VideoNotSupportedError(Exception):
    """视频产出尚未接入（DeepSeek 没有视频模型）。

    **消息就是 `fail_reason`**（`pipeline._run_generate` 显式 `except` 后原样落库）。
    """


class KeyInvalidError(Exception):
    """BYOK 的 Key 失效（provider 返回 401/403）。

    单独一个异常类型，是因为它有一条**别的异常都没有**的处理规矩：
    job 置 `failed(fail_reason=key_invalid)`，**绝不回落到平台 Key**。
    回落在异常类型上分不出来，就会在 except 里被一起兜住，然后白烧平台的钱。
    """


@dataclass(frozen=True)
class GuardVerdict:
    """素材预检的判定。`passed=False` 时 `reason` 必须能落到库里。"""

    passed: bool
    model: str
    reason: str | None = None
    detail: dict | None = None


@dataclass(frozen=True)
class RewriteResult:
    optimized_prompt: str
    quality_score: int
    model: str


@dataclass(frozen=True)
class GenResult:
    model: str
    cost_cents: int = 0
    content: str | None = None
    url: str | None = None
    # 本次调用消耗的计量单位数（token / 秒 / 张，按 model_price.unit 的口径）。
    # 07 的报销基数是 `cost_price_per_unit × 实际用量`，而 BYOK 时 cost_cents
    # 恒为 0——**只有 units 能算出 BYOK 该报多少**，故必须单独记。
    units: int = 0


@dataclass(frozen=True)
class JudgeResult:
    score: int
    relevance: int
    compliance: int
    quality: int
    reasons: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class OcrVerdict:
    """社媒截图的识别结果（04 的 L2 保底）。

    `parsed=None` 表示**一个数字都没认出来**。此时不得用 0 兜底——0 互动
    是一个合法值，用它冒充「没认出来」会让商户把一张糊图当真的批过去。
    """

    confidence: float
    model: str
    parsed: dict | None = None
    raw_text: str | None = None


# --------------------------------------------------------------------------- #
# HTTP 基础设施
# --------------------------------------------------------------------------- #
def _require_key() -> str:
    key = get_settings().deepseek_api_key
    if not key:
        raise AIConfigError(
            "未配置 DEEPSEEK_API_KEY。请在 backend/.env 里加一行 "
            "DEEPSEEK_API_KEY=sk-xxx 后重启服务。"
        )
    return key


def _deepseek_key() -> str:
    """优先用 `using_key` 绑上的客户 Key，否则读平台 `.env`。"""
    bound = _BOUND_KEY.get()
    if bound:
        return bound
    return _require_key()


def _make_client() -> httpx.AsyncClient:
    """建客户端。**`_TRANSPORT` 是唯一的网络出口**，测试在这里打桩。"""
    settings = get_settings()
    return httpx.AsyncClient(
        base_url=settings.deepseek_base_url,
        timeout=settings.deepseek_timeout_seconds,
        transport=_TRANSPORT,
        headers={"Authorization": f"Bearer {_deepseek_key()}"},
    )


def _raise_for_status(response: httpx.Response) -> None:
    """把上游状态码分成两类：Key 失效 vs 其它。

    只有 401/403 是 `KeyInvalidError`。把 429 也算进来的话，一次限流就会把用户的
    BYOK 标记成 invalid，而它其实只是「等会儿再来」。
    """
    if response.status_code in (401, 403):
        raise KeyInvalidError(
            f"DeepSeek 拒绝调用（HTTP {response.status_code}）：Key 失效或无权访问"
        )
    response.raise_for_status()


async def _chat(payload: dict) -> dict:
    """打一次 `/chat/completions`，非流式。"""
    _deepseek_key()
    async with _make_client() as client:
        response = await client.post("/chat/completions", json=payload)
    _raise_for_status(response)
    return response.json()


def _message_text(data: dict) -> str:
    choices = data.get("choices") or []
    if not choices:
        raise ValueError(f"上游没有返回 choices：{json.dumps(data, ensure_ascii=False)[:200]}")
    return ((choices[0].get("message") or {}).get("content")) or ""


def _extract_json(text: str) -> dict:
    """从模型回复里抠出 JSON 对象。

    模型爱把 JSON 包在 ``` 围栏里、或前面垫一句「好的，这是结果：」。先直接解析，
    失败再剥围栏、取最外层 `{...}`。**拿不到就抛**——这里不猜、不兜底。
    回落原文是 api 层 3 次重试的活（`_rewrite_with_retries`），不是接缝的事。
    """
    if not text:
        raise ValueError("模型没有返回任何内容")

    stripped = text.strip()
    candidates = [stripped]
    if stripped.startswith("```"):
        body = stripped.strip("`").strip()
        if body[:4].lower() == "json":
            body = body[4:]
        candidates.append(body.strip())
    start, end = stripped.find("{"), stripped.rfind("}")
    if start != -1 and end > start:
        candidates.append(stripped[start : end + 1])

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except ValueError:
            continue
        if isinstance(parsed, dict):
            return parsed

    raise ValueError(f"模型返回的不是 JSON 对象：{text[:200]!r}")


def _field(obj, name: str, default=None):
    """同时吃 ORM 对象与 dict——`assets` 在流水线里是 ORM 行，在用例里可能是 dict。"""
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _task_brief(task) -> str:
    if task is None:
        return "（未提供任务信息）"
    parts = [f"标题：{_field(task, 'title', '')}"]
    for label, attr in (
        ("品类", "category"),
        ("要求", "requirement"),
        ("描述", "description"),
    ):
        value = _field(task, attr)
        if value:
            parts.append(f"{label}：{value}")
    tags = _field(task, "tags")
    if tags:
        parts.append("标签：" + "、".join(str(t) for t in tags))
    return "\n".join(parts)


def _resolve_image_url(url: str, mime: str | None = None) -> str:
    """把素材 URL 变成上游可吃的 `image_url`。

    顺序：已是 `data:` → 原样；`http(s)://` → 原样；本机上传 → 读盘转 base64；
    其它 / 文件不存在 → 抛，不许空串骗过上游。
    """
    if not url:
        raise ValueError("素材 URL 为空")
    if url.startswith("data:"):
        return url
    if url.startswith("http://") or url.startswith("https://"):
        return url

    path = storage_service.resolve_upload_path(url)
    if path is None or not path.is_file():
        raise ValueError(f"本地素材不存在或路径非法：{url}")
    data = path.read_bytes()
    sniffed = storage_service.sniff_bytes(data) or mime or "application/octet-stream"
    b64 = base64.b64encode(data).decode("ascii")
    return f"data:{sniffed};base64,{b64}"


def _image_ref(asset) -> str:
    """素材引用。本地上传转 base64，见 `_resolve_image_url`。"""
    return _resolve_image_url(
        _field(asset, "url", "") or "",
        _field(asset, "mime"),
    )


def _image_message(url: str, prompt: str) -> dict:
    return {
        "role": "user",
        "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": url}},
        ],
    }


# --------------------------------------------------------------------------- #
# ① 素材预检（读图）
# --------------------------------------------------------------------------- #
_GUARD_SYSTEM = (
    "你是本地生活内容平台的素材预检员。判断用户上传的图片能否用于这条营销任务的推广"
    "（是否与任务相关、是否像商品或门店实拍、是否含违规内容）。"
    "只输出一个 JSON 对象，字段：passed（布尔）、reason（不通过时一句话理由，通过时为 null）。"
    "不要输出任何其它文字。"
)


async def guard_assets(assets: list, task) -> GuardVerdict:
    """DeepSeek 视觉模型判断：是否商品图 / 与任务是否相关 / 是否违规。"""
    settings = get_settings()
    content: list[dict] = [{"type": "text", "text": "任务信息：\n" + _task_brief(task)}]
    for asset in assets or []:
        content.append({"type": "image_url", "image_url": {"url": _image_ref(asset)}})
    content.append({"type": "text", "text": "以上素材能否用于这条任务？"})

    data = await _chat(
        {
            "model": settings.deepseek_vision_model,
            "messages": [
                {"role": "system", "content": _GUARD_SYSTEM},
                {"role": "user", "content": content},
            ],
            "max_tokens": settings.deepseek_max_tokens,
        }
    )
    # 解析不出结构就抛——**不许默认放行**。默认放行等于把这条产品底线交给运气
    parsed = _extract_json(_message_text(data))

    return GuardVerdict(
        passed=bool(parsed.get("passed")),
        model=settings.deepseek_vision_model,
        reason=parsed.get("reason") or None,
        detail=parsed,
    )


# --------------------------------------------------------------------------- #
# ③ 流式对话
# --------------------------------------------------------------------------- #
async def chat_stream(messages: list, *, provider: str):
    """DeepSeek 流式对话。产出 `str` 片段。"""
    settings = get_settings()
    payload = {
        "model": settings.deepseek_text_model,
        "messages": messages,
        "stream": True,
    }

    _deepseek_key()
    async with _make_client() as client:
        async with client.stream("POST", "/chat/completions", json=payload) as response:
            if response.status_code >= 400:
                # 流式响应下 `response.text` 还没读，得先把体吃掉再判状态码
                await response.aread()
                _raise_for_status(response)

            async for line in response.aiter_lines():
                line = line.strip()
                if not line or not line.startswith("data:"):
                    continue
                data = line[len("data:") :].strip()
                if data == "[DONE]":
                    return
                try:
                    chunk = json.loads(data)
                except ValueError:
                    # 心跳/注释行，跳过即可，不该把整条流打断
                    continue
                choices = chunk.get("choices") or []
                if not choices:
                    continue
                text = (choices[0].get("delta") or {}).get("content")
                if text:
                    yield text


# --------------------------------------------------------------------------- #
# ④ 提示词复写
# --------------------------------------------------------------------------- #
_REWRITE_SYSTEM = (
    "你是提示词优化师。把用户给的粗糙提示词改写成更适合生成营销文案的提示词："
    "保留原意，补足可执行的细节（场景、受众、卖点、语气）。"
    "只输出一个 JSON 对象，字段：optimized_prompt（优化后的提示词）、"
    "quality_score（0 到 100 的整数，你给这条提示词的打分）。不要输出任何其它文字。"
)


async def rewrite_prompt(raw_prompt: str, *, provider: str) -> RewriteResult:
    """prompt-optimizer：粗糙提示词 → 优化提示词 + 质量分。"""
    settings = get_settings()
    data = await _chat(
        {
            "model": settings.deepseek_text_model,
            "messages": [
                {"role": "system", "content": _REWRITE_SYSTEM},
                {"role": "user", "content": raw_prompt},
            ],
            "max_tokens": settings.deepseek_max_tokens,
        }
    )
    parsed = _extract_json(_message_text(data))

    optimized = parsed.get("optimized_prompt")
    if not optimized:
        raise ValueError("复写结果里没有 optimized_prompt")

    try:
        score = int(parsed.get("quality_score"))
    except (TypeError, ValueError):
        raise ValueError(f"复写结果里的 quality_score 不是整数：{parsed.get('quality_score')!r}")

    # prompt_draft 有 CHECK：0 ≤ quality_score ≤ 100，越界会撞库约束
    score = min(max(score, 0), 100)

    return RewriteResult(
        optimized_prompt=str(optimized),
        quality_score=score,
        model=settings.deepseek_text_model,
    )


# --------------------------------------------------------------------------- #
# ⑤ 生成
# --------------------------------------------------------------------------- #
_GENERATE_SYSTEM = (
    "你是本地生活商户的营销文案写手。按提示词写一条可以直接发布到社交媒体的推广文案："
    "口语、具体、有行动号召。只输出文案正文本身，不要标题、不要解释、不要 Markdown 标记。"
)


async def generate(prompt: str, *, kind: str, provider: str, task) -> GenResult:
    """文案走 DeepSeek；视频走即梦/可灵适配层（Key 空则拒）。"""
    if kind == "video":
        return await _generate_video(prompt, provider=provider, task=task)
    if kind != "copy":
        raise VideoNotSupportedError(_VIDEO_NOT_SUPPORTED)

    settings = get_settings()
    data = await _chat(
        {
            "model": settings.deepseek_text_model,
            "messages": [
                {"role": "system", "content": _GENERATE_SYSTEM},
                {"role": "user", "content": f"任务信息：\n{_task_brief(task)}\n\n提示词：\n{prompt}"},
            ],
            "max_tokens": settings.deepseek_max_tokens,
        }
    )

    content = _message_text(data)
    if not content:
        raise ValueError("模型没有产出任何文案")

    # **真实用量**，不是估的：报销基数 = cost_price_per_unit × units，估出来的话
    # BYOK 报销会按同一个数走
    units = int((data.get("usage") or {}).get("total_tokens") or 0)

    return GenResult(
        model=settings.deepseek_text_model,
        cost_cents=pricing.cost_cents_for("generate", units),
        content=content,
        url=None,
        units=units,
    )


def _video_settings(provider: str) -> tuple[str, str, str]:
    """返回 (api_key, base_url, model_label)。"""
    settings = get_settings()
    if provider == "jimeng":
        return settings.jimeng_api_key, settings.jimeng_base_url, "jimeng-video"
    if provider == "kling":
        return settings.kling_api_key, settings.kling_base_url, "kling-video"
    raise ValueError(f"未知视频 provider：{provider}")


async def _generate_video(prompt: str, *, provider: str, task) -> GenResult:
    """即梦 / 可灵 HTTP 接缝。Key 空 → 与追加 B 相同的 VideoNotSupportedError。"""
    bound = _BOUND_KEY.get()
    settings_key, base_url, model = _video_settings(provider)
    api_key = bound or settings_key
    if not api_key:
        raise VideoNotSupportedError(_VIDEO_NOT_SUPPORTED)

    payload = {
        "model": model,
        "prompt": prompt,
        "task": _task_brief(task),
    }
    async with httpx.AsyncClient(
        base_url=base_url.rstrip("/"),
        timeout=get_settings().deepseek_timeout_seconds,
        transport=_TRANSPORT,
        headers={"Authorization": f"Bearer {api_key}"},
    ) as client:
        response = await client.post("/v1/videos", json=payload)
    if response.status_code in (401, 403):
        raise KeyInvalidError(
            f"{provider} 拒绝调用（HTTP {response.status_code}）：Key 失效或无权访问"
        )
    response.raise_for_status()
    data = response.json()
    url = data.get("url") or (data.get("data") or {}).get("url")
    if not url:
        raise ValueError(f"{provider} 未返回视频 url：{json.dumps(data, ensure_ascii=False)[:200]}")
    units = int((data.get("usage") or {}).get("total_tokens") or data.get("units") or 1)
    return GenResult(
        model=model,
        cost_cents=pricing.cost_cents_for("generate", units),
        content=None,
        url=str(url),
        units=units,
    )


# --------------------------------------------------------------------------- #
# ⑥ 把关
# --------------------------------------------------------------------------- #
_JUDGE_SYSTEM = (
    "你是营销内容审核员。给这条文案打分，只输出一个 JSON 对象，字段："
    "score（0-100 总分）、relevance（与任务的相关性 0-100）、"
    "compliance（合规 0-100）、quality（文案质量 0-100）、"
    "reasons（理由字符串数组，没问题时给空数组）。不要输出任何其它文字。"
)


async def judge(*, kind: str, content: str | None, url: str | None, provider: str) -> JudgeResult:
    """LLM-as-judge：相关性 / 合规 / 质量。"""
    settings = get_settings()
    subject = content or url or ""
    data = await _chat(
        {
            "model": settings.deepseek_text_model,
            "messages": [
                {"role": "system", "content": _JUDGE_SYSTEM},
                {"role": "user", "content": subject},
            ],
            "max_tokens": settings.deepseek_max_tokens,
        }
    )
    parsed = _extract_json(_message_text(data))

    def _score(name: str) -> int:
        try:
            value = int(parsed.get(name))
        except (TypeError, ValueError):
            return 0
        return min(max(value, 0), 100)

    return JudgeResult(
        score=_score("score"),
        relevance=_score("relevance"),
        compliance=_score("compliance"),
        quality=_score("quality"),
        reasons=[str(r) for r in (parsed.get("reasons") or [])],
    )


# --------------------------------------------------------------------------- #
# ⑦ 截图 OCR（读图，04 的保底）
# --------------------------------------------------------------------------- #
_OCR_SYSTEM = (
    "你是数据录入员。读出这张社媒截图上的四个互动数：点赞、收藏、评论、转发。"
    "只输出一个 JSON 对象，字段：likes、collects、comments、shares"
    "（整数；图上看不出来的填 null）、confidence（0 到 1 的小数，你的把握）。"
    "不要输出任何其它文字。"
)

_OCR_COUNTS = ("likes", "collects", "comments", "shares")


async def ocr_metrics(image_url: str) -> OcrVerdict:
    """视觉模型读社媒截图，认出四个互动数（04 的截图保底）。

    与 `guard_assets` 同源同模型，但**是两件事**：预检看的是「合不合规」，
    这里看的是「有几个赞」。合并成一个函数会让 04 的识别被 03 的判定连带。
    """
    settings = get_settings()
    data = await _chat(
        {
            "model": settings.deepseek_vision_model,
            "messages": [
                {"role": "system", "content": _OCR_SYSTEM},
                _image_message(_resolve_image_url(image_url), "请读出这四个互动数。"),
            ],
            "max_tokens": settings.deepseek_max_tokens,
        }
    )
    raw_text = _message_text(data)
    parsed_raw = _extract_json(raw_text)

    try:
        confidence = float(parsed_raw.get("confidence") or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = min(max(confidence, 0.0), 1.0)

    counts = {}
    for name in _OCR_COUNTS:
        value = parsed_raw.get(name)
        try:
            counts[name] = None if value is None else int(value)
        except (TypeError, ValueError):
            counts[name] = None

    # 四个全空 = 一个都没认出来 → `parsed=None`。**绝不用 0 兜底**：
    # 0 是一个合法的互动量（04 的硬规则）
    parsed = None if all(v is None for v in counts.values()) else counts

    return OcrVerdict(
        confidence=confidence,
        model=settings.deepseek_vision_model,
        parsed=parsed,
        raw_text=raw_text,
    )
