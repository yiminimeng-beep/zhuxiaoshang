# 07 · 额度、计费与报销

> 谁的任务谁付钱。商户可以直接付，也可以让用户先垫付、作品过审后报销——报销有上限，且报销出去的是平台额度，不是现金。用户还可以自带 API Key。

---

## 一句话目标

> 每次 AI 调用的费用，最终由**内容所属任务的那个商户**承担，但承担方式有两种：商户直接付（`merchant_pay`）或用户先垫付、审核通过后由商户报销（`user_pay_reimburse`）。报销有单用户上限与单任务池子上限双重封顶，且只退还平台额度、不退还现金。用户可自带 provider API Key，此时平台零成本，但密钥必须加密存储、永不回显。

---

## 两种付费模式（**按任务选**，`task.pay_mode`）

| pay_mode | 谁先掏钱 | 用户风险 | 商户风险 | 适用 |
|---|---|---|---|---|
| `merchant_pay` | 商户额度 | 无 | 有（被薅），靠自己设日限额 | 拉新、低门槛拉参与 |
| `user_pay_reimburse` | 用户额度（或用户自己的 Key） | 垫付可能拿不回 | 有但**有上限**（池子封顶） | 防刷、只付结果 |

### 报销的三个铁规则

1. **只退平台额度，不退现金。** 商户额度 → 用户额度，是平台内转账。**退现金 = 二清**，本期不做。
2. **池子在建 job 时就预占，不是审核后才检查。** 否则 1000 个用户同时生成，池子早就穿了。
3. **72h 自动通过同样触发报销。** 这是商户的硬成本，前端必须在商户配置任务时明示。

---

## 密钥来源（与 pay_mode 正交）

| `billing_source` | 调 provider 用谁的 Key | 平台成本 | 合法 |
|---|---|---|---|
| `platform` | 平台统一持有的 Key | 有（平台真实账单） | ✅ |
| `byok` | 用户自己的 Key | **0** | ✅ 仅 `user_pay_reimburse` |

**组合矩阵**（不合法的一律 `422`，**不静默回落**）：

| pay_mode \ billing_source | `platform` | `byok` |
|---|---|---|
| `merchant_pay` | ✅ | ❌ `422`（商户付费却用别人的 Key，成本口径会乱） |
| `user_pay_reimburse` | ✅ | ✅ |

---

## 数据模型

### `quota_account`（**用户与商户共用一套账户**）
| 字段 | 类型 | 约束 |
|---|---|---|
| user_id | bigint | 主键，外键 → user |
| balance | int | **必填**，默认 0，**>= 0**，单位「点」，1 点 = 1 分 |
| reserved | int | **必填**，默认 0，>= 0，未结算预扣占用；**可用额 = `balance - reserved`** |
| debt | int | **必填**，默认 0，欠款点数 |
| total_recharged / total_consumed | int | **必填**，默认 0 |
| total_reimburse_in / total_reimburse_out | int | **必填**，默认 0（收款方 / 付款方） |
| daily_limit | int | 可空；`null` = 用平台默认（**商户语义**：自设日预算） |
| per_user_daily_limit | int | 可空；**商户语义**：限单个用户单日消耗 |
| per_user_task_limit | int | 可空；**商户语义**：限单个用户单任务累计 |
| user_daily_limit | int | 可空；**用户语义**：平台给该用户设的单日消耗上限 |
| status | enum | **必填**，`active` / `frozen` |
| updated_at | timestamptz | **必填** |

> `balance - reserved >= 0` 为硬约束；`reserved` 是"已承诺但未发生"的钱。

