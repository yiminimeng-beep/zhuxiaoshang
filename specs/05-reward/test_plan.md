# 05-reward · 测试计划

> 由 `spec.md` 的「端点」「数据模型」「边界」逐条转出。一个 ID = 一条测试，ID 与测试函数一一对应。
>
> 覆盖：**15 个端点**的正常路径 + **11 组边界** = **82 条**
> （`TI` 9 / `RE` 11 / `CP` 5 / `PL` 7 / `ML` 9 / `RD` 13 / `CN` 5 / `UP` 9 / `MN` 3 / `OW` 6 / `AC` 5）。
>
> 端点数的算法（实现完按 `app.openapi()` 点过）：本模块名下 17 条路由，其中
> `PUT /api/merchant/reward-rules/{task_id}` 与 `POST .../validate` 是 **02 已有**的，
> 05 只新增 `GET /api/merchant/reward-rules/{task_id}`（客户与跨商户都是 403，
> 让商户能回看自己配的阶梯）。故 05 自己 = **15 条**。
>
> ⚠️ **本计划给 spec 的端点表加了 2 条**（2026-09-15 用户确认）：`POST /api/merchant/coupons`
> 与 `PATCH /api/merchant/coupons/{id}`。原 spec 有 `coupon` 表、有「`valid_to` 早于
> `valid_from` → `422`」的边界，却没有任何端点能建券——`mall_item` 有
> `POST /api/merchant/mall/items`，券却没有对应的。**spec 已同步补上这两行。**

---

## 运行方式

```bash
cd backend
docker compose -f ../docker-compose.yml up -d     # PG:5433 / Redis:6380
.venv/Scripts/python -m pytest tests/test_reward_*.py -q -p no:randomly --no-header
```

⚠️ **同一时刻只跑一个 pytest**：所有进程共用测试库 `zhuxiaoshang_test`，
彼此 TRUNCATE 会把速度拖到 3~10 倍慢（看着像卡住）。

---

## 这个模块的测试要盯住什么

05 的钱比 07 多一层：07 只有「额度」一本账，05 有 **四本**（`reward_grant` /
`point_ledger` / `user_coupon` / `cash_payout`），且**发出去就不可撤回**（spec
「明确不做奖励撤回 / 追回」）。所以三条主线：

1. **不能多发**——`post_id` 唯一 + `(source, ref_type, ref_id)` 唯一，两道数据库约束
   兜住「重复投递结算事件」与「重复返还积分」。应用层的 `if` 判断只是第一道。
2. **不能少发**——四种奖励形态**部分成功要照发其余的**（券发完了不能让积分也不发），
   这与 07「奖励与报销互不牵连」是同一种「一件事坏掉不能拖垮另一件」。
3. **并发下账要对**——兑换的库存与积分是**两个**会被并发的量：
   `stock` 靠 `UPDATE ... WHERE stock >= n` 原子扣，`point_ledger` 靠行锁串行化。

---

## 用例里反复出现的前置

| 工厂 | 作用 |
|---|---|
| `reward_scene(db, merchant_id, user_id, **over)` | 一整套结算场景：`task`（含 `pay_mode`） + `reward_rule`（含 `tiers` / `max_reward_per_user`） + `claim` + `job` + `social_post`（`pending`）。返回 `SimpleNamespace` |
| `insert_reward_rule(db, task_id, tiers=None, **over)` | 直插 `reward_rule`。`tiers` 默认 `make_tiers(3)`（首档 `0~99`，二档 `100~199`，三档 `200~`） |
| `approve_post(client, token, post_id)` | 走 `POST /api/merchant/reviews/{id}/approve`。**结算的真实触发点**，不绕过去直接调 `settle`——除了纯逻辑组（`TI` / `CP`） |
| `insert_ledger(db, user_id, ...)` | 07 已有：直插 `point_ledger`（造「已有余额 / 已发累计」的前置） |
| `insert_coupon(db, merchant_id, **over)` | 直插券模板（造「已发完 / 已停用」的前置时用；建券本身走 `POST /api/merchant/coupons`） |
| `insert_mall_item(db, merchant_id, **over)` | 直插商城商品 |
| `redeem(client, token, item_id, **over)` | 打兑换端点（不自动断言，调用方看状态码） |
| `points_of(db, user_id)` | `SELECT coalesce(sum(change),0) FROM point_ledger WHERE user_id=...` |
| `grant_rows(db, post_id)` | 该 post 的 `reward_grant` 行（结算断言的主入口） |

