# 03-studio · 测试计划

> 由 `spec.md` 的「端点」与「边界」逐条转出。一个 ID = 一条测试，ID 与测试函数一一对应。
>
> **本轮一趟落地两块**（2026-09-15 确认）：
> ① 03 的全部 8 张表 + 全部端点；
> ② 07 的**第二趟**（`quota_reservation` / `reimburse_claim` / `reimburse_claim_job` +
> `reserve` / `settle` / `release`），否则 `POST /api/jobs` 根本没有落脚点。
>
> 落地后 **07 的 `RS-*` / `PB-*` / `LM-08`~`LM-14` 会一并转绿**——它们的用例已经写在
> `specs/07-token/test_plan.md` 里（`test_token_reserve.py` / `test_token_limits.py`），
> **本计划不重复**，只在文末列出「本趟同时转绿」清单。
>
> 覆盖：21 个端点的正常路径 + 10 组边界 = **150 条**（`JB` 27 / `GD` 13 / `CH` 12 /
> `RW` 14 / `GN` 21 / `JQ`+`EV`+`ME` 10 / `OW` 15 / `TP` 29 / `JC` 9）。

**红阶段核验（2026-09-15）**：`150 failed, 0 passed`，红因**只有三类**——
`relation "content_job" / "prompt_template" does not exist`、`cannot import name
'pipeline' / 'ai' / 'storage' from 'app.services'`、以及路由缺失导致的框架 404。
**没有一条红是测试自身的 bug**（无 `TypeError`、无 `ForeignKeyViolationError`），
即这些用例确实在等实现，而不是在自说自话。

---

## 运行方式

```bash
cd backend
docker compose -f ../docker-compose.yml up -d     # PG:5433 / Redis:6380
.venv/Scripts/python -m pytest -q
```

---

## 用例里反复出现的前置

| 工厂 | 作用 |
|---|---|
| `claimed_task(client, merchant_token, customer_token)` | 建 + 发布任务并由客户领取，返回 `(task, claim_id)` |
| `create_job(client, token, claim_id, **over)` | 建 job（不自动断言，调用方看状态码） |
| `create_job_ok(client, token, claim_id, **over)` | 建 job 并断言 `201`，返回响应体 |
| `assets(n=1)` | 造 n 个合法素材（`image/jpeg`，`size_bytes=1024`） |
| `insert_job(db, **over)` | 直插 `content_job` 并返回 id（状态机用例不走接口造状态） |
| `insert_output(db, job_id, **over)` | 直插 `gen_output` |
| `insert_template(db, merchant_id, **over)` | 直插 `prompt_template` |
| `grant_to(db, user_id, points)` | 给账户充值（07 已有 `grant`，这里补一个商户侧口味的别名） |
| `ledger_rows(db, user_id)` | 取该用户流水（07 已有） |

**素材常量**：`OK_MIME = "image/jpeg"`、`MAX_BYTES = 20 * 1024 * 1024`。
20MB 边界用例直接用 `20971520` / `20971521` 两个字面量，**不写成表达式**——
写成 `MAX_BYTES + 1` 的话，把常量改错会让用例跟着一起错，边界就守不住了。

---

## 测试缝（seam）——实现必须提供的三组名字

03 的外部依赖全是「打网络」：视觉预检、对话、复写、生成、把关、对象存储。
不打桩就只能真调 API，用例会依赖网络、第三方可用性与真金白银的计费。
约定三组接缝，**名字即契约**：实现改名 → 对应用例 `AttributeError`，而不是静默跳过。

### 1. AI 能力（`app/services/ai.py`）

```python
@dataclass(frozen=True)
class GuardVerdict:
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
    content: str | None = None   # kind=copy 时必填
    url: str | None = None       # kind=video 时必填
    cost_cents: int = 0
    model: str = ""

@dataclass(frozen=True)
class JudgeResult:
    score: int
    relevance: int
    compliance: int
    quality: int
    reasons: list[str]

async def guard_assets(assets: list[dict], task) -> GuardVerdict
async def chat_stream(messages: list[dict], *, provider: str) -> AsyncIterator[str]
async def rewrite_prompt(raw_prompt: str, *, provider: str) -> RewriteResult
async def generate(prompt: str, *, kind: str, provider: str, task) -> GenResult
async def judge(*, kind: str, content: str | None, url: str | None, provider: str) -> JudgeResult
```

### 2. 对象存储（`app/services/storage.py`）

```python
def presign_url(key: str, *, expires_in: int = 900) -> str
```

### 3. 流水线调度（`app/services/pipeline.py`）

后台阶段（① 预检、④⑤ 生成+把关）若用 `asyncio.create_task` 直接甩出去，
用例断言状态时会与后台任务抢跑，**红绿取决于机器快慢**。故拆成两个名字：

```python
async def dispatch(stage: str, job_id: int) -> None   # 排进队列（默认 create_task）
async def run_stage(stage: str, job_id: int) -> None  # 真正跑完一个阶段并落库
```

- 用例把 `dispatch` 换成**记录器**（只记下 `(stage, job_id)`，不跑），断言「建 job 后
  确实排了 `guard`」——这一步是路由的契约。
- 用例再直接 `await run_stage("guard", job_id)` 驱动状态机，断言落库的 `status`。
  这一步是阶段实现的契约。

**接缝用 `monkeypatch.setattr("app.services.ai.guard_assets", ...)` 打**。
router 必须走模块属性查找（`from app.services import ai as ai_service` 后
`ai_service.guard_assets(...)`）；写成 `from app.services.ai import guard_assets`
会绑一个本地名，打桩打不中（07 的 `verify_provider_key` 已踩过这个坑）。

统一封装：`patch_ai(monkeypatch, guard=..., rewrite=..., gen=..., judge=...)`、
`patch_dispatch(monkeypatch)` 返回记录列表。

### 接缝的补充约定（写用例时定下的）

| 约定 | 说明 |
|---|---|
| `stage` 只有两个取值 | `"guard"` 与 `"generate"`。②③ 复写是用户驱动的端点，不入队 |
| **阶段自己收尾** | 每个阶段跑到终态时**在同一步内结算**（`settled` / `released`）。不另设 `"settle"` 阶段——规格里的 ⑥ 是状态而非队列任务 |
| `chat_stream` 是**异步生成器** | 桩用「片段序列」表达，序列里放 `Exception` 即模拟「流到一半挂了」（`CH-09`） |
| `TimeoutError` → 超时 | 复写（30s）与生成（10min）都用 `TimeoutError` 表达超时，实现分别映射成 `rewrite_failed=true` 与 `fail_reason="生成超时"`。不另造异常类 |
| `KeyInvalidError` | **要新增**这一个异常名：provider 返回 401 时由 `ai.py` 抛出，实现映射成 `fail_reason="key_invalid"`（`GN-21`）。名字即契约 |
| `GuardVerdict.reason` 可空 | 但落库不得为空：判定为不通过却没给理由时，实现要有兜底文案（`GD-13`） |
| 建 job 请求体接受可选 `claim_id` | spec 的请求体列的是 `task_id`，而边界又要求「用别人的 `claim_id` 建 job → 403」。两句同时成立的唯一解释是该字段可传可不传，最终以「本人对该任务的领取」为准（`JB-10` / `JB-22`） |

---

## 一、建 job 与素材校验（`test_studio_job.py`）

