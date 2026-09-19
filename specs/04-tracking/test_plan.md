# 04-tracking · 测试计划

> 由 `spec.md` 的「端点」与「边界」逐条转出。一个 ID = 一条测试，ID 与测试函数一一对应。
>
> **本轮一趟落地两块**（2026-09-15 确认）：
> ① 04 的 **6** 张表（`social_post` / `metric_snapshot` / `ocr_result` /
> `review_log` / `appeal` / `track_event`）+ 全部 12 个端点；
> ② **07 的最后一环 `reimburse(post_id)`**——它的唯一触发点就是本模块的
> 「过审」动作（见 07-token spec「落地分两趟」的收尾项）。
>
> 覆盖：12 个端点的正常路径 + 8 组边界 = **118 条**
> （`SP` 12 / `MS` 8 / `SC` 9 / `TO` 10 / `RV` 13 / `BA` 16 / `AP` 19 /
> `PS` 6 / `RT` 12 / `E2E` 8 / `OW` 5）。

---

## 运行方式

```bash
cd backend
docker compose -f ../docker-compose.yml up -d     # PG:5433 / Redis:6380
.venv/Scripts/python -m pytest -q tests/test_track_*.py
```

---

## 测试缝（seam）——实现必须提供的四组名字

04 的外部依赖有四处，**名字即契约**：实现改名 → 对应用例 `AttributeError`，
而不是静默跳过。

### 1. 视觉识别（扩 03 已有的 `app/services/ai.py`）

```python
@dataclass(frozen=True)
class OcrVerdict:
    parsed: dict | None      # {likes, collects, comments, shares}；识别不出数字时为 None
    confidence: float         # 0.00~1.00
    model: str
    raw_text: str | None = None

async def ocr_metrics(image_url: str) -> OcrVerdict: ...
```

**为什么不新开一个模块**：03 的 `guard_assets` 已经打的是 `qwen3-vl`，同一家模型、
同一类「看图说话」的调用，再开一个 seam 只会让打桩点从一个变两个。

**`mismatch_flag` 不在这里算**：拿识别值去比对链接侧快照是 04 的业务规则
（`MISMATCH_CONFIDENCE = 0.70` 也定义在 `app/models/tracking.py`），
塞进视觉模型的返回值等于让 AI 决定业务阈值。

### 2. 72h 扫描（`app/services/review.py`）

```python
async def auto_approve_expired(session: AsyncSession) -> list[int]:
    """扫 `status=pending` 且 `review_deadline <= now` 的作品，置 auto_approved
    并写 `review_log`。返回本次处理的 post_id 列表。"""
```

**为什么是「可直接调的函数」而不是真 cron**：用例不可能等 72 小时。
spec 写的是「定时任务每 10 分钟扫一次」，**扫的动作**才是被测对象，**每 10 分钟**
是调度器的配置。真起一个 APScheduler 只会让用例依赖时钟。

### 3. 奖励结算（`app/services/reward.py`，**05 尚未落地**）

```python
async def settle(
    session: AsyncSession, *, post_id: int, user_id: int,
    task_id: int, engagement: int, rule_id: int | None
) -> dict | None:
    """05 的奖励结算入口。`reward_grant` 表不存在时**直接返回 None**。"""
```

与 07 的 `_log_action` 同款守卫：`to_regclass('public.reward_grant')`。
**不假装结算过**，也不替 05 建表（表结构该由 05 的 spec 决定）。

### 4. 报销（`app/services/quota.py` 的 `reimburse`，**本轮落地**）

```python
async def reimburse(session: AsyncSession, *, post_id: int) -> ReimburseClaim | None: ...
```

---

## 用例里反复出现的前置

