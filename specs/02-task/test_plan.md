# 02-task · 测试计划

> 由 `spec.md` 的「端点」与「边界」逐条转出。**当前状态：全部红**（实现尚未开始）。
> 一个 ID = 一条测试。ID 与测试函数一一对应，见「测试函数」列。
>
> 覆盖：15 个端点的正常路径 + 10 组边界 ≈ 100 条。

---

## 运行方式

```bash
cd backend
docker compose -f ../docker-compose.yml up -d     # PG:5433 / Redis:6380
.venv/Scripts/python -m pytest -q                 # 全红即为预期
```

测试用**独立库** `zhuxiaoshang_test`，每个用例前后 `TRUNCATE` 所有表。
并发用例**必须**打真 PostgreSQL：名额约束靠行锁/原子更新成立，SQLite 或内存库测不出来。

---

## 用例里反复出现的三个前置

为了不把「怎么造出一个已发布任务」抄 100 遍，helpers 提供三个工厂：

| 工厂 | 作用 |
|---|---|
| `create_task(client, token, **over)` | 建草稿，返回 `task` dict。默认标题/描述/时间窗都合法 |
| `publish_task(client, token, task_id, **over)` | 配一套合法阶梯并发布，返回 `task` |
| `make_tiers(n)` | 造 n 档**连续、无缺口、首档 min=0、末档 max=null** 的合法阶梯 |

「已发布任务」在多数用例里是**前置**，不是被测对象——它的失败会在工厂里直接炸出来，
不会伪装成被测断言的红。

---

## A. 草稿创建与修改正常路径（`test_task_draft.py`）

| ID | 前置 | 输入 | 期望 | 测试函数 |
|---|---|---|---|---|
| E-01 | 商户已登录 | `POST /api/merchant/tasks` 合法字段 | `201` + `task.id`，`status=draft` | `test_e01_create_draft` |
| E-02 | 草稿已存在 | `PATCH /api/merchant/tasks/{id}` 改 `title` | `200` + 新值落库 | `test_e02_patch_task` |
| E-03 | 草稿已存在 | `PUT /api/merchant/tasks/{id}/draft` `{title}` | `200` + `saved_at` | `test_e03_autosave` |

## B. 建任务校验（`test_task_draft.py`）

| ID | 前置 | 输入 | 期望 | 测试函数 |
|---|---|---|---|---|
| V-01 | 商户 | `title` 1 字符 | `422` | `test_v01_title_too_short` |
| V-02 | 商户 | `title` 64 字符 | **`201`**（边界内） | `test_v02_title_64_ok` |
| V-03 | 商户 | `title` 65 字符 | `422` | `test_v03_title_too_long` |
| V-04 | 商户 | `description` 9 字符 | `422` | `test_v04_description_too_short` |
| V-05 | 商户 | `description` 10 字符 | **`201`** | `test_v05_description_10_ok` |
| V-06 | 商户 | `end_at` 早于 `start_at` | `422` | `test_v06_end_before_start` |
| V-07 | 商户 | `end_at == start_at` | `422` | `test_v07_end_equals_start` |
| V-08 | 商户 | `quota=0` | `422` | `test_v08_quota_zero` |
| V-09 | 商户 | `quota=1` | **`201`** | `test_v09_quota_one_ok` |
| V-10 | 商户 | `quota=null` | **`201`**（不限名额） | `test_v10_quota_null_ok` |
| V-11 | 商户 | `start_at` 早于当前时间 1 秒 | `422` | `test_v11_start_in_past` |
| V-12 | **客户** | `POST /api/merchant/tasks` | `403` | `test_v12_customer_cannot_create` |
| V-13 | **admin** | `POST /api/merchant/tasks` | `403` | `test_v13_admin_cannot_create` |
| V-14 | 商户 | `tags` 6 个 | `422` | `test_v14_tags_too_many` |
| V-15 | 商户 | `tags` 单个 17 字符 | `422` | `test_v15_tag_too_long` |

## C. 奖励规则（`test_task_reward_rule.py`）