| ID | 前置 | 输入 | 期望 | 测试函数 |
|---|---|---|---|---|
| JB-01 | 已领取任务、商户有额度、计价表有行 | `POST /api/jobs` 合法体 | `201` + `{job_id, status:"guarding", provider, billing_source, reserved_points, reimburse_reserved}` | `test_jb01_create_job` |
| JB-02 | — | `assets=[]` | `422` | `test_jb02_no_asset_422` |
| JB-03 | — | `assets` 10 张 | `422` | `test_jb03_ten_assets_422` |
| JB-04 | — | `assets` 9 张 | `201`（上限含等号） | `test_jb04_nine_assets_ok` |
| JB-05 | — | `mime="application/pdf"` | `415` | `test_jb05_pdf_415` |
| JB-06 | — | `mime="image/gif"` | `415` | `test_jb06_gif_415` |
| JB-07 | — | `size_bytes=20971520` | `201`（20MB 整通过） | `test_jb07_exactly_20mb_ok` |
| JB-08 | — | `size_bytes=20971521` | `413` | `test_jb08_over_20mb_413` |
| JB-09 | 未领取该任务 | 建 job | `403` | `test_jb09_not_claimed_403` |
| JB-10 | 用别人的 `claim_id` | 建 job | `403` | `test_jb10_others_claim_403` |
| JB-11 | — | `kind="audio"` | `422` | `test_jb11_bad_kind_422` |
| JB-12 | 商户额度充足 | 请求体带 `merchant_id` / `payer` / `user_id` | `422`，且**不得按传入值扣费**（商户余额一分未动） | `test_jb12_payer_in_body_422` |
| JB-13 | 商户 `status=frozen` | 在它的任务上建 job | `403` | `test_jb13_frozen_merchant_403` |
| JB-14 | 计价表**缺**该 `(provider,model,op)` | 建 job | `503`（不得按 0 计费） | `test_jb14_missing_price_503` |
| JB-15 | 付款方 `available` < 预扣上界 | 建 job | `402`，且 `content_job` **0 行**（不得先扣再校验） | `test_jb15_insufficient_402_no_job` |
| JB-16 | 计价表无此 provider | `provider="openai"` | `422` | `test_jb16_unknown_provider_422` |
| JB-17 | `provider_visible=false` 的计价行 | 指定它建 job | `422` | `test_jb17_hidden_provider_422` |
| JB-18 | `kind="video"` | `provider="deepseek"`（文案厂商） | `422` | `test_jb18_provider_kind_mismatch_422` |
| JB-19 | `pay_mode=merchant_pay` 的任务 | `billing_source="byok"` | `422`（**不静默回落 platform**） | `test_jb19_byok_on_merchant_pay_422` |
| JB-20 | 用户无该 provider 的有效 Key | `billing_source="byok"` | `422` | `test_jb20_byok_without_key_422` |
| JB-21 | — | 不传 `provider` / `billing_source` | `201`，落库为平台默认（03 原契约不破） | `test_jb21_defaults_applied` |
| JB-22 | — | 建 job 成功 | `content_job.claim_id` == 传入值、`task_id` == 该 claim 的 `task_id`（付费方只由 `task_id` 推导） | `test_jb22_claim_task_bound` |
| JB-23 | — | 建 job 成功 | `quota_reservation` **恰好 1 条**、`job_id` 唯一；且 `quota_ledger` **0 条**（预扣不写流水） | `test_jb23_reservation_not_ledger` |
| JB-24 | `user_pay_reimburse`、`reimburse_pool=100` < 单次预占 120 | 建 job | `429`（**不是等报销时才发现**） | `test_jb24_pool_exhausted_429` |
| JB-25 | `user_pay_reimburse`、`reimburse_pool=1000` | 建 job | `201`，`reimburse_reserved > 0`，`task.reimburse_pool_reserved` 与之一致 | `test_jb25_pool_reserved_on_create` |
| JB-26 | — | 建 job 无 token | `401` | `test_jb26_no_token_401` |
| JB-27 | 同一 `claim_id` 连建 2 个 job | — | 两个都 `201`（spec 未限制一 claim 一 job） | `test_jb27_multi_job_per_claim_ok` |

---

## 二、素材预检（`test_studio_guard.py`）

| ID | 前置 | 输入 | 期望 | 测试函数 |
|---|---|---|---|---|
| GD-01 | 建 job 后 | 查 `dispatch` 记录 | 排了一次 `("guard", job_id)`——**建 job 确实把预检甩出去了** | `test_gd01_guard_dispatched` |
| GD-02 | stub `guard_assets` 返回 `passed=True` | `await run_stage("guard", job_id)` | `status=chatting`，`guard_result.passed=true`，`model` 落库 | `test_gd02_guard_pass_to_chatting` |
| GD-03 | stub 返回 `passed=False, reason="宠物照片与任务无关"` | `await run_stage("guard", job_id)` | `status=guard_failed`，`reason` **原样落库**，`detail` 落库 | `test_gd03_guard_reject_records_reason` |
| GD-04 | 同上 | 查 `status` | **不得**为 `generating`；`gen_output` 0 行 | `test_gd04_rejected_never_generates` |
| GD-05 | stub `guard_assets` 抛异常（超时/5xx） | `await run_stage("guard", job_id)` | `status=failed` + `fail_reason` 非空，**不允许默认放行** | `test_gd05_guard_error_fails_closed` |
| GD-06 | stub 返回 `passed=False, reason="含他品牌水印"` | `await run_stage("guard", job_id)` | `guard_failed`（水印同样挡） | `test_gd06_watermark_rejected` |
| GD-07 | `status=guard_failed` | `POST /api/jobs/{id}/generate` | `409` | `test_gd07_generate_after_guard_fail_409` |
| GD-08 | `guard_failed`（已真实调用过预检） | 查 `quota_ledger` | **无退款流水**（策略性失败，额度不退） | `test_gd08_no_refund_on_guard_fail` |
| GD-09 | 预检已通过 | `GET /api/jobs/{id}/guard` | `200` + `{passed:true, model, detail}` | `test_gd09_get_guard_result` |
| GD-10 | 预检未完成（`status=guarding`） | `GET /api/jobs/{id}/guard` | `409` | `test_gd10_guard_pending_409` |
| GD-11 | 用户 A / B | A 查 B 的 `GET /api/jobs/{id}/guard` | `403` | `test_gd11_others_guard_403` |
| GD-12 | — | `GET /api/jobs/999999/guard` | `404` | `test_gd12_unknown_job_404` |
| GD-13 | `passed=False` 且未给 `reason`（stub 返回 `None`） | `await run_stage` | `guard_result.reason` 非空（兜底文案），不落 NULL | `test_gd13_reason_never_null` |

> `GD-03` / `GD-06` 走的是同一段代码，差异只在 stub 的 `reason`。留着是因为
> spec 的「预检」小节把四类不合格素材各写成一条边界——它们钉的是**任一类不合格都
> 不得放行**，而不是四个不同的分支。真模型上线后这四条会自然分化成四条真实路径。

---

## 三、对话（`test_studio_chat.py`）