**阶梯常量**：`make_tiers(3, step=100, reward={"points": 50})` 造出
`[0,99] → [100,199] → [200,∞)`。要造「命中哪一档」的用例就传
`likes=N`（`engagement` 由 `insert_snapshot` 按三项算），
**不要把 `engagement` 直接写进快照**——那样「谁算的」就验没了。

**金额一律用能一眼验算的整数**：`cash` 以分为单位，`points` 是整数。
默认值取 `cash=100`（1 元）、`points=50`，红了不用心算。

---

## 测试缝（seam）——实现必须提供的名字

05 **不接任何外部网络**（没有 AI、没有存储），所以不需要 `monkeypatch` 类打桩器。
它需要的是三个**可直接调的函数**，名字即契约：

### 1. 结算入口（**已存在，04 在调**）

```python
# app/services/reward.py
async def settle(
    session: AsyncSession, *,
    post_id: int, user_id: int, task_id: int,
    engagement: int, rule_id: int | None = None,
) -> dict | None:
    """按 reward_rule 把 engagement 换算成奖励并落 reward_grant。"""
```

**签名不得改**：04 的 `review._run_hooks` 已经按这个形状调用（且**不传
`rule_id`**），`tests/helpers.py:RewardStub` 也按这个形状打桩。05 的活儿是把
`raise NotImplementedError` 换成真实现——`to_regclass('reward_grant')` 那道守卫
可以保留（表是 05 自己建的，守卫恒真，但删掉它会改动 04 已绿的用例所依赖的行为）。

**`rule_id` 由 05 自己解析**：04 传的是 `task_id`，`reward_rule.task_id` 唯一，
故 `SELECT ... WHERE task_id = :tid` 即可。

### 2. 积分入账（**05 新增，结算与兑换共用**）

```python
async def grant_points(
    session: AsyncSession, *,
    user_id: int, change: int,
    source: str, ref_type: str, ref_id: int,
    remark: str | None = None,
) -> int:
    """写一条 point_ledger 并返回 balance_after。**先锁行再算余额**。"""
```

**必须是同一个函数**：结算（+points）、兑换（-points）、取消返还（+points）
三处都动 `balance_after`。写成三份，「并发下 `balance_after` 串行」这条规则
就有三个实现、三个漏法（`PL-02`）。

### 3. 券过期（**05 新增，可直接调的定时任务**）

```python
async def expire_coupons(session: AsyncSession, *, now=None) -> int:
    """把 expire_at <= now 的 user_coupon 置为 expired，返回处理条数。"""
```

与 04 的 `auto_approve_expired` 同款：**不起真调度器**。用例不可能等到券过期，
「扫的动作」才是被测对象，「多久扫一次」是调度器的配置（spec 只说「定时任务」）。
本函数**不 commit**，交给调用方。

---

## TI · 阶梯匹配（9 条）

匹配是**纯函数**，可以脱离数据库单测——这也是它值得单独成组的原因：
结算的其余部分都要建一堆表，红了不好定位。

| ID | 断言 |
|---|---|
| TI-01 | `engagement=0` 命中第 0 档（`tier_index=0`），按第 0 档的 `reward` 发 |
| TI-02 | 二档 `[100,199]`：`engagement=100`（下界）→ `tier_index=1` |
| TI-03 | 同上：`engagement=199`（上界）→ `tier_index=1`（**含 max**，不是 `<`） |
| TI-04 | `engagement=200` → 落到三档（`tier_index=2`，末档 `max=null`） |
| TI-05 | `engagement=2000` 且末档 `{min:2000, max:null}` → `tier_index=2`（spec 原文用例） |
| TI-06 | **缺口**：人为造 `[0,99]` + `[200,∞)`（跳过 `100~199`）→ `engagement=150` → `status=below_threshold`、不发奖、**不得 500** |
| TI-07 | **无规则**：task 没有 `reward_rule` → `settle` 返回 `None`，`reward_grant` 不落行，**不得 500** |
| TI-08 | `reward_grant.engagement` 落库 == 传入的峰值（审计「当时按多少算的」） |
| TI-09 | 匹配用**结算时快照**：先 `settle`（按旧规则发 50 分），再改 `reward_rule.tiers` 为发 999 分并重跑 → 已有那行**逐列不变**（含 `reward_detail.points == 50`） |

