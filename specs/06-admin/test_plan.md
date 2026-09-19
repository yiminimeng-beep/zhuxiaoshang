# 06-admin · 测试计划

> 由 `spec.md` 的「端点」「数据模型」「边界」逐条转出。一个 ID = 一条测试，ID 与测试函数一一对应。
>
> 覆盖：**14 个端点**的正常路径 + **7 组边界** = **91 条**
> （`AC` 5 / `CS` 8 / `CM` 5 / `CD` 4 / `CP` 5 / `CB` 3 / `CX` 3 /
> `AU` 10 / `AB` 10 / `AL` 4 / `EX` 6 / `EC` 7 / `EO` 8 / `AP` 10 / `CC` 3）。
>
> 端点数的算法（照 spec 的端点表逐行点）：
> A 成本看板 6（`summary` / `by-merchant` / `by-day` / `by-provider` / `budget-alerts` /
> `merchants/{id}/detail`）+ B 用户管理 5（列表 / 详情 / `ban` / `unban` / `action-logs`）
> + C 异常处理 3（列表 / `content` resolve / `ocr` resolve）= **14**。
>
> ⚠️ **申诉的两个端点在 04，不在 06**：`GET /api/admin/appeals` 与
> `POST /api/admin/appeals/{appeal_id}/decide` 由 `04-tracking` 定义并已实现。
> 06 只**回改** `decide` 两处：补写 `admin_action_log`、给 appeal 行加锁。
> 故 `AP` 组测的是**已存在的端点**，属于「补断言」而不是「新端点」。

---

## 运行方式

```bash
cd backend
docker compose -f ../docker-compose.yml up -d     # PG:5433 / Redis:6380
.venv/Scripts/python -m pytest tests/test_admin_*.py -q -p no:randomly --no-header
```

⚠️ **同一时刻只跑一个 pytest**：所有进程共用测试库 `zhuxiaoshang_test`，
彼此 TRUNCATE 会把速度拖到 3~10 倍慢（看着像卡住）。

---

## 这个模块的测试要盯住什么

06 不产生业务数据，它只**看**别处的数据、**改**别处的状态。所以它有两条别的模块
没有的风险：

1. **看错账**。成本看板的每一个数字都是聚合出来的，聚合写错不会报错，只会
   安静地给出一份对不上的报表。故本模块有**两条一致性校验点**：
   `by-merchant` 合计 == `summary.total_cents`（`CM-02`），
   `by-day` 的逐日和 == `summary.total_cents`（`CD-01` 附带）。这两条是本计划
   里**唯一能自动发现聚合口径漂移**的用例——拿掉它们，看板可以整体偏移 30%
   而 97 条全绿。
2. **改了却不留痕**。封禁 / 解封 / 异常处理 / 申诉裁决四类动作都**必须**写
   `admin_action_log`。这类断言最容易写成「查一下刚写的行存不存在」——那样即使
   实现完全不写日志也会绿（查不到就断言查不到）。故一律写成
   `assert 行数 == 期望值`，且**在动作之前先把日志表清零**（`TRUNCATE` 由
   `conftest` 负责），使「0 行」与「没写」不可区分——这正是要的。

---

## 用例里反复出现的前置

### 新增工厂（本模块要补进 `tests/helpers.py`）

| 工厂 | 作用 |
|---|---|
| `insert_action_log(db, admin_id, action, target_type, target_id, **over)` | 直插审计日志（造「已有历史操作」的前置，以及 `AL` 组的排序断言） |
| `insert_budget_alert(db, merchant_id, **over)` | 直插熔断告警（`alert_date` 默认今天，`spend_cents` 3000 / `limit_cents` 5000） |
| `action_logs(db, *, target_type=None, target_id=None)` | 按 spec 形状查审计日志，倒序 |
| `cost_scene(db, merchant_id, user_id, **over)` | 一整套成本场景：`task` + `claim` + `job` + `gen_output`。默认产出一份 `copy`、`cost_cents=12`、`provider=deepseek` |
| `admin_users(client)` | `GET /api/admin/users` 的裸调用包装（不断言） |

### 已有工厂（复用，不重写）

| 工厂 | 作用 |
|---|---|
| `admin_token(client)` | 种子 admin（`999999`）的 access token。**所有 admin 端点用例的第一行** |
| `insert_job(db, task_id, claim_id, user_id, **over)` | 直插 `content_job`。`status="need_review"` / `"ready"` / `"failed"` 靠它摆 |
| `insert_output(db, job_id, **over)` | 直插 `gen_output`。**本轮要为它加 `created_at` 覆盖**（见下） |
| `insert_post(db, claim_id, job_id, user_id, **over)` | 直插 `social_post` |
| `insert_snapshot(db, post_id, **over)` | 直插 `metric_snapshot`（`engagement` 由三项算出） |
| `insert_appeal(db, post_id, user_id, **over)` | 直插 `appeal`（`status` 默认 `pending`） |
| `insert_review_log(db, post_id, action, **over)` | 直插 `review_log` |
| `quota_account_for(db, user_id, **over)` | 直插额度账户（05/07 已用） |
| `points_of(db, user_id)` | `SELECT coalesce(sum(change),0) FROM point_ledger`——`AU-10` 的对照 |

### ⚠️ 必须给 `insert_output` 加 `created_at` 覆盖

现有 `insert_output` 把 `created_at` **写死成 `now()`**（SQL 里直接 `now()`，
参数表里没有这一列）。而成本看板的**每一条**用例都要按 `created_at` 落在时间窗
内/外来造数——不修的话所有窗口边界用例都只能测「今天」，`from > to`、
367 天、缺失日期补 0 统统造不出来。

**这与 `quota_account_for` 吞限额列、`insert_task` 吞报销四列、`insert_claim`
吞列名是同一个坑的第四次**：helper 的 SQL 静默丢掉参数，用例看着在测，
实际测的是默认值。修法与那三次一致——**显式接收并写入**。

### 造数口径

- **金额一律用能一眼验算的整数**：`cost_cents` 默认 12，一算就知道 10 条 = 120。
- **时间窗一律相对 `now` 取整**：`today = datetime.now(timezone.utc).date()`，
  `from=today, to=today` 就是「只统计今天」。
- **`engagement` 绝不直接写进快照**——`insert_snapshot` 按 `likes+collects+comments`
  算，绕过它等于把「谁算的」验没了（`EO-01` 盯的就是这个）。

---

## A. 权限与入口 · `AC`（5 条）

| ID | 断言 |
|---|---|
| `AC-01` | 未登录打 `/api/admin/cost/summary` → `401`（不是 403，也不是 200） |
| `AC-02` | `customer` 打 14 个 admin 端点 → 一律 `403` |
| `AC-03` | `merchant` 打 14 个 admin 端点 → 一律 `403` |
| `AC-04` | 已封禁的 admin 打 admin 端点 → `403`（`deps` 对非 `active` 一律拦，这条确认它覆盖了 admin 角色） |
| `AC-05` | 冒烟：admin 打 A/B/C 三组各一个端点 → 全部 `200`（防「权限全对但端点全 404」平凡成立） |

> **`AC-02` / `AC-03` 为什么要遍历全部 14 个**：spec 明写「所有端点均要求
> `role=admin`」。只测一个的话，后面新加的端点漏挂 `_admin_auth` 不会被发现——
> 而漏挂的后果是**任意登录用户都能封禁他人**。遍历的代价是 28 次请求，值。

---

## B. 成本看板

### B1. 总览 · `CS`（8 条）· `GET /api/admin/cost/summary`

| ID | 断言 |
|---|---|
| `CS-01` | 造 3 份产出（12/12/12 分）→ `total_cents=36, gen_count=3`；`job_count` = 涉及的 job 数 |
| `CS-02` | `from > to` → `422` |
| `CS-03` | `from == to`（今天）→ `200`，只含今天产出的数据 |
| `CS-04` | 跨度 367 天 → `422`；366 天 → `200` |
| `CS-05` | 库里无任何产出 → `200`，`total_cents=0`、`gen_count=0`、数组为空，**不得 500、不得 null** |
| `CS-06` | 无数据时 `avg_cents` = `0`（**不是 NaN、不是 null**） |
| `CS-07` | `content_job.status=failed` 的 job，其产出成本**仍计入** `total_cents`（钱已经花了） |
| `CS-08` | 窗口外的产出（`created_at` 早于 `from`）**不计入** |

### B2. 按商户 · `CM`（5 条）· `GET /api/admin/cost/by-merchant`

