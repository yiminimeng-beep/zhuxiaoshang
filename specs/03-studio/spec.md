# 03 · 内容工坊（核心）

> 用户上传素材 → 与 AI 对话确定创作方向 → 提示词复写 → 生成文案/视频 → 双重把关 → 下载。

---

## 一句话目标

> 用户上传商品照片后可跟 AI 商量要做什么内容，系统把用户粗糙的提示词改写为高质量提示词，异步生成文案或视频；生成前后各有一次大模型把关，挡住无关/违规素材和低质产物，最终产出用户可下载的作品。

---

## 流水线（固定顺序，不可跳步）

```
① 素材预检 guard      qwen3-vl 判断：是否是商品图 / 与任务是否相关 / 是否违规
                      ↓ 不通过 → status=guard_failed（终止，不进后续）
② AI 对话 chat        DeepSeek，帮用户想创意，产出提示词草稿
                      ↓
③ 提示词复写 rewrite  prompt-optimizer，粗糙提示词 → 优化提示词 + 质量分
                      ↓
④ 生成 generate       文案：DeepSeek / 视频：即梦·可灵适配层
                      ↓
⑤ 产物把关 judge      LLM-as-judge 打分（相关性/合规/质量）
                      ↓ 低于阈值 → 自动重试（≤3 次）→ 仍不过 → need_review
⑥ 就绪 ready          可下载（预签名 URL，15 分钟有效）
```

**全程异步**：`①` 完成后即返回；`②③④⑤` 之间用队列串联，通过 SSE 推送进度。

---

## 数据模型

### `content_job`
| 字段 | 类型 | 约束 |
|---|---|---|
| id | bigint | 主键 |
| task_id | bigint | **必填**，外键 |
| claim_id | bigint | **必填**，外键 → task_claim（**必须先领取**） |
| user_id | bigint | **必填**，外键 |
| kind | enum | **必填**，`copy`（文案）/ `video` |
| status | enum | **必填**，`created` / `guarding` / `guard_failed` / `chatting` / `generating` / `judging` / `ready` / `need_review` / `failed` |
| fail_reason | text | 可空 |
| retry_count | int | **必填**，默认 0，上限 3 |
| deleted_at | timestamptz | 可空；**非空 = 已软删**，查询默认过滤 |
| created_at / updated_at | timestamptz | 必填 |

### `job_input_asset`
| 字段 | 类型 | 约束 |
|---|---|---|
| id | bigint | 主键 |
| job_id | bigint | **必填**，外键 |
| url | varchar(512) | **必填** |
| mime | varchar(32) | **必填**，仅 `image/jpeg` `image/png` `image/webp` |
| size_bytes | int | **必填**，<= 20MB |
| sort_order | int | **必填**，0~8 |

### `chat_message`
| 字段 | 类型 | 约束 |
|---|---|---|
| id | bigint | 主键 |
| job_id | bigint | **必填**，外键 |
| role | enum | **必填**，`user` / `assistant` |
| content | text | **必填**，<= 4000 字符 |
| model | varchar(64) | assistant 时必填 |
| tokens | int | 可空 |
| created_at | timestamptz | 必填 |

### `prompt_draft`
| 字段 | 类型 | 约束 |
|---|---|---|
| id | bigint | 主键 |
| job_id | bigint | **必填，唯一**（一 job 一份） |
| raw_prompt | text | **必填**，1~2000 字符（用户输入） |
| optimized_prompt | text | 可空（复写后） |
| quality_score | int | 可空，0~100 |
| iterations | int | **必填**，默认 0，上限 3 |
| rewrite_failed | boolean | **必填**，默认 false（复写无效时置 true） |
| optimizer_model | varchar(64) | 可空 |

### `gen_output`
| 字段 | 类型 | 约束 |
|---|---|---|
| id | bigint | 主键 |
| job_id | bigint | **必填**，外键 |
| type | enum | **必填**，`copy` / `video` |
| content | text | copy 必填 |
| url | varchar(512) | video 必填 |
| provider | varchar(32) | 必填，如 `deepseek` / `jimeng` / `kling` |
| cost_cents | int | **必填**，默认 0 |
| judge_score | int | 可空，0~100 |
| judge_detail | jsonb | 可空，`{relevance, compliance, quality, reasons[]}` |
| attempt | int | **必填**，第几次尝试（1 起） |
| is_active | boolean | **必填**，默认 true（重试时旧的置 false） |
| created_at | timestamptz | 必填 |

### `job_event`（进度流，SSE 用）
| 字段 | 类型 | 约束 |
|---|---|---|
| id | bigint | 主键 |
| job_id | bigint | **必填**，外键，索引 |
| stage | varchar(32) | **必填** |
| detail | jsonb | 可空 |
| created_at | timestamptz | 必填 |

### `guard_result`
| 字段 | 类型 | 约束 |
|---|---|---|
| job_id | bigint | 主键，外键 |
| passed | boolean | **必填** |
| reason | text | 可空（不通过时必填） |
| model | varchar(64) | 必填 |
| detail | jsonb | 可空 |

### `prompt_template`（商户预设模板 / 模板市场）
| 字段 | 类型 | 约束 |
|---|---|---|
| id | bigint | 主键 |
| merchant_id | bigint | **必填**，外键 → user（role=merchant） |
| name | varchar(64) | **必填**，2~64 字符 |
| content | text | **必填**，10~2000 字符（与 `prompt_draft.raw_prompt` 同长度上限） |
| category | varchar(32) | 可空 |
| tags | jsonb | 可空，数组；最多 5 个，每个 1~16 字符 |
| is_public | boolean | **必填**，默认 false。true = 进模板市场，所有商户可见 |
| source_template_id | bigint | 可空，外键 → prompt_template（**从市场复制来的溯源**） |
| usage_count | int | **必填**，默认 0 |
| deleted_at | timestamptz | 可空；非空 = 已软删 |

**可见性规则**（三条，测试逐条覆盖）
1. `is_public=true` 且未软删 → **所有商户**可见，可复制
2. `is_public=false` → 仅创建者商户自己可见，**以及该商户任务下的客户**（客户在内容工坊里能套用）
3. 已软删 → 任何人不可见，已复制的副本不受影响

---

## 端点 / 接口