> `TI-06` 的缺口在 02 的 `validate_tiers` 下建不出来——那条 `PUT /api/reward-rules`
> 会 `422`。所以这里**直插 `reward_rule`**：测的是「历史数据/脏数据进来了会不会
> 炸」，不是「02 的校验对不对」。spec 明写「不得报 500」，所以这条必须有。
>
> `TI-07` 是 04 真实会遇到的形状：任务没配规则就发布，客户照样能交作品、商户
> 照样能过审。此时「没有奖励」是正确结果，不是错误。

---

## RE · 结算与落库（11 条）

| ID | 断言 |
|---|---|
| RE-01 | 过审后恰好 1 条 `reward_grant`：`status=granted`、`granted_at` 非空、`tier_index` 与命中档一致、`claim_id` / `task_id` / `reward_rule_id` 都落对 |
| RE-02 | **幂等**：同一 post 连跑 `settle` 3 次 → `reward_grant` 仍 **1 行**，积分只加 **1 次** |
| RE-03 | 重复过审（第二次走 `POST .../approve`）→ `409`（04 的状态机拦的），**且没有第二行 grant** |
| RE-04 | `reward = {cash: 100, points: 50}` → 两个都发：`cash_payout` 1 行 + `point_ledger` 1 行（`source=task_reward`） |
| RE-05 | **券发完不整体回滚**：`reward = {points: 50, coupon_id: X}`，X 的 `total=1` 且 `issued=1` → 券发不出，但**积分照发**（`point_ledger` 有 1 行），`status=granted`，`reward_detail` 记录实际发出的部分 |
| RE-06 | 券模板 `status=inactive` → 不发券，积分照发，`reward_detail` 不含 `coupon_ids` |
| RE-07 | `reward = {benefit: "免费到店一次"}` → 只写进 `reward_detail.benefit`，**不产生** `cash_payout` / `point_ledger` / `user_coupon`（spec「明确不做 benefit 核销」） |
| RE-08 | `below_threshold` 也落一行 `reward_grant`（`status=below_threshold`，`granted_at` 为 null）——「没发」本身要可审计 |
| RE-09 | **72h 自动通过走同一条**：`expire_post` + `run_expiry()` → `reward_grant` 与人工过审**逐列同形** |
| RE-10 | **批量通过走同一条**：`POST /api/merchant/reviews/batch-approve` → 同样产生 `reward_grant` |
| RE-11 | **申诉受理走同一条**：`POST /api/posts/{post_id}/appeal` 申诉 → `POST /api/admin/appeals/{appeal_id}/decide`（`accept`）→ 同样产生 `reward_grant`；`action=appeal_accept` 时 `review_log` 与 grant 都只有 1 份 |

> `RE-05` 钉的是 spec 的原话：「**不得整体回滚**（积分和现金照发）」。
> 最顺手的写法是把整笔奖励包在一个事务里，券那一步抛异常就把积分一起回滚了——
> 状态码仍是 200，只有查库才看得见。
>
> `RE-09`/`RE-10`/`RE-11` 三条一起钉「**四个过审动作共用一个结算实现**」。
> spec 明写「72h 自动通过触发的结算必须与人工通过完全一致」，而最容易漏的
> 就是系统路径（人工路径有人复核，系统路径没人看）。

---

## CP · 现金上限（5 条）

上限只截断 **`cash`**（spec 的边界小节挂在「现金上限」下，示例与
`cash_payout.amount` 都指现金；积分不截断——见「已知取舍」2）。

| ID | 断言 |
|---|---|
| CP-01 | `max_reward_per_user=10000`，该用户在该任务**已发 8000**，本次应发 3000 → 实发 **2000**，`status=capped`，`reward_detail.cash=2000` |
| CP-02 | 同上，`cash_payout.amount == 2000`（**实发额，不是应发额 3000**） |
| CP-03 | `max_reward_per_user=null` → 不截断，`status=granted` |
| CP-04 | 累计口径是 **(user, task)**：同一用户在同一任务的**另一个 post** 已发 8000，本次照样被截断（不是按 post 算） |
| CP-05 | **任务之间互不影响**：同一用户在**另一个任务**已发 9000，本任务本次应发 3000 → 全额放出 |

> `CP-05` 是 `CP-04` 的反向。只测 `CP-04` 的话，把实现写成「跨任务累计」
> 也会全绿——两条一起才把 `(user, task)` 这个二元组钉死。