| ID | 前置 | 输入 | 期望 | 测试函数 |
|---|---|---|---|---|
| E-04 | 草稿 | `PUT /api/merchant/reward-rules/{task_id}` 合法阶梯 | `200` + 规则落库（`task_id` 唯一） | `test_e04_put_reward_rule` |
| E-05 | 草稿 | `POST /api/merchant/reward-rules/{task_id}/validate` | `200 {valid:true}`，**且不落库** | `test_e05_validate_does_not_persist` |
| RR-01 | 草稿 | `tiers=[]` | `422` | `test_rr01_empty_tiers` |
| RR-02 | 草稿 | 重叠 `[{0,500},{400,900},…]` | `422` | `test_rr02_overlap` |
| RR-03 | 草稿 | 缺口 `[{0,500},{600,null}]` | `422` | `test_rr03_gap` |
| RR-04 | 草稿 | 末档 `max` 非 `null` | `422` | `test_rr04_last_tier_open_ended` |
| RR-05 | 草稿 | 首档 `min != 0` | `422` | `test_rr05_first_tier_starts_at_zero` |
| RR-06 | 草稿 | 某档 `min > max` | `422` | `test_rr06_min_gt_max` |
| RR-07 | 草稿 | 负数 `min` | `422` | `test_rr07_negative_min` |
| RR-08 | 草稿 | `reward={}` | `422` | `test_rr08_empty_reward` |
| RR-09 | 草稿 | `cash` 为负数 | `422` | `test_rr09_negative_cash` |
| RR-10 | 草稿 | `cash` 为小数 | `422` | `test_rr10_fractional_cash` |
| RR-11 | 草稿 | 四档连续合法阶梯 | `200 {valid:true}` | `test_rr11_valid_four_tiers` |
| RR-12 | 商户 A 的草稿 | 商户 B 配规则 | `403` | `test_rr12_not_owner_403` |
| RR-13 | 草稿已配规则 | 再配一次（同 `task_id`） | `200`，**覆盖不新增**（`task_id` 唯一） | `test_rr13_upsert_single_row` |
| RR-14 | 草稿 | `validate` 失败时 | `422` + `detail.violations[]` 非空 | `test_rr14_violations_payload` |

## D. 发布与状态流转（`test_task_publish.py`）

| ID | 前置 | 输入 | 期望 | 测试函数 |
|---|---|---|---|---|
| E-06 | 草稿 + 合法规则 | `POST /api/merchant/tasks/{id}/publish` | `200 {status:published}` | `test_e06_publish` |
| E-07 | 已发布 | `POST .../pause` | `200`，`status=paused` | `test_e07_pause` |
| E-08 | 已暂停 | `POST .../close` | `200`，`status=closed` | `test_e08_close` |
| PB-01 | 草稿**无规则** | `publish` | `409` | `test_pb01_publish_without_rule` |
| PB-02 | 已发布 | 再 `publish` | `409` | `test_pb02_publish_twice` |
| PB-03 | 已关闭 | `publish` | `409` | `test_pb03_closed_cannot_publish` |
| PB-04 | 已暂停 | `publish` | **`200`**（允许恢复） | `test_pb04_paused_can_resume` |
| PB-05 | 已发布 | 把 `end_at` 改成过去 | `422` | `test_pb05_published_end_in_past` |
| PB-06 | 已发布且 `claimed_count=2` | `quota` 改成 1（< 已领） | `422` | `test_pb06_quota_below_claimed` |
| PB-07 | 已发布 | 改 `title` | `409`（只允许 description/end_at/requirement） | `test_pb07_published_cannot_change_title` |
| PB-08 | 已发布 | 改 `start_at` | `409` | `test_pb08_published_cannot_change_start` |
| PB-09 | 已发布 | 改 `description` | `200`（允许） | `test_pb09_published_can_change_description` |
| PB-10 | 草稿 | `end_at` 已过期后 `publish` | `422` | `test_pb10_publish_expired` |

## E. 付费模式（`test_task_pay_mode.py`）

| ID | 前置 | 输入 | 期望 | 测试函数 |
|---|---|---|---|---|
| PM-01 | 商户 | 建任务不传 `pay_mode` | `merchant_pay`（默认） | `test_pm01_default_pay_mode` |
| PM-02 | 商户 | `user_pay_reimburse` 缺 `reimburse_pool` | `422` | `test_pm02_missing_pool` |
| PM-03 | 商户 | `user_pay_reimburse` 缺 `reimburse_per_user_limit` | `422` | `test_pm03_missing_per_user_limit` |
| PM-04 | 商户 | `reimburse_per_user_limit > reimburse_pool` | `422` | `test_pm04_limit_gt_pool` |
| PM-05 | 商户 | `reimburse_per_user_limit=0` | **`201`**（纯自费） | `test_pm05_zero_per_user_limit_ok` |
| PM-06 | 商户 | `reimburse_pool=0` | **`201`**（等于不报销） | `test_pm06_zero_pool_ok` |
| PM-07 | 商户 | `merchant_pay` 却传 `reimburse_pool` | `422`（不该有的字段不接受） | `test_pm07_merchant_pay_rejects_pool` |
| PM-08 | 已发布 | 改 `pay_mode` | `409` | `test_pm08_cannot_change_pay_mode` |
| PM-09 | `user_pay_reimburse` 任务 | `GET /api/tasks/{id}` | 返回 `reimburse_pool_remaining` | `test_pm09_remaining_exposed` |
| PM-10 | 商户额度 < `reimburse_pool` | `publish` | **`402`**，且任务**不得变为 published** | `test_pm10_insufficient_quota_402` |
| PM-11 | `user_pay_reimburse` | `publish` 成功 | 商户 `quota_account.reserved` **增加** `reimburse_pool` | `test_pm11_pool_reserved_on_publish` |
| PM-12 | 已发布 `user_pay_reimburse` | `pause` | `reserved` 释放未使用部分 | `test_pm12_pause_releases_reservation` |

