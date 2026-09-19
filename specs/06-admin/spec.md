# 06 · 平台管理后台

> 8002 端口，平台运营用。第一版三个模块：AI 成本看板、用户与商户管理、异常处理列表。

---

## 一句话目标

> 平台运营能在一个页面上看到 AI 生成花了多少钱、按商户/按日/按模型拆开；能封禁解封用户与商户；能处理四类异常（AI 把关 3 次不过的作品、截图识别低置信、截图与插件数据不符、用户对商户驳回的申诉）。

---

## 页面承载方式（已定）

8002 = FastAPI API + **同一套 React + Vite + TS + Tailwind 编一套后台前端**，构建产物由 FastAPI 以静态文件挂载。

- 理由：与 8001 同技术栈，`hallmark` 设计规范可直接复用；不引入 Jinja2 第二套模板体系。
- 访问入口：`http://localhost:8002/admin`
- 未登录访问 `/admin` → 跳登录页；非 `admin` 角色登录后访问 `/api/admin/*` → `403`。

---

## 数据模型

### `admin_action_log`（**不可变审计，只追加**）
| 字段 | 类型 | 约束 |
|---|---|---|
| id | bigint | 主键 |
| admin_id | bigint | **必填**，外键 → user（role=admin） |
| action | enum | **必填**，`ban_user` / `unban_user` / `resolve_content` / `resolve_ocr` |
| target_type | varchar(32) | **必填**，`user` / `content_job` / `ocr_result` |
| target_id | bigint | **必填** |
| detail | jsonb | 可空，`{reason, before, after}` |
| created_at | timestamptz | **必填**，**不得 UPDATE / DELETE** |

**成本看板不建新表**，全部从既有表聚合：
- 花费与次数 → `gen_output.cost_cents` / `provider` / `created_at`
- 任务归属 → `content_job` → `task` → `merchant_id`
- 需要索引：`gen_output(created_at)`、`content_job(task_id)`

### `budget_alert`（成本熔断告警）
| 字段 | 类型 | 约束 |
|---|---|---|
| id | bigint | 主键 |
| merchant_id | bigint | **必填**，外键 |
| alert_date | date | **必填** |
| spend_cents | int | **必填**，当日已花 |
| limit_cents | int | **必填**，当日预算 |
| created_at | timestamptz | **必填** |
| **唯一约束** | — | `(merchant_id, alert_date)` 唯一（一天只告警一次） |

---

## 端点 / 接口

所有端点均要求 `role=admin`，否则 `403`。

### A. AI 成本看板

| 动作 | 输入 | 成功返回 | 失败情况 |
|---|---|---|---|
| 总览 | `GET /api/admin/cost/summary?from&to` | `200` `{total_cents, job_count, success_count, fail_count, avg_cents, gen_count}` | `422` `from > to` 或跨度 > 366 天 |
| 按商户 | `GET /api/admin/cost/by-merchant?from&to&page&size` | `200` `{items:[{merchant_id, shop_name, total_cents, gen_count, video_count, copy_count}]}` | `422` 参数非法 |
| 按日趋势 | `GET /api/admin/cost/by-day?from&to` | `200` `{items:[{date, total_cents, gen_count}]}` 按日期升序，**无数据的日期补 0** | `422` |
| 按模型 | `GET /api/admin/cost/by-provider?from&to` | `200` `{items:[{provider, total_cents, gen_count, avg_cents, success_rate}]}` | `422` |
| 熔断告警清单 | `GET /api/admin/cost/budget-alerts?from&to` | `200` `{items:[{merchant_id, shop_name, alert_date, spend_cents, limit_cents}]}` | `422` |
| 单商户明细 | `GET /api/admin/cost/merchants/{id}/detail?from&to` | `200` `{by_day[], by_provider[], recent_jobs[]}` | `404` 商户不存在 |

### B. 用户与商户管理

| 动作 | 输入 | 成功返回 | 失败情况 |
|---|---|---|---|
| 用户列表 | `GET /api/admin/users?role&status&keyword&page&size` | `200` `{items,total,page,size}` | `422` 参数非法 |
| 用户详情 | `GET /api/admin/users/{id}` | `200` `{user, merchant_profile?, stats:{task_count, claim_count, job_count, points_balance}}` | `404` |
| 封禁 | `POST /api/admin/users/{id}/ban`<br>`{reason}` | `200` | `422` `reason` 空或 < 5 字<br>`409` 已是 `banned`<br>`409` 目标为 admin<br>`404` |
| 解封 | `POST /api/admin/users/{id}/unban` | `200` | `409` 非 `banned` 状态<br>`409` 目标为 admin |
| 操作日志 | `GET /api/admin/action-logs?target_type&target_id&page` | `200` `{items,...}` | `403` |