| 工厂 | 作用 |
|---|---|
| `review_scene(client, db, **over)` | 建「已发布 + 已领取 + 已 ready 的 job」三步，返回 `(task, claim_id, job_id, customer_token)` |
| `submit_post(client, token, **over)` | 提交作品（不自动断言） |
| `submit_post_ok(...)` | 提交并断言 `201`，返回响应体 |
| `insert_post(db, **over)` | 直插 `social_post`（状态机用例不走接口造状态） |
| `insert_snapshot(db, post_id, **over)` | 直插 `metric_snapshot` |
| `expire_post(db, post_id, hours=73)` | 把 `submitted_at` / `review_deadline` 一起往前推（**两个字段必须同推**，见 `TO-01`） |
| `run_expiry(*, now=None)` | 开一条**独立 session** 跑 `await auto_approve_expired(session, now=now)` 并 commit，返回被处理的 `post_id` 列表。调度器每次跑在独立事务里，薄包装照抄这个口径；`now` 可传是为了 `TO-03` 的「恰好等于」（不能靠 `time.sleep`） |
| `patch_ocr` | `ai.ocr_metrics` **与** `storage.sniff_mime` 两个接缝的打桩器。`sniff` 决定"真实字节类型"，`push` 决定识别返回（或抛超时） |

**时间常量**：72 小时直接写字面量 `72 * 3600`，`TO-03` 的「恰好等于 now」
用一个显式构造的 `deadline = now` 用例，**不写成表达式比较**。

---

## SP · 提交作品（`POST /api/posts`）

| ID | 断言 |
|---|---|
| SP-01 | 正常提交 → `201`，响应含 `post` 与 `review_deadline` |
| SP-02 | `review_deadline == submitted_at + 72h`（容差 1 秒） |
| SP-03 | 同一 `claim_id` + 同一 `post_url` 提交两次 → `409` |
| SP-04 | 同一 `claim_id` 换一个**不同** URL 再提交 → `422`（一个领取只算一个作品） |
| SP-05 | `post_url = "not-a-url"` → `422` |
| SP-06 | `post_url = "javascript:alert(1)"` → `422`（防 XSS） |
| SP-07 | `platform=xhs` 但域名 `douyin.com` → `422` |
| SP-08 | `platform=xhs` + `xiaohongshu.com` → `201` |
| SP-09 | `platform=xhs` + `xhslink.com` → `201`（短链） |
| SP-10 | 引用**别人**的 `claim_id` → `403` |
| SP-11 | `job_id` 状态非 `ready`（`generating`）→ `409` |
| SP-12 | 该 claim 已有 `approved` 作品后再提交（换个 URL）→ `422` |

> `SP-12` 与 `SP-04` 看起来重叠，但拦的**不是同一道闸**：`SP-04` 靠
> `(claim_id, post_url)` 之外的那条「一 claim 一作品」规则，`SP-12` 是
> 在它之上再叠一条「已通过就别再交」。两道都要在，否则「先提交 A 被驳回、
> 再提交 B」会从 `SP-12` 的口子漏过去。

---

## MS · 数据快照（`POST /api/posts/{id}/metrics`）

| ID | 断言 |
|---|---|
| MS-01 | 正常追加 → `201`，响应含 `snapshot`，`engagement` 由后端算 |
| MS-02 | `likes=10, collects=5, comments=3` → `engagement == 18` |
| MS-03 | 负数 `likes=-1` → `422` |
| MS-04 | 小数 `likes=1.5` → `422` |
| MS-05 | 请求体带 `engagement` → `422`（不许客户端决定档位） |
| MS-06 | 追加第 2 次 → 第 1 次**仍在库中**，两行都在 |
| MS-07 | 已审核完成（`approved`）后追加 → `409` |
| MS-08 | 请求体带 `captured_at` → `422`（**spec 原文「拒绝客户端传入」**；此处取 422 而非静默忽略，理由见下） |

> `MS-02` 钉的是 **`shares` 不计入 `engagement`**：`10+5+3=18`，若实现顺手把
> `shares` 也加上，这条立刻红。spec 的公式只写了三项。
>
> **`MS-08` 从「忽略」改成「422」**（2026-09-15 实现期修订）：spec 的边界原文是
> 「快照 `captured_at` 由服务端写入，**拒绝客户端传入**」，「拒绝」就是拒。
> 静默忽略会让受控的插件客户端以为自己传的值生效了，一路错下去。与 `MS-05`
> 对 `engagement` 的处理保持一致：多传一个不该传的字段 → 422。