| ID | 前置 | 输入 | 期望 | 测试函数 |
|---|---|---|---|---|
| CH-01 | `status=chatting`，stub 流式吐 3 段 | `POST /api/jobs/{id}/chat` `{message}` | `200` SSE；流读完 `chat_message` 落 2 行（user + assistant）、assistant 行 `model` 非空 | `test_ch01_chat_round` |
| CH-02 | — | `message=""` | `422` | `test_ch02_empty_422` |
| CH-03 | — | 4001 字 | `422` | `test_ch03_4001_chars_422` |
| CH-04 | — | 4000 字 | `200`（上限含等号） | `test_ch04_4000_chars_ok` |
| CH-05 | 已落库 20 轮 | 第 21 轮 | `409`（上一轮已落库，本轮拒绝） | `test_ch05_turn_21_409` |
| CH-06 | `status=guarding`（预检未过） | `POST chat` | `409` | `test_ch06_chat_before_guard_409` |
| CH-07 | 已有 4 轮 | `GET /api/jobs/{id}/chat` | `200` + `{messages[]}` 8 条，按 `created_at` 升序 | `test_ch07_chat_history` |
| CH-08 | 用户 A / B | A 打 B 的 chat（POST / GET） | `403` | `test_ch08_others_chat_403` |
| CH-09 | stub 流到第 2 段抛错 | `POST chat` | SSE 里出现 `error` 事件；`chat_message` **不写半截 assistant 行** | `test_ch09_stream_error_no_half_message` |
| CH-10 | — | 客户端读到第 1 段即断开 | 已落的 user 消息保留、`job.status` 不变（仍 `chatting`） | `test_ch10_disconnect_keeps_state` |
| CH-11 | 付款方 `available=0` | `POST chat` | `402`，且**流未开始**（响应体不是 SSE） | `test_ch11_insufficient_402_before_stream` |
| CH-12 | — | `POST /api/jobs/999999/chat` | `404` | `test_ch12_unknown_job_404` |

---

## 四、提示词复写（`test_studio_rewrite.py`）

| ID | 前置 | 输入 | 期望 | 测试函数 |
|---|---|---|---|---|
| RW-01 | `status=chatting` | `POST /api/jobs/{id}/rewrite-prompt` `{raw_prompt}` | `200` + `{optimized_prompt, quality_score, rewrite_failed:false}`；`prompt_draft` 落库 | `test_rw01_rewrite_ok` |
| RW-02 | — | `raw_prompt=""` | `422` | `test_rw02_empty_422` |
| RW-03 | — | 2001 字 | `422` | `test_rw03_2001_chars_422` |
| RW-04 | — | 2000 字 | `200` | `test_rw04_2000_chars_ok` |
| RW-05 | stub 返回**与输入完全相同**的文本 | `POST rewrite-prompt` | 视为复写失败：`iterations` 递增后重试（stub 记录到 ≥2 次调用） | `test_rw05_identical_retried` |
| RW-06 | stub **恒定**返回与输入相同的文本 | `POST rewrite-prompt` | `200` + `rewrite_failed=true` + `optimized_prompt == raw_prompt`（回落原文，不让用户卡死）；`iterations==3` | `test_rw06_persistent_identical_falls_back` |
| RW-07 | stub 首次 `quality_score=40`（< 60），第 2 次 88 | `POST rewrite-prompt` | `200`，最终 `quality_score==88`，`iterations==2` | `test_rw07_low_score_iterates` |
| RW-08 | stub 三次都 < 60 | `POST rewrite-prompt` | `200`，`iterations==3`（上限），取最后一次结果 | `test_rw08_iteration_cap_3` |
| RW-09 | stub 抛 `TimeoutError` | `POST rewrite-prompt` | `200` + `rewrite_failed=true` + `optimized_prompt == raw_prompt` | `test_rw09_timeout_falls_back` |
| RW-10 | 同一分钟内第 11 次调用 | `POST rewrite-prompt` | `429` | `test_rw10_rate_limit_429` |
| RW-11 | — | 用户 A / B：A 打 B 的 | `403` | `test_rw11_others_403` |
| RW-12 | 已复写过一次 | 再复写一次 | `prompt_draft` **仍只有 1 行**（`job_id` 唯一），`quality_score` 被更新 | `test_rw12_draft_upsert` |
| RW-13 | 复写成功 | `GET /api/jobs/{id}` | `prompt_draft.quality_score` 可见（前端要展示"从 42 分到 88 分"） | `test_rw13_score_exposed` |
| RW-14 | 付款方 `available=0` | `POST rewrite-prompt` | `402` | `test_rw14_insufficient_402` |

---

## 五、生成与把关（`test_studio_generate.py`）

| ID | 前置 | 输入 | 期望 | 测试函数 |
|---|---|---|---|---|
| GN-01 | `status=chatting`，stub 文案 + judge 90 | `POST /api/jobs/{id}/generate` `{prompt}` | `202` + `{status:"generating"}` | `test_gn01_generate_accepted` |
| GN-02 | 同上 | `await run_stage("generate", job_id)` | `status=ready`；`gen_output` 1 行、`type=copy`、`content` == stub 返回值（原样落库，不静默截断） | `test_gn02_copy_ready` |
| GN-03 | stub 视频 + judge 90 | `POST generate` → `run_stage` | `status=ready`；`gen_output.type=video`、`url` 非空、`cost_cents` 落库 | `test_gn03_video_ready` |
| GN-04 | judge 第 1 次 40、第 2 次 90 | `run_stage("generate", job_id)` | `retry_count==1`；旧 `gen_output.is_active=false`，新行 `true` | `test_gn04_low_score_auto_retry` |
| GN-05 | judge 恒 40，`retry_count` 跑到 3 | `run_stage` | `status=need_review`；`is_active=true` 仍恰好 1 条（**不自动删产物**） | `test_gn05_three_fails_need_review` |
| GN-06 | 跑过 3 次自动重试 | 查 `gen_output` | `is_active=true` 的**有且仅有 1 条**；`attempt` 从 1 递增、无重复 | `test_gn06_single_active_output` |
| GN-07 | stub 抛 `GenerationTimeout`（> 10 分钟） | `run_stage` | `status=failed` + `fail_reason="生成超时"` | `test_gn07_generation_timeout` |
| GN-08 | stub 视频 provider 抛错 | `run_stage` | `status=failed`；`judge` / `generate` **从未被以 `kling` 调用**（不自动切可灵，防重复计费） | `test_gn08_video_error_no_fallback` |
| GN-09 | `status=guard_failed` | `POST generate` | `409` | `test_gn09_after_guard_fail_409` |
| GN-10 | `status=ready` | `POST generate` | `409` | `test_gn10_ready_cannot_regenerate_409` |
| GN-11 | 该用户并发生成数已达上限 | `POST generate` | `429` | `test_gn11_concurrency_429` |
| GN-12 | 付款方 `available=0` | `POST generate` | `402` | `test_gn12_insufficient_402` |
| GN-13 | `status=failed`（生成超时），`retry_count=0` | `POST /api/jobs/{id}/retry` | `202`，`retry_count==1`，`status` 回到 `generating` | `test_gn13_retry_after_failure` |
| GN-14 | `retry_count=3` | `POST retry` | `409` | `test_gn14_retry_cap_409` |
| GN-15 | `status=guard_failed` | `POST retry` | `409`（素材问题，重试无意义） | `test_gn15_retry_on_guard_failed_409` |
| GN-16 | `status=need_review` | `POST retry` | `202`（转人工后允许再来一次） | `test_gn16_retry_on_need_review_ok` |
| GN-17 | — | 用户 A / B：A 打 B 的 generate / retry | `403` | `test_gn17_others_403` |
| GN-18 | `status=chatting` | `POST generate` 后查 `dispatch` | 排了一次 `("generate", job_id)` | `test_gn18_generate_dispatched` |
| GN-19 | BYOK job（`cost_cents=0`） | `run_stage` 后查流水 | **无 `consume` 流水**；`gen_output.billing_source=byok` | `test_gn19_byok_no_consume` |
| GN-20 | BYOK job | `run_stage` | `guard` 与 `judge` **都被调用过**（安全不因自带 Key 豁免） | `test_gn20_byok_still_guarded` |
| GN-21 | BYOK job，stub 抛 Key 失效 | `run_stage` | `status=failed`、`fail_reason="key_invalid"`；**平台 Key 从未被调用** | `test_gn21_key_invalid_no_platform_fallback` |
| GN-22 | `kind=video` 的 job（**追加 B 引入**），stub 抛 `VideoNotSupportedError` | `run_stage` | `status=failed`、`fail_reason` **恰好** `"视频模型尚未接入"`（不得套「生成失败：」前缀）；预占 `released` | `test_gn22_video_kind_lands_a_readable_fail_reason` |