### C. 异常处理列表（最小可用）

| 动作 | 输入 | 成功返回 | 失败情况 |
|---|---|---|---|
| 异常列表 | `GET /api/admin/exceptions?type&page&size` | `200` `{items,total,page,size}` | `422` `type` 非法 |
| 处理内容异常 | `POST /api/admin/exceptions/content/{job_id}/resolve`<br>`{action, note}` | `200` | `409` job 状态非 `need_review`<br>`422` action 非法<br>`404` |
| 处理截图异常 | `POST /api/admin/exceptions/ocr/{ocr_id}/resolve`<br>`{action, note}` | `200` | `409` 该 ocr 非当前 active<br>`422` action 非法<br>`404` |

**`type` 三种取值与对应动作**

| type | 数据来源 | `action` 可选值 | 语义 |
|---|---|---|---|
| `content_review` | `content_job.status = need_review` | `approve` | 放行，`status → ready`，用户可下载 |
| | | `discard` | 作废，`status → failed`，`fail_reason` 写 `note` |
| `ocr_low_confidence` | `ocr_result.confidence < 0.70` 且 `is_active` | `accept` | 采纳截图数据，按其值写一条 `metric_snapshot(source=ocr)` |
| | | `reject` | 不采纳，只保留插件数据 |
| `ocr_mismatch` | `ocr_result.mismatch_flag = true` 且 `is_active` | `accept` | 同上 |
| | | `reject` | 同上 |
| `appeal` | `appeal.status = pending`（见 04） | `accept` | 支持用户，post → `approved`，**触发 05 结算** |
| | | `reject` | 维持驳回，post → `rejected`，不可再申诉 |

**申诉的接口定义在 `04-tracking/spec.md`**（`GET /api/admin/appeals` / `POST /api/admin/appeals/{id}/decide`），本模块只负责把它渲染成后台的一个页签，不重复定义端点。

---

## 边界（每条之后会变成一条测试）

### 权限与入口
- 未登录访问 `/api/admin/*` → `401`
- `customer` / `merchant` 访问 `/api/admin/*` → `403`
- 已封禁的 admin 访问 → `403`
- 管理员访问 `/` 与 `/admin`（HTML 路由）→ `200`

### 成本看板
- `from > to` → `422`
- `from=to`（同一天）→ `200`，只统计该日
- 跨度 367 天 → `422`；366 天 → `200`
- 无任何生成数据 → `200`，各字段为 0 / 空数组，**不得 500 或 null**
- `by-day` 中间缺失的日期 → **必须补 0**，不得跳过（否则前端折线图会断）
- `by-merchant` 的花费合计 **必须等于** `summary.total_cents`（同一时间窗）⚠️ 这是一致性校验点
- 已注销商户的历史花费**仍要计入**（按 `user_id` 聚合，用户软删不影响）
- `by-provider` 的 `success_rate` = 该 provider 成功产出数 / 总调用数；总调用数为 0 → 返回 0 而非 NaN
- 失败/超时的生成（`content_job.status=failed`）的成本**要计入花费**（钱已经花了）
- ⚠️ 本看板的 `total_cents` 是**平台真实成本**；07 的商户额度消耗是**向商户的计费额**。两套数允许不等（差值 = 毛利），**不得混用同一张表或同一个字段**
- 成本看板的金额**不因 07 的退款而回退**：系统故障退了商户的额度，但平台的 API 账单照样产生（钱花出去了）
- 熔断告警一天只写一条：同商户同日重复触发 → `budget_alert` 仍只有 1 行
- `size=101` → `422`