### `quota_ledger`（**不可变，只追加**）
| 字段 | 类型 | 约束 |
|---|---|---|
| id | bigint | 主键 |
| user_id | bigint | **必填**（账户主体，用户或商户），外键，索引 |
| change | int | **必填**，正负整数 |
| balance_after | int | **必填**，>= 0 |
| source | enum | **必填**，`recharge` / `consume` / `refund` / `adjust` / `debt_clear` / `reimburse_out` / `reimburse_in` |
| ref_type | varchar(32) | **必填** |
| ref_id | bigint | **必填** |
| job_id / task_id | bigint | 可空，外键 |
| spender_id | bigint | 可空，**实际消耗方**（商户付费时 = 那个用户；用于按用户出报表） |
| counterparty_id | bigint | 可空，对手方（报销转账的另一端） |
| billing_source | enum | 可空，`platform` / `byok` |
| provider | varchar(32) | 可空 |
| remark | varchar(256) | 可空 |
| created_at | timestamptz | **必填**，**不得 UPDATE / DELETE** |
| **唯一约束** | — | `(source, ref_type, ref_id)` 唯一（防重复入账） |

**写流水的时机**：预扣**不写**流水（只增 `reserved`）；**结算**与**释放**才写。释放时 `change=0` 不写行，靠 `quota_reservation.status` 审计。

### `quota_reservation`（预扣单）
| 字段 | 类型 | 约束 |
|---|---|---|
| id | bigint | 主键 |
| job_id | bigint | **必填，唯一**，外键（幂等核心） |
| payer_id | bigint | **必填**，付款方 user_id（`merchant_pay` = 商户；`user_pay_reimburse` = 用户） |
| spender_id | bigint | **必填**，消耗方 user_id |
| task_id | bigint | **必填**，外键 |
| billing_source | enum | **必填**，`platform` / `byok` |
| reserved | int | **必填**，>= 0（`byok` 时为 0） |
| actual | int | 可空 |
| status | enum | **必填**，`reserved` / `settled` / `released` |
| created_at / settled_at | timestamptz | 必填 / 可空 |
| **另** | — | `user_pay_reimburse` 时另有 `reimburse_reserved` int，**必填**，占商户报销池（见下） |

### `quota_recharge`（充值单）
| 字段 | 类型 | 约束 |
|---|---|---|
| id | bigint | 主键 |
| user_id | bigint | **必填**，外键（用户或商户） |
| amount_cents / points | int | **必填**，>= 1 |
| channel | enum | **必填**，本期只有 `manual`（线下转账 + 管理员记账） |
| operator_id | bigint | **必填** |
| remark | varchar(256) | 可空 |
| created_at | timestamptz | **必填** |

### `user_model_key`（**BYOK 密钥**）
| 字段 | 类型 | 约束 |
|---|---|---|
| id | bigint | 主键 |
| user_id | bigint | **必填**，外键 |
| provider | varchar(32) | **必填**，**白名单枚举**（见下） |
| label | varchar(32) | 可空，用户备注 |
| key_cipher | text | **必填**，AES-256-GCM 密文（含 nonce + tag） |
| key_masked | varchar(32) | **必填**，如 `sk-****a1b2`，**仅此用于展示** |
| key_version | int | **必填**，加密主密钥版本（轮换用） |
| status | enum | **必填**，`active` / `invalid` / `disabled` |
| fail_count | int | **必填**，默认 0 |
| last_verified_at / last_used_at | timestamptz | 可空 |
| created_at / updated_at | timestamptz | **必填** |
| **唯一约束** | — | `(user_id, provider)` 唯一 |

**provider 白名单（本期，与总览技术选型一致）**：`deepseek` / `dashscope`（通义 + qwen3-vl）/ `jimeng` / `kling`。
**不接受自定义 `base_url` / `endpoint`** —— 否则等于让用户把请求（含密钥）打到任意地址，SSRF + 密钥外泄。

### `model_price`（计价表，配置驱动）
| 字段 | 类型 | 约束 |
|---|---|---|
| id | bigint | 主键 |
| provider | varchar(32) | **必填** |
| model | varchar(64) | **必填**；`(provider, model, op, effective_from)` 唯一 |
| op | enum | **必填**，`guard` / `chat` / `rewrite` / `generate` / `judge` |
| unit | enum | **必填**，`token` / `call` / `second` / `image` |
| cost_price_per_unit | numeric(10,6) | **必填**，平台真实成本（**报销基数用它**） |
| price_per_unit | numeric(10,6) | **必填**，向商户/用户计费价 = 成本 × `markup_rate` |
| max_price_per_call | int | **必填**，单次调用计价上界（**预扣依据**） |
| markup_rate | numeric(5,2) | **必填**，默认 1.00 |
| supports_byok | boolean | **必填**，默认 false；是否允许用户自带 Key |
| provider_visible | boolean | **必填**，默认 true；是否出现在用户可选列表 |
| effective_from | timestamptz | **必填**，**改价只插新行，不改旧行** |

