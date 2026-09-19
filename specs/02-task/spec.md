# 02 · 任务发布与领取

> 商户发布营销创作任务并配置奖励规则；客户浏览并领取任务。

---

## 一句话目标

> 商户能创建任务（含名额、时间窗、奖励阶梯）并发布；客户能看到已发布任务并成功领取，名额与时间窗约束在并发下不可被突破。

---

## 数据模型

### `task`
| 字段 | 类型 | 约束 |
|---|---|---|
| id | bigint | 主键 |
| merchant_id | bigint | **必填**，外键 → user.id（role=merchant） |
| title | varchar(64) | **必填**，2~64 字符 |
| description | text | **必填**，10~2000 字符 |
| cover_url | varchar(512) | 可空 |
| category | varchar(32) | **必填** |
| requirement | text | 可空，对内容的要求说明 |
| tags | jsonb | 可空，标签数组 `["奶茶","新品"]`；**最多 5 个，每个 1~16 字符** |
| start_at | timestamptz | **必填**，>= 创建时间 |
| end_at | timestamptz | **必填**，> start_at |
| quota | int | 可空；`null` = 不限名额；非空时 >= 1 |
| claimed_count | int | **必填**，默认 0 |
| pay_mode | enum | **必填**，默认 `merchant_pay`；`merchant_pay`（商户先付）/ `user_pay_reimburse`（用户垫付、过审后报销），见 07 |
| reimburse_pool | int | `user_pay_reimburse` 时**必填**，>= 0；报销池总额（点）。发布时须已从商户额度真实锁定 |
| reimburse_pool_used | int | **必填**，默认 0 |
| reimburse_pool_reserved | int | **必填**，默认 0 |
| reimburse_per_user_limit | int | `user_pay_reimburse` 时**必填**，>= 0；单用户报销上限（点），`0` = 纯用户自费不报销 |
| status | enum | **必填**，`draft` / `published` / `paused` / `closed` |
| deleted_at | timestamptz | 可空；**非空 = 已进回收站**。所有查询默认过滤掉 |
| created_at / updated_at | timestamptz | 必填 |

### `reward_rule`（一个任务一套，发布前必填）
| 字段 | 类型 | 约束 |
|---|---|---|
| id | bigint | 主键 |
| task_id | bigint | **必填，唯一**，外键 |
| metric | enum | **必填**，本期固定 `engagement`（= 点赞 + 收藏 + 评论） |
| tiers | jsonb | **必填**，阶梯数组，见下方结构 |
| max_reward_per_user | int | 可空，单用户在该任务的现金奖励上限（**分**） |

**`tiers` 结构**（有序、连续、不重叠）：
```json
[
  {"min": 100,  "max": 499,  "reward": {"points": 50}},
  {"min": 500,  "max": 1999, "reward": {"points": 200, "coupon_id": 12}},
  {"min": 2000, "max": null, "reward": {"cash": 2000, "points": 500}}
]
```
- `reward` 四种可选键：`cash`（分）/ `coupon_id` / `points` / `benefit`（权益说明文本）。至少一个。
- 必须是整数区间；`max=null` 表示无上限。

### `task_claim`
| 字段 | 类型 | 约束 |
|---|---|---|
| id | bigint | 主键 |
| task_id | bigint | **必填**，外键 |
| user_id | bigint | **必填**，外键 |
| claimed_at | timestamptz | **必填** |
| status | enum | **必填**，`in_progress` / `submitted` / `closed` |
| **唯一约束** | — | `(task_id, user_id)` 唯一 |

---

## 端点 / 接口