### 用户管理
- `keyword` 可匹配 `account` / `email` / `nickname` / `shop_name`，空则不过滤
- `keyword` 含 SQL 通配符 `%` `_` → **按字面量处理**，不得做通配匹配（注入防护）
- `role` 传非法值 → `422`
- 封禁 `reason` 4 字 → `422`；5 字 → `200`
- 封禁后该用户 token 立即失效（下次请求 `403`）
- 封禁后写一条 `admin_action_log(action=ban_user)`，`detail` 含 reason
- 重复封禁 → `409`
- 解封非封禁用户 → `409`
- **封禁 admin → `409`**（防止管理员互封，锁死系统）
- 已注销用户出现在列表 → 允许（`status=deleted`），`ban` 它 → `409`（已不可用账号）
- 用户详情里 `points_balance` 必须与 05 的账本一致
- 每次封禁/解封/异常处理都**必须**写 `admin_action_log`，缺日志 → 测试失败

### 额度账户管理（端点定义在 07）
- admin 记账充值 → `quota_ledger` 多 1 条 `source=recharge`，`balance` 与 `total_recharged` 同步增加
- admin 人工调整传 `remark` < 5 字 → `422`；扣减后余额为负 → `422`
- admin 冻结商户额度 → 该商户的新建 job 立即 `403`，**进行中的 job 不受影响**
- admin 冻结有未结算预扣单的账户 → `409`（同 07）
- admin 冻结**用户**额度 → 该用户无法新建 job，已提交作品与奖励不受影响
- 充值 / 调整 / 冻结 / 解冻**各写一条 `admin_action_log`**，`detail` 含 before / after
- 两个 admin 同时给同一账户充值 → 两笔都成功，`balance` 不丢失（行锁串行，`balance_after` 递增不重复）
- 管理员**看不到**任何用户密钥明文，`/api/admin/*` 里不存在返回密钥的端点

### 成本看板 vs 07 计费（**两套账，不得混用**）
- 看板 `total_cents` = 平台真实成本；07 `total_consumed` = 向商户/用户计费额；差值 = 毛利
- **BYOK 调用**：`gen_output.cost_cents = 0` 且 `billing_source=byok`，**不得因 0 成本被判为失败调用**
- `success_rate` 的分子分母都必须包含 BYOK 调用（它成功就是成功，只是不花钱）
- BYOK 调用量单独可见（`/api/admin/quota/byok`），用于评估有多少成本被用户自己承担了
- 成本看板金额**不因 07 的系统故障退款而回退**（额度退了，API 账单照样产生）

### 异常处理
- `type` 传 `foo` → `422`
- `content_review` 列表只含 `status=need_review` 的 job
- 处理一个 `status=ready` 的 job → `409`（不允许事后改）
- `approve` 后 job `status=ready`，用户可下载
- `discard` 后 job `status=failed`，`fail_reason` 等于传入的 `note`，用户下载 → `409`
- `note` 为空 → 允许（选填），但 `approve`/`discard` 都必须写 `admin_action_log`
- `ocr_low_confidence` 列表只含 `confidence < 0.70` 且 `is_active=true` 的记录
- `accept` 截图数据 → 新增一条 `metric_snapshot(source=ocr)`，且 `engagement` 由后端算
- `accept` 后再看该 post 的奖励档位 → 按 04 的规则（取快照**峰值**）
- `reject` 截图数据 → 不新增快照，`ocr_result` 标记已处理
- 对已不是 `is_active` 的 ocr 处理 → `409`
- 同一异常重复处理 → `409`
- 处理异常时该 post 已 `approved`/`auto_approved` → `409`（奖励已结算，不可回溯）

### 申诉裁决
- `type=appeal` 列表只含 `appeal.status = pending`
- admin `accept` → post `status=approved`，**触发 05 结算**
- admin `reject` → post `status=rejected`，用户不可再申诉
- 商户调 `/api/admin/appeals/*` → `403`
- 已裁决的申诉再裁决 → `409`
- 裁决必须**同时**写 `admin_action_log` 与 `review_log`（各一条）
- 同一条申诉被两个 admin 同时裁决 → 恰好 1 个成功，另 1 个 `409`
- 裁决 `admin_note` 为空 → 允许（选填）
- 申诉被 `accept` 后奖励只结算一次（`reward_grant.post_id` 唯一约束兜底）

### 并发
- 两个 admin 同时 `approve` 同一个 job → 恰好 1 个成功，另 1 个 `409`
- 两个 admin 同时 `ban` 同一用户 → 恰好 1 个成功，`admin_action_log` 只有 1 条 `ban_user`

---

## 明确不做