---

## PL · 积分账本（7 条）

| ID | 断言 |
|---|---|
| PL-01 | 发积分后 `point_ledger.balance_after` == `GET /api/me/points` 的 `balance`（spec 原话） |
| PL-02 | **并发**：余额 0 时并发投递 +100 与 +50 → 两条流水的 `balance_after` 分别为 **100 / 150**（行锁串行，**不得出现两个 100**） |
| PL-03 | 同一 `(source, ref_type, ref_id)` 重复入账 → 唯一约束拒绝，**表里仍 1 行**；`balance_after` 不重复增长 |
| PL-04 | `change` 为负且超过余额 → 拒绝（`422` 或 `CheckViolation` 兜底），**`balance_after` 不得为负** |
| PL-05 | `GET /api/me/points/ledger` 返回 `{items,total,page,size}`，按 `created_at DESC, id DESC`，`source` / `change` / `ref_type` 都在 |
| PL-06 | 流水端点分页：`size` 上限 100（传 101 → 422 或夹到 100）；`page=2` 不重叠 |
| PL-07 | `point_ledger` **只追加**：本模块任何端点都不产生 `UPDATE` / `DELETE`（扫路由源码，同 07 的 `LG-07` 款） |

> `PL-02` 是**行锁**的用例：`balance_after` 不是算出来的，是「锁住上一行再算」。
> 朴素的「先查余额再插入」在并发下会写出两个 100。
>
> `PL-03` 的唯一约束是 spec 全局约定 #10「唯一约束即业务规则」的落点，
> 也是 `CN-03`（重复取消不重复返还）的兜底。

---

## ML · 商城商品（9 条）

| ID | 断言 |
|---|---|
| ML-01 | 商户上架 → `201`，`status` 默认 `on`，`sold=0` |
| ML-02 | 客户上架 → `403` |
| ML-03 | `GET /api/mall/items?merchant_id=X` 只返回 X 的 **`status=on`** 的商品（下架的不出现） |
| ML-04 | 列表分页 `{items,total,page,size}`，`size` 上限 100 |
| ML-05 | `merchant_id` 不存在 → `404`（spec 明写） |
| ML-06 | 详情 `GET /api/mall/items/{id}`：上架 → `200`；**下架 → `404`**（spec 明写「非上架返回 404」，不是 403） |
| ML-07 | `points_cost=0` 或 `stock=-1` → `422`（spec：`points_cost >= 1`、`stock >= 0`） |
| ML-08 | `PATCH` 把 `stock` 改成**小于 `sold`** → `422`（spec 原话） |
| ML-09 | `PATCH` 别人家的商品 → `403`（不是 404） |

---

## RD · 兑换（13 条）

兑换是本模块**并发最密**的地方：`stock` 与积分是两个独立的共享量，
且「校验」与「扣减」之间若不加锁就会被穿。

| ID | 断言 |
|---|---|
| RD-01 | 正常兑换 → `201`，返回 `{redemption, redeem_code, points_balance}`；`points_balance == 扣减后余额` |
| RD-02 | 兑换后：积分扣减、`point_ledger` 1 行（`source=redemption`，`change=-N`）、`stock-1`、`sold+1` |
| RD-03 | **并发 `stock=1`，100 个请求 → 恰好 1 个 `201`，99 个 `409`** ⚠️ 必须 `UPDATE ... WHERE stock >= n` 原子条件更新 |
| RD-04 | `stock=0` → `409` |
| RD-05 | `stock=1` 但 `quantity=2` → `409`（不够就整单拒，不是扣 1 个） |
| RD-06 | `per_user_limit=2` 已兑 2 次 → 第 3 次 `409` |
| RD-07 | `per_user_limit=2` 已兑 1 次、本次 `quantity=2` → `409`（计数看**总量**不只是次数） |
| RD-08 | `per_user_limit=null` → 不限 |
| RD-09 | 积分 99、`points_cost=100` → `422`，**且积分不得被扣、库存不得变** |
| RD-10 | `quantity=2` 校验的是 `points_cost * 2`（积分够 200 才放行） |
| RD-11 | `quantity=0` / `-1` / `100` → `422`（spec：`<=0` 或 `>99`） |
| RD-12 | **库存守恒**：不同用户并发兑换同一商品 → `stock + sold == 初始 total`（一个不多一个不少） |
| RD-13 | 兑换后商户把商品下架（`status=off`）→ **已产生的 `redemption` 不受影响**，`confirm` 仍 `200`（spec 原话） |

