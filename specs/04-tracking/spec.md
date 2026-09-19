# 04 · 数据回传

> 插件采集社媒数据；插件失败时截图保底；商户审核 72h 超时自动通过。

---

## 一句话目标

> 用户发布作品后回填作品链接与社媒数据（由浏览器插件采集）；插件采不到时可用截图走视觉大模型识别保底；商户须在 72 小时内审核，超时系统自动通过并放行奖励。

---

## 漏斗（每个作品三层保障，逐级下沉）

```
L1 插件正常   → social_post(source=plugin) + metric_snapshot(source=plugin)
                ↓ 插件提取失败 / 用户没用插件
L2 截图保底   → 上传截图 → qwen3-vl 识别 → ocr_result(confidence)
                ↓ 识别置信度低 / 与链接页面数据不符
L3 商家复审   → 人工核对，72h 不审 = 自动通过
```

---

## 数据模型

### `social_post`
| 字段 | 类型 | 约束 |
|---|---|---|
| id | bigint | 主键 |
| claim_id | bigint | **必填**，外键 → task_claim |
| job_id | bigint | **必填**，外键 → content_job |
| user_id | bigint | **必填**，外键 |
| platform | enum | **必填**，`xhs` / `douyin` / `kuaishou` / `bilibili` / `shipinhao` |
| post_url | varchar(1024) | **必填**，http/https |
| post_title | varchar(512) | 可空 |
| post_body | text | 可空 |
| source | enum | **必填**，`plugin` / `screenshot` / `manual` |
| status | enum | **必填**，`pending` / `approved` / `rejected` / `auto_approved` / `appealed` |
| submitted_at | timestamptz | **必填** |
| review_deadline | timestamptz | **必填**，= `submitted_at + 72h` |
| reviewed_at | timestamptz | 可空 |
| reviewer_id | bigint | 可空 |
| reject_reason | text | 可空（rejected 时必填，>= 10 字） |
| **唯一约束** | — | `(claim_id, post_url)` 唯一 |

### `metric_snapshot`（**只追加，不覆盖**）
| 字段 | 类型 | 约束 |
|---|---|---|
| id | bigint | 主键 |
| post_id | bigint | **必填**，外键，索引 |
| likes | int | **必填**，>= 0 |
| collects | int | **必填**，>= 0 |
| comments | int | **必填**，>= 0 |
| shares | int | **必填**，>= 0 |
| engagement | int | **必填**，= likes + collects + comments（**由后端计算，不接受传入**） |
| source | enum | **必填**，`plugin` / `ocr` / `manual` |
| raw | jsonb | 可空，插件原始 payload（审计用） |
| captured_at | timestamptz | **必填** |

### `ocr_result`
| 字段 | 类型 | 约束 |
|---|---|---|
| id | bigint | 主键 |
| post_id | bigint | **必填**，外键 |
| image_url | varchar(512) | **必填** |
| raw_text | text | 可空 |
| parsed | jsonb | 可空，`{likes, collects, comments, shares}` |
| model | varchar(64) | **必填** |
| confidence | numeric(3,2) | **必填**，0.00~1.00 |
| mismatch_flag | boolean | **必填**，默认 false（与链接侧数据不一致） |
| is_active | boolean | **必填**，默认 true（多张截图时最新一张为 true） |
| created_at | timestamptz | 必填 |

### `review_log`（不可变审计）
| 字段 | 类型 | 约束 |
|---|---|---|
| id | bigint | 主键 |
| post_id | bigint | **必填**，外键 |
| action | enum | **必填**，`approve` / `reject` / `auto_approve` / `batch_approve` / `appeal_accept` / `appeal_reject` |
| operator_id | bigint | 可空（system 自动通过时为 null） |
| reason | text | 可空 |
| created_at | timestamptz | **必填**，**不得 UPDATE / DELETE** |

### `appeal`（申诉，**一个 post 最多一条**）
| 字段 | 类型 | 约束 |
|---|---|---|
| id | bigint | 主键 |
| post_id | bigint | **必填，唯一** ← 唯一约束即"仅可申诉一次" |
| user_id | bigint | **必填**，外键 |
| reason | text | **必填**，10~500 字符 |
| status | enum | **必填**，`pending` / `accepted` / `rejected` |
| admin_id | bigint | 可空，裁决人 |
| admin_note | text | 可空 |
| created_at | timestamptz | **必填** |
| decided_at | timestamptz | 可空 |

**申诉方向（已定）**：**用户**对**商户的驳回**提起申诉 → 平台（admin）裁决。商户侧无申诉入口。

---

## 端点 / 接口