| 动作 | 输入 | 成功返回 | 失败情况 |
|---|---|---|---|
| 建 job | `POST /api/jobs`<br>`{task_id, kind, assets:[{url,mime,size_bytes}], provider?, billing_source?}` | `201`<br>`{job_id, status:"guarding", provider, billing_source, reserved_points, reimburse_reserved}` | `403` 未领取该任务<br>`422` 素材 0 张或 > 9 张<br>`415` 非图片 mime<br>`413` 单文件 > 20MB<br>**`422` 传 `merchant_id`/`payer`/`user_id`**<br>**`422` `billing_source=byok` 但 `pay_mode=merchant_pay`**<br>**`422` `billing_source=byok` 但无该 provider 有效 Key**<br>**`422` `provider` 不存在 / 不可见 / 与 `kind` 不匹配**<br>**`402` 付款方额度不足**<br>**`429` 报销池不足（`user_pay_reimburse`）**<br>`429` 并发/日限额超限<br>`403` 商户已冻结<br>`503` 计价表缺该组合 |
| 预检结果 | `GET /api/jobs/{id}/guard` | `200`<br>`{passed, reason?, detail}` | `404`<br>`403` 非本人<br>`409` 预检未完成 |
| 对话 | `POST /api/jobs/{id}/chat`<br>`{message}` | `200` **SSE 流** | `422` 空消息 / > 4000 字<br>`409` 已超 20 轮<br>`403` 非本人<br>`402` 额度不足（流开始前校验） |
| 对话历史 | `GET /api/jobs/{id}/chat` | `200` `{messages[]}` | `403` |
| 复写提示词 | `POST /api/jobs/{id}/rewrite-prompt`<br>`{raw_prompt}` | `200`<br>`{optimized_prompt, quality_score, rewrite_failed}` | `422` 空 / > 2000 字<br>`403`<br>`429` 复写调用超频<br>`402` 额度不足 |
| 开始生成 | `POST /api/jobs/{id}/generate`<br>`{prompt}` | `202`<br>`{status:"generating"}` | `409` 预检未通过<br>`409` 状态不允许<br>`429` 并发生成数超限<br>`402` 额度不足 |
| 查 job | `GET /api/jobs/{id}` | `200`<br>`{job, inputs, prompt_draft, outputs[]}` | `403` |
| 进度流 | `GET /api/jobs/{id}/events` | `200` **SSE** | `403` |
| 我的 job 列表 | `GET /api/me/jobs?task_id=` | `200` `{items,...}` | `401` |
| 下载 | `GET /api/jobs/{id}/download/{output_id}` | `200`<br>`{url, expires_in:900}` | `403` 非本人<br>`409` 产物未就绪<br>`403` 链接已过期 |
| 重试 | `POST /api/jobs/{id}/retry` | `202` | `409` `retry_count >= 3`<br>`409` 状态不允许<br>`403` |
| 删除 | `DELETE /api/jobs/{id}` | `204` **软删**（`deleted_at`） | `409` 状态非 `created`/`guard_failed`<br>`403` |

### 模板相关端点

| 动作 | 输入 | 成功返回 | 失败情况 |
|---|---|---|---|
| 建模板（商户） | `POST /api/merchant/prompt-templates`<br>`{name, content, category?, tags?, is_public?}` | `201` `{template}` | `403` 非商户<br>`422` 校验失败 |
| 改模板（商户） | `PATCH /api/merchant/prompt-templates/{id}` | `200` `{template}` | `403` 非本人<br>`404` |
| 删模板（商户） | `DELETE /api/merchant/prompt-templates/{id}` | `204` **软删** | `403` 非本人<br>`404` |
| 我的模板 | `GET /api/merchant/prompt-templates?keyword&page&size` | `200` `{items,...}` | `401` |
| 模板市场 | `GET /api/prompt-templates?category&keyword&page&size` | `200` `{items,...}`<br>仅 `is_public=true` 且未软删 | `422` 参数非法 |
| 某任务可用模板 | `GET /api/tasks/{task_id}/prompt-templates` | `200` `{items,...}`<br>= 该商户私有模板 + 全部公开模板 | `403` 未领取该任务<br>`404` |
| 复制公开模板 | `POST /api/merchant/prompt-templates/{id}/copy` | `201` `{template}`<br>`source_template_id` 指向原模板，`is_public=false` | `403` 非商户<br>`404` 非公开或不存在 |
| 套用模板 | `POST /api/prompt-templates/{id}/apply`<br>`{job_id}` | `200` `{content}`，`usage_count + 1` | `403` 无权使用<br>`409` job 状态不允许 |

---

## 边界（每条之后会变成一条测试）

### 素材预检（**必须挡住，这是产品底线**）
- 上传宠物照片去做"奶茶店新品"任务 → 预检不通过，`status=guard_failed`，`reason` 非空 → **不得进入 generating**
- 上传风景照（无商品主体）→ 不通过
- 上传含明显违规内容（文字/画面）→ 不通过
- 上传带其他品牌水印的图 → 不通过（避免侵权）
- 材质合格的奶茶商品图 → 通过，`status` 推进到 `chatting`
- 预检服务超时/报错 → `status=failed` + `fail_reason`，**不允许默认放行**
- 预检不通过时 `POST /generate` → `409`

### 素材校验
- `assets=[]` → `422`
- `assets` 10 张 → `422`；9 张 → 通过
- `mime=application/pdf` → `415`；`image/gif` → `415`
- `size_bytes=20971520`（20MB 整）→ 通过；`20971521` → `413`
- 未领取任务直接建 job → `403`
- 用别人的 `claim_id` 建 job → `403`
- `kind` 传非法值 → `422`

### 对话
- 空字符串消息 → `422`
- 4001 字消息 → `422`；4000 字 → 通过
- 第 21 轮对话 → `409`（上一轮落库，本轮拒绝）
- 流式响应中途用户断开 → 已落库的 assistant 消息保留，`job.status` 不变
- 模型服务报错 → SSE 发 `error` 事件，不写半截 assistant 消息
- 未预检通过就对话 → `409`

### 提示词复写（**用户明确要的能力**）
- 空提示词 → `422`
- 2001 字提示词 → `422`；2000 字 → 通过
- **复写前后文本完全相同（相似度 = 100%）→ 视为复写失败，`iterations + 1` 重试；3 次仍相同 → `rewrite_failed=true`，`optimized_prompt` 回落为 `raw_prompt`**，接口仍返回 `200`（不能让用户卡死）
- 复写后 `quality_score` 必须落库，可用于前端展示"从 42 分优化到 88 分"
- 复写质量分 < 阈值（建议 60）→ 继续迭代，上限 3 次
- 复写超时 30 秒 → 记 `rewrite_failed=true` 并返回原文
- 连续调用复写 10 次/分钟 → 第 11 次 `429`