- ❌ 不做管理员注册 / 新增管理员界面（`admin` 只由种子脚本写入）
- ❌ 不做细粒度权限 / RBAC / 权限组（admin 即全权）
- ❌ 不做商户资质审核流程（商户注册即用，不做人工审批）
- ❌ 不做数据导出（CSV / Excel 导出成本报表）
- ❌ 不做可视化图表的复杂交互（折线/柱状即可，不做钻取、对比、自定义维度）
- ❌ 不做成本预算的界面配置（预算阈值写在配置里，改配置需重启）
- ❌ 不做告警通知（不发邮件/短信/Webhook，只在清单里能查到）
- ❌ 不做内容工坊的实时监控大屏
- ❌ 不做插件健康度统计（插件上报成功率本期不做）
- ❌ 不做奖励台账界面与手工打款（`cash_payout` 仍由管理员直接操作数据库）
- ❌ 不做异常处理的历史回溯界面（`admin_action_log` 有记录但本期不做查询页）
- ❌ 不做管理员操作二次确认 / 审批流
- ❌ 不做申诉的自动裁决 / AI 辅助裁决（必须人工看）
- ❌ 不做后台的通知中心（有新的待处理异常不会提醒，需运营自己主动打开看）
- ❌ 不做批量封禁 / 批量裁决
- ❌ 不做后台的 dark mode / 主题切换
- ❌ 不做后台的移动端适配（按桌面 ≥1280px 设计）

---

# 追加（2026-09-15 确认）：用户反馈 + 今日使用量

> 用户诉求原文：「统计这个平台今天商家和用户使用量是多少」；
> 「在商家和用户页面做问题反馈按钮，让商家和用户提出合理建议，并留言到开发者」；
> 「在后端可以看到是商家反馈还是用户反馈的分类」。
> 归属：**挂进 06**（2026-09-15 用户确认）。
> 反馈需带「已处理」状态（2026-09-16 用户确认），且**收件箱页面本轮一并做**（见 F）。

## 一句话目标

> 平台运营能看到**今天有多少商家、多少用户在用**这个平台，以及**商家和用户提交的反馈**，且反馈按角色自动分成商家 / 用户两类。

---

## 数据模型

### `user_feedback`（新增，**只追加**）
| 字段 | 类型 | 约束 |
|---|---|---|
| id | bigint | 主键 |
| user_id | bigint | **必填**，外键 → `user`，**不加 `ondelete="CASCADE"`**（与 05 一致：留痕不随用户消失） |
| role | varchar(16) | **必填**，`merchant` / `customer`。**服务端按提交者身份推导，客户端不得指定** |
| category | varchar(16) | **必填**，`bug`（功能异常）/ `suggestion`（功能建议）/ `other`（其他） |
| content | text | **必填**，5 ~ 500 字（去首尾空白后按字符数计） |
| contact | varchar(128) | 可空，用户自愿留的联系方式 |
| status | varchar(16) | **必填**，默认 `open`；`open`（未处理）/ `resolved`（已处理） |
| resolved_at | timestamptz | 可空，标记为已处理的时间 |
| resolved_by | bigint | 可空，外键 → `user`，把它标为已处理的 admin |
| created_at | timestamptz | **必填**，服务端当前时间 |

索引：`(role, created_at DESC)`——列表按角色筛 + 倒序；`(status, created_at DESC)`——按处理状态筛；`(user_id)`——按人反查。
约束：`ck_user_feedback_category`、`ck_user_feedback_role`、`ck_user_feedback_status`（varchar + CHECK，不用 PG 原生 enum，理由同 `admin_action_log`）。

**处理动作要留痕**：`resolve` / `reopen` 各写一条 `admin_action_log`。故：
- `ADMIN_ACTIONS` 追加 `resolve_feedback` / `reopen_feedback`
- `TARGET_TYPES` 追加 `feedback`
（两处都是往元组里加值，不改列类型，不需要 ALTER。）

### 今日使用量**不建新表**
全部从既有表聚合：`user.last_login_at` / `user.created_at` / `user.role`、
`task.created_at`、`task_claim.claimed_at`、`social_post.submitted_at`、`content_job.created_at`。

---

## 端点 / 接口

### D. 用户反馈