| 动作 | 输入 | 成功返回 | 失败情况 |
|---|---|---|---|
| 提交作品 | `POST /api/posts`<br>`{claim_id, job_id, platform, post_url, post_title?, post_body?, source}` | `201` `{post, review_deadline}` | `409` 同 claim 同链接重复<br>`422` URL 非法或平台不匹配<br>`403` 非本人 claim<br>`409` job 未 ready<br>`422` 该 claim 已有 approved 作品 |
| 追加速据快照 | `POST /api/posts/{id}/metrics`<br>`{likes, collects, comments, shares, source, raw?}` | `201` `{snapshot}` | `422` 负数/非整数<br>`403` 非本人<br>`409` 已审核完成<br>`422` 试图传 `engagement` |
| 上传截图 | `POST /api/posts/{id}/screenshot`<br>`{image_url, mime, size_bytes}` | `202` `{ocr_result_id}` | `415` 非图片<br>`413` > 20MB<br>`403` 非本人 |
| 查识别结果 | `GET /api/ocr/{id}` | `200` `{parsed, confidence, mismatch_flag}` | `403` 非本人<br>`409` 未完成 |
| 我的作品 | `GET /api/me/posts` | `200` `{items, ...}`（含最新快照与倒计时） | `401` |
| 待审列表（商户） | `GET /api/merchant/reviews?status=pending` | `200` `{items}` 按 `review_deadline` 升序 | `403` 非商户 |
| 通过 | `POST /api/merchant/reviews/{post_id}/approve` | `200` | `409` 已审过<br>`403` 非本商户任务的作品 |
| 驳回 | `POST /api/merchant/reviews/{post_id}/reject`<br>`{reason}` | `200` | `422` reason 空或 < 10 字<br>`409` 已审过<br>`403` |
| **批量通过** | `POST /api/merchant/reviews/batch-approve`<br>`{post_ids: [int], confirm: true}` | `200`<br>`{succeeded: [int], failed: [{post_id, reason}]}` | `422` `post_ids` 空 / > 50 条<br>`422` 缺 `confirm`<br>`403` 非商户 |
| **提交申诉** | `POST /api/posts/{id}/appeal`<br>`{reason}` | `201` `{appeal}` | `422` reason 空 / < 10 字 / > 500 字<br>`409` post 非 `rejected`<br>`409` 已申诉过<br>`403` 非本人 |
| 查我的申诉 | `GET /api/me/appeals` | `200` `{items,...}` | `401` |
| 待裁决申诉（admin） | `GET /api/admin/appeals?status=pending` | `200` `{items,...}` | `403` 非 admin |
| 裁决申诉（admin） | `POST /api/admin/appeals/{id}/decide`<br>`{action, admin_note?}` | `200` | `422` action 非法<br>`409` 已裁决<br>`403` 非 admin |

**申诉状态机**
```
rejected ──用户申诉──> appealed ──admin accept──> approved（触发 05 结算 + 07 报销）
                             └───admin reject──> rejected（维持原判，不可再申诉）
```

**批量通过的行为定义**
- 逐条独立处理，**部分成功即返回 `200`**，成功与失败分列（不做全事务回滚）
- 每条成功的都必须各写一条 `review_log(action=batch_approve)` —— **不得只写一条汇总**
- 单次上限 **50 条**；前端需二次确认，后端校验 `confirm: true`

---

## 边界（每条之后会变成一条测试）

### 提交作品
- 同一个 `claim_id` + 同一个 `post_url` 提交两次 → `409`
- 同一个 `claim_id` 提交**不同** URL 两次 → **`422`**（一个领取只算一个作品，避免刷奖励）
- `post_url = "not-a-url"` → `422`
- `post_url = "javascript:alert(1)"` → `422`（防止 XSS）
- `platform=xhs` 但 URL 域名是 `douyin.com` → `422`
- `platform=xhs` 但 URL 域名是 `xiaohongshu.com` / `xhslink.com` → 通过
- 非本人的 `claim_id` → `403`
- 引用的 `job_id` 状态非 `ready` → `409`
- `source=screenshot` 但未上传截图 → 允许提交（`pending`），但**审核时需要截图，否则商户可驳回**
- 该 claim 已有 `approved` 作品再提交 → `422`

### 数据快照
- 传负数 `likes` → `422`
- 传小数 → `422`
- **传 `engagement` 字段 → `422`**（必须后端算，不接受客户端传，防止直接刷高奖励档位）
- `engagement` 计算正确性：`likes=10, collects=5, comments=3` → `engagement=18`
- 追加第 2 次快照 → 第 1 次仍在库中（不得覆盖）
- 审核完成后追加快照 → `409`
- 快照 `captured_at` 由服务端写入，**拒绝客户端传入**