### `reimburse_claim`（报销单）
| 字段 | 类型 | 约束 |
|---|---|---|
| id | bigint | 主键 |
| post_id | bigint | **必填，唯一**（幂等核心） |
| claim_id / task_id / merchant_id / user_id | bigint | **必填**，外键 |
| base_points | int | **必填**，应报基数 = Σ 计入 job 的 `cost_price × 实际用量` |
| covered_points | int | **必填**，实报额（受两个上限截断） |
| status | enum | **必填**，`settled` / `released` / `capped` / `pool_exhausted` |
| reason | text | 可空 |
| created_at / settled_at | timestamptz | 必填 / 可空 |

### `reimburse_claim_job`（**一个 job 只能被报销一次**）
| 字段 | 类型 | 约束 |
|---|---|---|
| reimburse_claim_id | bigint | **必填**，外键 |
| job_id | bigint | **必填，唯一**，外键 |
| points | int | **必填** |

### `task` 新增字段（定义在 02-task，此处列出语义）
| 字段 | 类型 | 约束 |
|---|---|---|
| pay_mode | enum | **必填**，默认 `merchant_pay` |
| reimburse_pool | int | `user_pay_reimburse` 时**必填**，>= 0，报销池总额 |
| reimburse_pool_used | int | **必填**，默认 0，已报销 |
| reimburse_pool_reserved | int | **必填**，默认 0，已预占 |
| reimburse_per_user_limit | int | `user_pay_reimburse` 时**必填**，>= 0（0 = 不报销，即纯用户自费） |

**池子可用额 = `reimburse_pool - reimburse_pool_used - reimburse_pool_reserved`**，且必须有对应的商户 `quota_account.reserved` 真实锁定（否则是空头承诺）。

---

## 扣费链路（服务端强制）

```
建 job：POST /api/jobs { task_id, kind, assets, provider?, billing_source? }
  ↓ ① 付费方判定（唯一来源，客户端不可指定）
      merchant_pay        → payer = task.merchant_id，spender = 当前用户
      user_pay_reimburse  → payer = 当前用户（垫付），商户占报销池
      请求体出现 merchant_id / payer / user_id → 422
  ↓ ② 组合合法性：billing_source=byok 且用户无该 provider 有效 Key → 422
      （2026-09-18：`merchant_pay` + byok **改为允许**，见文末追加）
  ↓ ③ 准入（任一不过即拒）
      商户 status=active ？ payer 可用额 >= 预扣 ？ 未超各方限额 ？
      未超并发？ 未超用户日限额？
      user_pay_reimburse：报销池可用额 >= reimburse_reserved ？否则 429
  ↓ ④ 行锁预扣（原子条件更新，影响行数=0 即失败）
      UPDATE quota_account SET reserved = reserved + :r
        WHERE user_id = :payer AND balance - reserved >= :r
      user_pay_reimburse 额外：
      UPDATE quota_account SET reserved = reserved + :rr
        WHERE user_id = :merchant AND balance - reserved >= :rr
      INSERT quota_reservation（job_id 唯一）
  ↓ ⑤ 异步执行 AI 链路（provider / billing_source 来自 job）
  ↓ ⑥ 结算
      成功：actual = ceil(Σ 实际用量 × price_per_unit)   （byok → actual = 0）
            balance -= actual；reserved -= r；写 ledger(source=consume)
      系统故障 / 用户取消        → reserved -= r，status=released，不写流水
      预检不过 / judge 3 次不过   → **照常结算不退款**（策略性失败）
  ↓ ⑦ 报销（仅 user_pay_reimburse，在审核通过时触发）
      见下节
```

---

## 报销流程