| ID | 断言 |
|---|---|
| `CM-01` | 两商户各 2 份产出 → 各自 `total_cents` 正确，`shop_name` 取自 `merchant_profile`；`video_count` / `copy_count` 按 `gen_output.type` 分列 |
| `CM-02` | ⚠️ **一致性**：同一时间窗下 `sum(items.total_cents) == summary.total_cents` |
| `CM-03` | 商户被注销（`user.status='deleted'`）后，其历史花费**仍计入**（按 `user_id` 聚合，软删不影响） |
| `CM-04` | `size=101` → `422` |
| `CM-05` | `page=2` 与 `page=1` 的 id 集合不重叠 |

### B3. 按日趋势 · `CD`（4 条）· `GET /api/admin/cost/by-day`

| ID | 断言 |
|---|---|
| `CD-01` | 3 天窗口，只在第 1、3 天有数据 → 返回**恰好 3 条**，升序，中间那天 `total_cents=0`（缺的必须补 0，否则前端折线断） |
| `CD-02` | 逐日 `total_cents` 之和 == `summary.total_cents`（同窗口） |
| `CD-03` | 全窗口无数据 → 每一天都在，全部为 0（**不得返回空数组**） |
| `CD-04` | 跨度 367 天 → `422` |

### B4. 按模型 · `CP`（5 条）· `GET /api/admin/cost/by-provider`

| ID | 断言 |
|---|---|
| `CP-01` | 两个 provider 各 2 份产出 → 各自 `total_cents` / `gen_count` / `avg_cents` 正确 |
| `CP-02` | `success_rate` = 该 provider 成功产出数 / 总调用数 |
| `CP-03` | 总调用数为 0 → `success_rate = 0`（**不是 NaN**；NaN 会让前端渲染成 `NaN%`） |
| `CP-04` | ⚠️ **BYOK**：`billing_source='byok'` 且 `cost_cents=0` 的调用，**不得被判为失败**——`success_rate` 的分子包含它 |
| `CP-05` | BYOK 调用的成本计 0，故它**不进** `total_cents`，但**进** `gen_count` |

### B5. 熔断告警 · `CB`（3 条）· `GET /api/admin/cost/budget-alerts`

| ID | 断言 |
|---|---|
| `CB-01` | 清单返回 `{merchant_id, shop_name, alert_date, spend_cents, limit_cents}`，按 `alert_date` 倒序 |
| `CB-02` | 同商户同日重复触发 → `budget_alert` **仍只有 1 行**（唯一约束 `(merchant_id, alert_date)` 兜底） |
| `CB-03` | 窗口外的告警不计入；`from > to` → `422` |

### B6. 单商户明细 · `CX`（3 条）· `GET /api/admin/cost/merchants/{id}/detail`

| ID | 断言 |
|---|---|
| `CX-01` | 返回 `by_day[]` + `by_provider[]` + `recent_jobs[]`，三者都只含该商户的数据 |
| `CX-02` | 不存在的 id → `404` |
| `CX-03` | 传一个 `customer` 的 id → `404`（它不是商户，等价于不存在；**不得 200 返回空报表**） |

---

## C. 用户与商户管理

### C1. 列表与详情 · `AU`（10 条）

| ID | 断言 |
|---|---|
| `AU-01` | `GET /api/admin/users` → `{items,total,page,size}`，字段含 `account/email/role/status` |
| `AU-02` | `?role=merchant` 只返回商户；`?role=customer` 只返回客户 |
| `AU-03` | `?status=banned` 只返回被封禁的（含已注销的 `deleted` 也能被 `?status=deleted` 筛出） |
| `AU-04` | `keyword` 分别命中 `account` / `email` / `nickname` / `shop_name`（4 次断言） |
| `AU-05` | ⚠️ **`keyword` 里的 `%` 与 `_` 按字面量处理**：造两个用户，一个 account 是 `a_b`，一个是 `axb`，`?keyword=a_b` **只能**命中前者（`_` 不得当通配符） |
| `AU-06` | `?role=foo` → `422`；`?size=101` → `422` |
| `AU-07` | `GET /api/admin/users/{id}` → `{user, stats:{task_count, claim_count, job_count, points_balance}}` 齐全 |
| `AU-08` | 商户详情额外返回 `merchant_profile`（`shop_name` 等）；客户详情**不含**该键 |
| `AU-09` | 不存在的 id → `404` |
| `AU-10` | `stats.points_balance` == `points_of(db, uid)`（与 05 的账本一致，**不是**另算一遍） |

> **`AU-05` 为什么单独给一条**：`%` / `_` 通配符是所有 `ILIKE '%kw%'` 实现的
> 默认行为，**不转义才是自然写法**。把 `keyword` 直接拼进 `ilike(f"%{kw}%")`，
> 用户搜 `a_b` 会命中 `axb`——这在功能上像是「模糊搜索更聪明」，在安全上是
> 逃逸的种子。这条用例的第一版实现必然红，正好证明它在兜底。

### C2. 封禁与解封 · `AB`（10 条）

| ID | 断言 |
|---|---|
| `AB-01` | `POST /api/admin/users/{id}/ban {"reason": "违规内容发布"}` → `200`，`user.status='banned'` |
| `AB-02` | `reason` **4 字** → `422`（spec 明写「< 5 字」） |
| `AB-03` | `reason` **5 字** → `200`（与 `AB-02` 成对：只有下界被卡死，才说明比较写的是 `< 5` 而不是 `<= 5` 或 `< 10`） |
| `AB-04` | 重复封禁 → `409` |
| `AB-05` | ⚠️ **封禁 admin → `409`**（防管理员互封锁死系统） |
| `AB-06` | 封禁已注销用户（`status='deleted'`）→ `409`（已不可用账号，封它没有意义） |
| `AB-07` | 封禁后该用户的原 token 下次请求 → `403`（封禁必须立即生效，不等 token 过期） |
| `AB-08` | `POST .../unban` → `200`，`status` 回到 `active`，且该用户可以正常登录使用 |
| `AB-09` | 解封一个非 `banned` 的用户 → `409` |
| `AB-10` | `ban` / `unban` 不存在的 id → `404` |

### C3. 操作日志 · `AL`（4 条）· `GET /api/admin/action-logs`

| ID | 断言 |
|---|---|
| `AL-01` | `?target_type=user&target_id=X` 只返回该 target 的日志，倒序 |
| `AL-02` | 直插 3 条不同 action 的日志 → 返回 3 条，`detail` 是**对象**（`as_json` 后可直接取 `["reason"]`），不是字符串 |
| `AL-03` | `?target_type=foo` → `422` |
| `AL-04` | 非 admin → `403`；`size=101` → `422` |

---

## D. 异常处理

### D1. 列表 · `EX`（6 条）· `GET /api/admin/exceptions?type=`

| ID | 断言 |
|---|---|
| `EX-01` | `?type=foo` → `422`（四个合法值：`content_review` / `ocr_low_confidence` / `ocr_mismatch` / `appeal`） |
| `EX-02` | `?type=content_review` 只含 `content_job.status='need_review'` 的 job；`ready` / `failed` 的不出现 |
| `EX-03` | `?type=ocr_low_confidence` 只含 `confidence < 0.70` **且** `is_active=true`；`confidence=0.70`（**等于**）不出现 |
| `EX-04` | `?type=ocr_mismatch` 只含 `mismatch_flag=true` 且 `is_active=true` |
| `EX-05` | `?type=appeal` 只含 `appeal.status='pending'`；已裁决的不出现 |
| `EX-06` | `{items,total,page,size}` 齐全；`size=101` → `422` |

### D2. 内容异常处理 · `EC`（7 条）· `POST /api/admin/exceptions/content/{job_id}/resolve`

| ID | 断言 |
|---|---|
| `EC-01` | `{"action":"approve"}` → `200`，job `status='ready'`（用户可下载） |
| `EC-02` | `{"action":"discard","note":"画面不符合要求"}` → `200`，`status='failed'`，`fail_reason` **等于** `note` |
| `EC-03` | `{"action":"foo"}` → `422` |
| `EC-04` | 处理一个 `status='ready'` 的 job → `409`（不允许事后改判） |
| `EC-05` | 不存在的 job_id → `404` |
| `EC-06` | ⚠️ `approve` 与 `discard` 各写**一条** `admin_action_log(action='resolve_content')`，`target_type='content_job'`；**`note` 为空也照写** |
| `EC-07` | 重复处理同一个 job → `409`，且 `admin_action_log` **仍只有 1 条** `resolve_content`（被拒的动作不留痕） |