> **PM-10 ~ PM-12 依赖 07-token 的 `quota_account` 表**。07 未落地前这三条**必然是红的**，
> 且红的原因是 `SchemaMissing`（表不存在），不是 02 的功能缺陷。见「已知取舍」#2。

## F. 领取正常路径与并发（`test_task_claim.py`）

| ID | 前置 | 输入 | 期望 | 测试函数 |
|---|---|---|---|---|
| E-09 | 客户 + 已发布任务 | `POST /api/tasks/{id}/claim` | `201` + `claim` | `test_e09_claim_201` |
| E-10 | 客户已领取 | `GET /api/me/claims` | `200` + 1 条 | `test_e10_my_claims` |
| E-11 | 客户已领取 | `DELETE /api/me/claims/{id}` | `204` | `test_e11_abandon_204` |
| CC-01 | 同一用户 | 对同一任务领两次 | 第一次 `201`，第二次 `409` | `test_cc01_double_claim_409` |
| CC-02 | `quota=10`，已领满 10 | 第 11 人领取 | `409` | `test_cc02_quota_exhausted` |
| CC-03 | **`quota=1`** | **100 个请求并发领取** | **恰好 1 个 `201`，99 个 `409`** | `test_cc03_concurrent_quota_one` |
| CC-04 | `quota=1` | 并发领取后查库 | `claimed_count` **恰好 1** | `test_cc04_claimed_count_exact` |
| CC-05 | `quota=null` | 100 个不同用户并发领取 | 全部 `201`（不限名额） | `test_cc05_unlimited_concurrent` |
| CC-06 | 任务未开始 | 当前时间 < `start_at` | `422` | `test_cc06_before_start_422` |
| CC-07 | 任务已结束 | 当前时间 == `end_at` | `422`（闭区间，`end_at` 视为已结束） | `test_cc07_end_at_closed` |
| CC-08 | 任务 `status=paused` | 领取 | `422` | `test_cc08_paused_task_422` |
| CC-09 | **商户本人** | 领自己的任务 | `403` | `test_cc09_self_claim_403` |
| CC-10 | 未登录 | 领取 | `401` | `test_cc10_claim_without_token` |

> `CC-03` / `CC-05` 是本模块的**核心用例**：只有真行锁（`SELECT … FOR UPDATE`）
> 或原子条件更新（`UPDATE … WHERE claimed_count < quota`）才过得去。
> 应用层「先查再写」在并发下必然超额，这条会直接把它照出来。

## G. 放弃（`test_task_claim.py`）

| ID | 前置 | 输入 | 期望 | 测试函数 |
|---|---|---|---|---|
| AB-01 | 已领取 `in_progress` | 放弃 | `204`，且 `claimed_count` **-1** | `test_ab01_abandon_decrements` |
| AB-02 | 领取已 `submitted` | 放弃 | `409` | `test_ab02_submitted_cannot_abandon` |
| AB-03 | 已放弃 | 同一用户**重新领取** | `201` | `test_ab03_can_reclaim_after_abandon` |
| AB-04 | 用户 A、B | A 放弃 B 的领取 | `403` | `test_ab04_others_claim_403` |

## H. 列表、详情与搜索（`test_task_list.py`）