---

## SC · 截图识别（保底机制）

| ID | 断言 |
|---|---|
| SC-01 | 正常上传 → `202` + `{ocr_result_id}` |
| SC-02 | `.txt` 改名成 `.png` → `415`（校验**真实 mime**，不看扩展名）。客户端把 `mime` 也谎报成 `image/png`，只有 `sniff_mime` 读到的真实字节是 `text/plain` |
| SC-02b | 自述 `mime=application/pdf` → `415`（**即使真实字节是合法图片**也不认。白名单只收 `image/jpeg` / `image/png` / `image/webp`） |
| SC-02c | 自述 `image/png` 但真实是 `image/jpeg` → `415`（两种都是合法图片，拒的是「说的和给的不一样」——放行会让日志里的类型永远不可信） |
| SC-03 | `size_bytes = 20MB + 1` → `413`；`= 20MB` → `202` |
| SC-04 | 识别不出任何数字（`parsed=None`）→ `422`，**不得用 0 兜底** |
| SC-05 | `confidence = 0.69` → `mismatch_flag = true` |
| SC-06 | 识别出 `likes=100`、插件快照 `likes=350` → `mismatch_flag = true` |
| SC-07 | 识别出 `likes=100`、插件快照也是 `100` → `mismatch_flag = false` |
| SC-08 | 同一 post 上传第 2 张 → 新的 `is_active=true`，旧的被置回 `false` |
| SC-09 | 识别值**低于**已有快照（新 100 < 旧 350）→ **允许**，但写一条 `metric_downgrade` 事件 |
| SC-10 | 识别接缝抛超时 → 上传仍 `202` 且 `ocr_result_id = None`；此后 `GET /api/ocr/{post_id}` → `409`（**还没好 ≠ 不存在**，回 404 会让客户端以为这个作品根本没有截图） |
| SC-11 | 识别完成后 `GET /api/ocr/{post_id}` → `200`，含 `parsed` / `confidence` / `mismatch_flag` |

> `SC-05` 与 `SC-06` 是**两个独立的判据**：前者是「模型自己没把握」，
> 后者是「模型有把握但与插件数据打架」。合并成一个布尔会让将来
> 「只信插件」或「只信识别」的调整无从下手。
>
> ⚠️ **`GET /api/ocr/{id}` 的 `{id}` 取的是 `post_id`，不是 `ocr_result.id`**
> （`SC-10` / `SC-11` / `OW-05` 都按 post 打）。路径里叫 `{post_id}` 更准确，
> spec 端点表写的是 `{id}`——按 post 理解，别照字面去猜。
>
> ⚠️ **`mismatch_flag` 的基准只认 `source == "plugin"` 的快照**：识别结果自己
> 也会写 `metric_snapshot`，拿「上一次识别的结果」当基准等于让识别跟自己比。
> `SC-06` / `SC-07` 的插件快照都由 `insert_snapshot(..., source="plugin")` 造。

---

## TO · 72 小时超时（**核心规则**）

| ID | 断言 |
|---|---|
| TO-01 | `expire_post` 把 `submitted_at` 前推 73h 后，`review_deadline` 仍 == `submitted_at + 72h`（**不因任何操作延期**） |
| TO-02 | `pending` 且已过期 → `run_expiry` 后 `status == "auto_approved"` |
| TO-03 | **边界**：`review_deadline` 恰好 `== now` → 判定超时（`<=` 而非 `<`） |
| TO-04 | 未到期（`deadline = now + 1h`）→ `run_expiry` 后**仍是** `pending` |
| TO-05 | 商户在到期前通过 → `review_log.action == "approve"`，**没有** `auto_approve` 行 |
| TO-06 | 商户在到期后通过 → `409`。**故意不先跑 `run_expiry`**：此时库里还是 `pending`，唯一能挡住商户的就是端点自己看的 `review_deadline` |
| TO-06b | 商户在到期后**驳回** → `409`（窗口一过，商户两边都不能反悔） |
| TO-07 | `auto_approve` 的 `review_log.operator_id` 为 `null` |
| TO-08 | 自动通过会调用奖励结算接缝，且传入的 `engagement` 是**峰值** |
| TO-09 | `run_expiry` 连跑 3 次 → `auto_approve` 日志**恰好 1 条**（幂等） |
| TO-10 | 积压：3 个 post（逾期 30h / 8h / 1min）→ 一次 `run_expiry` 全部扫到。⚠️ `expire_post(hours=N)` 是把 `submitted_at` 推到 N 小时前，故到期时刻是「**N − 72**」小时之前——要造「逾期 d」，`N` 得写 `72 + d` |
| TO-11 | 反向：还没到期就**不该**被扫到（不能把「扫得到」写成「扫一切」） |

