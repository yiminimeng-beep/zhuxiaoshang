# 07-token · 测试计划

> 由 `spec.md` 的「端点」与「边界」逐条转出。一个 ID = 一条测试，ID 与测试函数一一对应。
>
> **本轮做「第一趟」**（账户 / 计价 / 密钥 / 额度端点）。「第二趟」的用例**一并写在
> 本计划里**，但它们依赖 03 的 `content_job` / 04 的 `social_post`，落地前红的类型是
> `SchemaMissing` 或路由缺失——与 02 的 `PM-10~12` 同类，**不是第一趟的缺陷**。
>
> 覆盖：29 个端点的正常路径 + 9 组边界 ≈ 130 条。

---

## 运行方式

```bash
cd backend
docker compose -f ../docker-compose.yml up -d     # PG:5433 / Redis:6380
.venv/Scripts/python -m pytest -q
```

测试用**独立库** `zhuxiaoshang_test`，每个用例前后 `TRUNCATE` 所有表。

---

## 用例里反复出现的前置

| 工厂 | 作用 |
|---|---|
| `quota_account_for(db, user_id, **over)` | 直插一条 `quota_account`（默认 `balance=0, reserved=0, status='active'`） |
| `grant(db, user_id, balance)` | 给账户充值到指定余额（直插 + 一条 `recharge` 流水） |
| `ledger_rows(db, user_id)` | 取该用户的流水，按 `id` 升序 |
| `admin_token(client, seed_accounts)` | 用种子的 `999999` 管理员登录，返回 token |
| `insert_price(db, provider, model, op, **over)` | 直插 `model_price` 一行，返回 id |
| `post_keys(client, token, provider, api_key)` | 存一把 BYOK 密钥，返回响应体 |

**明文 Key 的探针**：`SECRET_KEY_SAMPLE = "sk-live-abcdefghijklmnop-a1b2"`。
凡断言「不出现在 X 里」的用例，都用 `SECRET_KEY_SAMPLE` 的**中段**
（`abcdefghijklmnop`）做子串搜索——整串匹配会被掩码挡掉而假绿。

---

## 测试缝（seam）——实现必须提供的两个名字

BYOK 的两件事没法从 HTTP 层测：**加密往返**与**调外部 provider**。测试需要一个
稳定的接缝，否则只能去 patch `httpx` 这种实现细节（换个 SDK 就全红）。约定两个名字：

| 接缝 | 签名 | 用例 |
|---|---|---|
| `app.services.model_key.verify_provider_key` | `async (provider: str, api_key: str) -> bool` | `E-23` / `K-12` / `K-21` |
| `app.services.model_key.decrypt_key` | `(key_cipher: str, key_version: int \| None = None) -> str` | `K-13` / `K-24` |

测试用 `monkeypatch.setattr("app.services.model_key.verify_provider_key", fake)`。
**若实现换掉这两个名字，对应用例会直接 `AttributeError` 而不是静默跳过**——这正是想要的：
接缝是契约，改名必须同步改测试。

---

## 一、第一趟

### A. 计价表与模型选择（`test_token_models.py`）

| ID | 前置 | 输入 | 期望 | 测试函数 |
|---|---|---|---|---|
| E-01 | 计价表有 `deepseek/deepseek-chat/chat` | `GET /api/models` | `200`，item 含 `provider/model/op/unit/price_per_unit/cost_price_per_unit/supports_byok/has_my_key/est_price_per_call` | `test_e01_models_list` |
| MC-01 | — | 无 token | `401` | `test_mc01_no_token_401` |
| MC-02 | — | `op=foo` | `422` | `test_mc02_bad_op_422` |
| MC-03 | — | `merchant_id=999999` | `404` | `test_mc03_unknown_merchant_404` |
| MC-04 | 有 `provider_visible=false` 的模型 | `GET /api/models` | **不含**该模型 | `test_mc04_hidden_model_excluded` |
| MC-05 | 同 `(provider,model,op)` 两行 `effective_from` 不同 | `GET /api/models` | 取**生效最新**那行的价格 | `test_mc05_latest_effective_wins` |
| MC-06 | `max_price_per_call=120` | `GET /api/models` | `est_price_per_call == 120`（与预扣同源） | `test_mc06_est_equals_max_price` |
| MC-07 | 用户已存该 provider 的 Key | `GET /api/models` | `has_my_key=true` | `test_mc07_has_my_key_true` |
| MC-08 | 用户未存 Key | `GET /api/models` | `has_my_key=false` | `test_mc08_has_my_key_false` |
| MC-09 | `supports_byok=false` 的模型 | `GET /api/models` | `supports_byok=false`（前端据此禁用 BYOK） | `test_mc09_byok_unsupported` |
| MC-10 | — | `op=chat` 但库里只有 `op=generate` 的行 | `200` 且**不含**该行（不跨界回退） | `test_mc10_op_mismatch_excluded` |
| MC-11 | 计价表改价插新行 | 查旧 job 时刻的价格 | 旧行仍在，`effective_from` 未变 | `test_mc11_price_row_immutable` |
| MC-12 | `merchant_id` 指向存在的商户 | `GET /api/models?merchant_id=` | `200`，价格与不传时**一致**（不做个性化定价） | `test_mc12_pricing_not_per_merchant` |