| ID | 前置 | 输入 | 期望 | 测试函数 |
|---|---|---|---|---|
| E-12 | 有已发布任务 | `GET /api/tasks?status=published` | `200 {items,total,page,size}` | `test_e12_list_tasks` |
| E-13 | 已发布任务 | `GET /api/tasks/{id}` | `200 {task,rule,merchant,claimed_by_me}` | `test_e13_task_detail` |
| LS-01 | draft/published/paused/closed 各一 | 列表 | **只返回 published** | `test_ls01_only_published` |
| LS-02 | 三条 `created_at` 递增 | 列表 | 按 `created_at` **倒序** | `test_ls02_order_desc` |
| LS-03 | — | `size=101` | `422` | `test_ls03_size_over_100` |
| LS-04 | — | `size=100` | `200` | `test_ls04_size_100_ok` |
| LS-05 | `title="奶茶新品"` | `keyword=奶茶` | 命中（匹配 `title`） | `test_ls05_keyword_title` |
| LS-06 | `description` 含某词 | `keyword=某词` | 命中（匹配 `description`） | `test_ls06_keyword_description` |
| LS-07 | — | 不传 `keyword` | 返回全量 | `test_ls07_no_keyword_all` |
| LS-08 | — | `keyword=""` | 不过滤，返回全量 | `test_ls08_empty_keyword` |
| LS-09 | 标题含 `%` 与 `_` 的任务各一 | `keyword=%` | **按字面量**只命中真的含 `%` 那条 | `test_ls09_keyword_literal_percent` |
| LS-10 | 上述同前 | `keyword=_` | 按字面量，**不得通配** | `test_ls10_keyword_literal_underscore` |
| LS-11 | — | `keyword=zzz`（0 命中） | `200 {items:[],total:0}`，**不是 404** | `test_ls11_no_match_empty_ok` |
| LS-12 | `title="MILK"` | `keyword=milk` | 命中（大小写不敏感） | `test_ls12_keyword_case_insensitive` |
| LS-13 | 任务 A `[奶茶,新品]`、B `[奶茶]` | `tags=奶茶,新品` | **只返回 A**（AND 语义） | `test_ls13_tags_and_semantics` |
| LS-14 | — | `tags` 6 个 | `422` | `test_ls14_tags_too_many` |
| LS-15 | — | `tags` 5 个 | `200` | `test_ls15_tags_five_ok` |
| LS-16 | — | `tags=""` | `422` | `test_ls16_tags_empty_string` |
| LS-17 | 有一条 `deleted_at` 非空的 published | 列表 | **不含**被软删的 | `test_ls17_excludes_soft_deleted` |
| LS-18 | 25 条已发布 | `page=1&size=10` 与 `page=2&size=10` | 两页**无重复** | `test_ls18_pagination_no_overlap` |
| LS-19 | 同上 | `page=2` | 排序**仍是** `created_at` 倒序 | `test_ls19_pagination_keeps_order` |

## I. 草稿自动保存（`test_task_autosave.py`）

| ID | 前置 | 输入 | 期望 | 测试函数 |
|---|---|---|---|---|
| AS-01 | 草稿有完整内容 | `PUT .../draft` 只传 `title` | `200`，**其余字段保持原值**（部分更新） | `test_as01_partial_update` |
| AS-02 | 草稿 | 相同内容提交 5 次 | 每次 `200`，`updated_at` 刷新，**只有 1 行** | `test_as02_idempotent_no_new_rows` |
| AS-03 | 已发布任务 | `PUT .../draft` | `409`（状态非 draft） | `test_as03_published_409` |
| AS-04 | 已关闭任务 | `PUT .../draft` | `409` | `test_as04_closed_409` |
| AS-05 | 草稿已有内容 | `tags` 6 个 | `422`，且**已有内容未被破坏**（事务回滚） | `test_as05_invalid_tags_rolls_back` |
| AS-06 | 草稿已有内容 | `title` 65 字符 | `422`，且已有内容未被破坏 | `test_as06_invalid_title_rolls_back` |
| AS-07 | 草稿 | 保存后立即 `GET /api/tasks/{id}`（本人） | 返回**最新值** | `test_as07_saved_value_readable` |
| AS-08 | 草稿（半成品） | 保存**不完整**任务（无规则、描述短） | `200`（**不做规则完整性校验**） | `test_as08_no_rule_check` |
| AS-09 | 商户 A、B | A 保存 B 的草稿 | `403` | `test_as09_not_owner_403` |
| AS-10 | 草稿 | 保存他人任务（非商户身份） | `403` | `test_as10_customer_cannot_autosave` |

## J. 归属与可见性（`test_task_list.py`）

| ID | 前置 | 输入 | 期望 | 测试函数 |
|---|---|---|---|---|
| MG-01 | 商户 A、B | A `PATCH` B 的任务 | `403` | `test_mg01_cross_tenant_403` |
| MG-02 | 商户本人的 `draft` | `GET /api/tasks/{id}`（本人带 token） | `200` | `test_mg02_owner_sees_own_draft` |
| MG-03 | 商户的 `draft` | 客户 `GET /api/tasks/{id}` | `404`（不是 403——不泄露存在性） | `test_mg03_customer_cannot_see_draft` |
| MG-04 | 不存在的 id | `GET /api/tasks/999999` | `404` | `test_mg04_missing_task_404` |
| MG-05 | 已发布任务 | 客户详情 | `claimed_by_me` 为 `false`；领取后为 `true` | `test_mg05_claimed_by_me_flag` |

