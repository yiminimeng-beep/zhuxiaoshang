# 05 · 奖励与积分商城

> 按商户预设阶梯结算奖励（现金/优惠券/积分/权益）；积分可在商户商城兑换。

---

## 一句话目标

> 作品审核通过（含 72h 超时自动通过）后，系统按该商户任务配置的阶梯规则一次性发放奖励，全过程幂等、可审计；用户积累的积分能在商户上架的商城商品中兑换。

---

## 结算触发器

**唯一入口**：`social_post.status` 变为 `approved` 或 `auto_approved` 时，投递结算事件到队列。

```
post approved / auto_approved
  ↓ 取 engagement = 该 post 所有 metric_snapshot 中的最大值（**取峰值，已确认**，见 04）
  ↓ 匹配 reward_rule.tiers 命中档位
  ↓ 计算 reward（cash / coupon / points / benefit）
  ↓ 应用 max_reward_per_user 上限截断
  ↓ 落 reward_grant（post_id 唯一，幂等）
  ↓ 发放：写 point_ledger / 发 user_coupon / 记 cash_payout
```

---

## 数据模型

### `reward_grant`
| 字段 | 类型 | 约束 |
|---|---|---|
| id | bigint | 主键 |
| post_id | bigint | **必填，唯一**（幂等的核心） |
| claim_id | bigint | **必填**，外键 |
| user_id | bigint | **必填**，外键 |
| task_id | bigint | **必填**，外键 |
| reward_rule_id | bigint | **必填**，外键 |
| engagement | int | **必填**，结算时所用的值（快照） |
| tier_index | int | 可空（未命中断为 null） |
| reward_detail | jsonb | **必填**，`{cash?, coupon_ids?, points?, benefit?}` |
| status | enum | **必填**，`granted` / `below_threshold` / `capped` / `failed` |
| fail_reason | text | 可空 |
| granted_at | timestamptz | 可空 |

### `coupon`（商户发布的券模板）
| 字段 | 类型 | 约束 |
|---|---|---|
| id | bigint | 主键 |
| merchant_id | bigint | **必填**，外键 |
| name | varchar(64) | **必填** |
| type | enum | **必填**，`cash_off`（满减）/ `discount`（折扣）/ `gift`（兑换券） |
| value | int | **必填**，满减=减多少分；折扣=折扣百分比（如 85 表示 8.5 折） |
| min_amount | int | 可空，门槛金额（分） |
| total | int | **必填**，发行总量 |
| issued | int | **必填**，默认 0 |
| valid_from / valid_to | timestamptz | **必填** |
| status | enum | **必填**，`active` / `inactive` |

### `user_coupon`
| 字段 | 类型 | 约束 |
|---|---|---|
| id | bigint | 主键 |
| user_id | bigint | **必填**，外键 |
| coupon_id | bigint | **必填**，外键 |
| code | varchar(32) | **必填，唯一** |
| status | enum | **必填**，`unused` / `used` / `expired` |
| obtained_at | timestamptz | **必填** |
| expire_at | timestamptz | **必填**，= coupon.valid_to |
| used_at | timestamptz | 可空 |

### `point_ledger`（**不可变，只追加**）
| 字段 | 类型 | 约束 |
|---|---|---|
| id | bigint | 主键 |
| user_id | bigint | **必填**，外键，索引 |
| change | int | **必填**，正负整数 |
| balance_after | int | **必填**，>= 0 |
| source | enum | **必填**，`task_reward` / `redemption` / `refund` / `adjust` |
| ref_type | varchar(32) | **必填**，如 `reward_grant` / `redemption` |
| ref_id | bigint | **必填** |
| remark | varchar(256) | 可空 |
| created_at | timestamptz | **必填**，**不得 UPDATE / DELETE** |
| **唯一约束** | — | `(source, ref_type, ref_id)` 唯一（防重复入账） |