### 生成与把关
- 生成文案：返回文本非空，且不含"作为 AI 助手"等元话语
- 生成视频：返回可访问 URL
- **LLM 把关分数 < 60 → 自动重试；重试时 `retry_count + 1`，旧的 `gen_output.is_active = false`**
- `retry_count` 达 3 且仍不过 → `status=need_review`，转人工，不自动删产物
- 生成超时（> 10 分钟）→ `status=failed`，`fail_reason="生成超时"`，允许用户 `retry`
- 视频 provider（即梦）报错 → **不自动切可灵**（防重复计费），`status=failed`，前端让用户选 provider 重试
- `retry` 时 `retry_count=3` → `409`
- `guard_failed` 状态 `retry` → `409`（素材问题，重试无意义）
- 自动重试产生的多次 `gen_output` 中，`is_active=true` 的**有且仅有 1 条**

### 并发与成本熔断（总览 §2 / 详见 07）
- 同一用户同时 3 个 running job，建第 4 个 → `429`
- 同一商户当日生成计数超预算 → `429` + 打告警日志

### 额度扣费（详见 07-token）
- 建 job 请求体带 `merchant_id` / `payer` / `user_id` → **`422`**，且不得按传入值扣费
- `merchant_pay`：按 `task_id → task.merchant_id` 推导付费方，扣商户额度
- `user_pay_reimburse`：扣用户额度垫付，同时**预占商户报销池**；两者任一无可用额 → 拒绝
- 建 job 前若付款方可用额 < 预扣上界 → `402`，**job 不得创建**，不得先扣再校验
- 建 job 成功即产生一条 `quota_reservation`（`job_id` 唯一）；**预扣不写流水**，结算才写
- 预检不通过（`guard_failed`）→ **额度不退**（策略性失败）；job 可删除，但不涉及退款
- 系统故障（模型 5xx / 超时 / 队列丢弃）导致 job `failed` → 释放预占，预扣单 `released`，**不产生流水**
- 产物把关 3 次不过 → job 转 `need_review`，**额度不退**（已真实产生调用成本）
- 结算按**建 job 时刻**生效的计价行（`effective_from <= job.created_at`），改价不影响已建 job
- 同一 job 重复结算事件投递 3 次 → `quota_ledger` 只多 1 条
- 付款方额度耗尽 → 表现为不可生成（`402`），**任务本身不下架、历史产物仍可下载**
- 用户主动 `DELETE /api/jobs/{id}`（`created`/`guard_failed` 态）→ 释放未结算预占，报销池预占一并退回
- 用户侧**不展示**消耗了商户多少额度（但 `user_pay_reimburse` 下展示"我垫付了多少、可报多少"）

### 模型选择与 BYOK（详见 07-token）
- 不传 `provider` / `billing_source` → 用平台默认，**03 现有契约不破**
- `provider` 不在 `GET /api/models` 的可选集内 → `422`
- `billing_source=byok` 但任务 `pay_mode=merchant_pay` → `422`（**不静默回落 platform**）
- `billing_source=byok` 用用户自己的 Key 调用 → `gen_output.cost_cents=0`、`billing_source=byok`
- BYOK 下**仍要跑预检与 judge**（安全不因自带 Key 豁免）
- BYOK 调用时 Key 失效 → job `failed(fail_reason=key_invalid)`，**绝不回落到平台 Key**
- job 详情 / 列表展示所用 `provider` 与 `billing_source`，用户能对上账
- 用户所选 `provider` 的 `est_price_per_call` 必须与后端预扣额同源（否则前端展示与实际扣费不一致）
- 预检通过后用户改选 `provider` → 允许重新预扣（先释放旧的再占新的），但**不得出现同时占两份**
- 第 4 个 job 被拒后，前 3 个必须不受影响（不得误杀）

### 归属、下载、删除
- 用户 A 访问用户 B 的 job → `403`
- 用户 A 下载用户 B 的产物 → `403`
- `status != ready` 时下载 → `409`
- 获取下载 URL 后 **16 分钟**再访问该 URL → `403`（预签名过期），需重新申请
- 重新申请下载 URL → 生成新的 15 分钟链接，旧的（若未过期）仍可用
- 删除 `created` / `guard_failed` 的 job → `204` **软删**（`deleted_at` 非空），**MinIO 对象保留不删**
- 删除后 `GET /api/jobs/{id}` → `404`
- 删除后 `GET /api/me/jobs` 不含它
- 删除 `ready` 的 job → `409`（已产出作品，不允许删）
- 删除 `need_review` 的 job → `409`
- 重复删除 → `404`

### prompt 模板
- `name` 1 字符 / 65 字符 → `422`；2 字符 / 64 字符 → 通过
- `content` 9 字符 → `422`；10 字符 / 2000 字符 → 通过；2001 字符 → `422`
- `tags` 6 个 → `422`；`tags` 单个 17 字符 → `422`
- 客户调 `POST /api/merchant/prompt-templates` → `403`
- 商户 A 改 / 删商户 B 的模板 → `403`
- `is_public=true` 的模板出现在 `GET /api/prompt-templates`（任何已登录用户）
- `is_public=false` 的模板**不出现**在模板市场
- `is_public=false` 的模板，但请求者持有该商户任务的领取 → 出现在 `GET /api/tasks/{task_id}/prompt-templates`
- 商户 A 的私有模板，商户 B 在 `GET /api/tasks/{A的任务id}/prompt-templates` → 不应出现（B 也不是该任务的领取者）
- 未领取该任务的客户查 `/api/tasks/{task_id}/prompt-templates` → `403`
- 复制公开模板 → 新模板 `is_public=false`、`source_template_id` 指向原模板、`usage_count` 从 0 开始
- 复制**私有**模板 → `404`
- 复制已软删的模板 → `404`
- 原模板后续被软删 → 已复制的副本仍可用（不被级联删）
- `apply` 后 `usage_count` 恰好 +1
- 并发 `apply` 同一模板 100 次 → `usage_count` 恰好 +100（原子自增，不得丢更新）
- `apply` 一个无权使用的模板 → `403`
- `apply` 到 `status=generating` 的 job → `409`
- `apply` 只返回 `content`，**不修改 job 状态**（用户仍可编辑后再调 `rewrite-prompt`）
- 商户软删模板 → 该模板不再出现在模板市场与其自己的列表
- **软删模板不物理删除**，且不影响历史 `prompt_draft`