### B. 额度账户 · 用户侧（`test_token_account.py`）

| ID | 前置 | 输入 | 期望 | 测试函数 |
|---|---|---|---|---|
| E-02 | 账户有余额 | `GET /api/me/quota` | `200` + `balance/reserved/available/debt/total_reimburse_in/user_daily_limit/status` | `test_e02_my_quota` |
| Q-01 | — | 无 token | `401` | `test_q01_no_token_401` |
| Q-02 | 该用户**没有**账户行 | `GET /api/me/quota` | `200`，各额均为 `0`，`status=active`（**不 404**） | `test_q02_no_row_returns_zeros` |
| Q-03 | `balance=100, reserved=30` | `GET /api/me/quota` | `available == 70`，且恒等于 `balance - reserved` | `test_q03_available_identity` |
| Q-04 | `debt=25` | `GET /api/me/quota` | `debt == 25`（欠款单独暴露，不从可用额里悄悄抵掉） | `test_q04_debt_visible` |
| Q-05 | `status=frozen` | `GET /api/me/quota` | `200` + `status=frozen`（查询不受限，写操作才受限） | `test_q05_frozen_still_readable` |

### C. 额度账户 · 商户侧（`test_token_account.py`）

| ID | 前置 | 输入 | 期望 | 测试函数 |
|---|---|---|---|---|
| E-03 | 商户已登录 | `GET /api/merchant/quota` | `200` + `balance/reserved/available/debt/today_consumed/today_reserved/daily_limit/per_user_daily_limit/per_user_task_limit/status` | `test_e03_merchant_quota` |
| C-01 | — | 无 token | `401` | `test_c01_no_token_401` |
| C-02 | 客户令牌 | `GET /api/merchant/quota` | `403` | `test_c02_customer_403` |
| C-03 | 当日有 `consume` 流水 | `GET /api/merchant/quota` | `today_consumed` == 当日 `consume` 绝对值之和 | `test_c03_today_consumed` |
| E-04 | 商户已登录 | `PATCH /api/merchant/quota/limits` `{daily_limit:1000}` | `200`，落库 | `test_e04_patch_limits` |
| L-01 | — | `daily_limit=-1` | `422` | `test_l01_negative_limit_422` |
| L-02 | 当日已耗 300 | `daily_limit=100` | `422`（不得把商户自己卡死） | `test_l02_below_today_422` |
| L-03 | 当日已耗 300 | `daily_limit=300` | `200`（等于已耗是允许的边界） | `test_l03_equal_today_ok` |
| L-04 | — | `per_user_daily_limit=null` | `200`，落库为 `NULL`（清空限额，不是设成 0） | `test_l04_null_clears_limit` |
| L-05 | — | 传 `balance` / `user_id` | `400`，且库内 `balance` 未变 | `test_l05_forbidden_field_400` |
| E-05 | 商户 `status=active` | `PATCH /api/merchant/quota/status` `{status:"frozen"}` | `200`，落库 `frozen` | `test_e05_freeze_self` |
| S-01 | — | `status="banana"` | `422` | `test_s01_bad_enum_422` |
| S-02 | 已是 `frozen` | 再传 `frozen` | `200`（幂等，spec 未要求 409） | `test_s02_freeze_idempotent` |
| E-06 | 计价表有行 | `GET /api/merchant/quota/price` | `200` + `items` 非空 | `test_e06_price_list` |
| C-04 | 客户令牌 | `GET /api/merchant/quota/price` | `403` | `test_c04_customer_price_403` |
| E-07 | `debt=25` | `GET /api/merchant/quota/debt` | `200` + `{debt:25, items}` | `test_e07_debt_detail` |
| E-08 | 有充值记录 | `GET /api/merchant/quota/recharges` | `200`，含 `amount_cents/points/channel/created_at` | `test_e08_recharges` |