## K. 删除与回收站（`test_task_trash.py`）

| ID | 前置 | 输入 | 期望 | 测试函数 |
|---|---|---|---|---|
| E-14 | 草稿 | `DELETE /api/merchant/tasks/{id}` | `204`，`deleted_at` 非空 | `test_e14_delete_to_trash` |
| E-15 | 有回收站任务 | `GET /api/merchant/tasks/trash` | `200` + 1 条 | `test_e15_trash_list` |
| TR-01 | 已关闭任务 | 删除 | `204` | `test_tr01_closed_can_delete` |
| TR-02 | 已发布任务 | 删除 | `409`（必须先 close） | `test_tr02_published_cannot_delete` |
| TR-03 | 已关闭但**有有效领取** | 删除 | `409` | `test_tr03_has_claim_cannot_delete` |
| TR-04 | 已进回收站 | `GET /api/tasks/{id}` | `404` | `test_tr04_deleted_detail_404` |
| TR-05 | 已进回收站 | 商户任务列表 | **不含它**（默认过滤） | `test_tr05_excluded_from_merchant_list` |
| TR-06 | 已进回收站 | `POST .../restore` | `200`，`deleted_at IS NULL` | `test_tr06_restore` |
| TR-07 | 删除前 `status=paused` | 恢复后 | `status` **仍是 paused**（删除不改 status） | `test_tr07_restore_keeps_status` |
| TR-08 | 已过期的 published 已软删 | 恢复 | `200`，但客户领取走时间窗 → `422` | `test_tr08_restored_expired_not_claimable` |
| TR-09 | 未删除的任务 | `restore` | `404`（不在回收站） | `test_tr09_restore_not_in_trash_404` |
| TR-10 | 已软删 | 再删一次 | `404` | `test_tr10_double_delete_404` |
| TR-11 | 商户 A、B | A 恢复 B 的回收站任务 | `403` | `test_tr11_restore_others_403` |
| TR-12 | 两条回收站任务（`deleted_at` 递增） | `trash` 列表 | 按 `deleted_at` **倒序** | `test_tr12_trash_order_desc` |
| TR-13 | 任务有 `reward_rule` 与 `task_claim` | 软删任务 | **两张表的行仍在**（软删只作用主表） | `test_tr13_children_rows_retained` |

---

## 已知取舍

1. **「列表/详情卡片须标注『需先垫付，过审后报销』」不入测试**——那是前端渲染职责，
   后端能保证的是 `pay_mode` 与 `reimburse_pool_remaining` 出现在响应里（`PM-09` 覆盖）。
   前端标注在 8001 的 E2E 里断言，不在此处。
2. **`PM-10` / `PM-11` / `PM-12` 依赖 07-token 的 `quota_account` 表**——07 未落地前必然红，
   红因是 `SchemaMissing`。这与 01-auth 的 C-09/O-02 同类，**不是 02 的缺陷**。
   07 落地后应自动转绿；到那时若要临时解耦，可在 02 侧先建最小的 `quota_account` 迁移。
3. **`reimburse_pool=0` 时「建 job 直接 429」不在此测**——建 job 属 03-studio，
   跨模块断言放 03 的测试里。这里只断言 `PM-06`（`pool=0` 允许建任务）。
4. **并发用例的请求数固定 100**——与 spec 一致。连接池上限（SQLAlchemy 默认
   `pool_size=5 + max_overflow=10`）会让请求分批排队，但**不影响断言的正确性**：
   无论怎么排队，`quota=1` 都只能有 1 个 `201`。排队只会让用例慢，不会让它假绿。
5. **`CC-07` 的「当前时间 == `end_at`」** 用「把 `end_at` 设成刚才那一瞬」实现，
   存在极小的时钟抖动风险；若偶发不稳，改为把 `end_at` 设为 `now() - 1ms`。
6. **`TR-13` 只断言 `task_claim` / `reward_rule` 存活**——spec 还要求「不影响审计表与
   `admin_action_log`」，但那些表归 05/06，07 未落地前无从断言，留到 05/06 补。

## 下一步

全部红之后，按 `E-01 → V-01 → … → E-04 → RR-01 → …` 顺序补实现，
**一个端点跑完红绿再下一个**。每补一块就跑一次 `pytest -q`，
确认新绿的没有把别的搞红；并**反向验证**：故意改坏一处实现，对应测试必须立刻变红。