> `RD-03` 是本模块最重要的一条。写法与 02 的 `CC-03`（并发领取代额）同款：
> 朴素「先查 `stock` 再写」会被并发穿成多个 `201`。**反向验证必须做这一条**。
>
> `RD-08` 是 `RD-06` 的反向；`RD-12` 是 `RD-03` 的守恒版——`RD-03` 钉「不超卖」，
> `RD-12` 钉「不少卖」（把 `sold` 算错成加两次也过不了）。

---

## CN · 取消与退款（5 条）

| ID | 断言 |
|---|---|
| CN-01 | 取消 `pending` → 积分原路返还，`point_ledger` 1 行（`source=refund`，`change=+N`），`balance_after` 与 `GET /api/me/points` 一致 |
| CN-02 | 取消后 `stock+1`、`sold-1`（**数量对称**：兑了 2 个退 2 个） |
| CN-03 | **重复取消 → `409`**，积分**不重复返还**（靠 `(source,ref_type,ref_id)` 唯一约束兜底，不是靠 `if status == 'pending'`） |
| CN-04 | 取消 `confirmed` 的兑换 → `409` |
| CN-05 | 商户取消**别人商品**下的兑换 → `403`（不是 404） |

> `CN-03` 的断言要看**流水行数**：状态机拦住了 `409`，但若实现是先退还再判状态，
> 钱已经出去了。故断言「`point_ledger` 里 `ref_type='redemption'` 且
> `ref_id=<本次>` 的行仍恰好 1 条」。
>
> `confirm` 的状态机（`409` 状态非 `pending`）由 `CN-04` 顺带覆盖。

---

## UP · 优惠券（9 条）

| ID | 断言 |
|---|---|
| UP-01 | 商户建券 → `201`，`{coupon}` 含 `id` / `issued=0` / `status=active`（默认值） |
| UP-02 | 建券边界：`valid_to < valid_from` → `422`；`total=0` / `-1` → `422`；`type="bogus"` → `422` |
| UP-03 | 改券：`total` 改成**小于已 `issued`** → `422`（已发出去的收不回来） |
| UP-04 | 结算发券 → `user_coupon` 1 行：`status=unused`、`expire_at == coupon.valid_to`、`obtained_at` 非空 |
| UP-05 | `coupon.total=10` 且已发 10 → 第 11 张发不出，`user_coupon` 仍 **10 行**，且 `coupon.issued` 不得变成 11 |
| UP-06 | 券的 `issued` 与 `user_coupon` 行数一致（发放是「`issued+1` 与 `insert` 同事务，一起成功或一起失败」） |
| UP-07 | `user_coupon.code` 全局唯一：同一 code 重复插入 → 唯一约束拒绝 |
| UP-08 | `GET /api/me/coupons?status=unused` 只返回 `unused`；不带 `status` 返回全部；非法 `status` → `422` |
| UP-09 | 客户建券 → `403`（`POST` 与 `PATCH` 都是） |

---

## MN · 券过期（3 条）

| ID | 断言 |
|---|---|
| MN-01 | `expire_at <= now` 的 `user_coupon` → `expire_coupons()` 后 `status=expired` |
| MN-02 | **幂等**：连跑 3 次 → 返回值第 2、3 次为 0，`status` 不变，**已发积分不受影响**（spec 原话） |
| MN-03 | 未过期的（`expire_at > now`）**不得**被改（反向：不能把「扫得到」写成「扫一切」） |

---

## OW · 归属（6 条）

| ID | 断言 |
|---|---|
| OW-01 | 客户打 `GET /api/merchant/reward-rules/{task_id}` → `403` |
| OW-02 | 商户查**别人任务**的奖励规则 → `403`（不是 404） |
| OW-03 | 商户 `confirm` / `cancel` 别人商品下的兑换 → `403`（不是 404） |
| OW-04 | 商户 `PATCH` **别人家的券** → `403`（不是 404） |
| OW-05 | `GET /api/me/rewards` 只含本人的 grant（另一用户的 grant 不出现） |
| OW-06 | `GET /api/me/redemptions` 只含本人的兑换；`GET /api/me/coupons` 只含本人的券 |