| 动作 | 输入 | 成功返回 | 失败情况 |
|---|---|---|---|
| 提交反馈 | `POST /api/feedback`<br>`{category, content, contact?}` | `201` `{id, role, category, content, contact, created_at}` | `401` 未登录<br>`403` 提交者是 admin / 已注销 / 已封禁<br>`422` `category` 非法、`content` < 5 或 > 500 字、`contact` > 128 字<br>`422` 请求体带 `role` / `user_id` |
| 反馈列表 | `GET /api/admin/feedback?role&category&status&page&size` | `200` `{items,total,page,size}`<br>items: `{id, user_id, account, nickname, role, category, content, contact, status, resolved_at, resolved_by, created_at}` | `403` 非 admin<br>`422` `role` / `category` / `status` 非法、`size=101` |
| 标记已处理 | `POST /api/admin/feedback/{id}/resolve` | `200` `{id, status:'resolved', resolved_at, resolved_by}` | `403` 非 admin<br>`404` 不存在<br>`409` 已是 `resolved` |
| 撤销已处理 | `POST /api/admin/feedback/{id}/reopen` | `200` `{id, status:'open', resolved_at:null, resolved_by:null}` | `403` 非 admin<br>`404` 不存在<br>`409` 本就不是 `resolved` |

### E. 今日使用量

| 动作 | 输入 | 成功返回 | 失败情况 |
|---|---|---|---|
| 今日使用量 | `GET /api/admin/stats/daily?date=YYYY-MM-DD` | `200` `{date, active:{merchant,customer}, new:{merchant,customer}, activity:{tasks_created, claims, posts_submitted, jobs_created}}` | `403` 非 admin<br>`422` `date` 格式非法 |

`date` 省略 → 取**北京时间（UTC+8）的今天**。

**口径（逐条可测）**

| 字段 | 来源 | 定义 |
|---|---|---|
| `active.merchant` / `active.customer` | `user` | 该日内 `last_login_at` 落区间、按 `role` 分组、**排除 `status='deleted'`** |
| `new.merchant` / `new.customer` | `user` | 该日内 `created_at` 落区间、按 `role` 分组、排除 `deleted` |
| `activity.tasks_created` | `task` | 该日 `created_at` 计数（含已软删——建了就建了） |
| `activity.claims` | `task_claim` | 该日 `claimed_at` 计数 |
| `activity.posts_submitted` | `social_post` | 该日 `submitted_at` 计数 |
| `activity.jobs_created` | `content_job` | 该日 `created_at` 计数 |

- `role='admin'` 的用户**不计入任何一类**——使用量说的是商家和用户。
- 切日按**北京时间**：区间 = `[date 00:00 CST, date+1 00:00 CST)`，转 UTC 后查库。

---

## 边界（每条之后会变成一条测试）

### 反馈
- 未登录 `POST /api/feedback` → `401`
- 商户提交 → 落库 `role='merchant'`；客户提交 → `role='customer'`
- 请求体带 `role` → `422`；带 `user_id` → `422`（身份由服务端推导，同全局约定 #11）
- admin 提交 → `403`（后台不是反馈者）
- 已注销用户提交 → `403`；已封禁用户提交 → `403`
- `content` 4 字 → `422`；5 字 → `201`（下界成对，同 `AB-02`/`AB-03`）
- `content` 500 字 → `201`；501 字 → `422`
- `content` 全空白 → `422`
- `category` 传 `foo` → `422`；三个合法值各能提交成功
- `contact` 省略 → `201`；129 字 → `422`
- 提交成功后 `user_feedback` **恰好 1 行**，且 `status='open'`、`resolved_at` / `resolved_by` 均为 NULL（**新提交不得是已处理**）
- **没有端点能删反馈**，也没有端点能改 `content` / `role` / `user_id`——只能改处理状态
- 列表按 `created_at DESC, id DESC` 倒序
- `?role=merchant` 只返回商家反馈；`?role=customer` 只返回用户反馈（**这是「分类」的核心断言**）
- `?status=open` 只返回未处理的；`?status=resolved` 只返回已处理的
- `?role=foo` → `422`；`?category=foo` → `422`；`?status=foo` → `422`；`size=101` → `422`
- 商户/客户访问反馈列表 → `403`
- 列表 items 的 `role` 是**提交时快照**：与提交者当时的角色一致

**处理状态**
- `resolve` 不存在的 id → `404`
- `resolve` 后 → `status='resolved'`，`resolved_at` 非空，`resolved_by` = 该 admin 的 id
- 重复 `resolve` → `409`，且 `admin_action_log` **不产生第二条**
- `reopen` 一条未处理的 → `409`
- `reopen` 后 → `status='open'`，且 `resolved_at` / `resolved_by` **均置回 NULL**
- 每次 `resolve` / `reopen` **各写恰好 1 条** `admin_action_log`（`action` 为 `resolve_feedback` / `reopen_feedback`，`target_type='feedback'`）
- 商户 / 客户调 `resolve` / `reopen` → `403`
- 两个 admin 同时 `resolve` 同一条 → **恰好 1 个成功、另 1 个 `409`**，且 `admin_action_log` 只有 1 条