---

## 明确不做

- ❌ **不发布到任何社交平台**（总览已定）。只产出文件供用户下载。
- ❌ 不做视频剪辑器 / 时间轴 / 手动拼接
- ❌ 不做图片编辑器（抠图、调色、加字）
- ❌ 不做 AI 对话上下文超过 20 轮（超出直接拒绝，不做摘要压缩）
- ❌ 不做多语言（只出中文）
- ❌ 不做自建模型 / GPU 推理（全云端 API）
- ❌ 不做批量生成（一次一 job，不做一次出 10 条）
- ❌ 不做生成历史版本对比 / 一键回滚（旧 `gen_output` 只作审计，前端不展示）
- ❌ 不做素材的图库管理 / 复用
- ❌ `need_review` 的处理界面在 **06-admin 异常列表页**（本模块只负责把状态置为 `need_review`）
- ❌ 不做生成内容的 AI 检测 / 去 AI 味后处理（只在提示词层优化）
- ❌ 不做视频时长/分辨率/比例的用户自定义（用模型默认，由 provider 决定）
- ❌ 不做模板的收藏 / 点赞 / 评分 / 排行
- ❌ 不做模板的官方精选 / 平台审核（`is_public` 由商户自行决定，平台不审）
- ❌ 不做 job 的回收站界面（软删后不提供恢复入口，`deleted_at` 仅供运维查验）
- ❌ 不做 MinIO 对象的物理清理定时任务（存储靠人工运维）
- ❌ 不做模板变量占位符（`{{product}}` 之类），模板就是一段纯文本

---

# 追加 A（2026-09-16）：素材上传端点

> 2026-09-16 用户确认：**补一个真上传端点**。此前全后端**没有任何** multipart
> 入口（`grep UploadFile` 零命中），素材与截图都是客户端**自己塞 URL** 进来
> （`AssetIn.url` / `ScreenshotIn.image_url`）。没有上传，用户在相册里选的图
> 就进不了 `assets`，08 的内容工坊只能是个摆设。
>
> 本段**只填 `sniff_mime` 的真实现**；`presign_url` 与下载链路**仍不动**（见「明确不做」）。

## 一句话目标

> 已登录用户把一张本地图片传给平台，拿回一个**能直接放进 `<img src>`** 的 URL，
> 再拿它去建 job 的 `assets`。平台**按真实字节**判断类型，不认扩展名、不认
> 请求里的 `Content-Type`。

## 数据模型

**不建表。** 文件落盘，文件名由服务端生成的不透明 token 决定。

| 项 | 值 |
|---|---|
| 落盘目录 | `settings.upload_dir`，默认 `backend/var/uploads`；不存在则创建 |
| 文件名 | `{secrets.token_urlsafe(24)}.{ext}`，`ext` 由**嗅探出的 mime** 反查 |
| 客户端文件名 | **一律丢弃**——不落盘、不回显（防路径穿越与 XSS 回显） |
| 大小上限 | `settings.upload_max_bytes`，默认 `20 * 1024 * 1024` |
| mime 白名单 | `image/jpeg` / `image/png` / `image/webp`（与 `models/studio.py` 的 `MIME_TYPES` **同源**，不另写一份） |
| 新增配置 | `upload_dir` / `upload_max_bytes` / `upload_url_prefix = "/api/uploads"` |

> 落盘目录必须进 `.gitignore`（`backend/var/`）。

## 端点 / 接口

| 动作 | 输入 | 成功返回 | 失败情况 |
|---|---|---|---|
| 上传 | `POST /api/uploads`，`multipart/form-data`，字段名 **`file`** | `201 {url, mime, size_bytes}` | `401` 未登录<br>`422` 缺 `file` 字段 / 字段名不对<br>`415` 嗅探出的 mime 不在白名单<br>`413` 超过 20MB |
| 读取 | `GET /api/uploads/{name}` | `200` 文件流 | `404` 不存在<br>`400` 名字形状非法 |