---

## 六、job 视图与进度流（`test_studio_job_view.py`）

| ID | 前置 | 输入 | 期望 | 测试函数 |
|---|---|---|---|---|
| JQ-01 | job 有素材 / 草稿 / 产物 | `GET /api/jobs/{id}` | `200` + `{job, inputs, prompt_draft, outputs}`；`inputs` 按 `sort_order` 升序 | `test_jq01_job_detail_shape` |
| JQ-02 | job 详情 | 查响应 | 含 `provider` 与 `billing_source`（用户能对上账） | `test_jq02_billing_visible` |
| JQ-03 | — | 用户 A / B：A 查 B 的 | `403` | `test_jq03_others_403` |
| JQ-04 | — | `GET /api/jobs/999999` | `404` | `test_jq04_unknown_404` |
| EV-01 | 跑过 guard | `GET /api/jobs/{id}/events` | `200` SSE，至少含 1 个 `stage` 事件 | `test_ev01_events_stream` |
| EV-02 | — | 用户 A / B | `403` | `test_ev02_others_403` |
| ME-01 | 两用户各有 job | `GET /api/me/jobs` | `200` + `{items,total,page,size}`，**只含自己的** | `test_me01_my_jobs` |
| ME-02 | 同用户两个任务的 job | `GET /api/me/jobs?task_id=` | 只含该任务的 | `test_me02_filter_by_task` |
| ME-03 | — | 无 token | `401` | `test_me03_no_token_401` |
| ME-04 | 已软删的 job | `GET /api/me/jobs` | **不含**它 | `test_me04_deleted_excluded` |

---

## 七、归属、下载、删除（`test_studio_ownership.py`）

| ID | 前置 | 输入 | 期望 | 测试函数 |
|---|---|---|---|---|
| OW-01 | 用户 A / B | A 访问 B 的 job | `403`（**不是 404**：404 会把 id 存不存在透出去） | `test_ow01_cross_user_403_not_404` |
| OW-02 | 用户 A / B | A 下载 B 的产物 | `403` | `test_ow02_download_others_403` |
| OW-03 | `status=generating` | `GET /api/jobs/{id}/download/{output_id}` | `409` | `test_ow03_not_ready_409` |
| OW-04 | stub `presign_url` | `status=ready` 时下载 | `200` + `{url, expires_in:900}`；**seam 收到的 `expires_in` 恰为 900** | `test_ow04_download_url_15min` |
| OW-05 | 连申请两次 | — | 两次都 `200`，且 seam 被调用 2 次（不缓存旧链接） | `test_ow05_reissue_new_url` |
| OW-06 | `output_id` 不属于该 job | 下载 | `404` | `test_ow06_output_not_in_job_404` |
| OW-07 | `status=created` | `DELETE /api/jobs/{id}` | `204`；`deleted_at` 非空；**行仍在**（软删） | `test_ow07_soft_delete_created` |
| OW-08 | `status=guard_failed` | `DELETE` | `204` | `test_ow08_delete_guard_failed_ok` |
| OW-09 | `status=ready` | `DELETE` | `409` | `test_ow09_delete_ready_409` |
| OW-10 | `status=need_review` | `DELETE` | `409` | `test_ow10_delete_need_review_409` |
| OW-11 | 已删除 | `GET /api/jobs/{id}` | `404` | `test_ow11_deleted_detail_404` |
| OW-12 | 已删除 | 再 `DELETE` | `404` | `test_ow12_double_delete_404` |
| OW-13 | 用户 A / B | A 删 B 的 job | `403` | `test_ow13_delete_others_403` |
| OW-14 | `status=created` 且已预扣 | `DELETE` | `quota_reservation.status="released"`、`reserved` 归零、**无流水**；`user_pay_reimburse` 的报销池预占一并退回 | `test_ow14_delete_releases_reservation` |
| OW-15 | `status=created` | `DELETE` | `job_input_asset` / `chat_message` **仍保留**（MinIO 对象与记录都不删，只标记） | `test_ow15_delete_keeps_rows` |

---

## 八、prompt 模板（`test_studio_templates.py`）