### `mall_item`
| 字段 | 类型 | 约束 |
|---|---|---|
| id | bigint | 主键 |
| merchant_id | bigint | **必填**，外键 |
| name | varchar(64) | **必填** |
| cover_url | varchar(512) | 可空 |
| description | text | 可空 |
| points_cost | int | **必填**，>= 1 |
| stock | int | **必填**，>= 0 |
| sold | int | **必填**，默认 0 |
| per_user_limit | int | 可空；`null` = 不限 |
| status | enum | **必填**，`on` / `off` |

### `redemption`
| 字段 | 类型 | 约束 |
|---|---|---|
| id | bigint | 主键 |
| user_id | bigint | **必填**，外键 |
| mall_item_id | bigint | **必填**，外键 |
| points_spent | int | **必填** |
| quantity | int | **必填**，>= 1 |
| status | enum | **必填**，`pending` / `confirmed` / `cancelled` |
| redeem_code | varchar(32) | **必填，唯一** |
| address | jsonb | 可空 |
| created_at | timestamptz | **必填** |

### `cash_payout`（**本期只记账，不打款**）
| 字段 | 类型 | 约束 |
|---|---|---|
| id | bigint | 主键 |
| user_id | bigint | **必填**，外键 |
| reward_grant_id | bigint | **必填，唯一**，外键 |
| amount | int | **必填**，分 |
| status | enum | **必填**，默认 `pending`；`pending` / `paid` |
| paid_at | timestamptz | 可空 |
| operator_id | bigint | 可空 |

---

## 端点 / 接口

| 动作 | 输入 | 成功返回 | 失败情况 |
|---|---|---|---|
| 查奖励规则 | `GET /api/merchant/reward-rules/{task_id}` | `200` `{rule}` | `403` 非本人 |
| 我的奖励 | `GET /api/me/rewards` | `200` `{items,...}` | `401` |
| 我的积分 | `GET /api/me/points` | `200` `{balance}` | `401` |
| 积分流水 | `GET /api/me/points/ledger` | `200` `{items,...}` | `401` |
| 我的券 | `GET /api/me/coupons?status=` | `200` `{items,...}` | `401` |
| 商城列表 | `GET /api/mall/items?merchant_id=` | `200` `{items,...}` | `404` 商户不存在 |
| 商城详情 | `GET /api/mall/items/{id}` | `200` `{item}` | `404` 或已下架（非上架返回 404） |
| 兑换 | `POST /api/mall/items/{id}/redeem`<br>`{quantity, address?}` | `201` `{redemption, redeem_code, points_balance}` | `409` 库存不足<br>`409` 超 `per_user_limit`<br>`422` 积分不足<br>`404` 商品已下架<br>`422` quantity <= 0 或 > 99 |
| 我的兑换 | `GET /api/me/redemptions` | `200` `{items,...}` | `401` |
| 商户上架商品 | `POST /api/merchant/mall/items` | `201` | `403` 非商户 |
| 商户改商品 | `PATCH /api/merchant/mall/items/{id}` | `200` | `403` 非本人<br>`422` `stock` 改成小于 `sold` |
| 商户建券 | `POST /api/merchant/coupons` | `201` `{coupon}` | `403` 非商户<br>`422` `valid_to < valid_from`、`total <= 0`、类型非法 |
| 商户改券 | `PATCH /api/merchant/coupons/{id}` | `200` `{coupon}` | `403` 非本人（不是 404）<br>`422` `valid_to < valid_from`<br>`422` `total` 改成小于 `issued` |
| 确认兑换（商户） | `POST /api/merchant/redemptions/{id}/confirm` | `200` | `403` 非本人商品<br>`409` 状态非 pending |
| 取消兑换（商户） | `POST /api/merchant/redemptions/{id}/cancel` | `200` **并原路返还积分** | `403`<br>`409` 已 confirmed |

---

## 边界（每条之后会变成一条测试）