### D. 流水与报表（`test_token_ledger.py`）

| ID | 前置 | 输入 | 期望 | 测试函数 |
|---|---|---|---|---|
| E-09 | 3 条流水 | `GET /api/me/quota/ledger` | `200`，按 `created_at` 倒序 | `test_e09_my_ledger` |
| E-10 | 3 条流水 | `GET /api/merchant/quota/ledger` | `200` + `{items,total,page,size}` | `test_e10_merchant_ledger` |
| LG-01 | — | `source=consume` | `200`，只含 `consume` | `test_lg01_source_filter` |
| LG-02 | — | `from > to` | `422` | `test_lg02_from_gt_to_422` |
| LG-03 | — | 跨度 366 天 | `200` | `test_lg03_span_366_ok` |
| LG-04 | — | 跨度 367 天 | `422` | `test_lg04_span_367_422` |
| LG-05 | — | `size=101` | `422` | `test_lg05_size_cap` |
| LG-06 | 跨两个账户的流水 | 商户查自己的流水 | 只含自己的（不串账户） | `test_lg06_no_cross_account` |
| E-11 | 有 `consume` 流水 | `GET /api/merchant/quota/usage?group_by=task` | `200` + `{items:[{key, points, call_count}]}` | `test_e11_usage_by_task` |
| US-01 | — | `group_by=banana` | `422` | `test_us01_bad_group_by_422` |
| US-02 | 同一批流水 | `group_by=task` 合计 vs `group_by=day` 合计 | **相等** | `test_us02_group_by_sums_equal` |
| US-03 | 商户付费的流水（`spender_id` 为该用户） | `group_by=user` | 按 `spender_id` 聚合出该用户 | `test_us03_group_by_user_uses_spender` |
| US-04 | 3 天里只有 2 天有数据 | `group_by=day` | 无数据那天补 `0`，**不跳空** | `test_us04_day_gap_zero_filled` |
| US-05 | 2 条流水 | 逐条核对 | `前一笔.balance_after + 本笔.change == 本笔.balance_after` | `test_us05_reconcile` |
| LG-07 | — | `PUT` / `DELETE` 流水 | 路由**不存在**（405 或 404，且 OpenAPI 里没有这两个方法） | `test_lg07_ledger_no_mutation_route` |
| LG-08 | 用户已注销 | `GET /api/me/quota/ledger`（用未注销的他人令牌查 admin 侧） | 流水仍在，可按 `user_id` 查到 | `test_lg08_closed_user_ledger_kept` |

### E. 管理员额度记账（`test_token_admin.py`）