| ID | 前置 | 输入 | 期望 | 测试函数 |
|---|---|---|---|---|
| TP-01 | 商户 | `POST /api/merchant/prompt-templates` 合法体 | `201` + `{template}`，`usage_count==0`、`is_public` 默认 false | `test_tp01_create` |
| TP-02 | — | `name` 1 字符 | `422` | `test_tp02_name_1_422` |
| TP-03 | — | `name` 65 字符 | `422` | `test_tp03_name_65_422` |
| TP-04 | — | `name` 2 字符 / 64 字符 | `201`（两端含等号） | `test_tp04_name_bounds_ok` |
| TP-05 | — | `content` 9 字符 | `422` | `test_tp05_content_9_422` |
| TP-06 | — | `content` 10 / 2000 字符 | `201` | `test_tp06_content_bounds_ok` |
| TP-07 | — | `content` 2001 字符 | `422` | `test_tp07_content_2001_422` |
| TP-08 | — | `tags` 6 个 | `422` | `test_tp08_tags_6_422` |
| TP-09 | — | `tags` 单个 17 字符 | `422`；16 字符 → `201` | `test_tp09_tag_len_422` |
| TP-10 | 客户令牌 | `POST /api/merchant/prompt-templates` | `403` | `test_tp10_customer_403` |
| TP-11 | 商户 A / B | A `PATCH` B 的模板 | `403`；B 改自己的 → `200` | `test_tp11_patch_others_403` |
| TP-12 | 商户 A / B | A `DELETE` B 的模板 | `403` | `test_tp12_delete_others_403` |
| TP-13 | — | `PATCH` / `DELETE` 不存在的 id | `404` | `test_tp13_unknown_404` |
| TP-14 | 自己的模板 | `GET /api/merchant/prompt-templates?keyword=` | `200` + `{items,total,page,size}`，含私有与公开、只含自己的 | `test_tp14_my_templates` |
| TP-15 | `is_public=true` 且未删 | 任何已登录用户 `GET /api/prompt-templates` | 出现 | `test_tp15_public_in_market` |
| TP-16 | `is_public=false` | 同上 | **不出现** | `test_tp16_private_not_in_market` |
| TP-17 | 商户 A 的私有模板 + 客户已领取 A 的任务 | 客户 `GET /api/tasks/{task_id}/prompt-templates` | 出现（私有 + 公开的并集） | `test_tp17_private_visible_to_claimer` |
| TP-18 | 商户 B 查 A 任务的模板列表 | 同上路径 | **不出现** A 的私有模板（B 不是该任务商户） | `test_tp18_other_merchant_private_hidden` |
| TP-19 | 未领取该任务的客户 | `GET /api/tasks/{task_id}/prompt-templates` | `403` | `test_tp19_not_claimed_403` |
| TP-20 | `is_public=true` 的模板 | 商户 B `POST .../copy` | `201`；`is_public=false`、`source_template_id` 指向原模板、`usage_count==0` | `test_tp20_copy_public` |
| TP-21 | `is_public=false` 的模板 | 商户 B `copy` | `404` | `test_tp21_copy_private_404` |
| TP-22 | 已软删的公开模板 | `copy` | `404` | `test_tp22_copy_deleted_404` |
| TP-23 | 复制后原模板被软删 | 查副本 | 副本仍可读写（**不被级联删**） | `test_tp23_copy_survives_source_delete` |
| TP-24 | 模板 + `status=chatting` 的 job | `POST /api/prompt-templates/{id}/apply` `{job_id}` | `200` + `{content}`，`usage_count` 恰好 +1 | `test_tp24_apply_bumps_usage` |
| TP-25 | 同上 | **100 并发** `apply` | `usage_count` 恰好 +100（原子自增，不得丢更新） | `test_tp25_concurrent_apply_atomic` |
| TP-26 | 无权使用的模板（商户 B 的私有模板） | 客户 `apply` | `403` | `test_tp26_apply_forbidden_403` |
| TP-27 | `status=generating` 的 job | `apply` | `409` | `test_tp27_apply_bad_status_409` |
| TP-28 | — | `apply` 后查 job | `status` **未变**（仍 `chatting`），只回 `content` | `test_tp28_apply_does_not_mutate_job` |
| TP-29 | 商户软删自己的模板 | 查模板市场、自己的列表、`prompt_template` 行 | 市场与列表都不含它；**行仍在**（软删）；历史 `prompt_draft` 不受影响 | `test_tp29_soft_delete` |

---

## 九、并发与成本熔断（`test_studio_limits.py`）

| ID | 前置 | 输入 | 期望 | 测试函数 |
|---|---|---|---|---|
| JC-01 | 同一用户已 3 个 running job | 建第 4 个 | `429` | `test_jc01_running_job_cap_429` |
| JC-02 | 同上 | 查前 3 个 job | **不受影响**（不得误杀） | `test_jc02_existing_jobs_intact` |
| JC-03 | 商户 `daily_limit=100`、当日已耗 900 | 两次建 job | 均 `429`，且 `budget_alert` **恰好 1 条**（一天一条） | `test_jc03_merchant_budget_429` |
| JC-04 | 同一 job | 查 `quota_reservation` | **恒为 1 行**（不得同时占两份） | `test_jc04_single_reservation_row` |
| JC-05 | BYOK job | `run_stage` 后查 `gen_output` / 流水 | `cost_cents==0`、`billing_source=="byok"`、无 `consume` 流水 | `test_jc05_byok_zero_cost` |
| JC-06 | 系统故障（stub 抛 5xx）导致 job `failed` | 查 `quota_reservation` / 流水 | 预占 `released`、`actual` 为 NULL、`reserved` 归零、**不产生流水** | `test_jc06_infra_failure_releases` |
| JC-07 | 产物把关 3 次不过 → `need_review` | 查流水 | 预占 `settled`，**额度不退**（已真实产生调用成本） | `test_jc07_need_review_still_billed` |
| JC-08 | 建 job 后新插一行更贵的价 | 查 `quota_reservation.reserved` | **不变**（仍按建 job 时刻的 120） | `test_jc08_price_frozen_at_job_time` |
| JC-09 | 同一 job 重复 `run_stage` 3 次 | 查流水 | `quota_ledger` 只多 **1 条** | `test_jc09_settle_idempotent` |

---

## 十、本趟同时转绿（07 第二趟，用例已在 07 计划里）

这些用例**现在在 07 的文件里**，落地 `POST /api/jobs` 后应自动由红转绿，
**本计划不重复实现**。核对时逐条确认它们变绿的**原因是断言真的过了**，
而不是被 `SchemaMissing` 之外的巧合蒙过去的：

| 文件 | 组 | 条数 | 依赖 |
|---|---|---|---|
| `test_token_reserve.py` | `RS-01`~`RS-12` | 12 | `content_job` + `quota_reservation` |
| `test_token_reserve.py` | `PB-01`~`PB-10` | 10 | `POST /api/jobs` |
| `test_token_limits.py` | `LM-01`~`LM-07` | 7 | `POST /api/jobs` |
| `test_token_limits.py` | `LM-08`~`LM-14` | 7 | `POST /api/jobs`（`LM-08`/`LM-09` 曾因路由 404 而平凡绿，**本趟必须复查**） |

### 本趟要新建的 07 表

| 表 | 关键约束 | 备注 |
|---|---|---|
| `quota_reservation` | `job_id` **唯一**；`status` ∈ `reserved`/`settled`/`released`；`reserved_points`、`settled_points`、`reimbursed` | `job_id` **本趟才有外键**指向 `content_job`（第一趟刻意留成裸列） |
| `reimburse_claim` | `post_id` **唯一**（重复报）；`status` ∈ `reserved`/`settled`/`capped`/`released` | `post_id` 指向 04 的 `social_post`，**04 未落地故仍为裸列 + 唯一索引**，与 `quota_ledger.job_id` 同一手法 |
| `reimburse_claim_job` | `job_id` **唯一**（一个 job 只进一张报销单） | |

`RB-01`~`RB-19`（报销）**本趟仍红**：它们要 04 的 `social_post` 与审核动作，
不给假表硬撑。这一组等 04。

---

## 已知取舍

1. **AI 与存储全部打桩，本趟不接真实 provider**（2026-09-15 确认）。
   用例只钉「接口契约 + 状态机 + 计费」——真调 DeepSeek / qwen3-vl / 即梦会让
   测试依赖网络、第三方可用性与真金白银。真实链路在联调时人工验一次，
   届时只需替换 `app/services/ai.py` 的实现，**用例一行不改**。
2. **`OW-04` 只钉「`expires_in` 恰为 900」这一个契约，不真的等 16 分钟**。
   预签名 URL 的过期由 MinIO 在对象存储侧判定，**不在本进程内**；要在测试里
   造出「16 分钟后访问被拒」只能靠假时钟假装，那测的是假时钟不是系统。
   故改为钉住我们真正掌握的那一半：**下发时确实要求 900 秒**。
3. **`GD-03` / `GD-06` 走同一段代码**，差异只在 stub 的 `reason`（见上文注）。
   保留是因为 spec 把四类不合格素材各写成一条边界，它们钉的是「任一类都不得放行」。
4. **「生成文案不含『作为 AI 助手』等元话语」不在本趟断言**。
   这是 provider 提示词层的责任，不是本进程的后处理。`GN-02` 改为断言
   **产物原样落库**（不静默截断/改写），这才是本进程能守的契约。
   若将来决定做元话语后处理，再补一条专门的用例。