### 阶梯匹配与结算
- `engagement=0` 且第一档 `min=0` → 命中第 0 档，按档发奖
- `engagement` 落在两档之间不应存在（02 的阶梯校验已强制连续），但**若历史数据出现缺口 → `status=below_threshold`，不发奖，记日志，不得报 500**
- `engagement=2000` 且第三档 `{min:2000, max:null}` → 命中第 2 档（`tier_index=2`）
- 同一 post 重复投递结算事件 3 次 → `reward_grant` 只产生 **1 条**（`post_id` 唯一约束 + upsert），积分只加 1 次
- 结算时 `engagement` 必须落库到 `reward_grant.engagement`（审计"当时按多少分算的"）
- 商户中途改阶梯 → **已结算的 post 不变**（用结算时快照），未结算的用新规则
- `reward.cash` 与 `reward.points` 同时存在 → 两个都发
- `reward.coupon_id` 指向的券 `total` 已发完 → 该项发放失败，`reward_detail` 记录实际发成功的部分，`status=granted`，**不得整体回滚**（积分和现金照发）
- 券模板已 `inactive` → 不发券，其余照发，记日志

### 现金上限
- `max_reward_per_user=10000`（100 元），单用户在该任务累计已发 8000 分，本次应发 3000 → **实发 2000**，`status=capped`，`reward_detail.cash=2000`
- `max_reward_per_user=null` → 不截断
- 截断后 `cash_payout.amount` 必须等于实发额，不是应发额
- 上限判断必须基于**该用户在该任务**的累计，不是该 post

### 积分账本
- 发放积分后 `point_ledger.balance_after` 必须与 `GET /api/me/points` 返回值一致
- 并发发放两笔积分（+100 / +50）→ `balance_after` 必须为 100 和 150（**行锁串行，不得出现两个 100**）
- 同一 `(source, ref_type, ref_id)` 重复入账 → 唯一约束拒绝，**不得产生第二行**
- `balance_after` 不得为负数

### 兑换（并发是重点）
- `stock=1`，100 个请求并发兑换 → **恰好 1 个 `201`，99 个 `409`** ⚠️ 必须用 `UPDATE ... WHERE stock > 0` 原子条件更新
- `stock=0` 兑换 → `409`
- `per_user_limit=2`，已兑 2 次，第 3 次 → `409`
- `per_user_limit=null` → 不限
- 积分 99，商品 `points_cost=100` → `422`，且**积分不得被扣**
- `quantity=2` 时校验的是 `points_cost * 2`，且 `stock >= 2`
- `quantity=0` / 负数 → `422`；`quantity=100` → `422`
- 兑换后积分立即扣减，写 `point_ledger(source=redemption, change=-N)`
- 并发兑换同一商品的不同用户 → 库存守恒：`stock + sold == total_initial`
- 商品 `status=off` → `404`（不是 403）
- 兑换后商户把商品下架 → 已产生的 `redemption` 不受影响，继续履约
- 商户把 `stock` 改成小于 `sold` → `422`

### 取消与退款
- 取消 `pending` 兑换 → 积分原路返还，写 `point_ledger(source=refund, change=+N)`
- 取消后库存 +1，`sold` -1
- 重复取消 → `409`，**积分不得重复返还**（靠 `(source,ref_type,ref_id)` 唯一约束兜底）
- 取消 `confirmed` 兑换 → `409`

### 与 07 报销的分账（**不得合并计算**）
- 报销（07）是**成本返还**，奖励（本模块）是**激励**，两者来源不同、账本不同、上限不同
- `max_reward_per_user` 只约束本模块的奖励，**不约束 07 的报销**；反之亦然
- 同一 post 可同时产生 1 条 `reward_grant` 与 1 条 `reimburse_claim`，互不影响
- 报销失败（池子不足）**不得**导致奖励少发或重发
- 奖励结算失败**不得**导致报销不发（两个事务独立，各自幂等）

### 归属
- 用户查他人积分 / 券 / 兑换记录 → `403`
- 商户 `confirm` 别人商品下的兑换 → `403`
- 商户查别人任务的奖励规则 → `403`