### D3. 截图异常处理 · `EO`（8 条）· `POST /api/admin/exceptions/ocr/{ocr_id}/resolve`

| ID | 断言 |
|---|---|
| `EO-01` | `{"action":"accept"}` → `200`，**新增一条** `metric_snapshot(source='ocr')`，且 `engagement` == `likes+collects+comments`（**由后端算**，不是照抄 ocr 的 parsed） |
| `EO-02` | `accept` 后该 post 的结算按 04 的**峰值**口径：先造一条插件快照 `engagement=500`，再 accept 一条更低的 OCR 数据 → 过审后命中的仍是 500 那档（新增快照不降反升地改写结果） |
| `EO-03` | `{"action":"reject"}` → `200`，**不新增** `metric_snapshot`，且该 `ocr_result.is_active` 被置为处理完毕 |
| `EO-04` | `accept` / `reject` 各写一条 `admin_action_log(action='resolve_ocr')`，`target_type='ocr_result'` |
| `EO-05` | 对 `is_active=false` 的 ocr 处理 → `409` |
| `EO-06` | `{"action":"foo"}` → `422`；不存在的 ocr_id → `404` |
| `EO-07` | ⚠️ 处理时该 post 已 `approved` / `auto_approved` → `409`（奖励已结算，不可回溯） |
| `EO-08` | 重复处理同一个 ocr → `409` |

> **`EO-02` 为什么值得一条**：`accept` 的直觉实现是「把 OCR 的数字写成最终值」。
> 而 04 的结算是**取快照峰值**——多一条更高或更低的历史快照，结果都可能变。
> 这条用例把「06 的 accept」与「04 的峰值口径」钉在一起，防的是两边各写各的。

---

## E. 申诉裁决 · `AP`（10 条）· 端点归 04，**06 补两处实现**

| ID | 断言 |
|---|---|
| `AP-01` | `{"action":"accept"}` → `200`，post `status='approved'` |
| `AP-02` | ⚠️ `accept` **触发 05 结算**：`reward_grant` 恰好 1 行（走 `services.review.approve` 的同一条 hook，而不是另开一条裁决专用路径） |
| `AP-03` | `{"action":"reject"}` → `200`，post `status='rejected'` |
| `AP-04` | ⚠️ **裁决同时写两张表**：`review_log` 1 条（`action='appeal_accept'` / `'appeal_reject'`）**且** `admin_action_log` 1 条（同 action，`target_type='appeal'`）。缺任一张 → 红 |
| `AP-05` | 已裁决的申诉再裁决 → `409` |
| `AP-06` | 商户调 `decide` → `403` |
| `AP-07` | `{"action":"foo"}` → `422`；不存在的 `appeal_id` → `404` |
| `AP-08` | `admin_note` 为空 → 允许（选填）；非空时落进 `appeal.admin_note` |
| `AP-09` | `reject` 后用户不可再申诉（`POST /api/posts/{id}/appeal` → `409`，`appeal.post_id` 唯一） |
| `AP-10` | `accept` 后**奖励只结算一次**：结算后再触发一次裁决/过审，`reward_grant` 仍 1 行（`post_id` 唯一约束兜底） |

> ⚠️ **`AP-04` 是本组最容易写成假绿的一条**。若写成「查 `admin_action_log` 里
> 有没有 `appeal_accept`」，实现完全不写也会因为查询返回空集而……不，空集会断言失败。
> 真正的坑在**反向**：写成 `assert len(rows) >= 0` 或 `assert rows is not None`，
> 则恒真。故一律写成 `assert len(...) == 1`，并**同时**断言两次。

---

## F. 并发 · `CC`（3 条）

| ID | 断言 |
|---|---|
| `CC-01` | 两个 admin **同时** `approve` 同一个 job → **恰好 1 个成功，另 1 个 `409`**；`admin_action_log` 只有 1 条 `resolve_content` |
| `CC-02` | 两个 admin **同时** `ban` 同一用户 → 恰好 1 个 `200`、1 个 `409`；`admin_action_log` 恰好 1 条 `ban_user`（不是 2 条，也不是 0 条） |
| `CC-03` | 两个 admin **同时**裁决同一条申诉 → 恰好 1 个 `200`、1 个 `409`；`admin_action_log` 恰好 1 条（见「反向验证」实测中订正的 ②） |

> ⚠️ **Barrier 与否，取决于打的是服务层还是端点层**。05 的 `PL-02` 打服务层纯函数，
> 裸 `gather` 下事件循环会把第一个协程一路跑到 `commit()` 再切给第二个，两个事务
> 不重叠——**去掉行锁照样绿**（实测连跑 3 次全绿），故 `PL-02` 必须用
> `asyncio.Barrier` 钉住「两边都已开事务、都还没读到目标行」那一刻。
>
> 06 的封禁 / 异常处理 / 裁决**没有可直调的服务函数**：判定与写入都在端点里，
> 中间隔着依赖注入与若干 `await`，事务从依赖注入起就已经是开的，两个请求天然重叠。
> 故 `CC-01`~`CC-03` 只用 `gather` 即可——**这不是猜的**：去掉
> `resolve_content` 的状态闸（RV-6）与去掉 appeal 的行锁（RV-7）都实测稳定复现
> `[200, 200]`，且装回后连跑 3 次全绿。
>
> 断言取**与顺序无关**的量（成功数 == 1、日志行数 == 1），不写死谁先谁后。

---

## 反向验证计划（实现完成后逐条实测）

**实测结论（2026-09-15，改坏 → 看红 → 改回，8 条全部跑过）**：

| # | 改坏处 | 应当变红 | 实测 |
|---|---|---|---|
| 1a | `by-merchant` 的聚合**漏掉窗口过滤** | `CM-02` | ✅ 红：`按商户合计 78 != 总览 66` |
| 1b | `merchant_detail` 的 provider 查询漏掉 `task.merchant_id` | `CX-01` | ✅ 红：leaked `jimeng` |
| 2 | `by-day` 改成「只返回有数据的日期」（不补 0） | `CD-01` / `CD-03` | ✅ 红：`assert 2 == 3` / `assert 0 == 3` |
| 3 | `user` 列表的 keyword 改回不转义 | `AU-05` | ✅ 红 |
| 4 | 封禁的下界改成 4 | `AB-02` | ✅ 红 |
| 5 | `audit.log_action` 直接 `return`（完全不写审计） | `AB-01` / `EC-06` / `EO-04` / `AP-04` | ✅ 4 条全红 |
| 6 | `resolve_content` 不判 `status='need_review'` | `EC-04` / `CC-01` | ✅ 红：`CC-01` 得到 `[200, 200]` |
| 7 | 申诉裁决去掉 appeal 行的 `FOR UPDATE` 锁 | `CC-03` | ✅ 红：`[200, 200]`，连跑 3 次 3 红 |
| 8 | `accept` OCR 时 `engagement` 直接取 `parsed` 里的值 | `EO-01` | ✅ 红：`NotNullViolationError: engagement` |

### ⚠️ 实测中订正的两处

**① RV-1 原来写的是「漏掉 `join content_job`」——那个改法结构上不成立。**
`content_job.task_id` 是 `NOT NULL`，每条 `gen_output` 都能一路 join 到 `task`，
所以「漏 join」既无法分组（SQL 直接报错，不是断言变红），也不会丢行。
**真正会让 `CM-02` 变红的漂移是「两边窗口不一致」**。而原来的 `CM-02`
造的数据**全落在窗口内**——漏掉窗口过滤照样算得相等，这条当时是**假兜底**。
已给 `CM-02` 补一笔窗口外的行（`_at(2)`），它才真正盯住「口径漂移」。

**② RV-7 原写「应当变红 `CC-01` / `AP-05`」——实测两条都不红，连跑 3 次全绿。**
`CC-01` 打的是内容异常处理，`AP-05` 是**顺序**的二次裁决（被 appeal 状态检查
拦住，与锁无关）。即**申诉裁决的行锁当时没有任何用例兜底**。已新增
`CC-03`（并发裁决 → 恰好 1 成功 + `admin_action_log` == 1），
去掉锁后连跑 3 次 3 红，装回后连跑 3 次 3 绿。

> `CC-03` 的文档里写明：拦住第二次的既可以是 appeal 行锁，也可以是 post 状态的
> 二次校验——**两者都行，但必须有一个在**。这条例外于本节开头「端点级并发不保证
> 复现竞态」的告诫：本用例实测稳定，不是偶发。

**③ 事后自查又补的两处（同属「断言写得比它做到的更满」）**