### 今日使用量
- 未登录 → `401`；商户/客户 → `403`
- `?date=2026-13-45` → `422`
- 库里没有任何数据 → `200`，所有计数为 **0**，**不得 500 或 null**
- 北京时间今天 00:00 之后的登录计入今天；昨天 23:59 的**不计入**
- `role='admin'` 的登录**不计入** `active`
- 已注销用户**不计入** `active` / `new`
- `?date=<昨天>` → 只统计昨天，今天的登录不计入
- `activity` 四类分别造一条当日数据，各自 +1

---

## 明确不做（追加）

- ❌ 不做运营**回复**、不做内部备注（只标记「已处理」，不写回复内容）
- ❌ 不做反馈的删除 / 撤回 / 编辑
- ❌ 不做反馈的图片附件
- ❌ 不做新反馈的邮件 / 短信 / Webhook 通知（运营自己打开看）
- ❌ 不做反馈的「我的历史反馈」列表（前端只提交，不回看）
- ❌ 不做使用量的历史趋势接口（本端点只给指定**某一天**；要看趋势由前端多次调用）
- ❌ 不做小时粒度、不做按渠道/地域拆分
- ❌ 不做使用量与成本看板的合并（两套口径，**不得混用同一张表或同一个字段**）

---

## F. 后台反馈收件箱页面（本轮一并做）

> 2026-09-15 用户确认「要加」。页面挂在 **8002 的 `/admin/feedback`**，
> 与 `08-frontend` 的商家/用户端（8001）是**两套前端**，只共用设计 token。

### 页面要求

| 区域 | 内容 |
|---|---|
| 筛选条 | 角色（全部 / 商家 / 用户）+ 处理状态（全部 / 未处理 / 已处理）+ 类型（全部 / 功能异常 / 功能建议 / 其他） |
| 列表 | 每行：角色徽章 · 类型 · 内容摘要 · 提交人（账号 / 昵称）· 提交时间 · 状态 |
| 行内操作 | 「标记已处理」/「撤销已处理」（随当前状态二选一） |
| 详情 | 点行展开完整内容 + 联系方式；长内容不截断丢字 |

### 边界

- **角色分类可见**（用户诉求原话「在后端可以看到是商家反馈还是用户反馈的分类」）：每条都带角色徽章，且按角色可筛
- 未处理条数在筛选条上可见（「未处理 N」）
- 空列表 → 「暂无反馈」，不是空白页
- 列表默认按提交时间**倒序**，默认筛「未处理」
- 非 admin 打开 `/admin/feedback` → 跳登录页（同 06 既有准入）
- 点「标记已处理」成功后，该行状态就地变「已处理」，**不整页刷新**
- 操作失败（`409` 已被别人处理）→ 就地提示 + 刷新该行

### 明确不做（本页）

- ❌ 不做回复框 / 内部备注
- ❌ 不做批量「全部标为已处理」
- ❌ 不做新反馈的未读红点 / 通知
- ❌ 不做导出

---

# 追加（2026-09-16）：后台另外三个页面（成本看板 / 用户管理 / 异常处理）

> 后端 A / B / C 三组端点**早已落地并全绿**（06 的 91 条），本轮只做**页面**。
> 挂在 8002 的 `/admin` 下，与 `/feedback` 同一套设计系统（`frontend-admin/src/tokens.css`：
> genre `modern-minimal` · macrostructure `Workbench` · theme `Coral` · nav `N9` · footer 无）。
> **不再新增 token、不再换主题**——这三页是既有系统里的新屏幕。

## 一句话目标

> 平台运营能在后台把当天的 AI 花费看完（按商户 / 按日 / 按模型）、能按名字把人找出来并封禁解封、
> 能把四类待办（内容待审 / 截图低置信 / 截图与插件不符 / 申诉）逐个清空。

---

## G0. 路由与导航（三页共用）

| 路径 | 页面 |
|---|---|
| `/admin/feedback` | 反馈收件箱（已有） |
| `/admin/cost` | 成本看板 |
| `/admin/users` | 用户管理列表 |
| `/admin/users/:id` | 用户详情 |
| `/admin/exceptions` | 异常处理 |