### 优惠券
- 券 `total=10`，第 11 张发放 → 见上文"券发完"规则
- 券过期 → 定时任务置 `status=expired`，**不影响已发积分**
- `user_coupon.code` 全局唯一，重复生成 → 唯一约束拒绝
- 券的 `valid_to` 早于 `valid_from` → `422`（建券与改券都校验）
- `total` 改成小于已 `issued` → `422`（已发出去的券收不回来，改小等于自相矛盾）
- 建券 `total <= 0` → `422`；`type` 不在 `cash_off` / `discount` / `gift` → `422`
- 商户改**别人**的券 → `403`（不是 404）

---

## 明确不做

- ❌ **不做真实资金池 / 提现通道**（涉二清合规）。`cash_payout` 只记账，实际打款线下对公，`status` 由管理员手工置 `paid`。
- ❌ 不做第三方支付分账对接
- ❌ 不做积分与人民币的双向兑换
- ❌ 不做优惠券核销端（商户线下核销，本期只发不核）
- ❌ 不做物流 / 快递跟踪
- ❌ 不做收货地址簿管理（兑换时填一次，不做地址列表）
- ❌ 不做奖励撤回 / 追回
- ❌ 不做积分过期清零
- ❌ 不做积分等级 / 会员成长体系
- ❌ 不做邀请返利 / 拉新奖励
- ❌ 不做奖励发放通知（短信/推送）
- ❌ 不做排行榜
- ❌ 不做 `benefit`（权益）的核销与履约跟踪，仅在 `reward_detail` 里记录文本说明

---

# 追加 A（2026-09-16）：按商家分组的「我的奖励」

> 2026-09-16 用户确认：**新增端点**。用户界面里「我的奖励 → 商家 → 我在这个商家的
> 积分和券」这条线，在后端**完全不存在**：
> `GET /api/me/points/ledger` 每项只有泛型的 `ref_type`/`ref_id`
> （`reward_serializers.py:43`，**没有** `task_id` / `merchant_id`），
> `GET /api/me/coupons` 每项不带 `merchant_id`（`:72`，带 `merchant_id` 的
> `coupon_public` 那个端点不用），全后端也没有任何给客户的按商家分组端点
> （唯一按商家分组的是 admin 的 `/api/admin/cost/by-merchant`）。
>
> 本段**只加读端点**：不动既有端点、不动既有表、不改既有出参。

## 一句话目标

> 用户能看到「我跟哪些商家有往来」，点进某一个，看到**在该商家挣到的积分**
> 与**在该商家持有的券**。

## 聚合口径（**写死，实现不许另找路径**）

### 积分 `points_earned`
```
point_ledger WHERE user_id = 本人 AND ref_type = 'reward_grant' AND change > 0
  JOIN reward_grant ON reward_grant.id = point_ledger.ref_id
  GROUP BY reward_grant.task_id → task.merchant_id
```

- **只算正流水**。兑换扣减（`source='redemption'`，负）**不归任何商家**。
- 归组靠 `reward_grant.task_id → task.merchant_id`，**不靠** `point_ledger` 上不存在的列。
- **不用** `reward_grant.reward_detail->>'points'` 求和：账本才是「实际入账」的权威；
  `reward_detail` 是「打算发多少」的记录（券发不出、`capped` 截断都在那里留痕）。
  `grant_points` 写入时 `ref_type="reward_grant"`、`ref_id=row.id`，见 `services/reward.py:290-296`。

### 券 `coupon_total` / `coupon_unused` / `coupons[]`
```
user_coupon JOIN coupon ON coupon.id = user_coupon.coupon_id
  WHERE user_coupon.user_id = 本人
  GROUP BY coupon.merchant_id
```