> `TO-03` 用「恰好等于」来钉 `<=`。若实现写成 `<`，这条会红而 `TO-02` 仍绿——
> 这正是它单独存在的意义：差一个等号，72 小时整点的作品就永远躺在待审队列里。
>
> `TO-08` 把「峰值」与「自动通过」绑在一起测：spec 明写「不得因是系统动作就
> 跳过结算」，而峰值结算最容易在系统路径上被漏掉（人工路径有人复核，
> 系统路径没人看）。

---

## RV · 商户审核

| ID | 断言 |
|---|---|
| RV-01 | `approve` 正常 → `200`，`status=approved`，`reviewed_at` / `reviewer_id` 落库 |
| RV-02 | `review_log` 写入 `action="approve"`，`operator_id` = 该商户 |
| RV-03 | `reject` 且 `reason` 为空 → `422` |
| RV-04 | `reason` 9 字 → `422`；**10 字 → `200`** |
| RV-05 | `reject` 正常 → `200`，`status=rejected`，`reject_reason` 落库 |
| RV-06 | 审核**别的商户**任务下的作品 → `403` |
| RV-07 | 已 `approved` 再 `approve` → `409` |
| RV-08 | 已 `rejected` 再 `approve` → `409`（只能走申诉） |
| RV-09 | 已 `appealed` 再 `approve` → `409` |
| RV-10 | 已 `appealed` 再 `reject` → `409`（等 admin 裁决，商户不得介入） |
| RV-11 | 已 `auto_approved` 再 `reject` → `409`（超时后不得反悔） |
| RV-12 | 待审列表**只含**本商户任务的作品，且按 `review_deadline` **升序** |
| RV-13 | 待审列表**不含** `appealed` 的作品（已升级到平台，不再压商户） |

---

## BA · 批量通过

| ID | 断言 |
|---|---|
| BA-01 | 全部合法 → `200`，`succeeded` 含全部 id，`failed == []` |
| BA-02 | `post_ids` 空数组 → `422` |
| BA-03 | `post_ids` 51 条 → `422`；**50 条 → `200`** |
| BA-04 | 缺 `confirm`（或 `confirm=false`）→ `422`，且**一条都没被改动** |
| BA-05 | `post_ids` 含重复 id → 去重，`succeeded` 不重复出现 |
| BA-06 | 混入**别的商户**的作品 → 该条 `failed(reason="forbidden")`，其余成功 |
| BA-07 | 混入已 `approved` 的作品 → `failed(reason="already_reviewed")`，其余成功 |
| BA-08 | 混入 `rejected` 的作品 → `failed(reason="invalid_status")` |
| BA-09 | 混入 `appealed` 的作品 → `failed(reason="invalid_status")` |
| BA-10 | 混入不存在的 id → `failed(reason="not_found")` |
| BA-11 | **部分失败不整批回滚**：3 好 1 坏 → 库里那 3 条真的是 `approved` |
| BA-12 | 通过 3 条 → `review_log(action="batch_approve")` **恰好 3 条**（不得只写一条汇总） |
| BA-13 | 每条日志的 `operator_id` 都是该商户，且 `action` 可与单条 `approve` 区分 |
| BA-14 | 批量通过与 `run_expiry` 竞争同一批 post → 不双重结算（每条 post 的 `review_log` 恰好 1 条终态） |
| BA-15 | 批量通过的 `engagement` 取值规则与单条通过**完全一致**（都用峰值） |
| BA-16 | 批量通过触发报销：3 条 `user_pay_reimburse` → 恰好 3 张报销单 |