**边界**

- 四个目的地（反馈 / 成本 / 用户 / 异常）在顶栏下方一条**页签轨**上，当前项以 accent 下划线标记
- 未登录访问上述任一 → 跳登录页（与 `/feedback` 同一道准入，`AdminGuard`）
- 非法 id（`/admin/users/abc`）或后端 `404` → 就地「用户不存在」，**不白屏、不崩**
- 未知路径 `*` → 落到 `/feedback`（保持既有行为）

**明确不做**

- ❌ 不做侧栏 / 汉堡菜单（4 个目的地，侧栏白吃 1/4 屏宽）
- ❌ 不做面包屑
- ❌ 不做页面切换动效

---

## G1. 成本看板 `/admin/cost`

**页面节奏（与 `/feedback` 有意不同）**：**数字在前，明细在后**。
区间控件**不单独占一行**——它缩在总额带右侧；`/feedback` 那条「筛选条独占一行」的节奏**不复制**。

### 区域

| 段 | 内容 | 数据来源 |
|---|---|---|
| 总额带 | 1 个主数字（总花费）+ 4 个次数字（生成数 / 成功 / 失败 / 均价） | `GET /api/admin/cost/summary` |
| 区间控件 | `from` / `to` + 4 个预设：今天 · 近 7 天 · 近 30 天 · 本月 | 同上（本地计算后传参） |
| 按模型 | 表：provider · 花费 · 次数 · 均价 · 成功率 | `GET /api/admin/cost/by-provider` |
| 按商户 | 表：店名 · 花费 · 生成数 · 视频数 · 文案数；**分页** | `GET /api/admin/cost/by-merchant` |
| 按日趋势 | 竖条带（每天一根），仅在有数据时画 | `GET /api/admin/cost/by-day` |
| 熔断告警 | 表：店名 · 告警日 · 当日花费 · 当日预算 | `GET /api/admin/cost/budget-alerts` |
| 商户明细 | 点「按商户」的行 → 就地展开该商户的 `by_day` / `by_provider` / `recent_jobs` | `GET /api/admin/cost/merchants/{id}/detail` |

### 边界

- **金额一律按「分」展示，除以 100 只在渲染那一层做**（全局约定 3：存的是分）。展示为 `¥12.34`
- **主数字与「按商户」合计必须一致**（后端已保证；前端**不得**自己再算一遍合计来展示——两个来源迟早会漂）
- 区间非法（`from > to` / 跨度 > 366 天）→ 后端 `422`；前端**就地**在区间控件上报错，**其余段保留上一次成功的数字**，不整页清空、不白屏
- 无任何数据 → 每段显示 0 / 空表说明文字，**不得**出现 `null` / `NaN` / `¥NaN`
- **熔断告警段为空时整段不渲染**（空表占位会把「没有告警」读成「加载失败」）
- 按日趋势**缺日补 0 由后端保证**；前端不得自己插空日期
- 按商户表分页 → `size` 上限 100（全局约定 4）
- 切换区间 → 五段一起重取；**正在请求时总额带显示上一层数字 + 一条轻提示**，不留白

### 明确不做（本页）

- ❌ **不引入图表库**（deps 里没有，也不打算加）。按日趋势用纯 CSS 竖条带；不画折线、不做缩略轴、不做 tooltip
- ❌ 不做导出 CSV / Excel
- ❌ 不做同比环比、不做预测
- ❌ 不做自动刷新 / 轮询
- ❌ 不做**计费额**（07 口径）与本看板（真实成本）的合并展示——两套账，**不得混用**

---

## G2. 用户管理 `/admin/users`

**页面节奏**：**搜索在前 + 主从**，详情走**独立路由**（`/admin/users/:id`），
与 `/feedback` 的行内展开、`/cost` 的行内展开都不同——详情可直链、可刷新、可后退。

### 区域