- **`CD-02` 的 docstring 把话说满了**：它原写「兜住『把窗口外的日期也算进来』」，
  但补 0 的循环**只吐 `[start, end]` 之内的日期**，窗口外的行被分到没被遍历的桶里
  静默丢弃，合计照样相等——**兜不住**。已把 docstring 改成明确区分「兜得住 / 兜不住」，
  并把「窗口外排除」指回 `CS-08`（从总览那一侧钉）。
- **`AB-10` 断言了两个 404，却只查了一条路由**：`ban` 有
  `assert_route_registered`，`unban` 没有——`unban` 若没注册，那行 404 会假绿。
  已两条路由各查一次（与 07 的 `LG-07`「至少扫到 2 条读路由」同类加固）。

---

## 已知取舍（写在这里，免得后人以为是漏测）

1. **不做前端**。spec 的「页面承载方式」写了 React + Vite + TS + Tailwind 后台，
   本轮**只交付后端 API**（2026-09-15 用户确认）。故 `/admin` 的 HTML 路由与
   「未登录跳登录页」两条 spec 边界**本轮不测**，等前端那一轮。
2. **`budget_alert` 的写入端点在 03**（`_raise_budget`），06 只读。故 `CB-02`
   的「一天只写一条」测的是**唯一约束**，不是 06 的代码——它已经在
   `test_studio_limits.py` 的 `LM-07` 里有对应。这里保留一条是为了让 06 的清单
   端点在「有告警」的场景下也被真正走到。
3. **`AC-02` / `AC-03` 遍历 14 个端点会写死在测试里**。端点增删需要同步改这两条。
   取舍：宁可偶尔忘改，也不要「漏挂 `_admin_auth` 而无人发现」。
4. **`CP-05` 断言 BYOK 进 `gen_count` 但不进 `total_cents`**，这是 spec
   「BYOK 的 `cost_cents=0`」的直接推论，spec 没有逐字写「不计入 total」。
5. **06 不改 07 的读数**。spec 有「成本看板 vs 07 计费是两套账」一组边界，
   其中「BYOK 调用量单独可见」由 07 的 `GET /api/admin/quota/byok` 提供
   （已实现且已有 `test_token_admin.py` 覆盖），06 不重复测。故本计划**不含**
   该端点的用例——两套账不得混用这条，靠 `CP-04` / `CP-05` 与 07 的既有用例
   从两侧分别钉住。
6. **`admin_action_log` 的「不可变」不做源码扫描**。05 的 `PL-07` 与 07 的
   `LG-07` 都用「扫源码找 UPDATE/DELETE」的方式盯审计表，这里**不重复**：
   06 是全项目唯一会写这张表的模块，且写入点只有 `_log_action` 一处。
   代价是「有人后来给审计表加了修改端点」不会被自动发现——记在此处备查。
7. **`AC-04`（封禁的 admin → 403）依赖 01 的 `deps.py`**，06 不重复实现。
   它测的是「06 的 `_admin_auth` 没有绕过 01 的状态闸」。

---

# 追加（2026-09-16）：D. 用户反馈 + E. 今日使用量

> 由 `spec.md` 末尾的「追加」段逐条转出。**4 个新端点 + 1 个聚合端点 = 57 条**
> （`FB` 19 · `FL` 15 · `FR` 11 · `DS` 12）。
> 本批**只做后端**；F 段（8002 `/admin/feedback` 收件箱页）见下面的「追加 2」。

## 这一批要盯住什么

1. **`role` 必须由服务端推导**。这是全局约定 #11（付费方服务端推导）的同款：
   客户端说「我是商家」不算数。故 `FB-04` / `FB-05`（带 `role` / `user_id` → `422`）
   与 `FB-02` / `FB-03`（不带 → 按提交者落对角色）**必须成对**——只测前者，
   「实现直接把请求体的 role 存进去」也能绿。
2. **新提交不得是已处理**。`status` / `resolved_at` / `resolved_by` 三个字段
   都得是干净的初始值（`FB-18`）。
3. **每次处理恰好留一条痕**，且**重复处理不得写第二条**（`FR-03` / `FR-06` /
   `FR-07` / `FR-10`）。写法必须是 `assert 行数 == 期望值`，不能是
   「查得到我刚写的那行」——后者在完全不写日志时也会因 `all([])` 恒真而绿。
4. **切日是北京时间（UTC+8）**。`DS-05` / `DS-08` / `DS-11` 三条从三个方向钉它：
   边界前 1 分钟、换一天、以及「UTC 和北京时间不是同一天」的那一刻。
5. **空库不得 `500` 也不得 `null`**（`DS-04`）。聚合端点最常见的坏法是
   `SUM()` 在零行时返回 `None`，直接透出去前端就渲染成 `null`。

---

## D. 用户反馈

### D1. 提交 · `FB`（19 条）· `POST /api/feedback`

| ID | 断言 |
|---|---|
| `FB-01` | 未登录 → `401` |
| `FB-02` | 商户提交 → `201`，落库 `role='merchant'` |
| `FB-03` | 客户提交 → `201`，落库 `role='customer'`（与 `FB-02` 成对：同一个端点、两种身份、两个角色） |
| `FB-04` | 请求体带 `role='admin'` → `422`，且**库里不落任何行**（不得「忽略了它但还是存了」） |
| `FB-05` | 请求体带 `user_id`（指向别人）→ `422`，且库里不落行 |
| `FB-06` | `admin` 提交 → `403`（后台不是反馈者） |
| `FB-07` | 已注销用户提交 → `403`（`deps` 对非 `active` 一律拦） |
| `FB-08` | 已封禁用户提交 → `403` |
| `FB-09` | `content` 4 字 → `422` |
| `FB-10` | `content` 5 字 → `201`（与 `FB-09` 成对，钉死下界是 5 不是 4 也不是 6） |
| `FB-11` | `content` 恰好 500 字 → `201` |
| `FB-12` | `content` 501 字 → `422`（与 `FB-11` 成对） |
| `FB-13` | `content` 全空白（`"   "`）→ `422`（按**去首尾空白后**的字数判） |
| `FB-14` | `category='foo'` → `422` |
| `FB-15` | `bug` / `suggestion` / `other` 三个合法值**各提交成功一次**（三次 `201`，落库 `category` 与传入一致） |
| `FB-16` | `contact` 省略 → `201`，落库 `contact` 为 `NULL` |
| `FB-17` | `contact` 129 字 → `422`；128 字 → `201`（成对） |
| `FB-18` | 提交成功后 `user_feedback` **恰好 1 行**，且 `status='open'`、`resolved_at` / `resolved_by` **均为 NULL** |
| `FB-19` | 响应体键**恰好**为 `{id, role, category, content, contact, created_at}`——**不含 `user_id`、不含 `status`** |

### D2. 列表 · `FL`（15 条）· `GET /api/admin/feedback`

| ID | 断言 |
|---|---|
| `FL-01` | 形状 `{items,total,page,size}` 齐全；item 含 `id / user_id / account / nickname / role / category / content / contact / status / resolved_at / resolved_by / created_at` |
| `FL-02` | 倒序：`created_at DESC, id DESC`（同一时刻造两条 → 后插的 id 在前） |
| `FL-03` | `?role=merchant` 只返回商家反馈 |
| `FL-04` | `?role=customer` 只返回用户反馈（**这是「分类」的核心断言**，用户诉求原文就是「能看到是商家反馈还是用户反馈」） |
| `FL-05` | `?status=open` 只返回未处理的 |
| `FL-06` | `?status=resolved` 只返回已处理的 |
| `FL-07` | `?category=bug` 只返回该类型 |
| `FL-08` | `?role=foo` → `422`（不是空列表） |
| `FL-09` | `?category=foo` → `422` |
| `FL-10` | `?status=foo` → `422` |
| `FL-11` | `size=101` → `422`；`size=100` → `200`（成对） |
| `FL-12` | 商户访问 → `403` |
| `FL-13` | 客户访问 → `403` |
| `FL-14` | 未登录 → `401` |
| `FL-15` | `account` / `nickname` 取自 `user` 表（提交人可追溯）；提交人被注销后该条**仍在列表里**（外键无 `CASCADE`，留痕不随人消失） |

### D3. 处理状态 · `FR`（11 条）