5. **「预检通过后改选 provider」无对应端点**（spec 的端点表里没有「改 provider」，
   `retry` 也不带请求体）。该条以 `JC-04`「同一 job 的 `quota_reservation` 恒为 1 行」
   来守「不得同时占两份」这个实质约束；真要做改选，先补 spec。
6. **`JB-27`（一 claim 多 job）是「spec 没禁止」的推论，不是 spec 明写**。
   写出来是为了把它钉成**有意的**行为：将来若要求一 claim 一 job，这条会红，
   提醒改的人那是产品决策而非顺手加个唯一索引。
7. **`JC-03` 的「商户当日生成预算」需要一个可配的阈值**。03 的 spec 只写了
   「超预算 → `429` + 告警」，没给数。实现取配置项（与 07 的 `daily_limit`
   同一套口径），用例把阈值调成极小值来触发，**不去猜生产默认值**。
8. **`budget_alert` 表归 06？否——本趟自建**。它记的是「熔断发生」这一事实，
   与 06 的 `admin_action_log`（人工操作留痕）不是一回事。放 03 的迁移里，
   07 的 `LM-07` 直接复用这张表。
9. **`ME-*` 的 `size` 上限沿用 02/07 的 `100`**。03 的 spec 未规定，取既有的
   分页约定，避免系统里出现两套。
10. **`OW-15`（软删保留子行）钉的是「软删只标记一行」**。spec 的
    「MinIO 对象保留不删」在本进程内无法直接观测（存储是打桩的），
    改为断言**关联行未被级联删除** + 存储接缝**没收到删除调用**。
11. **`RW-05`/`RW-06` 的「相似度 = 100%」用「字符串完全相等」实现**。
    spec 写的是「相似度」，但引入相似度算法（编辑距离？余弦？）会把阈值变成
    另一个需要拍板的参数。取「完全相同」这个最保守的解释：它一定能被判定为
    「复写没干活」，且不会误伤真实复写结果。真要做模糊相似度，需先补 spec。
12. **`CH-10`（中途断开）用 `httpx` 读到第 1 段就 `close()` 来模拟**。
   ASGI 传输下不会有真 TCP 断开，但「客户端提前停止消费」这一条路径是等价的：
   实现必须把已产出的 assistant 消息落库、且不因断开回滚 `job.status`。
13. **单次报销预占额 = 该 job 的 `est_price_per_call`**（与预扣同源的上界），
   `reimburse_per_user_limit == 0` 时取 0（纯用户自费，不占池子）。
   **这是本计划新定的口径，spec 没写**——它只写了「池子可用额 >= `reimburse_reserved`」。
   取上界是为了保守高估（宁可不放行也不穿池子）。若将来改成按实际用量预占，
   `JB-24` / `JB-25` 两条要一起改。
14. **`JC-03` 的「商户当日预算」直接复用 `quota_account.daily_limit`**，
   不再新造一个配置项。理由：07 已经有这个列、也已经有一类「日限额」语义，
   再引入一个配置旋钮会让「到底哪个在拦」变成两个可能。
   `budget_alert` 表由 03 建（`user_id` + `day` + `kind` 唯一，保证一天一条）。
15. **`JC-08` 用「预扣额不变」来断言价格冻结**，而不是去比结算金额。
   结算金额依赖真实用量，而用量是桩编的；预扣额才是**建 job 那一刻**由计价行
   直接推出来的量，改价改不动它，正是这条规则要守的东西。
16. **`test_tp18`（别的商户看不到 A 的私有模板）接受两种结果**：明确 `403`，
   或 `200` 但不含那条模板。spec 的措辞是「不应出现」，403 是更强的「不出现」。
17. **`PRIMARY_OP`（`kind → op` 映射）最终落在 `app/models/studio.py`，不是
   `app/api/studio_job.py`**（2026-09-15 补记）。原因：07 的 `quota.py` 也要按
   `kind` 推 op 来算价（报销预占额），而 `services` 层 import `api` 层是反向依赖。
   放模型层则 `api` 与 `services` 都能单向引用。当前 `studio_job.py` 与
   `quota.py` 都是 `from app.models.studio import PRIMARY_OP`。

## 下一步

按 `JB-01 → JB-02 → … → GD-01 → … → CH-01 → …` 顺序补实现，
**一个端点跑完红绿再下一个**。本趟一次性新建 03 的 8 张表 + 07 的 3 张表
（`drop_all` + `create_all` 会自动带上），再逐端点转绿。

全部转绿后：①核对 07 的 `RS`/`PB`/`LM` 是否真转绿（尤其 `LM-08`/`LM-09`
之前是平凡绿）；②**反向验证**——故意把 `reserve` 改成「先写流水再校验额度」，
`JB-23` 与 `RS-08` 必须立刻变红；③更新 `CLAUDE.md` 工作记录。

---

# 追加 B 测试计划（2026-09-16）：真接 DeepSeek + 计价写死在代码里

> 对应 `spec.md` 的「# 追加 B」。**上一段的 150 条全部保持不动**，
> 本段只加 AI 真实现 / 计价常量 / 种子三块的用例。
>
> 覆盖：**39 条**（`AI` 18 / `PR` 8 / `SN` 10 / `GR` 2 / `GN` 1）。
> 三个新文件：`test_studio_ai.py` · `test_studio_pricing.py` · `test_studio_seed.py`。
>
> ⚠️ 第 39 条 `GN-22` 落在**第五节的 GN 表**里（`test_studio_generate.py`），不在这三个新文件。
> 补它的原因：spec.md:469 把「`kind="video"` → `failed(fail_reason="视频模型尚未接入")`」
> 写成了契约，而边界清单里只有 `AI-14` 管「抛不抛」这一半，「落库写什么」那一半没人守。
> 写完才发现的漏项——**不是**写测试时就有意留的。

---

## 测试缝（新增一处，其余沿用上一段）

上一段的接缝是 `app/services/ai.py` 的六个函数（名字即契约）。
本段**不改签名**，只把函数体从 `raise NotImplementedError` 换成真 HTTP 调用。
新增一处：

| 缝 | 名字 | 用途 |
|---|---|---|
| 计价常量 | `app.services.pricing.PRICES` | 常量表（`op → 那一行`）；`seed.py` 把它投影进 `model_price` |

配置项（`app/config.py` 的 `Settings`）也是契约：
`deepseek_api_key` · `deepseek_base_url` · `deepseek_text_model` ·
`deepseek_vision_model` · `deepseek_timeout_seconds` · `deepseek_max_tokens`。

**打桩位置在 transport 层**：用 `httpx.MockTransport` 换掉实现内部的 transport，
而不是 monkeypatch `ai.py` 的函数。理由：这一趟要测的正是「HTTP 请求发得对不对」
（URL / 头 / 体 / 状态码分类），把函数整个换掉等于把被测对象换掉了。
故 `AiStub`（上一段那套）**测不了本段**，本段自带一个 `DeepSeekStub`（见下）。

---

## 用例里反复出现的前置（新增）

| 工厂 | 作用 |
|---|---|
| `deepseek_stub(monkeypatch)` | 装 `httpx.MockTransport`，记下每个请求的 `url` / `headers` / `json`，按队列回响应 |
| `settings_override(monkeypatch, **over)` | 覆盖 `get_settings()` 的返回值（临时给 key / 改模型名） |