- `url` 形如 `/api/uploads/<token>.<ext>`，**与前端同源**（开发期走 Vite proxy），可直接放 `<img src>`。
- `mime` 回显**嗅探值**，不是请求里的 `Content-Type`。
- `GET` **免鉴权**：`<img>` 不带 `Authorization` 头。可读性由 token 的不可猜性保证——语义等同「预签名 URL」。
- 名字形状非法 = 含 `/`、`\`、`..`、无扩展名、或扩展名不在白名单。

## 字节嗅探（填 `storage.sniff_mime`）

按**文件头魔数**判断，够用即可，不做完整解码：

| mime | 魔数 |
|---|---|
| `image/jpeg` | `FF D8 FF` |
| `image/png` | `89 50 4E 47 0D 0A 1A 0A` |
| `image/webp` | `52 49 46 46` + 偏移 8 起 `57 45 42 50`（即 `RIFF....WEBP`） |

读不到足够字节、或对不上任何一行 → 返回 `None` → 端点 `415`。

## 边界（每条之后会变成一条测试）

### 上传
- 未登录 `POST /api/uploads` → `401`
- 请求体没有 `file` 字段 → `422`
- 字段名写成 `upload` / `image` → `422`（**只认 `file`**）
- 把 `.txt` 改名成 `.jpg` 上传 → **`415`**（嗅探不看扩展名）
- GIF（`GIF89a`）→ `415`；PDF（`%PDF`）→ `415`
- 恰好 20MB 的 PNG → `201`；20MB + 1 字节 → `413`
- 合法 PNG → `201`，`mime == "image/png"`，`size_bytes` 等于实际写入字节数
- 请求 `Content-Type` 谎报 `image/jpeg` 但字节是 PNG → 回显 **`image/png`**
- 0 字节文件 → `415`（读不到魔数）
- 客户端文件名 `../../etc/passwd` → 服务端**不用它**；返回的 `url` 不含 `passwd`，落盘目录外**无**新文件
- 客户端文件名含 `<script>` → 不回显、不落盘
- 同一文件连传两次 → 两个**不同**的 `url`（不做内容去重），两个文件都在
- 上传目录不存在 → 自动创建，**不 `500`**

### 读取
- 上传成功后**立刻** `GET` 返回的 `url` → `200`，字节与上传内容**逐字节一致**（含非 UTF-8 字节）
- `GET` 的响应 `Content-Type` 是嗅探值
- `GET /api/uploads/不存在.jpg` → `404`
- `GET /api/uploads/..%2f..%2fconfig.py` → `400`，且**不返回** `config.py` 的内容
- `GET /api/uploads/a%2fb.png` → `400`（路径段不允许）
- `GET /api/uploads/xxx`（无扩展名）→ `400`
- 免鉴权：不带 `Authorization` 的 `GET` 也 `200`

### 接缝
- `201` 的体是 `{url, mime, size_bytes}` **三个键，不多不少**
- 落盘失败 → `500`，且**不**留下半截文件

## 明确不做

- ❌ **不建上传对象登记表**（无归属、无过期、无孤儿清理）。token 即凭据
- ❌ **不填 `presign_url`**，**不改下载链路的 15 分钟过期**——本轮没有真产物可下，
  `GET /api/jobs/{id}/download/{output_id}` 的过期规则仍不实现（沿用现状，不是本段引入的缺口）
- ❌ 不做图片压缩 / 缩放 / 裁剪 / 水印 / EXIF 清洗
- ❌ 不做分片上传 / 断点续传 / 一次多文件
- ❌ 不做对象存储（MinIO / S3）与 CDN，只存本地盘
- ❌ 不做上传限速与存储配额（额度管的是 AI 调用，不是存储）
- ❌ 不做内容安全审核——那是 `guard_assets`，发生在建 job **之后**
- ❌ 不做删除上传文件的端点

---

# 追加 B（2026-09-16）：真接 DeepSeek + 计价写死在代码里

> 2026-09-16 用户改口径（原「AI 桩 provider」方案作废）：
> - **「算了，先不做 token 计费功能，目前测试阶段，先做好页面，要有这个功能展示，
>   目前的 token 费用直接写在代码里面」**
> - **「告诉我用什么模型，我填入 key」** → 真接 DeepSeek
> - 追问后拍板：**「本轮只做文案，视频按钮禁用」** + **「价写死在代码里，账照跑」**
>
> 意思是：不建计费**管理**功能（充值 / 台账界面 / 改价），但**展示要有**，
> 单价从运营可配的 `model_price` 表降级成**代码里的常量**；
> 预扣 / 结算链路**原样保留**，所以界面上的余额**会真的减少**。

## 写本段前的现状（逐条 grep 过）
| 事实 | 出处 |
|---|---|
| `ai.py` 六个函数**全部** `raise NotImplementedError` | `services/ai.py:86-117` |
| `model_price` **零行种子** | `app/seed.py` 共 83 行，只建 3 个账号 |
| 后果：`POST /api/jobs` 直接 `503 计价表缺该组合` | `studio_job.py:293` |
| `httpx>=0.28` **已在依赖里** | `requirements.txt:11` |

## 一句话目标
> `ai.py` 六个函数真接 DeepSeek（OpenAI 兼容 HTTP），单价作为**代码常量**，
> 预扣 / 结算链路原样保留 —— 界面上的余额真的会少。
> **本轮只有文案产出**；视频在界面上禁用，后端不删枚举（见下）。

## 模型与密钥（用户自己填 key）
| 用途 | 模型 ID | 对应 op |
|---|---|---|
| 对话 `chat_stream` | `deepseek-v4-flash` | `chat` |
| 复写 `rewrite_prompt` | `deepseek-v4-flash` | `rewrite` |
| 文案 `generate(kind="copy")` | `deepseek-v4-flash` | `generate` |
| 把关 `judge` | `deepseek-v4-flash` | `judge` |
| 素材预检 `guard_assets` | **`deepseek-v4-flash-vision-exp`** | `guard` |
| 截图 OCR `ocr_metrics` | **`deepseek-v4-flash-vision-exp`** | — |

- BASE URL `https://api.deepseek.com`，OpenAI 兼容 `POST /chat/completions`。
- **一个 key 全覆盖**：`...-vision-exp`（2026-08-21 上线）既收文本也收图，**与 `deepseek-v4-flash` 同价**。故原先 spec 里写的通义 `qwen3-vl` **不再需要**。
- ⚠️ **不要用 `deepseek-chat`**：遗留别名，官方已宣布 **2026-07-24 停用**。
- ⚠️ `...-vision-exp` 是**实验模型**，ID 可能变 → 一律走配置项，不硬编码进函数体。

### 新增配置（`app/config.py`）
| 配置 | 默认 | 说明 |
|---|---|---|
| `deepseek_api_key` | `""` | 空 = 未配置 |
| `deepseek_base_url` | `"https://api.deepseek.com"` | |
| `deepseek_text_model` | `"deepseek-v4-flash"` | |
| `deepseek_vision_model` | `"deepseek-v4-flash-vision-exp"` | |
| `deepseek_timeout_seconds` | `60` | |
| `deepseek_max_tokens` | `2048` | 单次输出上限 |

- **key 放 `backend/.env`**：一行 `DEEPSEEK_API_KEY=sk-xxx`。`.env` 已在 `.gitignore`，**不得**进仓库、**不得**写进 spec 或测试。
- **未配置 key 时**：六个函数一律抛 `AIConfigError` → 流水线 `failed`。**绝不静默假装成功**。

## `ai.py` 真实现
签名、返回的 frozen dataclass、`KeyInvalidError` **全部不动**（接缝是钉住的契约，`ai.py:19-22`）。只把函数体从 `raise` 换成真调用。

**错误分类**：
| 上游情况 | 抛什么 | 流水线结果 |
|---|---|---|
| 401 / 403（key 错或失效） | **`KeyInvalidError`** | `failed(fail_reason="key_invalid")`，**绝不回落平台 Key** |
| 429 / 5xx / 网络错 / 超时 | **普通异常** | `failed(fail_reason=...)` + **释放预占** |
| 未配置 key | `AIConfigError` | 同上 |
| 响应 JSON 解析不出结构 | **普通异常**（`guard_assets` 尤其**不许默认放行**） | 同上 |
| `kind="video"` | `VideoNotSupportedError` | `failed(fail_reason="视频模型尚未接入")` |