| ID | 断言 |
|---|---|
| `FR-01` | `resolve` 不存在的 id → `404`（开头调 `assert_route_registered("POST", "/api/admin/feedback/999999/resolve")`，否则「路由没实现」也是 404，假绿） |
| `FR-02` | `resolve` 成功 → `status='resolved'`、`resolved_at` 非空、`resolved_by` **== 该 admin 的 id**（不是 NULL、不是别人） |
| `FR-03` | 重复 `resolve` → `409`，且 `admin_action_log` **仍只有 1 条**（不得因为「又点了一次」多写一条痕） |
| `FR-04` | `reopen` 一条**本就没处理**的 → `409`（`assert_route_registered` 同上） |
| `FR-05` | `reopen` 成功 → `status='open'`，且 `resolved_at` / `resolved_by` **均置回 NULL**（不是只改 status 留俩脏字段） |
| `FR-06` | `resolve` 写**恰好 1 条** `admin_action_log`：`action='resolve_feedback'`、`target_type='feedback'`、`target_id=该反馈 id`、`admin_id=该 admin` |
| `FR-07` | `reopen` 写**恰好 1 条**：`action='reopen_feedback'`、`target_type='feedback'` |
| `FR-08` | 商户调 `resolve` → `403`；**客户**调 `resolve` → `403`（两条都要，防「只挂了商户闸」） |
| `FR-09` | 商户 / 客户调 `reopen` → `403` |
| `FR-10` | ⚠️ **并发**：两个 admin 同时 `resolve` 同一条 → 恰好 1 个 `200`、另 1 个 `409`，且 `admin_action_log` **恰好 1 条**（断言取与顺序无关的量） |
| `FR-11` | **契约扫描**：源码里没有任何 `UPDATE user_feedback SET content/role/user_id` 或 `DELETE FROM user_feedback`。⚠️ 必须先断言 4 条路由已在 `app.openapi()["paths"]` 里，否则「端点一个没注册」时这条检查平凡成立（同 05 的 `PL-07` / 07 的 `LG-07`） |

---

## E. 今日使用量

### E1. 聚合 · `DS`（12 条）· `GET /api/admin/stats/daily`

| ID | 断言 |
|---|---|
| `DS-01` | 未登录 → `401` |
| `DS-02` | 商户 → `403`；客户 → `403` |
| `DS-03` | `?date=2026-13-45` → `422`；`?date=notadate` → `422`；`?date=2026-02-30` → `422`（格式合法但日期不存在） |
| `DS-04` | 库里空无一物 → `200`，`active` / `new` / `activity` **九个数全部为 `0`**，不得 `500`、不得 `null` |
| `DS-05` | 北京时间今天 `00:00` 整的登录**计入**今天；北京时间**昨天 `23:59`** 的登录**不计入**（成对，钉死下界用 `>=` 且区间左闭） |
| `DS-06` | `role='admin'` 的登录**不计入** `active` 任何一类 |
| `DS-07` | `status='deleted'` 的用户**不计入** `active`，也**不计入** `new` |
| `DS-08` | `?date=<昨天>` → 只统计昨天：今天的登录不计入、昨天的登录计入 |
| `DS-09` | `activity` 四类各造一条当日数据 → `tasks_created` / `claims` / `posts_submitted` / `jobs_created` **各自 `+1`**（四类分别造，任一类没接上都能定位到哪一类） |
| `DS-10` | `new.merchant` / `new.customer` 按 `created_at` 落区间 + `role` 分组——今天建 1 个商户 + 2 个客户 → `{merchant:1, customer:2}` |
| `DS-11` | `date` 省略 → 取**北京时间今天**。构造「UTC 日期与北京日期不同」的那一刻（如 UTC `2026-09-15 17:00` = 北京 `2026-09-16 01:00`），断言它被算进 `2026-09-16` 而不是 `2026-09-15` |
| `DS-12` | 响应形状 `{date, active:{merchant,customer}, new:{merchant,customer}, activity:{tasks_created, claims, posts_submitted, jobs_created}}` 齐全；`date` 回显的就是查询的那一天 |

> ⚠️ `DS-05` / `DS-08` / `DS-11` 是本节唯一能发现「切日用了 UTC」的三条。
> 拿掉它们，把区间换成 UTC 整天，其余 9 条全绿。

---

## 用例里反复出现的前置（本批）

### 新增工厂（补进 `tests/helpers.py`）

| 工厂 | 作用 |
|---|---|
| `feedback_payload(**over)` | 构造提交体：`category='suggestion'`、`content='这是一条足够长的反馈内容'`（12 字） |
| `submit_feedback(client, token, **over)` | `POST /api/feedback` |
| `submit_feedback_ok(client, token, **over)` | 先断 `201` 再取 body（同 `approve_ok` / `redeem_ok` 款） |
| `insert_feedback(db, user_id, **over)` | 直插一条反馈（造「已有历史反馈」的前置与列表场景），`role` 默认从 `user.role` 推导，`created_at` 默认 `now` |
| `feedback_rows(db, **filters)` | 按 spec 形状查库，倒序，`detail` 已解析 |
| `admin_feedback(client, token, **params)` | `GET /api/admin/feedback` |
| `resolve_feedback(client, token, fid, path=None)` | `POST /api/admin/feedback/{id}/resolve` |
| `reopen_feedback(client, token, fid, path=None)` | `POST /api/admin/feedback/{id}/reopen` |
| `daily_stats(client, token, date=None)` | `GET /api/admin/stats/daily` |
| `set_last_login(db, user_id, when)` | 改 `user.last_login_at`（造 `active` 场景的唯一手段） |
| `set_created_at(db, user_id, when)` | 改 `user.created_at`（造 `new` 场景） |
| `beijing_midnight(d)` / `beijing_day_end(d)` | 由「北京某日」算出对应的 UTC 时刻，供上两个工厂使用 |

> ⚠️ `set_last_login` / `set_created_at` 是**必须**的：`active` 与 `new` 都按
> 时间区间切，而测试里的时间戳由「现在」决定——不显式指定时刻，
> 「昨天 23:59 不计入」这条**根本造不出来**。

---

## 反向验证（2026-09-16 已逐条实测）

| # | 改坏处 | 应当变红 | 实测 |
|---|---|---|---|
| 1 | 提交端点改回「存请求体里的 `role`」（`extra="forbid"` → `"ignore"`） | `FB-04` / `FB-05`（带 `role` 不再 `422`） | ✅ 两条均红：`assert 201 == 422` |
| 2 | 新提交时 `status` 默认写成 `'resolved'` | `FB-18` | ✅ 红：`assert 'resolved' == 'open'` |
| 3 | `resolve` 不判「已是 resolved」 | `FR-03`（`409` 变 `200`）、`FR-10` | ✅ 两条均红；`FR-10` 打出 `[200, 200]`——正是「无行锁 + 无闸」的双成功 |
| 4 | `reopen` 只改 `status`，不清 `resolved_at` / `resolved_by` | `FR-05` | ✅ 红：`resolved_at` 仍是时间戳、`resolved_by` 仍是 admin id |
| 5 | `resolve` / `reopen` 各去掉一次 `audit.log_action` | `FR-06` / `FR-07` | ✅ 各自红：`logs == []`、`assert 0 == 1` |
| 6 | 切日改成 UTC 整天 | `DS-05` / `DS-08` / `DS-11` | ✅ 三条均红。⚠️ **附带**：`DS-06` / `DS-07` 也跟着红——它们把「窗口内」的时刻造成北京 `00:30`，UTC 整天切法下落到窗口外。**比预期多红了 2 条**，是「更严」而非「更松」 |
| 7 | `DS` 的聚合改用 `SUM(case ...)`（零行返回 `None`） | `DS-04` | ✅ 红：`activity` 四项**全部** `None`，正是「透出 null」那条 |
| 8 | 已注销用户照算进 `active` / `new` | `DS-07` | ✅ 红：`{'merchant': 0, 'customer': 2}`（应为 `1`） |

> 八处改坏全部命中；逐条改回后 **57 条全绿**。
> ⚠️ 第 6 条的附带红说明 `DS-06` / `DS-07` **同时**是「切日」的探针——
> 它们各自主张的（角色过滤、注销排除）也仍然成立（第 8 条单独验过），
> 只是不「只」测那一件事。**已知取舍 6** 记了这一点。

---

## 已知取舍（本批）

1. **`FB-19` 断言响应键「恰好」**。spec 的端点表列了六个键，故多返回一个
   `user_id` 也算契约漂移。取舍：接口一旦被前端依赖，收窄比放宽贵，此处从紧。