| ID | 前置 | 输入 | 期望 | 测试函数 |
|---|---|---|---|---|
| E-12 | 商户账户 `balance=0` | `POST /api/admin/quota/accounts/{id}/recharge` `{amount_cents:5000, points:5000}` | `201` + `{recharge, balance:5000}` | `test_e12_recharge` |
| RC-01 | — | `points=0` | `422` | `test_rc01_points_zero_422` |
| RC-02 | — | `points=-1` | `422` | `test_rc02_points_negative_422` |
| RC-03 | — | `amount_cents=0` | `422` | `test_rc03_amount_zero_422` |
| RC-04 | — | `channel="online"` | `422`（本期只有 `manual`） | `test_rc04_channel_online_422` |
| RC-05 | 账户不存在 | `accounts/999999/recharge` | `404` | `test_rc05_unknown_account_404` |
| RC-06 | 充值成功 | 查库 | `quota_recharge` 1 条 + `quota_ledger` 1 条 `source=recharge` + `total_recharged` 增加 | `test_rc06_recharge_writes_ledger` |
| RC-07 | 充值 5000 | 查库 | `balance` 加 5000、`reserved` 不变、`balance_after` == 新余额 | `test_rc07_balance_after_correct` |
| E-13 | 有多个账户 | `GET /api/admin/quota/accounts` | `200` + `{items,total,page,size}` | `test_e13_admin_accounts` |
| AD-01 | — | `role=banana` | `422` | `test_ad01_bad_role_422` |
| AD-02 | — | `role=merchant` | `200`，只含商户 | `test_ad02_role_filter` |
| AD-03 | — | `keyword` 命中账号 | `200`，命中 | `test_ad03_keyword_filter` |
| AD-04 | 无 token | `GET /api/admin/quota/accounts` | `401` | `test_ad04_no_token_401` |
| AD-05 | 商户令牌 | `GET /api/admin/quota/accounts` | `403` | `test_ad05_merchant_403` |
| E-14 | 账户有流水 | `GET /api/admin/quota/accounts/{id}` | `200` + `{account, recent_ledger, by_day, byok_call_count}` | `test_e14_account_detail` |
| AD-06 | — | `accounts/999999` | `404` | `test_ad06_unknown_detail_404` |
| E-15 | 账户 `balance=100` | `POST .../adjust` `{points:50, remark:"线下补偿"}` | `200` + `{balance:150}` | `test_e15_adjust` |
| AD-07 | — | `points=0` | `422` | `test_ad07_adjust_zero_422` |
| AD-08 | — | `remark="短"` | `422` | `test_ad08_remark_too_short_422` |
| AD-09 | `balance=10` | `points=-50` | `422`（扣后为负） | `test_ad09_negative_after_422` |
| E-16 | 账户 `active` | `POST .../status` `{status:"frozen", reason:"风控核查"}` | `200` | `test_e16_admin_freeze` |
| AD-10 | 已是 `frozen` | 再传 `frozen` | `409`（状态相同） | `test_ad10_same_status_409` |
| AD-11 | — | `reason="短"` | `422` | `test_ad11_reason_too_short_422` |
| E-17 | 有 byok 标记的流水 | `GET /api/admin/quota/byok?from&to` | `200` + `{items:[{provider, call_count, zero_cost_count, invalid_key_count}]}` | `test_e17_byok_stats` |
| AD-12 | — | `from > to` | `422` | `test_ad12_bad_range_422` |
| E-18 | 管理员 | `POST /api/admin/quota/prices` | `201` | `test_e18_admin_create_price` |
| AD-13 | 同 `(provider,model,op,effective_from)` 已存在 | 再插 | `409` | `test_ad13_duplicate_price_409` |
| AD-14 | 商户 A / B | A 查 B 的账户详情（用 B 的 id） | `403`（不是 404） | `test_ad14_cross_merchant_403` |
| AD-15 | 管理员充值 | 查 `admin_action_log` | **各写一条**（充值 / 调整 / 冻结） | `test_ad15_admin_action_logged` |

### F. BYOK 密钥（`test_token_keys.py`）