`deepseek_stub` 的默认响应：`200` + 一段合法 `chat/completions` 体。
需要别的情况时 `stub.push(status=401)` / `stub.push(status=429)` / `stub.push(body=...)`。

---

## 一、AI 真实现的契约（`test_studio_ai.py`，18 条）

| ID | 断言 |
|---|---|
| `AI-01` | 六个函数签名与 `ai.py` 现状**逐字一致**（`inspect.signature` 比对） |
| `AI-02` | 未配置 `deepseek_api_key` → 六个函数都抛 `AIConfigError`，且**一个 HTTP 请求都没发** |
| `AI-03` | 请求 URL == `{deepseek_base_url}/chat/completions`，头带 `Authorization: Bearer <key>` |
| `AI-04` | `chat_stream` 体里 `stream is True`、`model == settings.deepseek_text_model` |
| `AI-05` | `chat_stream` 逐段解析 `data:` 行 → 分出 3 段，拼接 == 3 段之和 |
| `AI-06` | `data: [DONE]` 之后**不再产出**新段 |
| `AI-07` | 上游 401 → 抛 `KeyInvalidError`；403 → 同样 |
| `AI-08` | 上游 429 → 抛**普通异常**（`not isinstance(exc, KeyInvalidError)`） |
| `AI-09` | 上游 500 → 同 `AI-08` |
| `AI-10` | 上游不响应（超时）→ 抛普通异常，**不挂死**（用 `MockTransport` 里 `asyncio.sleep` 超过 `deepseek_timeout_seconds`） |
| `AI-11` | `rewrite_prompt` 上游回合法 JSON → `RewriteResult` 三字段齐全 |
| `AI-12` | `rewrite_prompt` 上游回非 JSON 文本 → **抛异常** |
| `AI-13` | `generate(kind="copy")` → `content` 非空、`url is None`、`units == 响应里的 usage.total_tokens` |
| `AI-14` | `generate(kind="video")` → 抛 `VideoNotSupportedError`，且**一个 HTTP 请求都没发** |
| `AI-15` | `units=1000` → `cost_cents == ceil(price_per_unit × 1000)`，与 `pricing.PRICES` 同源 |
| `AI-16` | `guard_assets` / `ocr_metrics` 体里 `model == settings.deepseek_vision_model`，且 messages 里有图片 |
| `AI-17` | 改 `settings.deepseek_text_model` 后请求体里的 `model` **跟着变** |
| `AI-18` | `ocr_metrics` 认不出数字 → `parsed is None`（**不是** `{"likes": 0, ...}`），且 `0.0 <= confidence <= 1.0` |

### 测试护栏（2 条，同文件）

| ID | 断言 |
|---|---|
| `GR-01` | conftest 装的护栏函数被直接调用 → `AssertionError`，信息含 `patch_ai` |
| `GR-02` | 装了 `patch_ai` 的用例不受护栏影响（**既有 03 用例全绿即证明**，本文件只留一条冒烟） |

---

## 二、计价常量与投影（`test_studio_pricing.py`，8 条）

| ID | 断言 |
|---|---|
| `PR-01` | `PRICES` 恰好 **5 行**，op 覆盖 chat / rewrite / generate / judge / guard |
| `PR-02` | 五行 `provider == "deepseek"`；`guard` 行 model == 视觉模型，其余四行 == 文本模型 |
| `PR-03` | 跑完种子后 `model_price` 的行与 `PRICES` **逐字段相等** |
| `PR-04` | 改 `PRICES` 里某行 `price_per_unit` 再跑种子 → 库里那行**跟着变** |
| `PR-05` | 跑两次种子 → `model_price` 行数不变（`effective_from` 固定，不重复插） |
| `PR-06` | 五行 `unit == "token"`，且 `price_per_unit / cost_price_per_unit == markup_rate`（1.50） |
| `PR-07` | `GET /api/models?op=chat` → `200`，含该行，且 `est_price_per_call == max_price_per_call == 300` |
| `PR-08` | 种子后建 `kind=copy` job → **`201`**（不再是 `503`） |

---

## 三、种子（`test_studio_seed.py`，10 条）

| ID | 断言 |
|---|---|
| `SN-01` | 跑两次 `seed()` → 第二次返回的 `created == 0`，`task` 仍恰好 2 条 |
| `SN-02` | 两个演示账号各有 `quota_account`，`balance` 5000 / 2000，四个 `*_limit` 为 NULL |
| `SN-03` | `task` 恰好 2 条，`pay_mode` 分别 merchant_pay / user_pay_reimburse，`status` 都 published |
| `SN-04` | 两条任务各一条 `reward_rule`，`tiers` 2 档且末档 `max` 为 `null` |
| `SN-05` | 任务 2 的 `reimburse_pool=1000` / `reimburse_per_user_limit=500`，且 `500 >= 300` |
| `SN-06` | 测试用户对两条各有 `task_claim(status="in_progress")` → `GET /api/me/claims` 回 `total == 2` |
| `SN-07` | `GET /api/tasks?keyword=` 两条演示任务都可见 |
| `SN-08` | `APP_ENV=production` 跑 `python -m app.seed` → 退出码 1，库里**一行都没写** |
| `SN-09` | **账真的会动**：走完一个 copy job → 用户 `balance` 恰好少 `cost_cents`，`reserved` 回 0，`debt == 0` |
| `SN-10` | `quota_ledger` 多恰好 **1** 条 `source="consume"`；`quota_reservation` 那行为 `settled` |

---

## 已知取舍（本段）

1. **`AI-10` 的「超时」用 `asyncio.sleep(2)` + `deepseek_timeout_seconds=0.2` 模拟**。
   真等 60 秒会让这一条独占一分钟。断言的是「抛得出来」，不是「抛得正好在第 0.2 秒」。
2. **`AI-15` 的 `cost_cents` 用 `ceil` 而非 `round`**。点数是整数，`0.003 × 1000 = 3.0`
   正好整除；但 `units=1001` 时 `3.003` 若 `round` 会得 3、`int()` 会得 3，
   只有 `ceil` 保证「**不低估**成本」。这条口径由 `AI-15` 钉住。
3. **「价写死在代码里」的实际含义是「常量作源、种子作投影」**，不是「不写库」。
   `resolve_price` / `GET /api/models` / 预扣额三处都读库，且都有既存用例锁着；
   绕开库等于打翻它们。`PR-04` 就是这条设计的验收：改常量 → 库里跟着变。
4. **演示口径不是账单口径**。真实 DeepSeek 1.5 元/百万 token ≈ 0.00000015 元/token，
   而「点」是整数，照实换算每次调用都是 0 点、余额永远不动，界面上的计费展示
   会变成一块死的装饰。故取 `0.003 点/token`（一次 ~1000 token ≈ 3 点）。
   **这是演示参数，不是汇率**，改它只改 `pricing.py` 一行。
5. **`GR-01`/`GR-02` 的护栏是 autouse 的**，位置在 `conftest.py`。
   风险：将来有一条用例**故意**要跑真 `ai.py` 而忘了声明 `deepseek_stub`，
   它会当场炸而不是静默打网络——这正是护栏想要的行为，故接受。
6. **`SN-09`/`SN-10` 走的是**已有**的预扣/结算链路**（`reserve` → `settle`），
   本段一行没碰它。它们守的是「换掉计价来源之后账还对不对」，不是「结算逻辑对不对」
   （那是 07 的 `RS-*` / `JC-*` 的活）。