2. **`FR-11` 是源码扫描，不是行为测试**。它盯的是「有人后来加了改 / 删反馈的
   端点」这一种回归，靠的是 `app/api/**/*.py` 里出现的关键字。
   ⚠️ 与 05 的 `PL-07` 同款风险：写法若把 SQL 挪到 `services/`，扫描会漏——
   故扫描范围取整个 `app/`，不只是 `app/api/`。
3. **`DS-02` 只测两个角色各一次**，不做 `AC-02` 那种「遍历全部 admin 端点」，
  因为本批只有 1 个 admin 侧的聚合端点（`FL` 那侧另有 `FL-12` / `FL-13` 两条）。
4. **`DS-09` 只断言 `+1`，不断言只统计当日**。「当日」这一维由 `DS-05` / `DS-08`
   从 `user` 那一侧钉；四类业务表逐个造「窗口外」数据会让本组翻倍，收益不划算。
5. **F 段（8002 收件箱页）本轮不测**。页面在 `08-frontend` 那一轮连同
   `06` 后台前端骨架一起做，故 `spec.md` F 段的六条边界**此处不出现**。
6. **`DS-06` / `DS-07` 不是「只测一件事」的用例**。它们把「窗口内」的登录
   时刻造成北京 `00:30`，于是**同时**被「切日口径」兜住——把区间换成
   UTC 整天，这两条也会红（实测见上表第 6 条）。
   不改成北京 `12:00` 是因为：`DS-06` / `DS-07` 的对照值要落在**同一个
   北京日**内才直观，而 `00:30` 恰好是「离下界最近、最容易暴露左闭右开写错」
   的位置。代价是它们对切日也敏感，收益是多两条边界探针。

---

# 追加 2（2026-09-16）：F. 后台反馈收件箱页 · `AF`（27 条）

> 由 `spec.md` F 段逐条转出。页面是**独立工程** `frontend-admin/`，
> 挂在 8002 的 `/admin/feedback`，与 8001 只共用 `tokens.css`。
> 测试方式同 8001：`vi.stubGlobal('fetch')` 打桩 + `MemoryRouter` 渲染真实 `App`。

## 用例清单

### 准入（4 条）

| ID | 断言 |
|---|---|
| `AF-01` | 未登录打开 `/feedback` → 落 `/login`，且 `zxs.admin.redirect` 记下 `/feedback` |
| `AF-02` | `zxs.admin.auth` 里塞的是**商家**令牌 → 也算没登录 → `/login`（准入只认 admin） |
| `AF-03` | `zxs.auth`（8001 的键）里有商家登录态 → **后台仍是未登录** → `/login` |
| `AF-04` | 有 admin 登录态 → `/feedback` 直接进，且不经过 `/login` |

### 登录页（4 条）

| ID | 断言 |
|---|---|
| `AF-05` | admin 账号登录 → 写 `zxs.admin.auth`（role=admin）并落 `/feedback` |
| `AF-06` | **商家账号**在后台登录 → 提示「该账号不是管理员账号」，**不写入登录态** |
| `AF-07` | 登录回 `401` → 显示「账号或密码错误」 |
| `AF-08` | 登录回 `403` 且带 `detail` → 显示**后端那句话**（账号已注销），不用通用文案 |

### 列表与筛选（8 条）

| ID | 断言 |
|---|---|
| `AF-09` | 首次进页面默认带 `status=open` 请求列表（spec：默认筛「未处理」） |
| `AF-10` | 列表按后端给的顺序渲染（后端按 `created_at DESC`），前端**不重排** |
| `AF-11` | 每行六件套：角色徽章 · 类型 · 内容摘要 · 提交人（账号 · 昵称）· 时间 · 状态 |
| `AF-12` | 角色徽章文案区分：商家行显示「商家」、用户行显示「用户」 |
| `AF-13` | 「未处理 N」在筛选条上可见，且取的是**全局** `status=open` 的 `total` |
| `AF-14` | 筛选条**不动它**：改「类型」后该数字不变（它是待办数，不是当前页条数） |
| `AF-15` | 改「类型」→ 重新请求且带 `category` 参数（`status=open` 仍在） |
| `AF-16` | 改「角色」→ 重新请求且带 `role` 参数 |
| `AF-17` | 空列表 → 显示「暂无反馈」，不是空白页 |

### 详情（3 条）

| ID | 断言 |
|---|---|
| `AF-18` | 点行 → `aria-expanded=true`，出现完整内容 + 联系方式 |
| `AF-19` | **长内容一个字不丢**：500 字反馈展开后详情里就是那 500 字（摘要可以截，详情不许截） |
| `AF-20` | 再点一次 → 收起（`aria-expanded=false`，详情从文档里消失） |

### 处理动作（5 条）

| ID | 断言 |
|---|---|
| `AF-21` | 未处理行的按钮是「标记已处理」；点它 → `POST …/{id}/resolve` |
| `AF-22` | 成功后该行**就地**变「已处理」且按钮变「撤销已处理」——**列表请求数不变**（不整页刷新） |
| `AF-23` | 已处理行点「撤销已处理」→ `POST …/{id}/reopen` → 就地变回「未处理」 |
| `AF-24` | `409`（已被别人处理）→ 页面级提示 + **重新拉列表**（行可能在当前筛选下消失，逐行提示会跟着没） |
| `AF-25` | 其他失败（`500`）→ **该行内**提示，行仍在（不把整页打成错误态） |

### 令牌与顶栏（3 条）

| ID | 断言 |
|---|---|
| `AF-26` | 业务请求 `401` → `POST /api/auth/refresh` 一次，成功后**重放原请求**（原路径出现 2 次） |
| `AF-27` | 顶栏：昵称 + 「管理员」徽章 + 退出登录；退出 → 调 `logout`、清空本地、落 `/login` |

## 反向验证（2026-09-16 已逐条实测）

| # | 改坏处 | 应当变红 | 实测 |
|---|---|---|---|
| 1 | `readSession` 不再校验 `role === 'admin'` | `AF-02` | ✅ 红：`expected '/feedback' to be '/login'` |
| 2 | 登录页不校验 `user.role` 就写令牌 | `AF-06` | ✅ 红：给商家令牌写了登录态并跳走，提示压根没渲染 |
| 3 | 后台与 8001 共用同一个 `localStorage` 键 | `AF-03` | ✅ 红——**但第一次跑是绿的，见下** |
| 4 | 默认筛选去掉 `status=open` | `AF-09` | ✅ 红：实际请求 `?size=100` |
| 5 | 「未处理 N」改成用当前列表的 `total` | `AF-14` | ✅ 红：筛完变成 `未处理 0` |
| 6 | 详情里对内容做 `slice(0, 200)` | `AF-19` | ✅ 红：`fb-row__body` 只有 200 字 |
| 7 | `resolve` 成功后重取整个列表 | `AF-22` | ✅ 红：重取回来的还是「未处理」那一行 |
| 8 | `409` 也走逐行提示（不升到页面级） | `AF-24` | ✅ 红：找不到 `fb-notice` |
| 9 | 把 409 和 500 都当成整页错误态 | `AF-25` | ✅ 红：`fb-row-1` 整行被错误态顶掉 |

> 九处改坏全部命中；逐条改回后 **27 条全绿**。

### ⚠️ 第 3 条：计划写错了，`AF-03` 因此被改强

第 3 条**第一次跑是绿的**——不是实现有问题，是**这条用例测不到它声称要测的东西**。

原写法往 8001 的键 `zxs.auth` 里塞**商家**登录态。可是后台读到商家令牌时，
`readSession()` 里那句 `role !== 'admin'` 本来就会把它判成没登录，于是
**「键共用」与「键分开」两种实现的屏幕表现完全一样**，用例分不出来。

改法：塞进去的换成 **admin 令牌**。此时能被拦住就只剩一个原因——后台压根没读那个键。
改完第 3 条立刻红（`expected '/login'`，实到 `/feedback`）。

> 这条是本轮的意外收获：**用例声称的因果链，比用例本身更难写对**。
> 若没做反向验证，「共用键也不会串号」这个错误的安心会一直留在计划里。

## 已知取舍（本页）

1. **不测 CSS**。jsdom 不跑 CSS，摘要的省略号是纯视觉；`AF-19` 只能钉住
   「详情区里有完整的 500 字」，钉不住「摘要在视觉上被截了」。
2. **`AF-24` / `AF-25` 断的是「提示出现在哪一层」**（页面级 vs 行内），
   不是文案。文案改一个字不该让用例红。
3. **不分页 UI**。端点 `size` 上限 100，本页只请求一页；`total` 超过
   `items.length` 时显示一句「仅显示前 N 条」。没做翻页控件——
   spec F 段没要求，而无提示的静默截断比不做更糟。