### 截图识别（保底机制）
- 上传 `.txt` 改名为 `.png` → `415`（校验真实 mime）
- 截图 21MB → `413`
- 识别不出任何数字 → `422`，提示重传，**不得用 0 兜底**
- `confidence < 0.70` → `mismatch_flag` 置 true，且**前端不得显示"一键通过"**，只能人工核对
- 识别出 `likes=100`，插件快照是 `likes=350` → `mismatch_flag=true`，进人工队列
- 识别出 `likes=100`，插件快照也是 `100` → 一致，`mismatch_flag=false`
- 同一 post 上传第 2 张 → 新的 `is_active=true`，旧的重置为 false
- **识别数字降级（新值 < 旧值）→ 允许**（社媒数据可能因删帖回落），但记录告警

### 72 小时超时（**核心规则**）
- `review_deadline` 必须是 `submitted_at + 72h`，**不因任何操作而延期**
- 定时任务（每 10 分钟）扫描：`status=pending` 且 `review_deadline <= now` → 置 `auto_approved`
- **边界：`review_deadline` 恰好等于 `now` → 判定为超时（用 `<=`，不用 `<`）**
- 商户在 71h59m 审核通过 → 不触发自动通过，`review_log.action=approve`
- 商户在 72h 后审核 → `409`（已 auto_approved，不可再审）
- 自动通过必须写 `review_log(action=auto_approve, operator_id=null)`
- 自动通过后立即触发奖励结算（见 05）
- 定时任务重复跑 → 同一个 post 不得产生 2 条 `auto_approve` 日志（幂等）
- 系统停服 30 小时后再启动 → 积压的过期 post 全部被扫到并自动通过

### 商户审核
- 驳回时 `reason` 为空 → `422`；9 字 → `422`；10 字 → 通过
- 商户审核**别的商户**任务下的作品 → `403`
- 已 `approved` 的作品再 `approve` → `409`
- 已 `rejected` 的作品 `approve` → `409`（只能走申诉流程）
- 已 `appealed` 的作品 `approve` / `reject` → `409`（等 admin 裁决，商户不得介入）
- 已 `auto_approved` 的作品 `reject` → `409`（防止超时后反悔）
- 待审列表只含本商户任务的作品
- 待审列表按 `review_deadline` 升序（最急的排最前）
- 待审列表**不含** `appealed` 的作品（已升级到平台，不再压商户）

### 批量通过（一键全通过）
- `post_ids` 空数组 → `422`
- `post_ids` 51 条 → `422`；50 条 → `200`
- 缺 `confirm: true` → `422`
- `post_ids` 含重复 id → 去重后只处理一次，`succeeded` 不重复
- 全部合法 → `succeeded` 全量，`failed` 为空数组
- 混入**别的商户**的作品 → 该条进 `failed(reason=forbidden)`，**其余照常成功**
- 混入已 `approved` 的作品 → 该条进 `failed(reason=already_reviewed)`，其余成功
- 混入 `rejected` / `appealed` 的作品 → `failed(reason=invalid_status)`
- 混入不存在的 id → `failed(reason=not_found)`
- **部分失败不得整批回滚**（已成功的必须真的成功）
- 每条成功各写一条 `review_log(action=batch_approve)`：通过 3 条 → 恰好 3 条日志
- 批量通过与 72h 定时任务并发：不会对同一 post 结算两次（`reward_grant.post_id` 唯一约束兜底）
- 批量通过**不绕过**奖励计算，`engagement` 取值规则与单条通过完全一致
- 批量通过与单条 `approve` 产生的 `review_log.operator_id` 都是商户，可区分 `action`

### 申诉 / 复议
- **仅可申诉一次**：同一 post 第二次 `POST /appeal` → `409`
- 申诉 `reason` 9 字 → `422`；10 字 → 通过；501 字 → `422`
- 对 `pending` 的作品申诉 → `409`（还没被驳回，无需申诉）
- 对 `approved` / `auto_approved` 的作品申诉 → `409`
- 对 `appealed` 的作品再申诉 → `409`（同"仅一次"）
- 对**别人**的作品申诉 → `403`
- 申诉成功后 `social_post.status` → `appealed`
- 申诉期间 `POST /api/posts/{id}/metrics` 追加数据 → **允许**（用户还在涨互动，裁决时取峰值）
- admin `accept` → post `status → approved`，**触发 05 结算**，写 `review_log(action=appeal_accept)`
- admin `reject` → post `status → rejected`，写 `review_log(action=appeal_reject)`，**不可再次申诉**
- 已裁决的申诉再裁决 → `409`
- 非 admin 裁决 → `403`
- admin 裁决 `action` 传非法值 → `422`
- 商户调申诉裁决接口 → `403`
- 申诉被 `accept` 后奖励**只结算一次**（若该 post 之前已被误结算，`reward_grant` 唯一约束会挡住重复）