> `OW-05` / `OW-06` 是 spec「用户查他人积分 / 券 / 兑换记录 → 403」的**可实现替代**：
> `/api/me/*` 不接受 `user_id` 参数（传了会被 `extra="forbid"` 挡成 `422`），
> 「查他人」结构上不可达。改为造**两条真实数据**（A 的与 B 的），断言 A 的响应里
> 只有 A 的——这比「没有这个入口」是更强的保证。
>
> `OW-04` 与 `OW-03` 同款：**别人的资源回 `403` 而不是 `404`**（全局约定 #7）。
> 回 404 会把「不归你管」和「不存在」压成同一个答案，排查方向完全相反。

---

## AC · 与 07 报销的分账（5 条）

spec 明写这一段，且它是**合规**相关（「成本返还」与「激励」不能混），故单独成组。

| ID | 断言 |
|---|---|
| AC-01 | 同一 post 过审 → 同时产生 **1 条 `reward_grant` 与 1 条 `reimburse_claim`**，互不影响 |
| AC-02 | 报销**失败**（池子不足）→ 奖励照发（`reward_grant.status=granted`） |
| AC-03 | 奖励**失败**（`benefit` 形状非法或规则缺失）→ 报销照发（`reimburse_claim` 正常落） |
| AC-04 | `max_reward_per_user=0` → 现金奖励被截到 0，但**报销不受影响**（照额报） |
| AC-05 | 账本**不共用**：报销只写 `quota_ledger`，奖励只写 `point_ledger`——报销**不得**产生 `point_ledger` 行，奖励**不得**产生 `quota_ledger` 行 |

> `AC-02` / `AC-03` 与 04 的 `RT-04` 是一体两面：04 已经测了「奖励失败不拖垮报销」，
> 这里补反向。两笔钱各自包 `begin_nested()` 是 04 的 `_run_hooks` 就做好的事，
> 05 要保证的是**自己这一半不抛不该抛的异常**（比如没规则就抛）。
>
> `AC-05` 用「表名互斥」来钉，比读代码可靠。

---

## 反向验证（实现完成后必做）

| 改坏处 | 应变红的用例 | 实测结果（2026-09-15） |
|---|---|---|
| 兑换改成「先查 `stock` 再写」 | `RD-03`（必须恰好 1 个 `201`，实际会多个） | ✅ 红：`CheckViolationError: ck_mall_item_stock`，`stock` 被穿到 **−1** |
| `grant_points` 去掉行锁 | `PL-02`（`balance_after` 会出现两个 100） | ✅ 红：`断言并发入账未串行（合计不是 150）：[50, 100]`（**须先加 `Barrier`**，见「已知取舍」13） |
| 把整笔奖励包进一个事务（`issue_coupon` 发不出即抛） | `RE-05`（券失败时积分被一起回滚） | ✅ 红：`reward_grant 应恰好 1 行，实际 0 行`；同批 `RE-06`（券停用）不受影响，区分度精确 |
| 上限去掉 `rg.task_id` 过滤（跨任务累计） | `CP-04` | ✅ 红：`assert 0 == 2000`；连带 `CP-05` 也红（`'capped' != 'granted'`）——「跨任务」这一档被两条用例同时兜住 |
| 取消改成「先退还再判状态」 | `CN-03`（流水会多一行） | ✅ 红：`CheckViolationError: ck_mall_item_sold`，`sold` 被穿到 **−1** |

> 五行**均为实测**（改坏 → 跑 → 看红 → 改回，改回后 82 条全绿）。
> 其中两行（`RD-03` / `CN-03`）的失败点是**数据库 CHECK**，不是接口断言的
> 状态码——说明这两处的第一道闸是原子条件 UPDATE，DB 约束只是最后的保险。

---

## 已拍板的三个口径（2026-09-15）

### 1. ✅ 券模板补两个端点（**已改 spec**）

`coupon` 表在数据模型里有（`name` / `type` / `value` / `total` / `valid_from`
/ `valid_to`…），边界里也有「券的 `valid_to` 早于 `valid_from` → `422`」，
但原 spec 的 **14 个端点里没有任何一个能建券**——`mall_item` 有
`POST /api/merchant/mall/items`，券却没有对应的。

**决定：补 `POST /api/merchant/coupons` + `PATCH /api/merchant/coupons/{id}`**，
与商城商品对称。`specs/05-reward/spec.md` 的端点表与券边界已同步补上，
用例见 `UP-01`~`UP-03` / `UP-09` / `OW-04`。