4. **时区写死北京时间（UTC+8）**。`formatCst` 不跟随运行机器的时区，
   否则同一份断言在北京与 CI 上会得到不同的字符串。与 06 的
   「今日使用量按 UTC+8 切日」同一套口径。
5. **`AF-26` 是 8001 客户端逻辑的复用验证**，不是本页新写的代码。
   保留它是因为「两套前端复制了同一个 `client.ts`」之后，
   两边最容易各自漂移，需要各自有一条兜底。
6. **`AF-03` 塞的是 admin 令牌，不是商家令牌**（见上面反向验证第 3 条）。
   现实里 8001 不会存 admin 令牌，所以这一格是**为「键是否共用」专门构造的探针**，
   不是真实场景回放。真实场景（商家登录 8001 后打开 `/admin`）由 `AF-02` 覆盖。
7. **`AF-09` 断言的是**完整 url**，不是「带没带 `status=open`」**。
   参数顺序 role → status → category → size 是实现的构造顺序，
   改一次 `URLSearchParams` 的拼装顺序就会红。取舍：**从紧**——
   收窄比放宽便宜，而且真红了正好提醒回来看这里的注释。
8. **`AF-15` / `AF-16` 只断言最后一条列表请求**（`.at(-1)`）。
   `AF-09` 已经把「改筛选前的初始请求」钉死了，这里只关心「改完发出了什么」。
9. **`FeedbackInbox.tsx` 有一条 oxlint `set-state-in-effect` 警告**
   （effect 开头 `setLoading(true)`）。这是「与外部系统（fetch）同步」的正常写法，
   依赖数组是 `[filters, reloadToken]`，不会自激循环。要消掉得把 `loading`
   改成由 render 推导的派生值，收益不抵复杂度，故留警告、不引入这层抽象。

---

# 追加 3（2026-09-16）：G. 后台另外三个页面 · `NV` / `CT` / `UM` / `EX`（54 条）

> 由 `spec.md` G0 / G1 / G2 / G3 逐条转出。后端 A / B / C 三组端点**早已 91 绿**，
> 本轮**只写页面**，接的是既有契约。测试方式与 `AF` 同：`vi.stubGlobal('fetch')`
> 打桩 + `MemoryRouter` 渲染真实 `App`，不依赖后端在线。

## 这一批要盯住什么

三条「写错了屏幕上看着也对」的：

1. **`CT-15` —— 总额带的主数字必须来自 `/summary`，不许前端拿「按商户」的
   items 自己加一遍。** 自己加的实现屏幕上数字一样（后端保证两处同源），
   但一旦分页（`size=20`）就开始少算，而且**永远不会报错**。
2. **`CT-09` / `CT-08` —— 空段不渲染。** 「没有告警」与「加载失败」在
   一张空表上长得一模一样；测试断的是**该段根本不在文档里**，不是「表是空的」。
3. **`EX-06` —— 处理成功后该行就地消失、计数 −1、列表**不重取**。
   「重取整个列表」在屏幕上看起来完全一样。

以及一条契约级的：**申诉的动作体字段是 `admin_note`，不是 `note`**
（`DecideIn(extra="forbid")`，传 `note` 会 422）。`EX-12` 钉住它。

## 用例清单

### NV. 路由与页签轨（7 条）

| ID | 断言 |
|---|---|
| `NV-01` | 未登录打开 `/cost` → 落 `/login`，且 `zxs.admin.redirect` 记下 `/cost` |
| `NV-02` | 有 admin 登录态 → `/cost` 直接进，不经过 `/login` |
| `NV-03` | 页签轨四个目的地（反馈 / 成本 / 用户 / 异常），当前项 `aria-current="page"` |
| `NV-04` | 点页签轨「用户」→ `/users`，点「异常」→ `/exceptions` |
| `NV-05` | 未知路径 `/nope` → 落 `/feedback`（三页不得改坏既有回退） |
| `NV-06` | 被拦在 `/cost` → 登录成功**回跳 `/cost`**（不是 `/feedback`） |
| `NV-07` | 被拦在 `/users/12` → 登录成功回跳 `/users/12`（深链目标也要认） |

> `NV-06` / `NV-07` 是 `session.ts` 里 `KNOWN_TARGETS` 的探针。不补进去，
> 「登录后回跳」会静默退化成「永远回 `/feedback`」。

### CT. 成本看板 `/cost`（16 条）

| ID | 断言 |
|---|---|
| `CT-01` | 首次进页 → 五段各打一次：`/summary` · `/by-provider` · `/by-merchant?size=100` · `/by-day` · `/budget-alerts` |
| `CT-02` | 主数字按分渲染：`total_cents=123456` → `¥1234.56`（÷100 只在渲染层） |
| `CT-03` | 四个次数字：生成数 / 成功 / 失败 / 均价；`avg_cents` 同样走分渲染 |
| `CT-04` | 空集（`gen_count=0`、`avg_cents=0`）→ 显示 `¥0.00` 与 `0`，整段文本**不含** `NaN` / `null` |
| `CT-05` | 按模型表每行五列：provider · 花费 · 次数 · 均价 · 成功率（`0.5` → `50%`；`0` 时是 `0%` 不是 `NaN%`） |
| `CT-06` | 按商户表每行五列：店名 · 花费 · 生成数 · 视频数 · 文案数；`shop_name=null` → 显示 `商户 #12`（不留空） |
| `CT-07` | 按日趋势：条数 **= items 长度**（后端给 3 天就画 3 根，前端不插空日期），每根有可读标签（日期 + 金额） |
| `CT-08` | 按日趋势 `items: []` → **整段不渲染**（不留空条带） |
| `CT-09` | 熔断告警 `items: []` → **整段不渲染**（空表占位会把「没有告警」读成「加载失败」） |
| `CT-10` | 熔断告警有数据 → 四列：店名 · 告警日 · 当日花费 · 当日预算 |
| `CT-11` | 点「按商户」行 → 就地展开，打 `/merchants/{id}/detail`，显示该商户的按模型与最近 job；再点收起 |
| `CT-12` | 切「近 7 天」→ 五段一起重取，且五条的 `from` / `to` **完全相同**（同一时间窗） |
| `CT-13` | 切「今天」→ `from` 与 `to` 相等 |
| `CT-14` | 区间 `422` → **就地**在区间控件上报错；其余段**保留上一次成功的数字**（总额带没被清空） |
| `CT-15` | 主数字取自 `/summary`：把 `/by-merchant` 的合计搓成另一个数 → 屏幕上仍是 `/summary` 那个 |
| `CT-16` | 切区间请求未回来时 → 总额带**仍是上一层数字** + 一条轻提示（不留白、不闪 0） |

### UM. 用户管理 `/users` + `/users/:id`（17 条）

| ID | 断言 |
|---|---|
| `UM-01` | `/users` 首次请求 `?size=100`（不带任何筛选参数） |
| `UM-02` | 结果表每行五列：账号 · 昵称 · 角色徽章 · 状态 · 店名 |
| `UM-03` | 关键词搜索 → 请求带 `keyword`，且**原样**传（前端不自己拼 `%` / `_` 通配符） |
| `UM-04` | 三个筛选控件**一起提交**：填好关键词 + 角色「商户」+ 状态「已封禁」后点「搜索」，**只发一条**请求，且同时带 `keyword` / `role=merchant` / `status=banned` |
| `UM-05` | 空结果 → 「没有匹配的用户」 |
| `UM-06` | 点行 → 路由变 `/users/{id}`，并打 `GET /api/admin/users/{id}` |
| `UM-07` | 详情：账号 / 邮箱 / 角色 / 状态 / 店名 + 4 个统计数（任务 / 领取 / 生成 / 积分余额） |
| `UM-08` | 商户详情有「商户档案」区；**客户详情没有这个区**（后端本来就不返回该键） |
| `UM-09` | 详情页操作日志 → `GET /api/admin/action-logs?target_type=user&target_id={id}&size=20`，按后端顺序渲染 |
| `UM-10` | 详情 → 返回：列表的**筛选与页码保留**（列表请求仍带原来的 `keyword`） |
| `UM-11` | 封禁理由 < 5 字 → **前端先拦**（就地提示，且一次 `POST .../ban` 都没发） |
| `UM-12` | 封禁成功 → 状态就地变「已封禁」、按钮变「解封」，并重取详情（`GET /users/{id}` 出现 2 次） |
| `UM-13` | `409` 已是 banned → 就地提示「该用户已被封禁」并重取详情刷状态 |
| `UM-14` | `409` 目标是 admin → **原样显示后端文案**（不是泛泛的「操作失败」） |
| `UM-15` | 解封非 banned → `409` → 就地提示 |
| `UM-16` | 非法 id（`/users/abc`）或后端 `404` → 「用户不存在」，不白屏 |
| `UM-17` | 解封成功 → 状态回到「正常」，并重取详情（`GET /users/{id}` 出现 2 次） |