```
social_post → approved / auto_approved / appeal_accept
  ↓ 汇总：该 claim 下所有未报销 job 的 cost_price 基数  → base_points
  ↓ 三重截断：covered = min( base, 单用户剩余额度, 池子可用额 )
  ↓ 转账：商户 quota_account.balance - covered
          用户 quota_account.balance + covered
          两条 ledger（reimburse_out / reimburse_in，互为 counterparty）
  ↓ 落 reimburse_claim（post_id 唯一）+ reimburse_claim_job（job_id 唯一）
  ↓ 释放商户预占：reserved -= 该 claim 累计 reimburse_reserved

驳回 / 申诉被拒 → 释放预占，池子退回，用户自担（status=released）
```

**计费口径统一**：报销基数按 `model_price.cost_price_per_unit`（**成本价**，不含 markup），与该用户实际向 provider 付了多少、或平台向商户收了多少都无关。这样 BYOK 用户与平台额度用户拿到的报销额可比、可审计。

---

## 端点 / 接口

### 商户侧（`/api/merchant/quota/*`）

| 动作 | 输入 | 成功返回 | 失败情况 |
|---|---|---|---|
| 我的额度 | `GET /api/merchant/quota` | `200` `{balance, reserved, available, debt, today_consumed, today_reserved, daily_limit, per_user_daily_limit, per_user_task_limit, status}` | `403` |
| 额度流水 | `GET /api/merchant/quota/ledger?from&to&source&page&size` | `200` `{items,total,page,size}` | `422` 跨度 > 366 天 |
| 用量报表 | `GET /api/merchant/quota/usage?from&to&group_by=task\|user\|provider\|day` | `200` `{items:[{key, points, call_count}]}` | `422` `group_by` 非法 |
| 报销台账 | `GET /api/merchant/reimbursements?task_id&status&page&size` | `200` `{items,total,page,size}` | `403` |
| 我的充值记录 | `GET /api/merchant/quota/recharges` | `200` `{items,...}` | `403` |
| 改限额 | `PATCH /api/merchant/quota/limits`<br>`{daily_limit?, per_user_daily_limit?, per_user_task_limit?}` | `200` | `422` 负数<br>`422` 低于今日已消耗 |
| 冻结 / 解冻自己额度 | `PATCH /api/merchant/quota/status`<br>`{status}` | `200` | `422` 非法枚举<br>`409` 有未结算预扣单 |
| 看计价表 | `GET /api/merchant/quota/price` | `200` `{items}` | `403` |
| 待付欠款 | `GET /api/merchant/quota/debt` | `200` `{debt, items}` | `403` |

### 用户侧（`/api/me/*`）

| 动作 | 输入 | 成功返回 | 失败情况 |
|---|---|---|---|
| 我的额度 | `GET /api/me/quota` | `200` `{balance, reserved, available, debt, total_reimburse_in, user_daily_limit, status}` | `401` |
| 额度流水 | `GET /api/me/quota/ledger?from&to&page&size` | `200` `{items,...}` | `401` |
| 我的报销记录 | `GET /api/me/reimbursements?task_id&page&size` | `200` `{items:[{task_id, post_id, base_points, covered_points, status, settled_at}],total}` | `401` |
| **某任务的可报销预览** | `GET /api/me/reimburse-preview?task_id=` | `200` `{pay_mode, per_user_limit, my_consumed_points, my_reimbursed_points, my_remaining, pool_remaining, est_points}` | `403` 未领取该任务 |
| 我的充值记录 | `GET /api/me/quota/recharges` | `200` `{items,...}` | `401` |

> `reimburse-preview` 是**用户决策前的必要信息**：不给他看"能报多少、池子还剩多少"，就是让他盲赌。

### 模型选择与密钥（用户侧）