| ID | 前置 | 输入 | 期望 | 测试函数 |
|---|---|---|---|---|
| E-19 | 用户已登录 | `POST /api/me/model-keys` `{provider:"deepseek", api_key}` | `201` + `{id, provider, key_masked, status:"active"}` | `test_e19_create_key` |
| E-20 | 已存一把 | `GET /api/me/model-keys` | `200`，item 只含 `id/provider/label/key_masked/status/last_verified_at/last_used_at` | `test_e20_list_keys` |
| E-21 | 已存一把 | `PATCH /api/me/model-keys/{id}` `{label:"备用"}` | `200` + 新 label | `test_e21_patch_key_label` |
| E-22 | 已存一把 | `DELETE /api/me/model-keys/{id}` | `204`，行已删 | `test_e22_delete_key` |
| E-23 | 已存一把，provider 校验通过 | `POST /api/me/model-keys/{id}/verify` | `200` + `{status, last_verified_at}` 非空 | `test_e23_verify_ok` |
| K-01 | — | `provider="openai"` | `422`（白名单外） | `test_k01_openai_422` |
| K-02 | — | `provider="anthropic"` | `422` | `test_k02_anthropic_422` |
| K-03 | — | 带 `base_url` | `422`（防 SSRF） | `test_k03_base_url_422` |
| K-04 | — | 带 `endpoint` | `422` | `test_k04_endpoint_422` |
| K-05 | — | 带 `url` | `422` | `test_k05_url_422` |
| K-06 | 已存 `deepseek` | 再存 `deepseek` | `409` | `test_k06_duplicate_provider_409` |
| K-07 | 用户 A / B | A 改 B 的密钥 | `403` | `test_k07_patch_others_403` |
| K-08 | 用户 A / B | A 删 B 的密钥 | `403` | `test_k08_delete_others_403` |
| K-09 | 用户 A / B | A verify B 的密钥 | `403` | `test_k09_verify_others_403` |
| K-10 | — | `PATCH /api/me/model-keys/999999` | `404` | `test_k10_unknown_key_404` |
| K-11 | — | `DELETE /api/me/model-keys/999999` | `404` | `test_k11_delete_unknown_404` |
| K-12 | provider 校验返回 401 | `POST /api/me/model-keys` | `422`，且**不入库为 active**（行不存在或 `status=invalid`） | `test_k12_verify_fail_not_active` |
| K-13 | 明文 `SECRET_KEY_SAMPLE` 已存 | 查 `user_model_key.key_cipher` | **不含**明文中段，且能解回原文 | `test_k13_cipher_no_plaintext` |
| K-14 | 同上 | `GET /api/me/model-keys` 响应体 | **不含**明文中段 | `test_k14_response_no_plaintext` |
| K-15 | 同上 | 保存响应的整段文本 | **不含**明文中段 | `test_k15_create_response_no_plaintext` |
| K-16 | 同上 | 触发一次 500 的异常栈 | 栈与日志里**不含**明文中段 | `test_k16_stack_no_plaintext` |
| K-17 | 短 key `sk-abcd1234` | 保存响应 | `key_masked` 形如 `sk-****1234`，**整串原文一次都不出现** | `test_k17_mask_shape` |
| K-18 | 密钥存在 | `PATCH` 改 `api_key` | 新密文落库、`key_masked` 更新、明文仍不入库 | `test_k18_rotate_key_secret` |
| K-19 | `fail_count=3` | 查库 | `status=disabled`（达 3 次自动停用） | `test_k19_fail_count_disables` |
| K-20 | 用户已注销 | 查 `user_model_key` | 行**物理删除**（凭据非审计数据） | `test_k20_closed_user_keys_purged` |
| K-21 | 用户被封禁 | 查 `user_model_key` | 行保留但 `status=disabled` | `test_k21_banned_user_keys_disabled` |
| K-22 | 解封用户 | 查 `user_model_key` | 恢复可用（`active`） | `test_k22_unban_restores_keys` |
| K-23 | `key_version` 落库 | 查库 | `key_version >= 1`（轮换用） | `test_k23_key_version_persisted` |
| K-24 | 仅密文（无主密钥） | 用错误主密钥解密 | 抛错，**解不出**明文 | `test_k24_wrong_master_key_fails` |
| K-25 | — | `POST /api/me/model-keys` 无 token | `401` | `test_k25_no_token_401` |

### G. 权限（`test_token_perms.py`）

| ID | 前置 | 输入 | 期望 | 测试函数 |
|---|---|---|---|---|
| P-01 | 无 token | `/api/me/quota`、`/api/me/quota/ledger`、`/api/me/model-keys` | 一律 `401` | `test_p01_me_endpoints_401` |
| P-02 | 客户令牌 | `/api/merchant/quota`、`/ledger`、`/usage`、`/price`、`/debt`、`/limits`、`/status` | 一律 `403` | `test_p02_customer_merchant_403` |
| P-03 | 商户令牌 | `/api/admin/quota/accounts`、`/byok`、`/prices` | 一律 `403` | `test_p03_merchant_admin_403` |
| P-04 | 商户 A / B | A 查 B 的额度（admin 端点） | `403`，不是 `404` | `test_p04_cross_account_403` |
| P-05 | 无 token | `/api/models` | `401` | `test_p05_models_401` |

---

## 二、第二趟（等 03/04 落地）

> 这一组现在**必须红**，红因是 `SchemaMissing` 或路由缺失。第一趟落地后
> 它们仍然是红的，属预期。

### H. 预扣与结算（`test_token_reserve.py`）