> `BA-11` 是这一组最贵的一条：spec 明写「不做全事务回滚」，而实现里最顺手的
> 写法恰恰是「一个事务包住全部循环」。**故意把一条坏的混在末尾**——
> 若是全事务实现，前面已通过的也会一起被回滚，这条就红了。

---

## AP · 申诉 / 复议

| ID | 断言 |
|---|---|
| AP-01 | `rejected` 的作品申诉 → `201`，`status=appealed`，`appeal.status=pending` |
| AP-02 | `reason` 9 字 → `422`；**10 字 → `201`** |
| AP-03 | `reason` 501 字 → `422` |
| AP-04 | 对 `pending` 的作品申诉 → `409` |
| AP-05 | 对 `approved` 的作品申诉 → `409` |
| AP-06 | 对 `auto_approved` 的作品申诉 → `409` |
| AP-07 | 对已 `appealed` 的作品再申诉 → `409`（唯一约束即「仅一次」） |
| AP-08 | 对**别人**的作品申诉 → `403` |
| AP-09 | 申诉期间 `POST /api/posts/{id}/metrics` → **允许**（用户还在涨互动，裁决时取峰值） |
| AP-10 | `GET /api/me/appeals` 只返回本人的申诉 |
| AP-11 | `GET /api/admin/appeals?status=pending` 只返回待裁决的 |
| AP-12 | 非 admin 打 `GET /api/admin/appeals` → `403` |
| AP-13 | admin `accept` → `200`，post `status=approved`，`appeal.status=accepted`，`decided_at` 落库 |
| AP-14 | `accept` 写 `review_log(action="appeal_accept")` |
| AP-15 | `accept` 触发奖励结算与报销（两个接缝都被调用） |
| AP-16 | admin `reject` → post `status` 回到 `rejected`，`appeal.status=rejected` |
| AP-17 | `reject` 后**不可再次申诉**：再 `POST /appeal` → `409` |
| AP-18 | 已裁决的申诉再 `decide` → `409` |
| AP-19 | `action` 传非法值 → `422`；商户调 `decide` → `403` |

> `AP-17` 与 `AP-07` 拦的是两件事：`AP-07` 是「`appealed` 状态下不能再提」，
> `AP-17` 是「`appeal.status=rejected` 之后也不能再提」。若实现只按
> `appeal.post_id` 唯一来判，两条都绿；若实现只按 `post.status == "rejected"`
> 来判（这是最自然的写法），`AP-17` 会红——**被驳回的申诉会让 post 回到
> `rejected`，那个状态天然满足「可以申诉」**。这正是它值得单独测的原因。

---

## PS · 结算取值规则（**取峰值**）

| ID | 断言 |
|---|---|
| PS-01 | 3 条快照 `engagement` = 100 / 1800 / 900 → 结算接缝收到的值是 **1800** |
| PS-02 | 峰值只来自 `metric_snapshot`：把 `social_post` 上塞一个伪造的高值字段，不影响结果 |
| PS-03 | 审核发生在某条快照之后 → 该快照**仍参与**（`<= reviewed_at` 的全部） |
| PS-04 | 审核**之后**追加的快照 → 结算接缝**未被二次调用**（审核后不允许再涨档） |
| PS-05 | `ocr_result` 被 admin 采纳写成 `metric_snapshot` 后，峰值**重算**（取更晚的 `captured_at`） |
| PS-06 | 峰值恰有两条相同 → 取任意一条，结算值相同（不因实现选 `max(id)` 或 `max(captured_at)` 而变） |

> **本组钉的是「传给 05 的那个数」**，不是 `reward_grant.engagement` 列——
> 05 的表还没落地（见「已知取舍」1）。断言接缝参数与断言落库列在语义上等价：
> 05 要写的就是这个入参。05 落地后 `PS-01` 此行改为直接查库。

---

## RT · 报销触发（与 05 奖励同源同期）