| 动作 | 输入 | 成功返回 | 失败情况 |
|---|---|---|---|
| **可选模型列表** | `GET /api/models?op=&merchant_id=` | `200` `{items:[{provider, model, op, unit, price_per_unit, cost_price_per_unit, supports_byok, has_my_key, est_price_per_call}]}` | `401`<br>`422` `op` 非法<br>`404` `merchant_id` 不存在 |
| 我的密钥列表 | `GET /api/me/model-keys` | `200` `{items:[{id, provider, label, key_masked, status, last_verified_at, last_used_at}]}` | `401` |
| 新增密钥 | `POST /api/me/model-keys`<br>`{provider, api_key, label?}` | `201` `{id, provider, key_masked, status}` | `422` provider 非白名单<br>`422` 带 `base_url`/`endpoint`<br>`422` 校验失败<br>`409` 该 provider 已有密钥 |
| 改密钥 | `PATCH /api/me/model-keys/{id}`<br>`{api_key?, label?, status?}` | `200` `{id, key_masked, status}` | `403` 非本人<br>`404`<br>`422` |
| 删密钥 | `DELETE /api/me/model-keys/{id}` | `204` | `403` 非本人<br>`404` |
| 校验密钥 | `POST /api/me/model-keys/{id}/verify` | `200` `{status, last_verified_at}` | `403`<br>`404`<br>`422` 校验失败（provider 401） |

### 管理员侧（`/api/admin/quota/*`，`role=admin`）

| 动作 | 输入 | 成功返回 | 失败情况 |
|---|---|---|---|
| 账户列表 | `GET /api/admin/quota/accounts?role&status&keyword&page&size` | `200` `{items,total,page,size}` | `403`<br>`422` `role` 非法 |
| 账户详情 | `GET /api/admin/quota/accounts/{id}` | `200` `{account, recent_ledger, by_day, byok_call_count}` | `404` |
| 记账充值 | `POST /api/admin/quota/accounts/{id}/recharge`<br>`{amount_cents, points, remark}` | `201` `{recharge, balance}` | `422` < 1<br>`404` |
| 人工调整 | `POST /api/admin/quota/accounts/{id}/adjust`<br>`{points, remark}` | `200` `{balance}` | `422` points=0 或 remark < 5 字<br>`422` 扣后余额为负 |
| 冻结 / 解冻 | `POST /api/admin/quota/accounts/{id}/status`<br>`{status, reason}` | `200` | `409` 状态相同<br>`422` reason < 5 字 |
| BYOK 使用情况 | `GET /api/admin/quota/byok?from&to` | `200` `{items:[{provider, call_count, zero_cost_count, invalid_key_count}]}` | `422` |
| 报销台账（**争议处理用**） | `GET /api/admin/quota/reimbursements?merchant_id&user_id&task_id&status&page&size` | `200` `{items,total,page,size}` | `403`<br>`422` 参数非法 |
| 计价表维护 | `POST /api/admin/quota/prices` | `201` | `409` 同 `(provider,model,op,effective_from)` 重复 |

> 管理员的充值 / 调整 / 冻结**必须各写一条 `admin_action_log`**（见 06）。

### 内部（非 HTTP，03-studio 调用）

| 动作 | 触发点 | 语义 |
|---|---|---|
| `reserve(...)` | 建 job | 行锁增 `reserved`（payer 侧 + 商户报销池侧），写预扣单；可用额不足抛 402 / 429 |
| `settle(job_id, actual, breakdown)` | job 终态 | 扣 `balance`、减 `reserved`、写流水 |
| `release(job_id)` | 系统故障 / 用户取消 | 减 `reserved`，不写流水 |
| `reimburse(post_id)` | 审核通过 | 三重截断 + 商户→用户转账 + 落报销单 |

---

## 边界（每条之后会变成一条测试）

### 付费方绑定与组合合法性
- 建 job 请求体带 `merchant_id` / `payer` / `user_id` → **`422`**，不得按传入值扣费
- `merchant_pay` 任务传 `billing_source=byok` → **允许**（2026-09-18 修订；成本仍 0，不静默改成 platform）
- `billing_source=byok` 但无该 provider 有效 Key → `422`
- 用户用 B 商户的 `task_id` 建关于 A 内容的 job → 扣 B 的口径（付费方只看 `task_id`）
- `task_id` 不属于该用户的 claim → `403`
- 已冻结商户的任务被建 job → `403`
- 跨账户查额度 / 流水 / 报销台账 → `403`（不是 404）