| 段 | 内容 | 数据来源 |
|---|---|---|
| 搜索行 | 关键词（账号 / 昵称 / 店名）+ 角色 select（全部 / 商户 / 客户）+ 状态 select（全部 / 正常 / 已封禁） | `GET /api/admin/users?role&status&keyword&page&size` |
| 结果表 | **密集表格**：账号 · 昵称 · 角色徽章 · 状态 · 店名 | 同上 |
| 分页 | `page` / `size` | 同上 |
| 详情（独立路由） | 资料 + 4 个统计数（任务 / 领取 / 生成 / 积分余额）+ 商户档案（**仅商户有**）+ 操作日志 | `GET /api/admin/users/{id}` · `GET /api/admin/action-logs` |
| 封禁 / 解封 | 详情页右上角动作按钮 | `POST /api/admin/users/{id}/ban` · `/unban` |

### 边界

- 搜索按**字面量**匹配（后端已转义 `%` `_`）；前端**不得**自己拼通配符
- 关键词搜索必须能命中**店名**（后端 join 了 `merchant_profile`；前端传 `keyword` 即可）
- 结果为空 → 「没有匹配的用户」，不是空白页
- 列表默认按 `user.id` 稳定排序，分页时**同一行不会重复出现**
- 点行 → 进 `/admin/users/:id`；返回时**列表的筛选与页码保留**（不重置）
- 详情页：客户**不显示**「商户档案」区（后端本来就不返回这个键），不是显示一片空白
- 封禁：
  - `reason` < 5 字 → **前端先拦**（就地提示），不浪费一次请求；后端 `422` 也要能兜住
  - 成功 → 就地变「已封禁」+ 按钮换成「解封」，**不整页刷新**
  - `409` 已是 `banned` → 就地提示「该用户已被封禁」并刷新该行状态
  - `409` 目标是 admin → 原样显示后端文案（**不能封禁管理员**），不是泛泛的「操作失败」
- 解封：非 `banned` → `409` 就地提示
- 封禁 / 解封成功后，**详情页能立刻读到新状态**（重取 `GET /users/{id}`）

### 明确不做（本页）

- ❌ 不做新建 / 编辑用户（后台只管封禁与解封）
- ❌ 不做导出、不做批量封禁
- ❌ 不做用户画像 / 行为时间线
- ❌ 不做积分调账入口（07 的 admin 调账端点不在本页）

---

## G3. 异常处理 `/admin/exceptions`

**页面节奏**：**队列之队列**。顶部是一个**四段分段控件、每段带计数**——
计数是这一页的导航，不是装饰。`/feedback` 用三个下拉，这里**不用下拉**。

### 区域

| 段 | 内容 |
|---|---|
| 分段控件 | 内容待审 N · 截图低置信 N · 数据不符 N · 申诉 N（4 个计数各自独立取） |
| 清单 | 按当前段换列的列表（见下表） |
| 行内动作 | 每行按类型给 2 个动作 + 可选备注 |

**按类型换列**

| 段（type） | 列 | 动作 |
|---|---|---|
| 内容待审 `content_review` | job id · 类型（视频/文案）· 任务 id · 用户 id · 时间 | `approve` 放行 · `discard` 作废 |
| 截图低置信 `ocr_low_confidence` | 缩略图 · `confidence` · 模型 · 时间 | `accept` 采纳 · `reject` 不采纳 |
| 数据不符 `ocr_mismatch` | 缩略图 · `mismatch_flag` · 模型 · 时间 | `accept` · `reject` |
| 申诉 `appeal` | `reason` **全文不截断** · post id · 用户 id · 时间 | `accept` 支持 · `reject` 维持 |

### 边界

- 计数与列表**必须来自同一口径**：计数用 `?size=1` 读 `total`，不得前端自己数当前页的行数
- 处理成功 → 该行**就地消失**（这是队列，不是流水），计数 **−1**，**不整页刷新**
- `409`（已被别人处理）→ 就地提示 + **重取该段**（列表与计数一起）
- `422`（action 非法）在正常 UI 上不可达；若出现 → 就地提示，不静默吞掉
- 空队列 → 「这一队清空了」，不是空白页
- 切换分段 → 只重取该段的列表；**已取过的计数保留**（切回来不闪 0）
- 申诉段：`reason` 是用户写的原文，**截断会丢掉判断依据**，必须全文显示（可换行，不省略号）
- 缩略图加载失败 → 显示占位块 + `image_url` 文本，**不留破图**

### 明确不做（本页）

- ❌ 不做批量处理（一次一行）
- ❌ 不做申诉的二次申诉入口
- ❌ 不做异常的历史归档页（处理完就离开队列）
- ❌ 不做截图的全屏查看器 / 标注工具
- ❌ **不做自动刷新**（运营手动切段时自然重取）