| ID | 前置 | 输入 | 期望 | 测试函数 |
|---|---|---|---|---|
| RS-01 | `available=100` | 预扣 60 的两个 job **并发** | **恰好 1 个成功**，另一个 `402` | `test_rs01_concurrent_reserve_one_wins` |
| RS-02 | `available=59` | 预扣 60 | `402`，且 `reserved` **未被改动** | `test_rs02_insufficient_no_change` |
| RS-03 | 预扣 60 | 结算 actual=45 | `balance -= 45`、`reserved -= 60`、流水 1 条 `consume(-45)` | `test_rs03_settle_less_than_reserved` |
| RS-04 | 预扣 60 | 结算 actual=60 | 不退不补，`balance -= 60` | `test_rs04_settle_equal` |
| RS-05 | 预扣 60 | 结算 actual=63 | `balance -= 60`、`debt += 3`、**`balance` 不得为负** | `test_rs05_over_actual_goes_to_debt` |
| RS-06 | 预扣 60 | release | `reserved -= 60`、`status=released`、**无 consume 流水** | `test_rs06_release_no_ledger` |
| RS-07 | 预检不过 / judge 3 次不过 | 走正常结算 | **照常扣费不退款** | `test_rs07_policy_failure_still_billed` |
| RS-08 | 同一 `job_id` | 重复投递结算 3 次 | `quota_ledger` 只多 **1 条** | `test_rs08_settle_idempotent` |
| RS-09 | 并发结算 | 查流水 | `balance_after` 递增且不重复 | `test_rs09_balance_after_monotonic` |
| RS-10 | — | 直接把 `balance` 改负 | DB `CHECK` 拒绝 | `test_rs10_negative_rejected_by_db` |
| RS-11 | 任意状态 | 查库 | `available == balance - reserved` 恒等 | `test_rs11_available_invariant` |
| RS-12 | BYOK job | 结算 | `reserved=0`、`actual=0`、**无 consume 流水** | `test_rs12_byok_zero_cost` |

### I. 付费方绑定与组合合法性（`test_token_reserve.py`）

| ID | 前置 | 输入 | 期望 | 测试函数 |
|---|---|---|---|---|
| PB-01 | `merchant_pay` 任务 | 建 job 带 `merchant_id` / `payer` / `user_id` | `422`，**不按传入值扣费** | `test_pb01_payer_in_body_422` |
| PB-02 | `merchant_pay` 任务 | 建 job 带 `billing_source=byok` | `422`（不得静默改成 platform） | `test_pb02_merchant_pay_byok_422` |
| PB-03 | `user_pay_reimburse` 任务，用户无该 provider Key | 建 job 带 `billing_source=byok` | `422` | `test_pb03_byok_without_key_422` |
| PB-04 | 商户 A / B | 用 B 的 `task_id` 建 job | 扣 **B** 的口径（付费方只看 `task_id`） | `test_pb04_payer_from_task_id` |
| PB-05 | `task_id` 不属于该用户的 claim | 建 job | `403` | `test_pb05_not_my_claim_403` |
| PB-06 | 商户 `status=frozen` | 在它的任务上建 job | `403` | `test_pb06_frozen_merchant_403` |
| PB-07 | 建 job 不传 `provider` / `billing_source` | — | 用平台默认（与 03 契约兼容） | `test_pb07_defaults_applied` |
| PB-08 | 计价表缺该 `(provider,model,op)` | 建 job | `503`，**不得按 0 计费** | `test_pb08_missing_price_503` |
| PB-09 | `provider_visible=false` 的模型 | 直接指定它建 job | `422` | `test_pb09_hidden_model_422` |
| PB-10 | 已过 `effective_from` 的旧价 | 按建 job 时刻定价 | 用**建 job 时刻**的价格 | `test_pb10_price_at_job_time` |

### J. 报销（`test_token_reimburse.py`）