### EX. 异常处理 `/exceptions`（14 条）

| ID | 断言 |
|---|---|
| `EX-01` | 首次进页 → 四段计数各一条 `?type=X&size=1` 请求（读 `total`，不是数当前页行数） |
| `EX-02` | 分段控件四段文案 + 各自计数：内容待审 N · 截图低置信 N · 数据不符 N · 申诉 N |
| `EX-03` | 默认段「内容待审」的列表请求是 `?type=content_review&size=100` |
| `EX-04` | 内容段列：job id · 类型（视频/文案）· 任务 id · 用户 id · 时间；动作「放行」「作废」 |
| `EX-05` | 点「放行」→ `POST /api/admin/exceptions/content/{job_id}/resolve`，体 `{action:'approve'}` |
| `EX-06` | 处理成功 → 该行**就地消失**、该段计数 −1、**列表不重取**（列表请求数不变） |
| `EX-07` | 切「截图低置信」→ 只重取该段列表；四段计数**不重取** |
| `EX-08` | 切回已取过计数的段 → 计数不闪 0（计数请求总数不增） |
| `EX-09` | `409`（已被别人处理）→ 就地提示 + 重取该段（列表与计数各 +1） |
| `EX-10` | `422`（action 非法）→ 就地提示，不静默吞 |
| `EX-11` | 空队列 → 「这一队清空了」 |
| `EX-12` | 申诉段 `reason` **全文不截断**（500 字）；动作走 `POST /api/admin/appeals/{id}/decide`，体是 `admin_note`（**不是** `note`） |
| `EX-13` | 截图段缩略图加载失败 → 占位块 + `image_url` 文本（不留破图） |
| `EX-14` | 计数与列表**同源**：控件上的数字就是 `?size=1` 的 `total`（列表只有 1 行、`total=7` 时显示 7） |

## 测试基建（本批新增的一处）

`stub-api.ts` 的 `StubResponse` 加一个可选 `pending`：命中的请求返回一个
**永不 resolve 的 Promise**。`CT-16` 要断言「请求在飞的时候屏幕上是什么」，
用即时 resolve 的桩测不出来（`await user.selectOptions(...)` 会把微任务冲干净）。

其余复用：`stubApi` 按**完整 url**（含查询串）精确匹配——本批大量用例
就是靠它区分 `/summary` 与 `/by-merchant?...`、`?type=content_review&size=1`
与 `?type=content_review&size=100`。

`render-app.tsx` 的 `LocationProbe` 从 `pathname` 改成
`pathname + search`：`UM-10` 要断言「返回后列表的筛选还在」，而筛选就存在
URL 的查询串里。既有 `AF` 断言的都是无查询串的路径，不受影响。

## 已知取舍（本批）

1. **不测 CSS**。jsdom 不跑 CSS：按日趋势的条子**高度比例**、缩略图占位块的
   尺寸都钉不住。`CT-07` 只钉「根数 = 后端给的天数、每根有可读标签」——
   比例算错会体现在图上，不会体现在 DOM 上。这是本页最大的测不到的角落，
   靠一次性人工烟测补（见下）。
2. **`CT-15` 是「不自己算」的证明**，但它证明的是**总额带那一处**没有二次聚合。
   别的地方（比如按日趋势自己求和）没有等价探针——那两处本来就不展示合计。
3. **`UM-10` 的「保留」靠浏览器历史**。列表筛选存在 URL 查询串里，返回用
   `navigate(-1)`，于是「保留」是历史栈给的，不是我们另存了一份状态。
   深链直接进详情再点「返回」（历史里没有列表那一页）会退到 `/users`——
   用例只覆盖前者。
4. **`EX-14` 与 `EX-01` 是一件事的两面**：`EX-01` 钉「发了 size=1 的请求」，
   `EX-14` 钉「屏幕上用的是那个 total 而不是当前页行数」。两条都留，
   因为「发了请求却用了行数」是一种很自然的写法。
5. **申诉的裁决端点归 04**（`/api/admin/appeals/{id}/decide`），不是
   `/api/admin/exceptions/...`。本页要按段换端点，`EX-12` 顺带钉住这一点。
6. **不做自动刷新**（spec G3 明写）。所以全部断言都建立在「操作后由我们
   自己重取」之上，没有任何 `vi.useFakeTimers` 的用例。

## 反向验证（2026-09-16 已逐条实测）

逐条改坏 → 看红 → 改回，**一次只坏一处**，每次都回到全绿（`81 passed`）再动下一处。

| # | 改坏处 | 应当变红 | 实测 |
|---|---|---|---|
| 1 | `CostDashboard` 商户段的 `&${qs}` 改回 `?${qs}`（= **本轮抓到的真 bug**） | `CT-12` | ✅ 红：五条的 `from` / `to` 全空，`expected 2 to be 1` |
| 2 | 总额带主数字改成本地累加 `/by-merchant` 的 items | `CT-15` | ✅ 红：屏幕上是搓过的假数，不是 `/summary` 那个 |
| 3 | 熔断告警 `items: []` 仍渲染空段 | `CT-09` | ✅ 红：文档里出现了本该不存在的段 |
| 4 | 切区间在飞时先清空总额带（`setSummary(null)`） | `CT-16` | ✅ 红：总额带留白，不再是上一层数字 |
| 5 | 封禁去掉前端 5 字预检，直接发请求 | `UM-11` | ✅ 红：`POST .../ban` 被发出去了（应为 0 次） |
| 6 | 详情「返回」改成 `navigate('/users')`（丢掉查询串） | `UM-10` | ✅ 红：回来时列表请求不再带原 `keyword` |
| 7 | 处理成功后重取整段列表（而非就地删行） | `EX-06` | ✅ 红：列表请求数 +1 |
| 8 | 申诉动作体字段 `admin_note` 改成 `note` | `EX-12` | ✅ 红：体里没有 `admin_note` |
| 9 | 计数改用当前页行数（`items.length`）而非 `?size=1` 的 `total` | `EX-14` | ✅ 红：`total=7` 只 1 行时显示 1 |
| 10 | 计数 effect 依赖加上当前段（切回即重取） | `EX-08` | ✅ 红：切回旧段时计数请求数增加 |
| 11 | 非法 id 仍照发请求 / 不落「用户不存在」 | `UM-16` | ✅ 红：白屏，找不到 `usr-missing` |
| 12 | 内容段 resolve 路径漏掉段名（`${EXC}/${id}/resolve`） | `EX-05` | ✅ 红：请求落到了不存在的路径 |
| 13 | `session.ts` 的 `KNOWN_PREFIXES` 去掉 `/users/` | `NV-07` | ✅ 红：深链回跳退化成 `/feedback` |

> 十三处全部命中，改回后 **54 条全绿**。第 1 条不是人为改坏——它是写实现时
> 真的踩进去的那一脚，用例在它落地前就把红灯亮了。

### 本轮一并订正的**用例侧**毛病（不是实现的问题）

| 症状 | 根因 | 处置 |
|---|---|---|
| `CT-04` `strip.getByTestId is not a function` | `await findByTestId(...)` 返回的是**元素**，不是 `within` 容器 | 套一层 `within(strip)` |
| `CT-11` 找不到 `/doubao/`（报「明细加载失败」） | 桩路径写成 `by-merchant/{id}/detail`，后端真路径是 `merchants/{id}/detail` | 抽 `detailUrl(id)` 帮手统一替换 |
| `UM-12` / `UM-13` / `UM-14` 红在前端自带的文案上 | 造数用的是 **4 字**理由（「刷单多次」「测试一下」），前端预检与后端下界都是 5 | 理由补到 5 字（差一字那一刀仍由 `UM-11` 守着） |
| `UM-04` `expected 3 to be 2` | 三个筛选控件**各自 change 即提交**，一次操作发了多条请求 | 改成整表 `FormData` 一次性提交（一个 `key` 重挂载 = 一条历史记录） |
| `tsc -b` TS6133：`unbanOk` 声明未使用 | —（没删导入） | **补 `UM-17`**（解封成功），顺手补上一个真缺口：解封此前只有 409 那条 |