| 动作 | 输入 | 成功返回 | 失败情况 |
|---|---|---|---|
| 建草稿 | `POST /api/merchant/tasks` | `201` `{task}` | `422` 字段校验失败<br>`403` 非商户 |
| 改任务 | `PATCH /api/merchant/tasks/{id}` | `200` `{task}` | `403` 非本人<br>`404` 不存在<br>`409` 状态不允许改该字段 |
| 草稿自动保存 | `PUT /api/merchant/tasks/{id}/draft`<br>`{任意字段子集}` | `200` `{task, saved_at}` | `409` 状态非 `draft`<br>`422` 字段非法<br>`403` 非本人 |
| 配置奖励规则 | `PUT /api/merchant/reward-rules/{task_id}` | `200` `{rule}` | `422` 阶梯不连续/重叠/非法<br>`403` 非本人 |
| 校验阶梯（不落库） | `POST /api/merchant/reward-rules/{task_id}/validate` | `200` `{valid:true}` | `422` + `detail.violations[]` |
| 发布 | `POST /api/merchant/tasks/{id}/publish` | `200` `{status:"published"}` | `409` 无奖励规则<br>`422` `end_at` 已过<br>`409` 当前状态非 draft<br>**`402` `user_pay_reimburse` 但额度不足以锁定报销池**<br>**`422` `user_pay_reimburse` 缺 `reimburse_pool` 或 `reimburse_per_user_limit`**<br>**`422` `reimburse_per_user_limit > reimburse_pool`** |
| 暂停 / 关闭 | `POST /api/merchant/tasks/{id}/pause` `.../close` | `200` | `409` 状态流转非法 |
| 任务列表（客户，可搜索） | `GET /api/tasks?status=published&keyword&tags&page&size` | `200` `{items,total,page,size}` | `422` size > 100<br>`422` `tags` 超 5 个 |
| 任务详情 | `GET /api/tasks/{id}` | `200` `{task, rule, merchant, claimed_by_me}` | `404` 不存在或非 published（本人除外） |
| 领取 | `POST /api/tasks/{id}/claim` | `201` `{claim}` | `409` 已领取<br>`409` 名额已满<br>`422` 不在时间窗内<br>`403` 领自己的任务 |
| 我的任务 | `GET /api/me/claims?status=` | `200` `{items,...}` | `401` |
| 放弃 | `DELETE /api/me/claims/{id}` | `204` | `409` 已提交不可放弃<br>`403` 非本人 |
| 删除（进回收站） | `DELETE /api/merchant/tasks/{id}` | `204` | `409` `status=published`<br>`409` 已有有效领取<br>`403` 非本人 |
| 回收站列表 | `GET /api/merchant/tasks/trash` | `200` `{items,...}` | `401` |
| 回收站恢复 | `POST /api/merchant/tasks/{id}/restore` | `200` `{task}` | `404` 不在回收站<br>`403` 非本人 |

---

## 边界（每条之后会变成一条测试）

### 校验
- `title` 1 字符 / 65 字符 → `422`；64 字符 → 通过
- `description` 9 字符 → `422`；10 字符 → 通过
- `end_at` 早于 `start_at` → `422`；等于 → `422`
- `quota` = 0 → `422`；= 1 → 通过；= null → 通过
- `start_at` 早于当前时间 1 秒 → `422`
- 客户调 `POST /api/merchant/tasks` → `403`

### 阶梯校验（`validate` 必须逐条挡住）
- 空数组 `[]` → `422`
- 阶梯重叠：`[{0,500},{400,900}]` → `422`
- 阶梯有缺口：`[{0,500},{600,null}]`（501~599 无覆盖）→ `422`
- 最后一档 `max` 不是 `null` → `422`（否则高互动用户无奖励）
- 第一档 `min` 不是 `0` → `422`
- `min > max` → `422`
- 负数 `min` → `422`
- `reward` 为空对象 `{}` → `422`
- `cash` 为负数 / 小数 → `422`
- 合法四档连续阶梯 → `200 {valid:true}`

### 发布与状态流转
- `draft` 无奖励规则直接发布 → `409`
- `published` 任务再发布 → `409`
- `closed` → `published` → `409`
- `paused` → `published` → `200`（允许恢复）
- `published` 时改 `end_at` 为过去时间 → `422`
- `published` 时把 `quota` 改成小于 `claimed_count` → `422`
- `published` 任务改 `title` → `409`（只允许改 `description` / `end_at` / `requirement`）
- 已发布任务的 `start_at` 不可改 → `409`

### 付费模式（详见 07-token）
- 不传 `pay_mode` → 默认 `merchant_pay`
- `pay_mode=user_pay_reimburse` 缺 `reimburse_pool` → `422`
- `pay_mode=user_pay_reimburse` 缺 `reimburse_per_user_limit` → `422`
- `reimburse_per_user_limit > reimburse_pool` → `422`（单用户上限不可能超过池子）
- `reimburse_per_user_limit=0` → 允许（纯用户自费，商户不报销）
- `reimburse_pool=0` → 允许（等于不报销），但建 job 时直接 `429`
- 发布 `user_pay_reimburse` 任务时商户可用额 < `reimburse_pool` → `402`，**任务不得发布**
- 发布成功 → 商户 `quota_account.reserved` 增加 `reimburse_pool`（**池子是真金白银锁住的，不是空头承诺**）
- `merchant_pay` 任务传 `reimburse_pool` → `422`（不该有的字段不接受）
- 已发布任务改 `pay_mode` → `409`（不可中途改，已有 job 的计费口径会错乱）
- `paused` / `closed` 任务释放未使用的报销池预占 → 商户 `reserved` 相应减少
- 任务详情返回 `reimburse_pool_remaining`（剩余可报销额），**客户可见**（不给他看就是让他盲赌）
- 任务列表 / 详情卡片上须标注「需先垫付，过审后报销」