| ID | 前置 | 输入 | 期望 | 测试函数 |
|---|---|---|---|---|
| RB-01 | 单用户上限 500，用户在该任务花 1000 | 审核通过 | 报 **500**，`status=capped` | `test_rb01_per_user_cap` |
| RB-02 | 池子剩 300，应报 500 | 审核通过 | 报 **300**，`status=capped` | `test_rb02_pool_cap` |
| RB-03 | 池子可用额不足预占 | 建 job | `429`（**不是等报销时才发现**） | `test_rb03_pool_exhausted_at_job` |
| RB-04 | 池子已预占，商户余额随后被花光 | 审核通过 | 已预占的报销**仍能兑现** | `test_rb04_pool_reserved_external` |
| RB-05 | 72h 自动通过 | — | 报销**自动生效**（与人工通过同一路径） | `test_rb05_auto_approve_reimburses` |
| RB-06 | admin `accept` 申诉 | — | 报销落地；`post_id` 唯一**不重复报** | `test_rb06_appeal_accept_reimburses` |
| RB-07 | 审核驳回 | — | `status=released`，池子退回，用户自担 | `test_rb07_reject_releases` |
| RB-08 | 驳回后申诉成功 | 同一 `post_id` | upsert 落地，**只报一次** | `test_rb08_reject_then_appeal_once` |
| RB-09 | 同一 job 出现在两张报销单 | — | `reimburse_claim_job.job_id` 唯一拒绝 | `test_rb09_job_once` |
| RB-10 | 报销成功 | 查两账户 | **只增平台额度**；用户 `total_reimburse_in` 与实收一致 | `test_rb10_reimburse_transfer` |
| RB-11 | 同一 post | 同时走 05 奖励与 07 报销 | 两者独立计算、独立上限，**都能拿** | `test_rb11_reward_and_reimburse_separate` |
| RB-12 | 用户注销后 | 查 `reimburse_claim` / `quota_ledger` | 保留，可按 `user_id` 查到 | `test_rb12_closed_user_claims_kept` |
| RB-13 | 商户余额不足 | 审核通过 | 不得出现「通过了但报不出钱」（预占在建 job 时已锁） | `test_rb13_no_approved_but_unpayable` |
| RB-14 | 已领取该任务 | `GET /api/me/reimburse-preview?task_id=` | `200` + `{pay_mode, per_user_limit, my_consumed_points, my_reimbursed_points, my_remaining, pool_remaining, est_points}` | `test_rb14_preview` |
| RB-15 | **未**领取该任务 | 同上 | `403` | `test_rb15_preview_not_claimed_403` |
| RB-16 | 有报销记录 | `GET /api/me/reimbursements` | `200` + `{items:[{task_id, post_id, base_points, covered_points, status, settled_at}],total}` | `test_rb16_my_reimbursements` |
| RB-17 | 有报销记录 | `GET /api/merchant/reimbursements` | `200` + `{items,total,page,size}` | `test_rb17_merchant_reimbursements` |
| RB-18 | — | `GET /api/merchant/reimbursements?status=banana` | `422` | `test_rb18_bad_status_422` |
| RB-19 | 管理员 | `GET /api/admin/quota/reimbursements` | `200`（争议处理台账） | `test_rb19_admin_reimbursements` |

### K. 限额与熔断 · 依赖建 job 的部分（`test_token_limits.py`）

| ID | 前置 | 输入 | 期望 | 测试函数 |
|---|---|---|---|---|
| LM-01 | 日限额 1000、当日已耗 900 | 预估 200 的 job | `429` | `test_lm01_daily_limit_429` |
| LM-02 | 同上 | 预估 50 的 job | `200` | `test_lm02_under_limit_ok` |
| LM-03 | `daily_limit=null` | 建 job | 用**平台默认**（配置文件），**不得视为不限** | `test_lm03_null_uses_platform_default` |
| LM-04 | `per_user_daily_limit=200`，某用户烧满 | 该用户再建 job | `429`；**其他用户不受影响** | `test_lm04_per_user_daily` |
| LM-05 | `per_user_task_limit=500`，烧满 | 该用户在该任务再建 job | `429` | `test_lm05_per_user_task` |
| LM-06 | 并发上限 3（平台配置） | 第 4 个并行 job | `429` | `test_lm06_concurrency_cap` |
| LM-07 | 触发熔断 | — | 写一条 `budget_alert`，**一天一条** | `test_lm07_budget_alert_once_per_day` |
| LM-08 | 三种限额都为 `null` | 建 job | 仅受余额约束（可成功） | `test_lm08_all_null_balance_only` |
| LM-09 | BYOK job 且用户 `balance=0` | 建 job | **能建**（不扣平台额度），但仍受商户报销池与用户日限额约束 | `test_lm09_byok_zero_balance_ok` |
| LM-10 | BYOK 调用 | 查 `gen_output` | `cost_cents=0` 且 `billing_source=byok`，**无 consume 流水** | `test_lm10_byok_gen_output_zero` |
| LM-11 | BYOK 的报销基数 | — | 按 `model_price.cost_price_per_unit` 计，与用户实际付给 provider 多少无关 | `test_lm11_byok_reimburse_base` |
| LM-12 | 06 成本看板 | BYOK 调用 | 识别为 **0 成本**，不因 `cost_cents=0` 误判为失败 | `test_lm12_cost_board_byok_zero` |
| LM-13 | BYOK 调用 | — | **仍要跑预检与 judge**（安全不因自带 Key 豁免） | `test_lm13_byok_still_guarded` |
| LM-14 | Key 失效（provider 401） | 调用 | job `failed(fail_reason=key_invalid)`、`status=invalid`、`fail_count+=1`、**绝不回落平台 Key** | `test_lm14_key_invalid_no_fallback` |