### 预扣与结算
- 可用额 100，预扣 60 的两个 job 并发 → **恰好 1 个成功**，另一个 `402`
- 可用额 59，预扣 60 → `402`，**`reserved` 不得被改动**
- 预扣 60、实际 45 → `balance -= 45`，`reserved -= 60`，流水 1 条 `consume(-45)`
- 预扣 60、实际 60 → 不退不补
- 预扣 60、实际 63（计价表失准）→ `balance -= 60`（只扣已锁的），`debt += 3`，**`balance` 不得为负**
- 系统故障 / 队列丢弃 / 用户取消 → `reserved` 退回，`status=released`，**不得产生 consume 流水**
- 预检不过 / judge 3 次不过 → **照常结算不退款**
- 同一 `job_id` 重复投递结算 3 次 → `quota_ledger` 只多 **1 条**（`(source,ref_type,ref_id)` + `quota_reservation.job_id` 双保险）
- 并发下两条流水的 `balance_after` 必须递增不重复（行锁串行）
- `balance` / `reserved` 任何情况下不得为负（DB `CHECK` 兜底）
- `available` 必须恒等于 `balance - reserved`

### 报销
- 单用户上限 500（5 元），用户在该任务花了 1000 分 → 报销 **500**，`status=capped`，剩余 500 用户自担
- 池子剩 300 分，应报 500 → 报 **300**，`status=capped`
- 池子可用额不足预占 → 建 job 直接 `429`（**不是等报销时才发现池子空了**）
- 池子预占必须在商户 `quota_account.reserved` 里真实锁定；商户余额被别人花光后，已预占的报销仍能兑现
- 72h 自动通过 → 报销**自动生效**（与人工通过同一条路径）
- 申诉被 admin `accept` → 报销同样落地（`post_id` 唯一兜底，不重复报）
- 审核驳回 → `status=released`，池子退回，用户自担
- 驳回后申诉成功 → 用同一 `post_id` upsert 落地，**只报一次**
- 同一 job 出现在两张报销单 → `reimburse_claim_job.job_id` 唯一拒绝
- 报销**只增平台额度，不进现金**；用户 `total_reimburse_in` 与实收一致
- 报销与 05 奖励**分账**：两者独立计算、独立上限，同一 post 可同时拿奖励与报销
- 用户注销后 `reimburse_claim` / `quota_ledger` 保留，可按 `user_id` 查到
- 商户拒付（余额不足）→ 预占已在建 job 时锁定，**不得出现"审核通过了但报不出钱"**

### 密钥安全（**BYOK 的核心，逐条必须测**）
- 明文 Key **永不入库、永不出现在任何响应体、日志、异常栈、tracing 中**（测试断言响应与日志里不含明文片段）
- `GET /api/me/model-keys` 只返回 `key_masked`，形如 `sk-****a1b2`
- 保存响应的 `key_masked` 长度 < 8 的原文一次都不出现
- 用户 A 改 / 删 / verify 用户 B 的密钥 → `403`
- 不存在的密钥 id → `404`
- 同一 user 同一 provider 存第二把 → `409`
- 请求体带 `base_url` / `endpoint` / `url` → `422`（防 SSRF 与密钥外泄到任意地址）
- provider 传 `openai` / `anthropic`（非白名单）→ `422`
- 保存时校验失败（provider 返回 401）→ `422`，`status=invalid`，**不得入库为 active**
- 调用时 Key 失效（provider 401/403）→ job `failed(fail_reason=key_invalid)`，`status=invalid`，`fail_count += 1`，**绝不静默回落到平台 Key**（回落等于白烧平台的钱）
- `fail_count` 达 3 → 自动 `disabled`，须用户重新保存才能再用
- 用户注销 → `user_model_key` **物理删除**（凭据非审计数据，与"审计不可删"不冲突）
- 用户被封禁 → 密钥保留但 `status=disabled`，解封后恢复可用
- 加密主密钥轮换 → `key_version` 新旧并存，重加密任务逐行迁移，期间两类密钥都能正常调用
- 数据库被拖库（仅拿到密文）→ 无 `KEY_ENCRYPTION_KEY` 无法解出明文