### 领取（并发是重点）
- 同一用户对同一任务领取两次 → 第一次 `201`，第二次 `409`
- `quota=10` 且已领 10 人，第 11 人领取 → `409`
- **`quota=1` 且 100 个请求并发领取 → 恰好 1 个 `201`，99 个 `409`** ⚠️ 必须用行锁或原子条件更新
- 领取成功后 `claimed_count` 恰好 +1（并发下不得多增）
- `quota=null` → 不限人数，100 个并发全部 `201`
- 当前时间 < `start_at` → `422`
- 当前时间 == `end_at` → `422`（边界取闭区间，`end_at` 视为已结束）
- 任务 `status=paused` 时领取 → `422`
- 商户领取自己发的任务 → `403`
- 未登录领取 → `401`

### 归属与可见性
- 任务列表只返回 `status=published` 的任务，不含 `draft` / `paused` / `closed`
- 列表按 `created_at` 倒序
- `size=101` → `422`
- 商户 A 改商户 B 的任务 → `403`
- 客户访问 `GET /api/tasks/{draft任务id}` → `404`
- 商户本人访问自己的 `draft` 任务详情 → `200`

### 放弃
- 放弃 `in_progress` 的领取 → `204`，且 `claimed_count` -1
- 放弃 `submitted` 的领取 → `409`
- 放弃后同一用户可**重新领取** → `201`
- 放弃他人领取记录 → `403`

### 搜索
- `keyword` 匹配 `title` 与 `description`
- `keyword` 为空 / 不传 → 不过滤，返回全量
- `keyword` 含 `%` `_` → **按字面量处理**，不得通配匹配（注入防护）
- `keyword` 匹配 0 条 → `200` `{items:[], total:0}`，**不得 404**
- `keyword` 英文大小写不敏感（`MILK` 与 `milk` 结果一致）
- `tags=奶茶,新品` → 返回**同时含这两个标签**的任务（AND 语义）
- `tags` 传 6 个 → `422`；5 个 → `200`
- `tags` 传空串 `""` → `422`
- 搜索结果只含 `status=published` 且 `deleted_at IS NULL` 的任务
- 搜索 + 分页：第 2 页与第 1 页无重复
- **搜索不改变排序**，固定 `created_at` 倒序

### 草稿自动保存
- 只传 `title` → `200`，其余字段**保持原值**（部分更新语义，不得清空未传字段）
- 相同内容重复提交 5 次 → 每次 `200`，`updated_at` 刷新，**不产生 5 条记录**
- 对 `status=published` / `closed` 的任务调 → `409`
- 传 `tags` 6 个 → `422`，且**已有内容不得被破坏**（事务回滚）
- 传 `title` 65 字符 → `422`，同上不得破坏已有内容
- 保存后立即 `GET` 详情 → 返回最新值
- 草稿自动保存**不做奖励规则完整性校验**（允许半成品）
- 保存他人任务 → `403`

### 删除与回收站
- 删除 `draft` 任务 → `204`，`deleted_at` 非空
- 删除 `close` 后的任务 → `204`
- 删除 `published` 任务 → `409`（必须先 close）
- 删除**已有有效领取**的任务 → `409`（即使已 close）
- 删除后 `GET /api/tasks/{id}` → `404`
- 删除后出现在 `GET /api/merchant/tasks/trash`
- 删除后**商户自己的任务列表不再含它**（默认过滤 `deleted_at`）
- 恢复 → `deleted_at` 置 null，任务回到原 `status`（**删除动作不改 status**）
- 恢复一个 `end_at` 已过期的 `published` 任务 → `200`，但客户端仍不可领取（走时间窗校验）
- 恢复不在回收站的任务 → `404`
- 重复删除同一任务 → `404`
- 恢复他人的回收站任务 → `403`
- 回收站列表按 `deleted_at` 倒序
- **软删只作用于 `task` 主表**：`task_claim` / `reward_rule` 记录保留不删
- 软删**不影响**审计表与 `admin_action_log`

---

## 明确不做

- ❌ 不做任务模板 / 复制任务
- ❌ 不做排序选项（搜索已做，但排序固定 `created_at` 倒序，不给用户选）
- ❌ 不做任务推荐 / 个性化排序
- ❌ 不做分享裂变 / 邀请码
- ❌ 不做回收站的**彻底删除**与自动清理（软删记录永久保留，只能恢复、不能销毁）
- ❌ 不做标签云 / 热门标签统计（标签仅用于筛选）
- ❌ 不做搜索历史 / 搜索建议 / 拼音搜索
- ❌ 不做多商户联合任务
- ❌ 不做 `metric` 的其他类型（播放量、转化数本期不做，固定 `engagement`）
- ❌ 不做领取后的自动提醒 / 催办通知