---

## 已知取舍

1. **`/api/me/quota` 与 `/api/merchant/quota` 在账户行不存在时返回全 `0`，不返回 `404`**
   （`Q-02`）。spec 的边界里没有为这两个端点规定 `404`，而 `404` 会让全新用户的前端
   在首屏就报错。实现按「懒建行 / 读不到就按 0 渲染」处理，二者对客户端等价。
2. **`GET /api/merchant/quota/ledger` 的 `size` 上限沿用 02 的 `100`**（`LG-05`）。
   07 的 spec 没写这个上限。取 100 是为与 02 的 `MAX_PAGE_SIZE` 一致，避免一个分页
   约定在系统里出现两套。
3. **「今日消耗」按 UTC 自然日**（`C-03`）。全系统的 `created_at` 都是 UTC，
   若按本地时区切日，报表与外键时间会互相打架。前端要展示本地日需自行换算。
4. **`GET /api/models?merchant_id=` 的 `merchant_id` 只校验存在性，不影响价格**
   （`MC-12`）。spec 的「明确不做」里有「不做按用户的个性化定价」，所以这个参数
   只为将来按商户过滤可选模型留位；现在传与不传价格必须一致——这条正是防它偷偷生效。
5. **`verify` 打真实 provider 的部分被替换成上面那两个接缝**（`E-23` / `K-12`）。
   真调外部 API 会让用例依赖网络与第三方可用性。测试只断言「校验通过 → `active`」
   与「校验 401 → `422` + `invalid`」两条契约；真实调用在联调时人工验一次。
6. **`K-16` 用「请求体带 `base_url` 被 `422` 拒绝」来逼出明文回显风险**。
   这是最真实的泄漏向量：若实现把 `api_key` 交给 pydantic 的 `extra="forbid"`，
   框架生成的 `422` 会把**整个请求体**放进 `detail[].input` 回给客户端，
   密钥就随错误响应泄出去了。用例断言 `422` 响应体里搜不到明文中段。
   它覆盖不了**所有**日志路径，但覆盖了最可能漏的那条。
7. **`AD-15`（`admin_action_log`）依赖 06**。06 落地前它会红，红因是表缺失。
   本计划把它放第一趟，因为「充值必须留痕」是账务底线，不该等到最后才补。
8. **`LG-07`（流水不可改）用「OpenAPI 里不存在 `PUT`/`DELETE` 方法」断言**，
   而不是扫源码。后者脆弱且会被注释误判；前者是运行时契约。
9. **`F` 组的 `provider` 白名单只测 `openai` / `anthropic` 两个反例**。
   白名单本身（`deepseek` / `dashscope` / `jimeng` / `kling`）由 `E-19` 与
   `K-06` 正向覆盖，不必逐个反例都写。
10. **`K-21` / `K-22` 断言的是行为，不是 `user_model_key.status` 的取值**。
    spec 原文写「封禁 → 密钥保留但 `status=disabled`」，但那是一次**级联写入**，
    触发点在「封禁用户」这个动作上——而封禁端点在 06。测试里只能直改 `user.status`，
    绕过了任何端点，所以断言字段值会永远红且红得没有意义。
    改为断言用户可见的保证：**封禁期间密钥不可用（`401`/`403`）且行保留；
    解封后恢复可用（`200`）**。字段级的 `disabled` 级联等 06 落地时随封禁端点一起测。
11. **`K-24` 的「换主密钥解不出」用 `key_version=999` 触发**，而不是真去轮换主密钥。
    轮换涉及重加密任务与新旧并存，属运维动作；这里只钉住「版本不匹配要抛错，
    而不是返回一段垃圾明文」这个安全性质。

## 下一步

按 `E-01 → MC-01 → … → E-02 → Q-01 → …` 顺序补**第一趟**实现，
**一个端点跑完红绿再下一个**。每补一块跑一次 `pytest -q`。
第一趟全绿后：①确认 02 的 `PM-10~12` 与 01 的 `O-02` 转绿；
②**反向验证**——故意改坏一处实现（如把 `key_cipher` 改成明文落库），
对应测试必须立刻变红。