### BYOK 计费
- BYOK 调用 → `gen_output.cost_cents = 0` 且 `gen_output.billing_source=byok`，`quota_ledger` **不产生 consume 行**
- BYOK 下用户 `balance=0` 也能建 job（因为不扣平台额度），但仍受商户报销池与用户日限额约束
- BYOK 的报销基数按 `model_price.cost_price_per_unit` 计，与该用户实际向 provider 付了多少钱无关
- 06 成本看板必须把 BYOK 调用识别为 **0 成本**，不得因 `cost_cents=0` 误判为失败调用
- BYOK 调用仍要跑预检与 judge（安全不因自带 Key 而豁免）

### 模型选择
- `GET /api/models` 无 token → `401`；`op` 非法 → `422`
- `provider_visible=false` 的模型不出现在列表里；直接指定它建 job → `422`
- `op=generate` 选了个 `op=chat` 的模型 → `422`（provider 与 op 不匹配）
- 建 job 不传 `provider` / `billing_source` → 用平台默认（**保持与 03 现有契约兼容**）
- `est_price_per_call` 必须取 `max_price_per_call`，与预扣额一致（**前端展示的数必须与后端预扣的数同源**）
- 计价表缺该 `(provider, model, op)` 行 → 建 job `503`，**不得按 0 计费**
- 计价表改价插新行，旧行不动；已建 job 按建 job 时刻定价

### 限额与熔断
- 日限额 1000、当日已耗 900、新建预估 200 → **`429`**；预估 50 → `200`
- `daily_limit=null` → 用平台默认（配置文件），**不得视为不限**
- `per_user_daily_limit=200`，某用户当日烧满 → 该用户 `429`，其他用户不受影响
- `per_user_task_limit=500`，某用户在該任务烧满 → `429`
- 限额从 500 改到 100 而当日已耗 300 → `422`，不得把商户自己卡死
- 并发上限（平台配置，如单商户 3 个并行生成）：第 4 个 → `429`
- 熔断触发写一条 `budget_alert`（见 06），一天一条
- 三种限额都为 `null` 时仅受余额约束

### 流水与报表
- 对账：`前一笔.balance_after + 本笔.change = 本笔.balance_after` 必须成立
- `quota_ledger` 无 UPDATE / DELETE 接口与代码路径
- 用量报表 `group_by=task` 合计 == `group_by=day` 合计
- `group_by=user` 用 `spender_id` 聚合（商户付费时定位"哪个用户烧的"）
- `group_by=day` 无数据日期补 0，不得跳空
- `from > to` → `422`；跨度 366 天 → `200`，367 天 → `422`
- 已注销账户的流水仍可查

### 权限
- 无 token → `401`；`customer` 访问 `/api/merchant/quota/*` → `403`；`merchant` 访问 `/api/admin/quota/*` → `403`
- 商户 A 访问商户 B 的额度详情（admin 端点）→ `403`
- `PATCH /api/merchant/quota/limits` 试图传 `balance` / `user_id` → `400`，数据库不变
- 冻结自己额度时存在 `reserved` 预扣单 → `409`（否则结算无处落账）

---

## 明确不做

- ❌ **不做现金报销 / 提现**（涉二清）。报销只增平台额度，额度不可提现、不可转赠
- ❌ **不做 C 端在线支付通道**（用户充值同商户，走线下 + 管理员记账）
- ❌ 不做额度转让 / 账户间调拨 / 额度退款提现
- ❌ 不做额度过期清零
- ❌ 不做自动续费 / 信用卡式后付费 / 账单催收
- ❌ 不做成本预测 / 预算建议
- ❌ 不做按用户的个性化定价（同一计价表，人人一样）
- ❌ 不做积分（05）与额度（07）互转 —— **两套账**：积分是给用户的激励，额度是付平台成本的
- ❌ 不做实时余额推送 / 低余额提醒
- ❌ 不做用户侧查看"本次消耗了商户多少额度"（商户经营数据，不暴露）
- ❌ **不做密钥共享 / 代填 / 平台代管密钥**
- ❌ **不做自定义 `base_url` / 代理地址**（SSRF + 密钥外泄风险）
- ❌ 不做非白名单的境外 provider（`openai` / `anthropic` 等一律拒绝，与总览"只接国内模型"一致）
- ❌ 不做密钥明文导出 / 备份
- ❌ 不做报销的商户逐条审批（与 04 审核同一个动作，不搞二次确认）