### 2. ✅ `max_reward_per_user` 只截断现金，不截断积分

spec 把这条规则写在「**现金上限**」小节下，示例（`8000 + 3000 → 2000` 分）、
`cash_payout.amount` 的断言、以及字段名里的 `reward` 都指向现金。
积分是否也受该上限约束，spec 没说。

**决定：只截断现金**（`CP` 组 5 条按此写）。若将来要改成「现金 + 积分合计封顶」，
`CP-01` / `CP-02` 两条要改，且得先定「1 积分抵多少分现金」的换算——
那是个 spec 没有的旋钮。

### 3. 「投递结算事件到队列」在实现上是**同步调用**

spec 的结算触发器画的是「投递事件到队列」，但 04 已经落地为 `_run_hooks` 里
**同步 `await settle(...)`**，且 04 的 116 条用例（含 4 条反向验证）都建立在这个
形状上。改成真队列要动 04 的已绿代码。

**本计划按同步调用写**：幂等由 `post_id` 唯一约束保证，与「队列至少投递一次」
的最终效果等价。若将来要上真队列，`RE-02` 与 `RE-03` 两条不改也仍然成立
（测的是终态，不是调用方式）。

---

## 已知取舍

1. **`reward_grant` / `point_ledger` / `cash_payout` 三张表必须无 `ON DELETE CASCADE`**
   ——01 的 `C-09` / `C-09b` 盯的就是「注销后审计行还在」。这两条用例现在红的
   原因正是 `point_ledger` 不存在；05 建表后应**自动转绿**，且若把外键写成
   `ondelete="CASCADE"`，它们会立刻变红。**这是 05 与 01 的接口**。
2. **`metric` 本期固定 `engagement`**（02 的 spec 明写），05 不做其它 metric 的分支。
   `reward_rule.metric` 列存在但不参与计算。
3. **`cash_payout` 只记账不打款**（spec「明确不做」）：本模块只写
   `status=pending`，**没有任何端点能把它置成 `paid`**——06 的 spec 明写
   「不做奖励台账界面与手工打款（`cash_payout` 仍由管理员直接操作数据库）」。
   故 `CP-02` 断言的是 `amount`，不断言 `status` 流转。
4. **`benefit` 只记文本**（spec「明确不做 benefit 核销」）：`RE-07` 断言它
   不产生任何一本账。
5. **积分不可提现、不退现金**（全局约定）：本模块**没有**任何把积分换回人民币的
   端点。兑换只花积分，取消只退积分。
6. **`user_coupon` 不做核销端点**（spec「不做优惠券核销端」）：`used` 状态本期
   只能由数据库直接改，没有端点。故 `UP` 组只测发放与过期，不测核销。
7. **兑换的「地址」只存不校验**：`address` 是 jsonb，spec 说「兑换时填一次，
   不做地址列表」，故只断言原样落库，不做结构校验。
8. **`per_user_limit` 的计数口径**：spec 说「已兑 2 次」，但 `quantity` 允许多件。
   `RD-07` 取**累计件数**（`sum(quantity)` 而非 `count(*)`）——「上限 2 件」
   比「上限 2 单」更符合「每用户限兑 N 件」的常识。**这是本计划定的口径，spec 没写死。**
9. **`OW-04` / `OW-05` 是「查他人」的可实现替代**（见 OW 组的注）。
   spec 原文的「查他人 → 403」在 `/api/me/*` 的形状下不可达。
10. **`point_ledger` 的分页沿用 02/07 的 `?page&size`**（全局约定 #4，
    `size` 上限 100），不为 05 另立一套。
11. **结算失败不写 `status=failed` 的正常路径**：spec 的 `reward_grant.status`
    有 `failed` 一值，但触发它的场景（数据库炸、约束冲突）在用例里造不出来
    ——造得出来就说明那是正常路径了。故 **`failed` 本期无用例**，
    只在实现里保留该枚举值。
12. **`expire_coupons` 不 commit**（与 `auto_approve_expired` 同款），
    事务边界交给调用方（定时任务或测试）。