- `chat_stream`：`stream: true`，**逐行解析 SSE** `data:` 行，取 `choices[0].delta.content` 增量 `yield`，遇 `data: [DONE]` 停。
- `rewrite_prompt`：要求模型输出 **JSON**（`{optimized_prompt, quality_score}`），取不到就抛——**回落原文是 api 层 3 次重试的活**（`_rewrite_with_retries`），不是这里。
- `generate(kind="copy")`：`units` 取上游 `usage.total_tokens`（**真实用量**），`cost_cents` 由 `units` 乘代码常量算出。
- `generate(kind="video")`：**直接抛 `VideoNotSupportedError`，不发任何 HTTP 请求**。
- `guard_assets` / `ocr_metrics`：用**视觉模型**，图片以 `image_url`（`data:` base64 或 `GET /api/uploads/...`）传入。
- `ocr_metrics` 认不出数字 → **`parsed=None`**，`confidence` 落 0.00~1.00。**绝不用 0 兜底**。

### 视频为什么不在 API 层挡住
`kind=video` **仍然可以建 job**（`JOB_KINDS` 不动、`PROVIDER_KINDS` 不动）。理由：直接改成 `422` 会打翻 6 处已有用例（`test_token_reserve.py:259/282`、`test_studio_generate.py:90/212`、`test_studio_job.py:246`、`test_admin_cost.py:173`），而那些用例要测的是**别的规矩**。视频的闸门放在**生成**这一关（`VideoNotSupportedError`）——错误信息更准，且零回归。界面上则由 08 追加 C 把按钮**禁用**。

闸门分两半钉，因为改坏任一处表现不同：`AI-14` 守「`ai.generate` 抛不抛+发不发请求」，
`GN-22` 守「抛完之后 `pipeline` 往 `fail_reason` 里写什么」（恰好 `"视频模型尚未接入"`，
不套 `生成失败：` 前缀）+ 预占 `released`。`GN-22` 是写实现时补的漏项——原边界清单只列了前一半。

## 计价写死在代码里（`app/services/pricing.py`，新文件）
**常量表是唯一真相**；`seed.py` 把它**投影**进 `model_price` 表。为什么仍要写库：`quota_service.resolve_price` / `GET /api/models` / 预扣额都读库（07 的链路 + 已有测试都锁着它）。**常量作源、种子作投影**是零回归的写法。

| op | model | unit | cost_price_per_unit | price_per_unit | markup_rate | max_price_per_call |
|---|---|---|---|---|---|---|
| `chat` | `deepseek-v4-flash` | `token` | 0.002 | 0.003 | 1.50 | 300 |
| `rewrite` | `deepseek-v4-flash` | `token` | 0.002 | 0.003 | 1.50 | 300 |
| `generate` | `deepseek-v4-flash` | `token` | 0.002 | 0.003 | 1.50 | 300 |
| `judge` | `deepseek-v4-flash` | `token` | 0.002 | 0.003 | 1.50 | 300 |
| `guard` | **`deepseek-v4-flash-vision-exp`** | `token` | 0.002 | 0.003 | 1.50 | 300 |

- `cost_price_per_unit` = 报销基数口径（不含 markup）；`price_per_unit` = 实收。金额单位是**点**（1 点 = 1 分）。
- **口径说明**：这是**演示口径**，不是 DeepSeek 账单的等比换算——真实 1.5 元/百万 token ≈ 每 token 0.00000015 元，而「点」是整数，那样算每次调用都是 0 点、余额永远不动。取 0.003 点/token，一次 1000 token 的调用 ≈ **3 点**。
- 只有 `chat` 这一行在**关键路径**上（`PRIMARY_OP["copy"] == "chat"`）；其余四行是为了 `GET /api/models?op=` 有正常输出。
- `effective_from` 写**固定过去时刻**（`2026-01-01T00:00:00Z`）：`uq_model_price_row` 含 `effective_from`，用 `now()` 重跑种子会反复插行。
- **全部列显式写**，不靠 `server_default`（「helper 静默吞列」已栽过三次）。

## 测试的护栏（不给真网络留缝）
`conftest.py` 加一个 **autouse** fixture：把 `ai` 的六个函数**全部换成「一调用就 `AssertionError("测试不得调用真实 AI，请使用 patch_ai")`」**。装了 `patch_ai` / `patch_ocr` 的用例会覆盖它，照常工作；**忘了装的用例当场炸**。

## 种子数据扩展（`app/seed.py`，83 → 约 190 行）
沿用既有两条：**幂等**、**`APP_ENV=production` 拒绝执行**。

### 额度账户
| 账号 | role | `quota_account.balance` |
|---|---|---|
| `000000` | merchant | 5000 |
| `000001` | customer | **2000** |

四个 `*_limit` 列**显式写 NULL**。

### 两条任务（覆盖两种付费模式）
| # | 标题 | pay_mode | quota | 报销池 / 单人上限 | 要验的界面 |
|---|---|---|---|---|---|
| 1 | 门店探店短视频 | `merchant_pay` | 20 | — | 「商户出钱」这条计费路径 |
| 2 | 新品试吃图文 | `user_pay_reimburse` | 20 | `reimburse_pool=1000` / `reimburse_per_user_limit=500` | 「垫付 / 可报 / 池子还剩」 |

- 两条都 `status="published"`、`start_at < now < end_at`、`description` ≥ 10 字。
- 各配一条 `reward_rule`：`metric="engagement"`、两档 `tiers`（`0-49 → cash 5`、`50+ → cash 20`）、`max_reward_per_user=20`。
- 任务 2 的 `reimburse_per_user_limit (500) ≥ max_price_per_call (300)`。

### 领取
测试用户对**两条任务各一条** `task_claim(status="in_progress")`。**没有这一步，创作台的「选任务」弹层是空的，手工验收第一步就卡死。**

> ⚠️ 任务 1 的标题是「门店探店短视频」——**那是任务主题，不是产出类型**。本轮所有 job 的 `kind` 都是 `copy`。
> ⚠️ 演示账号余额**要够建 job**：预扣 300 点，客户 2000 点能跑 6 次；重跑种子不会重置余额（幂等），需手工改库。