---

## 落地分两趟（2026-09-15 确认）

07 的表有四处外键指向尚未落地的模块：`quota_reservation.job_id` / `quota_ledger.job_id` /
`reimburse_claim_job.job_id` → 03 的 `content_job`，`reimburse_claim.post_id` → 04 的 `social_post`。
而 03 的建 job 又要调 07 的 `reserve()`——**互相依赖，必须有一方分两趟做**。
决定：**07 分两趟，第一趟只做不依赖 03/04 的账户与计价。**

### 第一趟（本次做）
- 建表：`quota_account` / `quota_recharge` / `model_price` / `user_model_key` / `quota_ledger`
- `quota_ledger.job_id` **先只留列、不建外键**（`content_job` 未落地，建了 `create_all` 就失败）；
  03 落地后补 `ALTER TABLE ... ADD CONSTRAINT`。`task_id` 外键指向 02 的 `task`，正常建
- 端点：商户额度 / 流水 / 用量报表 / 限额 / 冻结解冻 / 计价表 / 欠款 / 充值记录；
  用户额度 / 流水 / 报销记录 / 充值记录；`GET /api/models`；密钥 CRUD 六件套；
  admin 额度账户列表 / 详情 / 记账充值 / 人工调整 / 冻结解冻 / BYOK 使用情况 / 计价表维护
- 解开：02 的 `PM-10` / `PM-11` / `PM-12`（只要 `quota_account` 的 `balance` / `reserved`）
  与 01 的 `O-02`（`GET /api/merchant/quota`）

### 第二趟（等 03/04 落地后做）
- 建表：`quota_reservation` / `reimburse_claim` / `reimburse_claim_job`
- 内部函数：`reserve(...)` / `settle(...)` / `release(...)` / `reimburse(post_id)`
- 相关边界：预扣与结算、报销、BYOK 计费里依赖 `gen_output` 的几条
- `GET /api/me/reimburse-preview` 依赖「我是否领取了该任务」，02 已提供 `task_claim`，
  但「已消耗 / 已报销」要等预扣单落地，故一并放第二趟

> 第二趟的用例在 `test_plan.md` 里**一并写出**，落地前它们红的类型是
> `SchemaMissing`（表不存在）或路由缺失——与 02 的 `PM-10~12` 同类，
> **不是第一趟的缺陷**。

---

## 已确认的口径（2026-09-15）

1. **用户充值形成的 C 端预付款 —— 已确认接受**，守住两条红线：①只走线下对公转账 + 管理员记账，**不开在线支付通道**；②报销只退平台额度，**不可提现、不可转让**。任一被打破即构成二清，必须重新评审。
2. **BYOK 的小幅套利 —— 已确认接受**（用户自带 Key 时按平台成本价拿报销额度，理论上可攒额度）。由 `reimburse_pool` + `reimburse_per_user_limit` 双重封顶，**封顶值为硬上限，任何情况下不得突破**。
3. **72h 自动通过 + 报销 = 商户不审核也要付钱 —— 已确认接受**。缓解：报销池在任务发布时就真实锁定，池子大小 = 商户愿意为该任务承担的最大报销额。

---

# 追加（2026-09-18）：允许 merchant_pay + BYOK

> 与 03 追加 D / 08 追加 F 对齐：客户可在站内用自带 Key 创作，不限任务付费模式。

- **原**：`merchant_pay` + `billing_source=byok` → `422`
- **现**：**允许**；`gen_output.cost_cents=0`、`billing_source=byok`，不扣商户 AI 额度
- **仍拒**：`byok` 但用户无该 provider 的 active Key → `422`
- 既有「不得静默改成 platform」仍成立：显式传了 `byok` 就按 byok 走或 422，不偷换成 platform