- `coupon_total` = 该商家的券**总数**（所有状态）；`coupon_unused` = 其中 `status='unused'` 的张数。
- `coupons[]` 每项（**嵌套 `coupon`**——两层都有 `id` 与 `status`，平铺必撞车）：
  `{id, coupon_id, code, status, obtained_at, expire_at, used_at, coupon:{id, name, type, value, min_amount, valid_from, valid_to}}`

### 商家名
`merchant_profile.shop_name`（`String(64)` 非空，`models/user.py:70`）；无该行时回落
`user.nickname`。两者都空 → `""`，**不编造**。`logo_url` 取 `merchant_profile.logo_url`，无则 `null`（不是空串）。

## 端点 / 接口

| 动作 | 输入 | 成功返回 | 失败情况 |
|---|---|---|---|
| 我的奖励（按商家） | `GET /api/me/rewards/by-merchant?page&size` | `200 {items:[{merchant_id, merchant_name, logo_url, points_earned, coupon_total, coupon_unused}], total, page, size}` | `401`<br>`422` 分页参数非法 |
| 某商家的积分与券 | `GET /api/me/rewards/by-merchant/{merchant_id}?page&size` | `200 {merchant:{merchant_id, merchant_name, logo_url}, points_earned, coupons:[...], coupons_total, page, size}` | `401`<br>`404` 本人与该商家**无任何往来**<br>`422` 分页参数非法 |

- 列表按 `merchant_id` **升序**（稳定，分页才不重不漏）。
- `size` 上限 100（全局约定 #4）；`page >= 1`。
- 本组端点**不做角色门禁**，与其它 `/api/me/*` 一致——商户账号调会拿到空列表，不是 403。

## 边界（每条之后会变成一条测试）

### 归组正确性
- 只在 A 商家挣过积分 → 列表只有 A 一条
- 在 A / B 各挣过 → 两条，各自金额正确
- 在 A 的**两个任务**各挣过 → **合并成一条**，金额为两笔之和
- 有 A 的券但从 A 没挣过积分 → A **仍在列表里**，`points_earned=0`
- 从 A 挣过积分但没 A 的券 → `coupon_total=0`、`coupon_unused=0`
- 兑换过商品（负流水）→ **不影响**任何 `points_earned`
- 无任何往来 → `{items:[], total:0}`，**不是 404**
- **对账**：列表所有 `points_earned` 之和 == 本人全部 `ref_type='reward_grant'` 正流水之和

### 券
- 同商家 3 张（2 `unused` / 1 `used`）→ `coupon_total=3`、`coupon_unused=2`
- 券项里 `status`（user_coupon）与 `coupon.status`（券模板）**是两个字段**，不得互相顶替
- `coupon.name` / `value` / `type` 能读到（界面要显示「满 20 减 5」）

### 归属与 404
- 传**存在但本人无往来**的 `merchant_id` → `404`
- 传**不存在**的 `merchant_id` → `404`
- `merchant_id` 传字符串 / `0` / 负数 → `422`
- 未登录 → `401`

### 分页
- `size=101` → `422`；`size=100` → `200`；`page=0` → `422`
- 往来商家 25 个、`size=20` → 第 1 页 20 条、第 2 页 5 条，`total=25`，两页**无重复**
- 某商家券 25 张、`size=20` → `coupons_total=25`，第 2 页 5 张

### 不编造
- 无 `merchant_profile` 的商户 → `merchant_name` 回落昵称；两者皆空 → `""`，
  **不得**出现 `null` / `"undefined"` / `NaN`

## 明确不做

- ❌ 不做「我在该商家的**消耗**」——那是 07 的额度，商户经营数据，03/07 已明写不向用户暴露
- ❌ 不做跨商家汇总端点（那等于把 `GET /api/me/points` 再返一遍）
- ❌ 不做按任务分组（用户要的是按商家）
- ❌ 不做时间区间筛选 / 排序选项（固定 `merchant_id` 升序）
- ❌ **不改** `GET /api/me/points/ledger` 与 `GET /api/me/coupons` 的出参（既有测试锁着）
- ❌ 不建表、不加索引以外的库结构变更