7. **`SN-08` 不能在本进程内改 `APP_ENV` 再 `import`**——`Settings` 在 import 时读环境变量。
   用例改为 `subprocess.run([sys.executable, "-m", "app.seed"], env={...APP_ENV: production})`，
   断言 `returncode == 1` 且库里无新行。**必须真起子进程**，否则测的是假的。
8. **视频闸门在「生成」这一关**（`VideoNotSupportedError`），不在 `POST /api/jobs`。
   `JOB_KINDS` / `PROVIDER_KINDS` 一行不动，故 6 处既存的 video 用例零回归。
   `AI-14` 钉的是「不发请求」，回归由那 6 条继续守着。

---

## 下一步

1. 先跑本段三组用例，确认**全红**且红因只有两类：
   `cannot import name 'pricing'` / `TypeError: <六个函数仍 raise NotImplementedError>`
   ——不出现 `TestBug`、不出现 `KeyError: 'choices'` 之类「测试自己写错」的红。
2. 再补实现：`config.py` 加六个配置 → `services/pricing.py` 建常量表 →
   `ai.py` 六个函数填真调用 → `seed.py` 扩种子 → `conftest.py` 装护栏。
3. **反向验证**（实测四处，全部命中）：
   - 把 `ai.py` 里 401/403 的分类从 `KeyInvalidError` 改成普通异常 → `AI-07` 红，
     且 `AI-08` / `AI-09` **仍绿**（它们只断「不是 Key 失效」，正确地不受影响）；
   - 把 `pricing.py` 的 `price_per_unit` 从 0.003 改成 0.004 → `AI-15` + `PR-06` 红。
     ⚠️ **计划里写的 `PR-03` 没有红，这是对的**：`PR-03` 比的是「库 vs 常量」，
     常量一动、种子一跑，两边**一起**动，它天然测不出常量本身错了——
     能抓住「价写错」的是 `PR-06`（`price == cost × markup`）与 `AI-15`（那个手算的 3）。
     原计划把它列成探测器是**计划本身的错**，这里订正。
   - 把 `seed.py` 的 `effective_from` 从固定值改成 `now()` → `PR-05` + `SN-01` 红，
     外加 `PR-04` 一起红（`effective_from` 每次都新，upsert 永远匹配不上，
     「改常量 → 库里跟着变」这条链直接断）。
   - 去掉 `pipeline._run_generate` 里 `except VideoNotSupportedError` 那一支 →
     `GN-22` 红（`fail_reason` 变成 `生成失败：视频模型尚未接入`）。
4. 全量回归：`898 + 39 = 937`，重定向到文件跑，**同一时刻只跑一个 pytest**。
   ✅ 实测 `937 passed in 3133.76s (0:52:13)`，0 failed。
   ⚠️ 基线是 **898** 不是 829：`HANDOVER.md` §四 比 `CLAUDE.md` 新
   （829 + 06 追加 57 条反馈 + 12 条静态挂载 = 898）。本轮 39 条全绿，数目对得上。

---

# ׷�� A��2026-09-17�����ز��ϴ� �� `UP`��Լ 24 ����

> Spec��`specs/03-studio/spec.md` ׷�� A������������ `storage.sniff_mime`��`presign_url` ������

## �ϴ� �� `UP`��`POST /api/uploads`��

| ��� | ���� | ���� |
|---|---|---|
| UP-01 | δ��¼ | 401 |
| UP-02 | �� `file` �ֶ� | 422 |
| UP-03 | �ֶ��� `upload` / `image` | 422 |
| UP-04 | `.txt` ���� `.jpg` | 415 |
| UP-05 | GIF / PDF | 415 |
| UP-06 | ǡ�� 20MB PNG | 201 |
| UP-07 | 20MB + 1 | 413 |
| UP-08 | �Ϸ� PNG | 201��`mime=image/png`��`size_bytes` ׼ |
| UP-09 | Content-Type �ѱ� jpeg���ֽ��� PNG | ���� `image/png` |
| UP-10 | 0 �ֽ� | 415 |
| UP-11 | �ļ��� `../../etc/passwd` | url �� passwd��Ŀ¼�������ļ� |
| UP-12 | �ļ����� `<script>` | ������ |
| UP-13 | ͬ�ļ��������� | ������ͬ url�����ļ����� |
| UP-14 | �ϴ�Ŀ¼������ | �Զ��������� 500 |
| UP-15 | 201 ��ǡ�� `{url,mime,size_bytes}` ���� | |

## ��ȡ �� `UG`��`GET /api/uploads/{name}`��

| ��� | ���� | ���� |
|---|---|---|
| UG-01 | �ϴ������� GET url | 200�����ֽ�һ�� |
| UG-02 | Content-Type = ��ֵ̽ | |
| UG-03 | ������ `.jpg` | 404��`assert_route_registered`�� |
| UG-04 | `..%2f..%2fconfig.py` | 400������ config ���� |
| UG-05 | `a%2fb.png` | 400 |
| UG-06 | ����չ�� | 400 |
| UG-07 | ���Ȩ GET | 200 |

## �ӷ�

| ��� | ���� | ���� |
|---|---|---|
| US-01 | `sniff_mime` �� jpeg/png/webp ħ�� | �Ե��� |
| US-02 | �Բ��� / ̫�� | `None` |

---

# 追加 C/D 测试计划（2026-09-18）：IR / CF / VG / BK

> 对应 `spec.md` 追加 C（base64）与追加 D（平台 Key + 视频 + BYOK 放宽）。

| ID | 前置 | 动作 | 期望 | 文件 |
|---|---|---|---|---|
| IR-01 | 本地落盘 png | `guard_assets` 经 MockTransport | 请求体 image_url 以 `data:image/` 开头 | `test_studio_image_ref.py` |
| IR-02 | https URL | 同上 | image_url 仍为原 https | 同上 |
| IR-03 | 已是 data: | 同上 | 不二次编码 | 同上 |
| IR-04 | 路径不存在 | `guard_assets` | 抛异常且零上游请求 | 同上 |
| IR-05 | 本地上传 | `ocr_metrics` | 与 guard 同一套 data: | 同上 |
| CF-01 | — | 读 `.env.example` | 含三行 KEY | `test_studio_config_keys.py` |
| CF-02 | — | Settings | jimeng/kling 字段默认可空 | 同上 |
| CF-03 | 改 deepseek_api_key | 发请求 | Bearer 跟着变 | 可并入 AI 或本文件 |
| VG-01 | jimeng key 空 | `generate(kind=video, provider=jimeng)` | VideoNotSupportedError，零 HTTP | `test_studio_video_gen.py` |
| VG-02 | kling key 空 | provider=kling | 同上 | 同上 |
| VG-03 | jimeng key 非空 + Mock 成功 | generate video | url 非空、content is None | 同上 |
| VG-04 | key 非空 + 401 | generate | KeyInvalidError | 同上 |
| VG-05 | — | 跑 AI-14 / GN-22 | 仍绿 | 既有文件 |
| BK-01 | merchant_pay + 有 Key | 建 job byok | 201 | 改 `test_jb19` / `test_pb02` |
| BK-02 | byok 无 Key | 建 job | 422 detail 非空 | 既有 pb03/jb20 |