| ID | 断言 |
|---|---|
| RT-01 | `user_pay_reimburse` 任务过审 → 落一张 `reimburse_claim`，`post_id` 唯一 |
| RT-02 | `merchant_pay` 任务过审 → **不产生**报销单 |
| RT-03 | 四个触发点各测一次：单条 `approve` / `batch-approve` 中的每条 / `auto_approve` / `appeal_accept` |
| RT-04 | 审批动作同时调用奖励接缝与报销，**一个失败不影响另一个**（让奖励接缝抛异常，报销仍落地） |
| RT-05 | `reimburse_claim_job` 记录本次报销覆盖的 job，`job_id` 唯一 |
| RT-06 | 商户额度减少、用户额度增加，金额相等；两条流水 `reimburse_out` / `reimburse_in` 互为 `counterparty_id` |
| RT-07 | 重复投递（单条通过 + `run_expiry` 竞争）→ 报销单**恰好 1 张**，重复那次返回 `None` 不报错 |
| RT-08 | 三重截断①：单用户上限 500、应报 1000 → 报 **500**，`status=capped` |
| RT-09 | 三重截断②：池子可用 300、应报 500 → 报 **300**，`status=capped` |
| RT-10 | 驳回（`reject` / `appeal_reject`）→ 预占释放、池子退回、**不产生报销单** |
| RT-11 | 驳回后申诉成功 → 报销照常落地，且 `post_id` 唯一保证**只报一次** |
| RT-12 | 预占被别的操作吃掉导致可用额不足 → **已预占的部分必须兑现**，缺口不报，`status=capped` |

> `RT-04` 是这一组最容易被写成「顺序执行、前一个抛异常就整个 500」的一条。
> spec 明写「两者独立计算、独立上限，一个失败不影响另一个」——奖励是 05 的
> 钱、报销是 07 的钱，让 05 的故障拖垮 07 的兑现，等于用户白垫。

---

## E2E · 申诉弹窗的前端交互约束（**本轮不做，见「已知取舍」2**）

| ID | 断言 | 状态 |
|---|---|---|
| E2E-01 | 弹窗文案含「仅有一次，不可撤销」 | ⬜ 等前端 |
| E2E-02 | 两个按钮初始 `disabled` | ⬜ 等前端 |
| E2E-03 | 弹出满 2 秒后两者同时可点 | ⬜ 等前端 |
| E2E-04 | 2 秒内点击 → 无反应，**不发起请求** | ⬜ 等前端 |
| E2E-05 | 「取消」关闭弹窗，**不消耗**申诉次数，可再次进入 | ⬜ 等前端 |
| E2E-06 | 「确定」且接口 `201` 才消耗次数 | ⬜ 等前端 |
| E2E-07 | 接口 `409` → 提示「已申诉过」并刷新状态 | ⬜ 等前端 |
| E2E-08 | 已申诉过的作品，入口置灰或隐藏 | ⬜ 等前端 |

---

## OW · 归属与越权（跨模块通用）

| ID | 断言 |
|---|---|
| OW-01 | `GET /api/me/posts` 只返回本人的作品，含最新快照与倒计时 |
| OW-02 | 客户打 `GET /api/merchant/reviews` → `403` |
| OW-03 | 别的商户对 A 的作品 `approve` / `reject` → `403`（不是 404） |
| OW-04 | 对**别人**的作品追加快照 → `403` |
| OW-05 | `GET /api/ocr/{post_id}` 取**别人**的识别结果 → `403`（`{id}` 取 **post_id**，见 SC 组脚注） |

> ⚠️ 三个 `OW-*` 都**不是 404**：把「不归你管」和「不存在」压成同一个答案，
> 排查方向会完全反过来（前者找归属、后者找数据）。`OW-03` 是 spec 的原话。

---

## 已知取舍

1. **`reward_grant`（05）尚未落地，本轮的「结算」只到接缝为止**。
   `app/services/reward.py` 的 `settle` 用 `to_regclass` 守卫，表不在就返回
   `None`，**不替 05 建表**（表结构该由 05 的 spec 定）。因此 `PS-*` 与
   `TO-08` 断言的是**传给接缝的 `engagement`**。这与 07 当年对
   `admin_action_log` 的处理是同一套：宁可空着，也不建一张形状不对的表。