### 申诉入口的前端交互约束（**需 E2E 测试覆盖**）
- 用户点「申诉」后，弹窗文案必须告知**"申诉机会仅有一次，提交后不可撤销"**
- 弹窗内「确定申诉」与「取消申诉」按钮**初始为 `disabled`**
- 弹窗出现后**满 2 秒**，两个按钮才同时变为可点击
- 2 秒内点击按钮 → 无任何反应，**不发起请求**
- 点「取消申诉」→ 关闭弹窗，**不消耗申诉次数**，用户可再次进入
- 只有「确定申诉」且接口返回 `201` 才消耗次数
- 「确定申诉」接口返回 `409` → 弹窗提示"已申诉过"，并刷新作品状态
- 已申诉过的作品，「申诉」入口置灰或隐藏（不可再次进入弹窗）

### 结算取值规则（**已确认：取峰值**）
> **取「审核动作发生时，该 post 所有 `metric_snapshot` 中的 `engagement` 最大值」。**

理由：防止用户先提交低位数据快速骗过审核，审核后再慢慢涨。
影响：用户看到"我现在 1800 互动，却按 2000 档算"会困惑，**前端必须明确提示"按峰值结算"**。

- 结算值写入 `reward_grant.engagement`（审计"当时按多少分算的"）
- 快照被 admin 采纳（`ocr_result` → `metric_snapshot`）后，**峰值要重新计算**，取更晚的 `captured_at` 那条快照参与比较
- 峰值只看 `metric_snapshot`，**不看** `social_post` 上的任何字段
- 3 条快照 `engagement` 分别为 100 / 1800 / 900 → 取 **1800**
- 审核发生在某个 `captured_at` 之后 → **之后产生的快照不参与**（审核后不允许再涨档）

### 报销触发（**与 05 奖励同源同期**，详见 07-token）
> 审核通过的动作同时触发两件事：**05 奖励结算** 与 **07 报销结算**（若任务 `pay_mode=user_pay_reimburse`）。两者独立计算、独立上限，**一个失败不影响另一个**。

- 触发点有四个：单条 `approve`、`batch-approve` 中的每条、72h `auto_approve`、admin `appeal_accept`
- `user_pay_reimburse` 任务过审 → 落 `reimburse_claim`（`post_id` 唯一），商户额度 → 用户额度
- `merchant_pay` 任务过审 → **不产生报销单**
- 72h 自动通过触发的报销**必须与人工通过完全一致**（不得因是系统动作就跳过）
- 重复投递（单条通过 + 自动通过竞争）→ `reimburse_claim.post_id` 唯一 + `reward_grant.post_id` 唯一，各只生效一次
- 批量通过 3 条 `user_pay_reimburse` 作品 → 恰好 3 张报销单
- 审核驳回（`reject` / `appeal_reject`）→ 报销预占释放，池子退回，用户自担
- 驳回后申诉成功 → 报销照常落地，**只报一次**
- 报销与奖励是**两笔不同的钱**：奖励来自 `reward_rule`（可能是积分/券/现金），报销来自商户额度（只退平台额度）
- 过审时商户可用额不足以支付报销（预占已被其他操作消耗）→ **已预占的必须兑现**；预占不足的部分不报，`reimburse_claim.status=capped`

---

## 明确不做

- ❌ 不做曝光量 / 展现量采集（**四个方案都拿不到，已确认放弃**）
- ❌ 不做后端自动爬虫（平台明令禁止批量抓取）
- ❌ 不做平台官方 OAuth 授权接入（个人开发者申请不下来）
- ❌ 不做插件的服务端实现（插件是独立 Chrome MV3 项目，本 spec 只定义它调用的 API）
- ❌ 不做评论内容采集与情感分析（只采**数量**）
- ❌ 不做粉丝数 / 账号数据采集
- ❌ 不做数据可视化图表（只展示数字与倒计时，不做趋势图）
- ❌ 不做**二次申诉** / 申诉升级（仅一次，裁决即终局）
- ❌ 不做商户侧申诉（申诉方向固定为「用户 → 平台」，商户无入口）
- ❌ 不做申诉的自动裁决（必须人工，见 06-admin）
- ❌ 不做批量**驳回**（只做批量通过；驳回必须逐条写原因）
- ❌ 不做批量审核的撤销 / 反悔
- ❌ 不做自动审核（除 72h 超时这一条系统规则外）
- ❌ 不做多作品提交（一个 claim 只对应一个作品）
- ❌ 不做截图伪造检测（靠人工复核 + 72h 兜底，本期不做图像取证）
- ❌ 不做申诉时上传补充证据（只能写文字 reason）