## 已知偏差（**本段不修**，挂悬案）
`pipeline.py:134` 在 `guard_failed` 时调 `_settle(session, job.id, 0)`，而 `settle` 里 `cost = max(actual, 0) = 0` → `payable = 0` → **预占全额退回**，余额一分不掉。这是「**退款**」。但 spec 03 明写：**「预检不通过（`guard_failed`）→ 额度不退」**。两边不一致，且**没有任何用例兜住**（`grep guard_failed` × `reserve|settle|balance` 零命中）。
- **本轮不动**：改了要么动 `pipeline.py`、要么动 spec 那句话，**两条路都得先确认**。
- **与 08 追加 C 的关系**：预检现在是**真视觉模型**在判，`guard_failed` 真的会发生。

## 边界（每条之后会变成一条测试）
### AI 真实现的契约（18 条，全部用 `httpx.MockTransport` 打桩在 transport 层）
- `AI-01` 六个函数签名与 `ai.py` 现状**逐字一致**（`inspect.signature` 比对）
- `AI-02` 未配置 `deepseek_api_key` → 六个函数都抛 `AIConfigError`，且**一个 HTTP 请求都没发**
- `AI-03` 请求 URL == `{deepseek_base_url}/chat/completions`，头带 `Authorization: Bearer <key>`
- `AI-04` `chat_stream` 的体里 `stream == true`、`model == settings.deepseek_text_model`
- `AI-05` `chat_stream` 逐段解析 `data:` 行 → 分 3 段产出，拼接 == 3 段之和
- `AI-06` `data: [DONE]` 之后**不再产出**新段
- `AI-07` 上游 401 → 抛 **`KeyInvalidError`**；上游 403 → 同样
- `AI-08` 上游 429 → 抛**普通异常**（**不是** `KeyInvalidError`）→ 流水线 `failed`，`fail_reason != "key_invalid"`
- `AI-09` 上游 500 → 同 `AI-08`
- `AI-10` 超时（超过 `deepseek_timeout_seconds`）→ 抛普通异常，**不挂死**
- `AI-11` `rewrite_prompt` 正常回 JSON → `RewriteResult` 三字段齐全
- `AI-12` `rewrite_prompt` 上游回非 JSON 文本 → **抛异常**
- `AI-13` `generate(kind="copy")` → `content` 非空、`url is None`、`units == 响应里的 usage.total_tokens`
- `AI-14` `generate(kind="video")` → 抛 `VideoNotSupportedError`，且**一个 HTTP 请求都没发**
- `AI-15` `cost_cents == ceil(price_per_unit × units)`，与 `pricing.py` 常量同源
- `AI-16` `guard_assets` / `ocr_metrics` 请求体里 `model == settings.deepseek_vision_model`（**不是** text model），且 messages 含图片
- `AI-17` 改 `settings.deepseek_text_model` 后请求体里的 `model` **跟着变**（没有硬编码）
- `AI-18` `ocr_metrics` 认不出数字 → **`parsed is None`**（不是 `{"likes": 0, ...}`），且 `0.0 <= confidence <= 1.0`

### 计价常量与投影（8 条）
- `PR-01` 常量表恰好 **5 行**，op 覆盖 chat/rewrite/generate/judge/guard
- `PR-02` 五行 `provider == "deepseek"`；`guard` 行 model == 视觉模型，其余四行 == 文本模型
- `PR-03` 跑完种子后 `model_price` 的行与常量表**逐字段相等**
- `PR-04` 改常量里某行 `price_per_unit` 再跑种子 → 库里那行**跟着变**（常量是源）
- `PR-05` 跑两次种子 → `model_price` 行数不变（`effective_from` 固定）
- `PR-06` 五行 `unit == "token"`，且 `price_per_unit / cost_price_per_unit == markup_rate`（1.50）
- `PR-07` `GET /api/models?op=chat` → `200`，含该行，且 `est_price_per_call == max_price_per_call == 300`
- `PR-08` 种子后建 `kind=copy` job → **`201`**（不再是 `503`）

### 种子（10 条）
- `SN-01` 跑两次 `seed()` → 第二次 `created == 0`
- `SN-02` 两个演示账号各有 `quota_account`，`balance` 5000 / 2000，四个 `*_limit` 为 NULL
- `SN-03` `task` 恰好 2 条，`pay_mode` 分别 merchant_pay / user_pay_reimburse，`status` 都 published
- `SN-04` 两条任务各一条 `reward_rule`，`tiers` 2 档且末档 `max` 为 `null`
- `SN-05` 任务 2 的 reimburse_pool=1000 / per_user_limit=500，且 per_user_limit >= max_price_per_call
- `SN-06` 测试用户对两条各有 `task_claim(status="in_progress")` → `GET /api/me/claims` 回 `total == 2`
- `SN-07` `GET /api/tasks?keyword=` 两条演示任务都可见
- `SN-08` `APP_ENV=production` 跑 `python -m app.seed` → 退出码 1，库里**一行都没写**
- `SN-09` **账真的会动**：走完一个 copy job → 用户 `balance` 恰好少 `cost_cents`，`reserved` 回到 0，`debt == 0`
- `SN-10` `quota_ledger` 多恰好 **1** 条 `source="consume"`；`quota_reservation` 那行为 `settled`

### 测试护栏（2 条）
- `GR-01` 直接调 conftest 装的那个护栏函数 → 抛 `AssertionError`，信息里含「patch_ai」
- `GR-02` 装了 `patch_ai` 的用例不受护栏影响（既有 03 用例**全绿**即证明）

> 合计 **38 条**（AI 18 + PR 8 + SN 10 + GR 2）。

## 明确不做（本段）
- ❌ **不做视频产出** —— DeepSeek 没有视频模型，界面上禁用（08 追加 C）
- ❌ **不做计费管理功能**：充值 / 台账界面 / 在线改价 / 套餐
- ❌ **不做真实资金**：仍只有线下 + 管理员记账（CLAUDE.md 悬案 3）
- ❌ **不填 `presign_url`** —— 本轮只有文案，产物是文本不是文件，`GET /api/jobs/{id}/download/{output_id}` **仍不会被走到**
- ❌ 不接第二家厂商（通义 qwen3-vl / 即梦 / 可灵）—— 一个 DeepSeek key 全覆盖
- ❌ 不改 `ai.py` 的**签名**、不改 `KeyInvalidError` 的语义
- ❌ **不改 `guard_failed` 的退款行为** —— 见「已知偏差」，挂悬案
- ❌ 不做流式以外的并发调优（限速 / 重试退避 / 连接池调参）
- ❌ 不做 prompt 缓存 / 上下文压缩
- ❌ 不做 AI 调用的**计费看板**（06 的成本看板已有）