2. **`E2E-01`~`E2E-08` 本轮不写，标记「等前端」留在计划里**（2026-09-15 用户确认）。
   仓库里**还没有前端**（`D:\project` 下只有 `backend/`、`specs/`、
   `docker-compose.yml`），没有可驱动的东西。在 pytest 里假装测 DOM 只会得到
   一批永远为真的断言。**这几条留在计划里不删**：它们是 spec 明写的契约，
   前端一落地就该补。届时用 Playwright 单开一个 `e2e/` 目录，不进 pytest 计数。
3. **`platform ↔ 域名` 白名单由本计划给出**（spec 只举了 `xhs` 一个例子），
   实现放在 `app/api/post_serializers.py` 的一张常量表里：

   | platform | 允许的域名 |
   |---|---|
   | `xhs` | `xiaohongshu.com`、`xhslink.com` |
   | `douyin` | `douyin.com`、`v.douyin.com`、`iesdouyin.com` |
   | `kuaishou` | `kuaishou.com`、`v.kuaishou.com` |
   | `bilibili` | `bilibili.com`、`b23.tv` |
   | `shipinhao` | `channels.weixin.qq.com`、`weixin.qq.com` |

   **只比对 host 的后缀**（含其子域），不比对 path/query——短链与分享链的
   path 形态无穷无尽，比了只会误伤。新增平台时改这张表 + 补一条 `SP-*`。
4. **`engagement` 不含 `shares`**。spec 的公式是 `likes + collects + comments`，
   `MS-02` 用 `10+5+3=18` 钉死这一点。`shares` 仍要采（审计与将来调权重要用），
   只是不进档位。
5. **「识别数字降级记告警」落到 04 自建的 `track_event` 表**（`SC-09`；
   2026-09-15 用户确认）。spec 只说「记录告警」，没指定落点。取 03 已有的
   `job_event` 表会跨模块耦合（那是一张按 `job_id` 记流水线阶段的表），
   故在 04 内新建 `track_event`（`post_id` + `kind` + `detail` + `created_at`）。
   若将来并入统一的告警中心，`SC-09` 只需改查询目标。
6. **`PS-05` 是 04 唯一一条「实现前就已绿」的用例**（116 条里那 1 条 passed）。
   它全程只调 `review.peak_engagement(...)`，而那是 03 那一批**已落地**的服务层
   代码——故它是**对既有代码的真实单元测试**，既不是假绿，也不兜 04 的新逻辑。
   它进「绿得不算数」清单：**别把它算作 04 端点已被覆盖的证据**。
7. **`BA-11` 只兜得住「循环中途炸掉」这一种写法**（反向验证实测）。把
   `batch_approve` 的「逐条提交」换成「一个事务包住整个循环」时，16 条 BA 用例
   **仍全绿**——因为该写法在功能上确实等价（都没有全批回滚）。真正让
   `BA-11`/`BA-03`/`BA-10` 变红的是「**先不预检、循环里才解引用**」那种写法
   （`AttributeError: 'NoneType' object has no attribute 'claim_id'`）。
   故 spec「不做全事务回滚」这条目前**由实现纪律保证，不由测试兜底**。
8. **`TO-*` 全部直接调 `auto_approve_expired(session)`**，不起调度器。
   「每 10 分钟」不在本进程内，测调度器等于测框架。
9. **`BA-14` 的「并发」用「先 `run_expiry` 再批量」的顺序来构造**，
   而不是真起两个协程。理由：`run_expiry` 与批量通过都在**同一个行锁**下
   改 `social_post.status`，真正的不变量是「终态只落一次」，
   顺序构造足以把它逼出来；真并发只会让失败原因变成 `DeadlockDetected`，
   反而看不清是哪一层的锅。