13. **并发用例必须有 `asyncio.Barrier`，只 `asyncio.gather` 测不出东西**（`PL-02`
    实测）：事件循环会把第一个协程**一路跑到 `commit()`** 才切给第二个，两个事务
    根本不重叠。`PL-02` 最初就是这么写的——**去掉 `grant_points` 的 `FOR UPDATE`
    后它照样绿**（连跑 3 次均 `1 passed`）。改法是每个协程先 `SELECT 1` 把事务
    真开起来，再 `await barrier.wait()`，然后断言取**与顺序无关**的量
    （`max(balance_after) == 150`，而不是「排序后 == [100,150]」——串行之后谁先
    谁后是调度决定的）。改完后去掉 `FOR UPDATE` 立刻红成 `[50, 100]`。
    **凡「先并发再断言」的用例都应照此办理。**
14. **`RE-09` 的对照作品必须挂在同一个任务下**：它逐列比对自动通过 vs 人工过审，
    而 `task_id` / `reward_rule_id` 就在比对列里。最初用了两个独立的
    `reward_scene`（= 两个任务），这两列天然不同，等于把对比放宽了两档。
    改用 `another_post(db, auto_scene)` —— 在**同一任务**下再挂一篇作品
    （同任务同用户只能有一个未关闭的 claim，故复用同一个 `claim_id`，只换
    `content_job`）。

---

## 下一步

1. **红**：写 `tests/test_reward_{tiers,settle,cap,points,mall,redeem,cancel,coupon,perms,split}.py`，
   并把 7 张表建好（`app/models/reward.py`）。此时 15 个端点一个都没有，
   **应全红**，且红因只应是三类：表缺失 / 路由缺失 / `settle` 抛 `NotImplementedError`。
2. **绿**：按组 `TI → RE → CP → PL → ML → RD → CN → UP → MN → OW → AC` 逐组转绿，
   新增 `app/api/reward_serializers.py` / `me_reward.py` / `mall.py` /
   `merchant_mall.py`（券建在 `merchant_mall.py` 里，与商城商品同一套归属校验），
   把 `app/services/reward.py` 的 `settle` 填成真实现，
   并加 `grant_points` / `expire_coupons`。
3. **回归**：05 落地后**回跑 01 的 `C-09` / `C-09b`**——它们应转绿（`point_ledger`
   出现）。**这是 05 交付的验收条件之一。**
4. **反向验证**：做齐上表 5 条。
5. 更新 `CLAUDE.md` 工作记录与 `HANDOVER.md`。

---

# ׷�� A��2026-09-17�������̼ҡ��ҵĽ������� `BM`��Լ 22 ����

> Spec��`specs/05-reward/spec.md` ׷�� A��ֻ�Ӷ��˵㣻�������г��Ρ�

## ���� �� `BM`

| ��� | ���� | ���� |
|---|---|---|
| BM-01 | ֻ�� A �������� | �б��� A |
| BM-02 | A / B ������ | ��������������ȷ |
| BM-03 | A ��������������� | �ϲ�һ�������֮�� |
| BM-04 | �� A ��ȯ���޻��� | A ���б���`points_earned=0` |
| BM-05 | �л�����ȯ | `coupon_total=0`��`coupon_unused=0` |
| BM-06 | �һ�����ˮ | ��Ӱ�� `points_earned` |
| BM-07 | ������ | `{items:[], total:0}` |
| BM-08 | �б� points ֮�� == ����ȫ�� reward_grant ����ˮ֮�� | |

## ȯ �� `BC`

| ��� | ���� | ���� |
|---|---|---|
| BC-01 | ͬ�̼� 3 �ţ�2 unused / 1 used�� | total=3 unused=2 |
| BC-02 | `status` ��Ƕ�� `coupon.status` �����ֶ� | |
| BC-03 | `coupon.name` / `value` / `type` �ɶ� | |

## ���� �� `BO`

| ��� | ���� | ���� |
|---|---|---|
| BO-01 | ���ڵ��������� merchant_id | 404 |
| BO-02 | �����ڵ� merchant_id | 404 |
| BO-03 | �ַ��� / 0 / ���� | 422 |
| BO-04 | δ��¼ | 401 |

## ��ҳ �� `BP`

| ��� | ���� | ���� |
|---|---|---|
| BP-01 | size=101 �� 422��size=100 �� 200��page=0 �� 422 | |
| BP-02 | 25 �̼� size=20 | ҳ1=20 ҳ2=5 total=25 ���ظ� |
| BP-03 | ĳ�̼� 25 ��ȯ size=20 | coupons_total=25 ҳ2=5 |

## ������ �� `BN`

| ��� | ���� | ���� |
|---|---|---|
| BN-01 | �� merchant_profile �� ���� nickname���Կ� �� `""`���� null�� | |