---

# 追加 C（2026-09-18）：本地上传图转 base64 再交视觉模型

> 用户反馈：客户界面「上传图片传不上去」。根因不是 `POST /api/uploads` 落盘失败，
> 而是预检 / OCR 把相对路径 `/api/uploads/<name>` **原样**塞进 DeepSeek 的 `image_url`，
> 上游拿不到本机文件。追加 B 已写「可用 `data:` base64」，本段把这条补成真。

## 一句话目标

> 凡走 `guard_assets` / `ocr_metrics` 的素材，若 URL 指向本机上传目录，则**读盘 → `data:{mime};base64,...`** 再发给上游；公网 `https://` 与已有 `data:` **原样透传**。库里的 `job_input_asset.url` **仍存相对路径**，不改表、不改上传响应。

## 数据模型

无新表、无改列。转换只发生在发上游的瞬间。

## 端点 / 接口

无新 HTTP 端点。改动面：`app/services/ai.py` 的素材引用解析（现 `_image_ref`），
供 `guard_assets` / `ocr_metrics`（及任何经它组图的路径）共用。

| 动作 | 输入 | 成功返回 | 失败情况 |
|---|---|---|---|
| 解析素材 URL | `asset.url`（及可选 `mime`） | 可交给 DeepSeek 的 `image_url` 字符串 | 本地文件缺失 / 非法相对路径 → **抛异常**（不许空串骗过上游） |

### 解析规则（按顺序）

1. 已是 `data:` → **原样**
2. 已是 `http://` / `https://` → **原样**
3. 形如 `/api/uploads/<name>`（与 `upload_url_prefix` 一致）且本地文件存在 →
   读字节，mime 优先 `storage.sniff_bytes`，否则用 `asset.mime`，拼
   `data:{mime};base64,{b64}`
4. 其它相对路径 / 穿越 / 文件不存在 → **抛**（`ValueError` 或同类），流水线按既有失败路径走

## 边界（每条之后会变成一条测试）

- `IR-01` 本地已落盘的 `/api/uploads/x.png` → 发出的请求体里该图 `image_url` **以 `data:image/` 开头**，且解码后字节与盘上一致
- `IR-02` `https://…` → 请求体里 **仍是原 URL**（既有 `AI-16` 场景不回归）
- `IR-03` 已是 `data:image/png;base64,…` → **原样**，不二次编码
- `IR-04` `/api/uploads/不存在.png` → **抛异常**，且 **不发** 上游 HTTP（或发之前就炸）
- `IR-05` `ocr_metrics(image_url="/api/uploads/…")` 与 `guard_assets` **同一套**解析（不各写一份）

## 明确不做（本段）

- ❌ 不把 base64 写回 `job_input_asset.url` / 不改上传 API 响应
- ❌ 不猜公网 base URL、不接 MinIO、不改 `presign_url`
- ❌ 不改六个 AI 函数的**对外签名**
- ❌ 不在仓库写入真实 Key（只提供 `.env.example` 空模板）

---

# 追加 D（2026-09-18）：平台兜底 Key + 视频接缝 + 客户 BYOK 联调口径

> 用户口径修正：
> 1. **客户页面内**可自行增改所用 **provider / model / API Key**（走既有 `/api/me/model-keys` + `/api/models`，前端此前未做）；
> 2. 做文案或做视频时若缺 Key / Key 失效 → **弹窗**说明问题与添加步骤，可跳转到密钥页；
> 3. 平台 `.env` 仍作**未选 BYOK 时的兜底**；视频厂商 Key 也可只配在客户 BYOK 里（不必现在填真 Key）。

## 一句话目标

> 客户能在站内管理自己的模型密钥；创作时优先/可选 BYOK；缺 Key 时弹窗指引而不是哑巴失败。
> 视频 `generate`：对应 provider 的**用户 Key 或平台 Key**任一可用即发 HTTP，否则 `VideoNotSupportedError`。

## 数据模型

无新表。继续用 `user_model_key` / `model_price`。

## 配置（平台兜底，`backend/.env.example`）

| 环境变量 | 用途 |
|---|---|
| `DEEPSEEK_API_KEY` / `DEEPSEEK_BASE_URL` / 模型名 | 平台兜底（`billing_source=platform`） |
| `JIMENG_API_KEY` / `JIMENG_BASE_URL` | 视频默认 provider 平台兜底 |
| `KLING_API_KEY` / `KLING_BASE_URL` | 备选视频平台兜底 |

客户自己的 Key **不进 `.env`**，进 `user_model_key`（AES 加密，列表只回 `key_masked`）。

## 调用 Key 选择顺序（`generate` / 对话 / 复写 / 预检等）

1. job.`billing_source=byok` → **只用**该用户该 `provider` 的 active Key；失效 → `key_invalid`，**绝不**回落平台
2. job.`billing_source=platform`（或未传）→ 用平台 `.env`；空 → `AIConfigError`
3. `kind=video` 且两边 Key 都空 → `VideoNotSupportedError("视频模型尚未接入")`（`AI-14`/`GN-22` 保持）

## 07 口径修订（本段连带）

原规则：`merchant_pay` + `billing_source=byok` → `422`。  
**本轮改为允许**：客户在商户出钱任务下也可选 BYOK（成本仍 0，不扣商户 AI 额度）。  
非法组合只剩：`byok` 但用户无该 provider 的 active Key → `422`（文案由弹窗解释）。

## 边界（后端，摘）

- `CF-01`…`CF-03`、`VG-01`…`VG-05`（同前：example 文件、空 Key 行为、Mock 成功/401）
- `BK-01` `merchant_pay` + `billing_source=byok` + 用户有 deepseek Key → 建 job **201**（不再 422）
- `BK-02` `byok` 且无 Key → **422**，`detail` 可读（供弹窗展示）

## 明确不做

- ❌ 不在仓库提交真 Key
- ❌ 不做后台「替客户改 Key」UI
- ❌ 不自动 failover 即梦↔可灵
- ❌ 不改 `guard_failed` 退款悬案

**前端弹窗与密钥页见 08 追加 F；本段只定后端门闩。**