10. **`RT-12` 的原文字面口径结构上不可达，已改圈为等价性质（2026-09-15 实现期修订）**。
    spec 写的是「预占被别的操作吃掉导致可用额不足 → 已预占的必须兑现」，但要造
    「可用额 < 已预占额」得先破坏 `ck_quota_account_available`
    （`balance − reserved >= 0`），而该 CHECK 恰恰保证了 `balance >= reserved >=
    Σ reimburse_reserved`——那条路径走不到，硬造就得先把约束拆了，那测的就不是
    业务规则了。改圈为**可实现的等价性质**：`pool` 可用额取 `pool − used`，
    **不得再减 `reserved`**。用 `pool=200, reserved=200, used=0` 构造：写成
    `pool − used − reserved` 会得到 `pool_exhausted` 且一分不报，而那个 job 的
    预占本就是为了这次报销才锁的——`RT-12` 盯的就是这个减法。
11. **`GET /api/ocr/{post_id}` 的 `409 未完成`**：本模块的识别是打桩的（同步返回），
    故 `409` 分支只能靠「不装桩 + 让接缝停住」来造。取巧写法是直插一条
    `ocr_result` 但把 `confidence` 置空——**不这么做**：`confidence` 是
    `NOT NULL`，改库造非法态会让用例测的是「数据库会不会拦我」。
    改为让 stub 的 `ocr_metrics` 抛 `TimeoutError`，此时 `ocr_result` 尚未落库，
    `GET` 拿到的是「该 post 无识别结果」→ `409`（`SC-10`）。
12. **一个 `claim` 只对应一个作品**（spec 的「明确不做」），故 `SP-04` 的
    `422` 与 `SP-03` 的 `409` 都必须在。若将来允许多作品，先改 spec。
13. **`reimburse` 的 `covered` 里有四项，其中一项没有用例兜底**（反向验证实测）：
    `covered = min(base, per_user_remaining, pool_available, merchant.balance)`。
    `RT-06` 把 `merchant_balance` 设成 1000 而 `covered` 只取到 100，故
    `merchant.balance` 这一项**从未成为限制项**——去掉它 116 条仍全绿。
    已记入「绿得不算数」清单，待补一条「余额小于应报额」的用例。

---

## 落地记录（2026-09-15）

1. **红**：116 条用例先写、5 张表先建（`tracking.py`），13 个端点未开工，
   实测 `115 failed / 1 passed`——那 1 条绿是 `PS-05`（见「已知取舍」6）。
   红因逐条核过，**一律是「端点未实现 → 404」**，无一例环境错或测试自身 bug。
2. **绿**：`SP → MS → SC → TO → RV → BA → AP → PS → RT → OW` 逐组转绿，
   新增 `app/api/post_serializers.py` / `post.py` / `review.py` / `appeal.py`
   与 `review.ensure_reviewable()`，`main.py` 挂 3 个 router。**116 条全绿**。
3. **07 收尾**：`quota_service.reimburse(post_id)` 随 `social_post` 过审一并接通；
   `tests/test_token_reimburse.py` 那批**此前只读不写**的表首次被真实写入。
4. **反向验证**（改坏 → 看红 → 改回）：
   | 改坏处 | 变红的用例 | 结论 |
   |---|---|---|
   | 峰值 `max` → 最后一条 | `PS-01` / `PS-03` / `TO-08` / `BA-15` | 峰值口径被兜住 |
   | `reimburse` 里去掉 `reserved` 同步 | `RT-06`（另被 CHECK `ck_quota_account_available` 拦住） | 转账两侧同步被兜住 |
   | `submit_post` 的「已过审 422」与「同链接 409」对调 | `SP-03`（`assert 422 == 409`） | 判定顺序是契约，被兜住 |
   | 批量通过改成「一个事务包住整个循环」 | **无**（16 条 BA 全绿） | ⚠️ **不兜底**，见「已知取舍」7 |
   | 批量通过改成「不预检、循环里解引用」 | `BA-03` / `BA-10` / `BA-11` | 只兜得住这一种写法 |
5. **未做**：`E2E-01`~`E2E-08`（等前端，见「已知取舍」2）；「余额小于应报额」用例（见「已知取舍」13）。
6. 已更新 `CLAUDE.md` 工作记录。
