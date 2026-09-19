# 08 · 商家 / 用户前端（8001）

> 端口 **8001**。React 18 + Vite + TypeScript + Tailwind，与 `00-overview/spec.md` 的技术选型一致。
> 本轮（2026-09-15 用户确认）**只做**：登录页 + 商户首页 + 客户首页 + 首页上的「问题反馈」按钮。

---

## 一句话目标

> 商家和用户各自打开 8001，从登录页的**两个按钮**之一起步；登录后落到**各自不同**的首页（首页有核心数据摘要），并能从首页把「问题反馈」发给平台方。

---

## 页面承载方式

- 端口 **8001**，React 18 + Vite + TypeScript + Tailwind。
- **开发期**：Vite dev server（8001）。所有 `/api/*` 由 **Vite proxy 转发到 `http://localhost:8002`**。
  - 走 proxy 意味着浏览器只看见 8001 这一个源 → **浏览器不发跨源请求 → 8002 不需要加 CORS**。本方案不引入 CORS 中间件，减少一个开放面。
- **生产期**：Vite 构建产物由 8002 的 FastAPI 以静态文件挂载（与 06 后台同款做法），同源，proxy 自然消失。
- 后端基址由 `VITE_API_BASE` 控制，默认空串（走同源 / proxy）。

---

## 鉴权与令牌

- 登录：`POST /api/auth/login` → `{access_token, refresh_token, user}`
- **两个令牌都存 `localStorage`**。
  - ⚠️ **已知取舍**：`localStorage` 对 XSS 不设防。本轮不做 httpOnly Cookie——8001/8002 是跨源，且本地是 http，`SameSite=None; Secure` 的 Cookie 发不出去。**生产上线（HTTPS + 同源）前必须改成 httpOnly Cookie。**
- 请求拦截：所有请求带 `Authorization: Bearer <access_token>`。
- **`401` 处理**：用 `refresh_token` 调 `POST /api/auth/refresh` 换一次；成功则重放原请求；再失败 → 清空本地令牌并跳 `/login`。
- 退出登录：调 `POST /api/auth/logout`，无论成败都清空本地并跳 `/login`。

---

## 路由表

| 路径 | 页面 | 准入 |
|---|---|---|
| `/login` | 登录页 | 未登录；已登录则按角色直接跳首页 |
| `/` | 重定向 | 已登录 → `/merchant` 或 `/customer`；未登录 → `/login` |
| `/merchant` | 商户首页 | `role=merchant` |
| `/customer` | 客户首页 | `role=customer` |
| `*` | 404 页 | — |

**准入规则**

- 未登录访问受保护路由 → 跳 `/login`，**记住原目标，登录成功后回跳**。
- 角色不符（商户令牌访问 `/customer`）→ 静默跳回自己的首页，**不报错**。
- `admin` 登录 → 提示「管理员请使用平台后台」并清空登录态（本轮不做 admin 端）。

---

## 登录页（`/login`）

- 一页两态：**先选身份，再填表单**。
- 两个大按钮：**商家登录** / **用户登录**。点任一 → 展开同一个表单（账号 + 密码），顶部徽标显示当前所选身份，可一键切回重选。
- 未选身份就提交 → 提示「请先选择登录身份」，不发请求。
- 提交 `POST /api/auth/login`。
- **身份校验（关键）**：返回的 `user.role` 与所选按钮不符 →
  **不写入登录态**，提示「该账号是<商户 / 客户>账号，请用<商户 / 客户>登录」。
- `admin` 账号 → 不写入登录态，提示「管理员请使用平台后台」。
- 错误文案：`401` →「账号或密码错误」；`403` → 按后端 `message` 显示（已注销 / 已封禁）。
- 提交中：按钮禁用 + loading，**防重复提交**。

---

## 商户首页（`/merchant`）

自上而下：

1. **顶栏**：昵称 + 「商户」徽章 + 退出登录。
2. **数据摘要**（3 项，实时拉取）：

   | 卡片 | 端点 | 取数 |
   |---|---|---|
   | 我的任务 | `GET /api/merchant/tasks` | `total` |
   | 待我审核 | `GET /api/merchant/reviews` | `items` 的条数（该端点**不分页**、不返回 `total`，见 04 实现） |
   | 额度余额 | `GET /api/merchant/quota` | `balance` |

> 统一口径：**优先 `total`，没有就数 `items` 的长度**（前端 `totalOf()`）。
> 哪天某个列表端点改了分页，才需要回来把它钉成 `total`。

3. **「问题反馈」按钮**（常驻，移动端悬浮右下，桌面端在摘要区旁）。
4. 空态：三项全 0 → 显示「还没有数据」。

## 客户首页（`/customer`）

1. **顶栏**：昵称 + 「客户」徽章 + 退出登录。
2. **数据摘要**（3 项）：

   | 卡片 | 端点 | 取数 |
   |---|---|---|
   | 我领取的任务 | `GET /api/me/claims` | `total` |
   | 我的积分 | `GET /api/me/points` | `balance` |
   | 我的券 | `GET /api/me/coupons` | `total` |

3. **「问题反馈」按钮**。
4. 空态同上。

> 两个首页**结构上必须不同**：商户端偏「经营」（任务 / 审核 / 额度，横排摘要行）；客户端偏「我的所得」（领取 / 积分 / 券，卡片堆叠）。不得只换主色。

---

## 反馈表单

- 从首页按钮打开（弹层 / 抽屉）。
- 字段：
  | 字段 | 控件 | 约束 |
  |---|---|---|
  | 类型 | 三选一 | 功能异常 / 功能建议 / 其他，默认「功能建议」 |
  | 内容 | 多行文本 | 5 ~ 500 字，**实时字数**；< 5 字时禁用提交并提示 |
  | 联系方式 | 单行文本 | 选填，≤ 128 字 |
- 提交 `POST /api/feedback`，**请求体绝不带 `role` / `user_id`**（服务端自己推导）。
- 成功 → 关闭弹层、清空内容、提示「已收到，感谢你的建议」。
- `422` → 字段级错误提示；`401` → 跳登录页。
- 提交中按钮禁用，防重复提交。

---

## 设计基调（hallmark）

- **受众**：本地生活商户（非技术小老板）+ 普通消费者。
- **基调**：**Modern-minimal**（2026-09-15 用户已选）。
- **移动优先**：以 `375px` 为主设计宽度（H5）。`768 / 1280` 上居中限宽。
- **响应式地板**（2026-09-15 用户已选「按 hallmark 做，只保证不崩」）：320 / 375 / 414 / 768 / 1280 五档**无页面级横向滚动**；窄屏侧栏收起，宽内容在容器内滚。
- 全部颜色与字体走 `tokens.css` 的**命名 token**，禁止内联色值 / 裸 `font-family`。
- 标题一律**正体**（`font-style: normal`），不用斜体做强调。
- ⚠️ **不编造数据**：任何指标没有真实值时显示 `—`，绝不写死假数字、假评价。

---

## 边界（每条之后会变成一条测试）

### 路由与准入
- 未登录访问 `/merchant` → 跳 `/login`
- 未登录访问 `/customer` → 跳 `/login`
- 未登录访问 `/` → 跳 `/login`
- 商户令牌访问 `/customer` → 跳 `/merchant`
- 客户令牌访问 `/merchant` → 跳 `/customer`
- 已登录访问 `/login` → 直接跳自己的首页
- 未登录被拦后登录成功 → **回跳到原目标**（不是一律跳首页）
- 未知路径 → 404 页

### 登录
- 默认不选中任何身份；未选身份提交 → 提示且**不发请求**
- 用商户账号点「用户登录」→ 提示身份不符，且 `localStorage` **没有**写入令牌
- `admin` 账号登录 → 提示走后台，且**没有**写入令牌
- 商户账号点「商家登录」→ 落 `/merchant`；客户账号点「用户登录」→ 落 `/customer`
- `401` → 显示「账号或密码错误」
- 提交中按钮禁用（连点两次只发一个请求）

### 令牌
- 刷新页面后登录态仍在（`localStorage`）
- `access_token` 过期 → 自动 refresh 一次并重放原请求，页面不跳登录
- `refresh_token` 也失效 → 清空本地并跳 `/login`
- 退出登录 → 本地令牌清空，且回不到受保护页

### 首页
- 某个摘要接口 `500` → **那一张卡显示 `—`，其余卡照常显示**（一个接口挂不得拖垮整页）
- 三个接口全挂 → 显示错误态 + 重试按钮，**不白屏**
- 空数据 → 显示「还没有数据」，不是 `NaN` / `undefined` / 空白
- 页面上不出现任何 `role` / `user_id` 输入项

### 反馈
- 内容 4 字 → 提交按钮禁用（前端拦）；后端仍独立返回 `422`（两端都要测）
- 内容 500 字 → 可提交；501 字 → 前端拦
- 提交成功 → 弹层关闭、内容清空、出现成功提示
- 提交中按钮禁用（连点两次只发一个请求）
- 请求体里**没有** `role` / `user_id` 字段
- 提交 `401` → 跳登录页
- 商户提交后，后端列表里该条 `role='merchant'`；客户提交后为 `'customer'`

---

## 测试方式

- **Vitest + @testing-library/react + jsdom**（与 Vite 同生态）。
- 覆盖：路由准入表、身份校验、令牌刷新与 `401` 处理、摘要卡降级、反馈表单校验与提交体形状。
- 后端契约（响应字段名）在测试里以 **fixture 固定形状**断言，不依赖后端在线。
- 视觉 / 排版 / 响应式：**手工验收**（五档宽度截图），不进自动化。

---

## 明确不做（本轮）

- ❌ 商户端 / 客户端的**功能页**（发布任务、审核、内容工坊、数据回传、奖励商城、额度充值……）——下一轮
- ❌ 06 平台管理后台前端（`/admin`）——另一轮
- ❌ 注册页（账号由种子脚本 / 后台创建，前端不做注册入口）
- ❌ 找回密码 / 改密码入口（后端 `POST /api/me/password` 已有，前端本轮不做）
- ❌ 「我的历史反馈」列表（只提交，不回看）
- ❌ 深色模式 / 主题切换
- ❌ 国际化（只做简体中文）
- ❌ E2E 自动化（Playwright）——本轮用单测 + 手工验收
- ❌ 前端不引入状态管理库（Redux / Zustand）——本轮页面少，Context + hooks 足够

---

# 追加 A（2026-09-16）：商户侧「发布 + 管理任务」

> 2026-09-16 用户确认：8001 这一轮先做**商户侧：发布 + 管理任务**。
> 后端 02 的商户端点**早已 127 绿**，本轮**只写页面**，接既有契约。
> 上一轮的登录页 / 两个首页 / 反馈弹层**不动**（54 条继续绿）。

## 一句话目标

> 商户在 8001 上完成 `建任务 → 配奖励阶梯 → 发布 → 暂停/关闭 → 删除/恢复` 的全程，
> **不用回后台**；已发布的任务只能改它允许改的那几个字段。

## 页面与路由

三条路由，全在 `Guard role="merchant"` 之内（角色不符静默跳 `/customer`，不报错）。

| 路径 | 页面 | 说明 |
|---|---|---|
| `/merchant/tasks` | 任务列表 | 「进行中」/「回收站」两个页签，页签存 URL（`?tab=trash`） |
| `/merchant/tasks/new` | 新建任务 | 分区表单，底部常驻动作条（保存草稿 / 发布） |
| `/merchant/tasks/:id` | 任务详情 | 按 `status` 决定哪些字段可编辑、有哪些动作 |

商户首页已有的「我的任务」摘要卡**改成可点**，进 `/merchant/tasks`。

## 端点（全部已落地，本轮只消费）

| 动作 | 端点 |
|---|---|
| 建任务（草稿） | `POST /api/merchant/tasks` → `201 {task}` |
| 我的任务 | `GET /api/merchant/tasks` → `{items,total}`（**不吃查询参数、不分页**） |
| 回收站 | `GET /api/merchant/tasks/trash` → `{items,total}` |
| 详情 + 奖励规则 | **`GET /api/tasks/{id}`** → `{task, rule, merchant, claimed_by_me}` |
| 改任务 | `PATCH /api/merchant/tasks/{id}` → `{task}` |
| 自动保存草稿 | `PUT /api/merchant/tasks/{id}/draft` → `{task,saved_at}` |
| 校验阶梯（不落库） | `POST /api/merchant/reward-rules/{task_id}/validate` → `{valid:true}` |
| 存阶梯 | `PUT /api/merchant/reward-rules/{task_id}` → `{rule}` |
| 发布 / 暂停 / 关闭 | `POST /api/merchant/tasks/{id}/publish` `/pause` `/close` → `{status}` |
| 删除（进回收站） | `DELETE /api/merchant/tasks/{id}` → `204` |
| 恢复 | `POST /api/merchant/tasks/{id}/restore` → `{task}` |

## 数据形状（前端只读这些键）

`task`：`id` · `title` · `description` · `category` · `cover_url` · `requirement` ·
`tags` · `start_at` · `end_at` · `quota`（`null` = 不限）· `claimed_count` ·
`pay_mode`（`merchant_pay` / `user_pay_reimburse`）· `reimburse_pool` ·
`reimburse_per_user_limit` · `status`（`draft`/`published`/`paused`/`closed`）

`rule`：`metric`（恒 `engagement`）· `tiers`（`[{min,max,reward}]`，末档 `max:null`）·
`max_reward_per_user`（可为 `null`）

`reward` 的合法键：`cash` / `points`（整数，非负）· `coupon_id`（整数）· `benefit`（文字）。

## 五个「写错了屏幕上看着也对」的硬点

1. **详情页必须用 `GET /api/tasks/{id}`，不能用 `GET /api/merchant/tasks/{id}`。**
   后者只回 `{task}`，不带 `rule`；前者一次给齐 `{task, rule, merchant, claimed_by_me}`。
   用错了，刷新页面阶梯就变空，而屏幕上一切正常。
   （代价：`GET /api/tasks/{id}` 对 `deleted_at` 非空的任务 404，所以**回收站不进详情**。）

   > **订正（2026-09-16，核后端源码时发现）**：这里原先写「全项目**没有**读奖励
   > 规则的端点」——**不准确**。`GET /api/merchant/reward-rules/{task_id}`
   > **是存在的**（`backend/app/api/reward.py`）。
   > 规则本身不变：详情页用 `GET /api/tasks/{id}`，一次拿齐即可，
   > **别再多打一个请求**（`MR-07` 钉的正是「复用详情 payload」，
   > 测试里只给 `GET /api/tasks/{id}` 打了桩，多打一个就 `599`）。

2. **PATCH 只发「要改的」字段。** 后端读 `model_dump(exclude_unset=True)`：
   把整个 task 原样发回去（`title` 一字未改）也会撞上 `409 任务已发布，不允许修改 title`。
   屏幕上「我什么都没改」和「我发全了」长得一样。

3. **时间必须是带偏移的 ISO。** `<input type="datetime-local">` 给的是
   `2026-09-20T10:00`（无时区）。裸发过去，Pydantic 解出 **naive** datetime，
   后端拿它和 aware 的 `utcnow()` 比较 → `TypeError` → **500**（不是 422）。
   前端一律按**北京时间**当 wall clock 转换：发 `toISOString()`，收回来再转回北京时间。

4. **`validate` 的违规不是 `detail` 字符串。** 失败体是
   `{"detail": {"violations": [...]}}`——`ApiError.detail` 只认字符串，这里拿到 `null`。
   要单独读 `payload.detail.violations` 并**逐条**列出（否则只剩一句「保存失败」，商户不知道该改哪一档）。

5. **`validate` 绿 ≠ 已保存。** `validate` 不落库；要真的存下来必须再 `PUT`。
   只调 validate 的实现，重进页面阶梯是空的。

## 页面细节

### 列表 `/merchant/tasks`

- 每行：标题 · 状态徽章（草稿/已发布/已暂停/已关闭）· 时间窗（北京时间）·
  名额 `claimed_count / quota`（`quota=null` → 「不限」）· 付费模式徽章。
- `user_pay_reimburse` 的行**必须**标出「需垫付」——这是商户自己选的口径，他得看得见。
- 「进行中」= `GET /api/merchant/tasks`；「回收站」= `GET /api/merchant/tasks/trash`。
- 回收站行**只给「恢复」**：删除已生效，编辑无意义。

### 新建 `/merchant/tasks/new`

- 分区：`基本信息`（标题/品类/描述/封面/要求/标签）· `时间与名额` ·
  `付费方式` · `奖励阶梯`。
- 必填：标题（2~64）· 描述（10~2000）· 品类（1~32）· 开始/结束时间。
  标签 ≤5 个、每个 ≤16 字；名额 `null` 或 ≥1。
- **时间默认填「现在」**（可改），输入框按北京时间。
- 付费方式默认 `merchant_pay`；选 `user_pay_reimburse` 才展开
  报销池与单用户上限两个整数输入，并校验 `0 ≤ 上限 ≤ 池子`。
- 底部两个动作：
  - **保存草稿** → `POST /api/merchant/tasks` → 跳 `/merchant/tasks/{id}`（之后靠自动保存）
  - **直接发布** → `POST` → `PUT reward-rules` → `POST publish`，**三步按序**，任一步失败就地停住并显示后端文案
- ⚠️ 后端**没有「空草稿」**：`POST` 就要求 title/description/category/时间齐全。
  所以「保存草稿」前必须先把这几项凑齐，凑不齐就拦在前面、一次请求都不发。

### 详情 `/merchant/tasks/:id`

按状态决定可编辑面（**这是本页的全部复杂度**）：

| `status` | 可编辑 | 动作 |
|---|---|---|
| `draft` | 全部字段 + 阶梯 | 保存 · 自动保存 · 发布 · 删除 |
| `published` | 仅 `description` / `end_at` / `requirement` / `quota` | 暂停 · 关闭（**不能删**） |
| `paused` | 同 `published` | 发布（恢复）· 关闭 · 删除 |
| `closed` | 全部只读 | 删除 |

- 只读字段**不进 PATCH 体**（硬点 2）。
- 动作成功后**重取详情**——屏幕上的状态可能已经被别处改过。
- 删除是两步：先点「删除」→ 按钮变「确认删除」（不发请求）→ 再点才 `DELETE`。
  （jsdom 里 `window.confirm` 不可用，行内二次确认才测得住。）
- 非法 `:id`（`/merchant/tasks/abc`）→「任务不存在」，**不发请求**。
- 回收站里的任务（`deleted_at` 非空）打这个页 → `404` → 「任务不存在」。

### 奖励阶梯（新建页与详情页**同一个组件**）

- 档位可增删；第一档 `min` 锁 0，末档 `max` 锁 `null`（无上限）。
- 每档 `reward` 至少填 `cash` / `points` / `benefit` 之一，整数不得为负、不接小数。
- 「校验阶梯」→ `POST .../validate` → 违规**逐条**列在阶梯区。
- 「保存阶梯」→ `PUT /reward-rules/{id}`。
- 未配置规则的任务：阶梯区空 + 一句「未配置奖励阶梯，不能发布」，**发布按钮 disabled**。
- `metric` 只读显示「互动量（点赞 + 收藏 + 评论）」，无选择器。

## 边界（每条之后会变成一条测试）

### MT 路由与入口（5 条）
- `MT-01` 未登录访问 `/merchant/tasks` → 落 `/login`，且登录成功后**回跳 `/merchant/tasks`**
- `MT-02` 客户令牌访问 `/merchant/tasks` → 静默跳 `/customer`，不报错
- `MT-03` 商户令牌访问 → 停在 `/merchant/tasks`，不跳走
- `MT-04` 商户首页「我的任务」卡片可点 → `/merchant/tasks`
- `MT-05` 注销后 `localStorage` 无令牌，再访问受保护页仍被拦

### ML 列表与回收站（9 条）
- `ML-01` 首次进页 → `GET /api/merchant/tasks`，**不带任何查询参数**
- `ML-02` 每行五要素：标题 · 状态徽章 · 时间窗 · `claimed_count/quota` · 付费模式徽章
- `ML-03` `quota=null` → 名额显示「不限」（不是 `0/0`、不是 `NaN`）
- `ML-04` 空列表 → 「还没有任务」
- `ML-05` 切「回收站」→ 打 `/tasks/trash`，且 URL 变 `?tab=trash`（刷新仍停在回收站）
- `ML-06` 回收站行只有「恢复」，没有「编辑 / 删除」
- `ML-07` 列表 `500` → 错误态 + 重试按钮，不白屏；点重试重发一次
- `ML-08` 删除成功后该行从「进行中」消失（**不重取整个列表**）
- `ML-09` 恢复成功后该行从回收站消失

### MC 新建（12 条）
- `MC-01` 标题 1 字 → 前端拦，**一次 `POST` 都不发**
- `MC-02` 描述 9 字 → 拦；10 字 → 放行
- `MC-03` `end_at == start_at` → 拦（含相等）
- `MC-04` 名额填 `0` → 拦；留空 → 体里 `quota` 为 `null`
- `MC-05` 标签 6 个 → 拦；单个标签 17 字 → 拦；5 个 × 16 字 → 放行
- `MC-06` 提交体里的 `start_at` / `end_at` 是**带 `Z` 或 `+08:00` 的 ISO**，不是裸串
- `MC-07` 服务端给的 UTC ISO 回填到时间输入框时是**北京时间** wall clock
- `MC-08` 「保存草稿」→ 恰好 1 次 `POST /api/merchant/tasks`，请求体**不含** `reward` 相关键
- `MC-09` `POST` 返回字段级 `422` → 后端那句 `msg` 落在对应字段下方（不是整页一句「保存失败」）
- `MC-10` 「直接发布」→ `POST` → `PUT reward-rules` → `POST publish`，**恰好各 1 次且按此顺序**
- `MC-11` `POST` 403「仅限商户」→ 就地显示后端文案
- `MC-12` 付费方式选 `user_pay_reimburse` → 体里**同时**带 `reimburse_pool` 与 `reimburse_per_user_limit`；上限 > 池子 → 前端拦

### ME 编辑（9 条）
- `ME-01` `draft` 详情可改全部字段；改标题保存 → `PATCH` 体里**只有 `title`**
- `ME-02` `published` 详情：标题 / 品类 / 标签 / `start_at` 为只读，且**不出现在 PATCH 体里**
- `ME-03` `published` 改描述 → `PATCH` 体里**只有 `description`**
- `ME-04` `published` 改 `quota` → 允许发出
- `ME-05` `PATCH` `409` → **原样**显示后端 `detail`，并重取详情
- `ME-06` `PATCH` `422`（`quota` 小于已领取）→ 就地提示，不静默吞
- `ME-07` `closed` 任务 → 无「保存」，字段全只读
- `ME-08` 详情 `404` → 「任务不存在」，不白屏
- `ME-09` 详情 `500` → 错误态 + 重试；非法 `:id` 不发请求

### MR 奖励阶梯（10 条）
- `MR-01` 默认一档 `min=0` / `max=null`，且 `min` 不可编辑
- `MR-02` 可增档、可删档；末档 `max` 恒为 `null`
- `MR-03` 「校验」→ `POST /reward-rules/{id}/validate`，体含 `metric` / `tiers` / `max_reward_per_user`
- `MR-04` `422` 的 `detail.violations` 是数组 → **逐条**列在阶梯区
- `MR-05` 合法阶梯 → 显示通过，且「保存阶梯」可用
- `MR-06` 「保存阶梯」→ `PUT /reward-rules/{id}` → `200 {rule}`
- `MR-07` 刷新详情页 → 阶梯从 `GET /api/tasks/{id}` 的 `rule` 回填（不是内存缓存）
- `MR-08` `rule=null` → 阶梯区空 + 「未配置奖励阶梯，不能发布」+ 发布按钮 `disabled`
- `MR-09` `reward` 三项全空 / `cash` 填小数 → 前端拦
- `MR-10` `metric` 只读，页面上**没有** metric 选择器

### MP 发布与状态机（12 条）
- `MP-01` `draft` 有规则 → 「发布」→ `POST /publish`，状态变「已发布」
- `MP-02` 无规则时强行发 → `409 尚未配置奖励规则，不能发布` **原样**显示
- `MP-03` `end_at` 已过 → `422 任务已过期，不能发布` 原样显示，**状态不变**
- `MP-04` `402 商户可用额度不足以锁定报销池` 原样显示，**状态不得变**
- `MP-05` `published` → 「暂停」→ 状态「已暂停」
- `MP-06` `paused` → 「发布」→ 恢复「已发布」
- `MP-07` `published` / `paused` → 「关闭」→ 「已关闭」
- `MP-08` `closed` → 没有「暂停 / 关闭 / 发布」三个按钮
- `MP-09` 删除是两步：首点只变「确认删除」，**一次 `DELETE` 都不发**；再点才发
- `MP-10` 删除 `published` → `409 已发布的任务需先关闭才能删除` 原样显示
- `MP-11` 删除有有效领取 → `409` 原样显示
- `MP-12` 动作成功后**重取详情**（`GET /api/tasks/{id}` 出现第 2 次）

### MS 自动保存（5 条）
- `MS-01` `draft` 改描述后停止输入 → `PUT /tasks/{id}/draft`，体里**只有 `description`**
- `MS-02` 连打 5 个字只发**一次**请求（防抖）
- `MS-03` `published` 详情**不触发**自动保存（那会 `409`）
- `MS-04` 自动保存失败 → 顶部「未保存」提示，**输入框内容不清空**
- `MS-05` 成功后显示「已保存 HH:mm」（北京时间，取自 `saved_at`）

### MX 降级与不编造（3 条）
- `MX-01` 空值一律显示 `—`；整页文本不出现 `NaN` / `undefined` / `null`
- `MX-02` 页面上**没有** `merchant_id` / 商户归属之类的输入项
- `MX-03` `401` 走 `client.ts` 既有刷新 → 再失败清空 → 守卫送 `/login`，**不就地报错**

> 合计 **65 条**（`MT` 5 + `ML` 9 + `MC` 12 + `ME` 9 + `MR` 10 + `MP` 12 + `MS` 5 + `MX` 3）。

## 明确不做（本轮）

- ❌ **客户侧**的领取 / 我的任务 / 放弃 —— 下一轮
- ❌ 商户审核队列（`GET /api/merchant/reviews` + 通过 / 驳回）——属 04，下一轮
- ❌ 商户额度充值 / 报销台账（07 的商户端点）——另做
- ❌ 券选择器：`reward.coupon_id` 需要 05 的商户券列表接口，本轮只支持 `cash` / `points` / `benefit`
- ❌ 任务模板 / 复制任务 / 列表批量操作
- ❌ 任务数据看板（领取人数趋势、转化）——后端本轮没有对应聚合端点
- ❌ 排序选项：列表固定后端给的 `created_at` 倒序，不给商户选
- ❌ 回收站的彻底删除（软删记录只能恢复，spec 02 明写）
- ❌ 客户侧任务卡片上的「需先垫付，过审后报销」标注 —— 那是客户轮的事
- ❌ 富文本描述 / 图片上传组件（`cover_url` 本轮只填 URL 字符串）
- ❌ 深色模式 / 主题切换 / 国际化 / 状态管理库（同上一轮）

---

# 追加 B（2026-09-16）：客户侧外壳 + 视觉系统升级

> 2026-09-16 用户确认：「**先改用户端的界面，这个界面太丑了**」，参考
> `https://www.deepseek.com/` 的**布局**；颜色「找一个合适的就行」。
>
> 用户给的核心映射：**中间那个输入框就是本项目的主题**（输入图片 + 商户信息 →
> AI 按 workflow 出图和文案 → 下载发社媒）；DeepSeek 的「和 DeepSeek 对话」
> 与「使用 API 开放平台」两个入口卡，对应本项目的「**我领取的任务**」与
> 「**我的奖励**」，后者点进去是**商家**，再点商家看到**在该商家的积分和券**。

## 前置（不满足就不能开工）

- **03 追加 A（素材上传端点）先绿**
- **05 追加 A（按商家分组的我的奖励）先绿**

## 视觉系统升级（`frontend/src/tokens.css`）

**底色与主色不动**：沿用既有的暖纸（`--color-paper` / oklch hue 50）+ 珊瑚
（`--color-accent`）。换的是**布局与间距的骨架**，不是品牌色。理由：现有一套
自洽的 token 是商户侧 65 条用例的外观基线，换色会同时改掉商户侧的观感，收益不成正比。

**macrostructure 换代**：客户首页从 `Workbench` 换成新的
`/* Hallmark · macrostructure: 03 Marquee Hero (composer 变体) */`。
商户侧三页**仍是 `Workbench`**——同一 app 里不同页面用不同 macrostructure 是允许的；
hallmark 的多样性规则只禁止**连续两次输出撞同一个**。

**新增 token（必须先在 `tokens.css` 立好，再引用——slop-test gate 48）**：

| token | 值 | 用途 |
|---|---|---|
| `--color-glass` | `oklch(100% 0 0 / 0.55)` | 玻璃面（只给创作台与浮层） |
| `--color-glass-border` | `oklch(100% 0 0 / 0.8)` | 玻璃面描边（亮白） |
| `--color-glass-hover` | `oklch(100% 0 0 / 0.72)` | 聚焦 / hover 底色 |
| `--blur-glass` | `12px` | 玻璃面的 `backdrop-filter` |
| `--radius-panel` | `16px` | 面板 / 弹层 |
| `--radius-hero` | `24px` | 创作台 |
| `--spacing-xl` | `2.5rem` | 区块间距 |
| `--spacing-2xl` | `4rem` | 英雄区上下留白 |
| `--text-hero` | `clamp(1.75rem, 3.2vw + 1rem, 2.375rem)` | 标语（上限 38px） |
| `--tracking-hero` | `0.2em` | **中文标语字距**（这是参考设计的骨相，不是装饰） |
| `--lh-hero` | `1.55` | 标语行高 |
| `--weight-hero` | `400` | 标语**字重**——轻，不是粗 |
| `--dur-reveal` | `400ms` | 首屏淡入上移 |

- 标语一律**正体 400 字重 + 宽字距**，`font-style: normal`（gate 38a）。
- 玻璃面**只用于**创作台与弹层；列表、表格、商户侧页面一律不用（否则整页变成一团模糊）。
- 首屏入场：透明度 0→1 + 上移 16px，`prefers-reduced-motion: reduce` 时**直接显示**、不动。

## 路由表（追加）

全在 `Guard role="customer"` 之内（角色不符静默跳 `/merchant`，不报错）。

| 路径 | 页面 | 说明 |
|---|---|---|
| `/customer` | 客户首页 | 标语 + 创作台 + 两个入口卡（**结构重做**） |
| `/customer/tasks` | 找任务 | 列表 + 关键词搜索 + 领取 |
| `/customer/tasks/:id` | 任务详情 | 奖励阶梯 + 商家信息 + 领取 |
| `/customer/claims` | 我领取的任务 | 领取列表 + 去创作 / 放弃 |
| `/customer/rewards` | 我的奖励 | 商家列表（带该商家积分与券数） |
| `/customer/rewards/:merchantId` | 该商家的积分与券 | 积分大字 + 券列表 |

## 客户首页（`/customer`）——**结构重做**

自上而下：

1. 顶栏（沿用既有 `AppBar`）。
2. **标语**：一行，居中，`--text-hero` + `--tracking-hero` + 字重 400。
   文案定一个短的（如「让每个小店都有好内容」），**不编造数据、不写假指标**。
3. **创作台**（玻璃卡，`--radius-hero`，`max-width: 720px`，居中）：
   - 上半：`<textarea>`，`rows=2`，`maxLength=2000`（与 `prompt_draft.raw_prompt` 的上限一致），
     占位文案「说说你想做什么内容」。无边框，聚焦时整卡描边变亮。
   - 下半：控制行 `justify-between`——左「图片」按钮（带已选张数 `3/9`），右「开始创作」主按钮。
4. **两个入口卡**（并排；窄屏堆叠）：

   | 卡 | 取数 | 卡上的数 |
   |---|---|---|
   | 我领取的任务 | `GET /api/me/claims` | `total` |
   | 我的奖励 | `GET /api/me/points` + `GET /api/me/coupons` | `balance` 积分 · `total` 张券 |

   > 原首页的**三张摘要卡合并成两张**（「我的券」并入「我的奖励」）。
   > 这是**有意改结构**，不是缩小字号——原客户首页的用例要**按新结构改写**，
   > 不是删掉。商户首页三卡**不动**。

5. 「问题反馈」按钮（沿用既有 `FeedbackLauncher`）。

### 创作台的「开始创作」流程（**顺序写死**）

1. 未选任务 → 打开「选择任务」弹层，数据来自 `GET /api/me/claims`。
2. `items` 为空 → **不弹层**，就地提示「先领一个任务」+ 一个跳 `/customer/tasks` 的按钮，
   **一个请求都不发**。
3. 弹层里选一个领取 → 逐张上传已选图片（**串行**）：`POST /api/uploads`。
   任一张失败 → 就地停住，显示后端文案，**后面的不发**。
4. 全部上传成功 → `POST /api/jobs {task_id, kind, assets:[{url,mime,size_bytes}], claim_id}`
   → `201` → 跳 `/customer/studio/{job_id}`。
5. 产出类型（创作台两个按钮，**可切换**）：
   - **写文案** → `kind=copy`（默认选中，`aria-pressed` / `composer__kind--active`）
   - **做视频** → `kind=video`（可选中；**不再 disabled**）
   - 旁注：无视频平台 Key 时仍可选题（建 job 允许），生成阶段若 Key 空会失败——旁注文案用
     「视频需在服务端配置 Key」类短提示，**不用**笼统「暂未开放」误伤文案。
   - 点选只改本地 `kind` 状态，**不发**请求；真正带 `kind` 的是随后的 `POST /api/jobs`。
   **本轮没有图片产出**（后端 `JOB_KINDS` 只有 `copy`/`video`）。
- `task` 的 query 参数可预选任务：`/customer?task=12` → 创作台直接锁到该任务（来自「去创作」入口）。
- 选中任务后，创作台**上方显示该任务的商户名与标题**（只读，来自 `GET /api/tasks/{id}`）——
  商户信息由任务带来，**不叫用户手填**。

## 找任务（`/customer/tasks`）

- `GET /api/tasks?keyword&tags&page&size`（该端点**免登录**）。
- 每张卡：标题 · **商户名** · 品类 · 时间窗（北京时间）· 名额 `claimed_count/quota` · 付费模式徽章。
- `quota=null` → 显示「不限」（复用 `lib/tasks.ts` 的 `quotaLabel`，**不要新写一份**）。
- `user_pay_reimburse` 的卡**必须**标「需垫付，过审后报销」——用户要自掏腰包，得看得见。
- 领取 `POST /api/tasks/{task_id}/claim` → `201 {claim}`；已领过 → 后端 `409` **原样**显示。

## 任务详情（`/customer/tasks/:id`）

- `GET /api/tasks/{task_id}` → `{task, rule, merchant, claimed_by_me}`（**一次拿齐**，不打第二个请求）。
- 展示奖励阶梯（`rule.tiers`，`metric` 只读显示「互动量（点赞 + 收藏 + 评论）」）+ 商家信息。
- `claimed_by_me` 决定按钮：未领 → 「领取」；已领 → 「去创作」（跳 `/customer?task={id}`）。
- `pay_mode=user_pay_reimburse` 时另拉 `GET /api/me/reimburse-preview?task_id=`
  显示「我垫付了多少 / 能报多少 / 池子还剩多少」；`403`（未领取）→ **整块不显示**，不报错。

## 我领取的任务（`/customer/claims`）

- `GET /api/me/claims?status` → `{items,total}`，每项 = `claim_public` + 内嵌 `task`。
- 每行：任务标题 · 商户名 · 领取时间（北京时间）· `claim.status` 徽章。
- 「去创作」→ 跳 `/customer?task={task_id}`（复用创作台，不为它单独做页面）。
- 「放弃」→ `DELETE /api/me/claims/{claim_id}` → `204`，**两步确认**（首点只变「确认放弃」，不发请求）。

## 我的奖励（`/customer/rewards`）

- `GET /api/me/rewards/by-merchant?page&size`。
- 每张卡：商家 `logo_url`（无则用商家名首字做色块占位，**不是破图**）· 商家名 ·
  `points_earned` 积分 · `coupon_unused / coupon_total` 张券。
- 点卡 → `/customer/rewards/{merchant_id}`。
- 空态「还没有奖励」。
- ⚠️ 商家名为 `""` 时显示 `—`（**不编造**）。

## 该商家的积分与券（`/customer/rewards/:merchantId`）

- `GET /api/me/rewards/by-merchant/{merchant_id}`。
- 上半：商家名 + `points_earned`（大字，`--text-2xl` 量级）+ 一句「在该商家挣到的积分」。
- 下半：券列表，每张显示 `coupon.name` · 面额（`type=cash_off` → 「满 X 减 Y」；
  `discount` → 「X 折」；`gift` → 「赠品」）· `user_coupon.status` 徽章 · `expire_at`（北京时间）。
- `404` → 「这个商家还没有你的记录」；券为空 → 「还没有这个商家的券」。

## 边界（每条之后会变成一条测试）

### CT 路由与准入（6 条）
- `CT-01` 未登录访问 `/customer/tasks` → 落 `/login`，登录后**回跳原目标**
- `CT-02` 商户令牌访问 `/customer/rewards` → 静默跳 `/merchant`，不报错
- `CT-03` 客户令牌访问以上五条路由 → 都停在原地
- `CT-04` `/customer/rewards/abc` → 「商家不存在」，**不发请求**
- `CT-05` 客户令牌访问 `/merchant/tasks` → 仍静默跳 `/customer`（既有行为不回归）
- `CT-06` 顶栏在客户侧五页都在，「退出登录」可用

### CH 客户首页（12 条）
- `CH-01` 首页有标语、创作台、两个入口卡（不是三张摘要卡）
- `CH-02` 「我领取的任务」卡上的数 = `GET /api/me/claims` 的 `total`
- `CH-03` 「我的奖励」卡同时显示 `GET /api/me/points` 的 `balance` 与 `GET /api/me/coupons` 的 `total`
- `CH-04` 三个取数接口里**某一个 `500`** → 只有那张卡上的数变 `—`，其余照常，**卡仍可点**
- `CH-05` 三个全挂 → 错误态 + 重试按钮，**不白屏**
- `CH-06` 点「我领取的任务」卡 → `/customer/claims`；点「我的奖励」卡 → `/customer/rewards`
- `CH-07` 空数据 → 显示「还没有数据」，**不出现** `NaN` / `undefined` / `null`
- `CH-08` 页面上**没有** `role` / `user_id` 输入项
- `CH-09` 未选任务点「开始创作」→ 打开选择弹层，**不**直接建 job
- `CH-10` 一个任务都没领 → 点「开始创作」**一个请求都不发**，就地提示并给去领任务的入口
- `CH-11` 选了任务但一张图都没选 → 前端拦，**不发** `POST /api/jobs`
- `CH-12` `/customer?task=12` → 创作台预选该任务并显示它的商户名与标题

### CU 上传与建 job（12 条）
- `CU-01` 选 3 张图 → 显示 `3/9`；选第 10 张 → 拦（最多 9）
- `CU-02` 选图后**立刻**的请求数为 **0**（只做本地预览）
- `CU-03` 点「开始创作」→ 对每张图各发一次 `POST /api/uploads`，**串行**
- `CU-04` 第 2 张上传 `415` → 停住，**第 3 张不发**，显示后端文案
- `CU-05` 全部上传成功 → 恰好 1 次 `POST /api/jobs`
- `CU-06` `POST /api/jobs` 的体：`assets[].url/mime/size_bytes` 取自上传响应，**不含** `merchant_id`/`payer`/`user_id`
- `CU-07` 体里带 `claim_id`，且等于所选领取的 id
- `CU-08` 默认选中「写文案」时体里 `kind === "copy"`；切到「做视频」后再点开始创作 → 体里 `kind === "video"`
- `CU-09` `201` → 跳 `/customer/studio/{job_id}`
- `CU-10` `402`（额度不足）→ 就地显示后端文案 + 一个去 `/customer/...` 看余额的提示，**不跳页**
- `CU-11` 「写文案」「做视频」均可点；当前项有选中态（`aria-pressed="true"` 或 `composer__kind--active`）
- `CU-12` 仅切换类型**不发**任何请求；旁注**不含**笼统「暂未开放」

### CL 找任务与领取（9 条）
- `CL-01` 进页 → 1 次 `GET /api/tasks`
- `CL-02` 每卡六要素：标题 · 商户名 · 品类 · 时间窗 · `claimed_count/quota` · 付费模式
- `CL-03` `quota=null` → 「不限」（不是 `0/0`）
- `CL-04` `user_pay_reimburse` 的卡带「需垫付，过审后报销」
- `CL-05` 搜关键词 → 请求带 `keyword`
- `CL-06` 领取 → 1 次 `POST /api/tasks/{id}/claim`，成功后就地变「已领取」
- `CL-07` 领取 `409` → **原样**显示后端文案
- `CL-08` 列表 `500` → 错误态 + 重试，不白屏
- `CL-09` 空列表 → 「还没有任务」

### CD 任务详情与预览（7 条）
- `CD-01` 进页 → 只打 1 次 `GET /api/tasks/{id}`（**不打**第二个请求取规则）
- `CD-02` 阶梯逐档显示 `min` / `max` / 奖励内容；末档 `max=null` → 「以上」
- `CD-03` `metric` 只读，页面上**没有** metric 选择器
- `CD-04` `claimed_by_me=false` → 按钮是「领取」；`true` → 「去创作」
- `CD-05` `user_pay_reimburse` + 已领取 → 打 `GET /api/me/reimburse-preview`，显示三个数
- `CD-06` `reimburse-preview` 返回 `403` → 该区块**不显示**，页面其余正常，**不报错**
- `CD-07` `404` → 「任务不存在」，不白屏

### CC 我领取的任务（6 条）
- `CC-01` 进页 → 1 次 `GET /api/me/claims`
- `CC-02` 每行四要素：任务标题 · 商户名 · 领取时间（北京时间）· 状态徽章
- `CC-03` 「去创作」→ 跳 `/customer?task={task_id}`，**不发请求**
- `CC-04` 「放弃」首点只变「确认放弃」，**一次 `DELETE` 都不发**；再点才发
- `CC-05` 放弃成功后该行消失（**不重取整个列表**）
- `CC-06` 空列表 → 「还没有领取任务」

### CR 我的奖励（9 条）
- `CR-01` 进页 → 1 次 `GET /api/me/rewards/by-merchant`
- `CR-02` 每卡四要素：商家名 · 积分 · `coupon_unused/coupon_total` · logo（或首字占位）
- `CR-03` `points_earned=0` 的商家仍出现，显示 `0`
- `CR-04` 点卡 → `/customer/rewards/{merchant_id}`
- `CR-05` 详情页显示商家名 + 大字积分
- `CR-06` 券列表显示 `coupon.name` 与面额文案（`cash_off` → 「满 X 减 Y」）
- `CR-07` 券的 `expire_at` 按**北京时间**显示
- `CR-08` 详情 `404` → 「这个商家还没有你的记录」
- `CR-09` 空态「还没有奖励」/「还没有这个商家的券」，**不出现** `NaN` / `undefined`

### CX 视觉地板与不编造（6 条）
- `CX-01` 320 / 375 / 414 / 768 / 1280 五档**无页面级横向滚动**
- `CX-02` 两个入口卡在窄屏**堆叠**、宽屏并排（不是挤成两行字）
- `CX-03` 创作台内文本是**正体**（`font-style: normal`），不出现斜体标题
- `CX-04` 组件里**没有**内联 `#hex` / `oklch()` / 裸 `font-family`（全部走命名 token）
- `CX-05` 整页文本不出现 `NaN` / `undefined` / `null`
- `CX-06` 客户侧不出现任何商户经营数据（`GET /api/merchant/*` 一次都不打）

> 合计 **65 条**（CT 6 + CH 12 + CU 10 + CL 9 + CD 7 + CC 6 + CR 9 + CX 6）。
> **改写**：原「客户首页」的用例按新结构改写（三卡 → 两卡 + 创作台），
> 条数由 12 上下浮动；商户侧原有 65 条**必须继续全绿**。

## 明确不做（本段）

- ❌ **AI 内容工坊页面**与**数据回填页面**——见追加 C（下一段）
- ❌ 图片产出（`kind=image`）——后端枚举里没有，本轮不加
- ❌ 商户侧任何页面的结构改动（只吃新 token，不改骨架）
- ❌ 任务收藏 / 分享 / 评论
- ❌ 客户侧任务列表的排序选项（固定后端给的顺序）
- ❌ 券详情页 / 券核销（05 明写只发不核）
- ❌ 积分商城页面（`/api/mall/*` 需要先选商家，另做）
- ❌ 深色模式 / 主题切换 / 国际化 / 状态管理库（同上一轮）
- ❌ 端到端自动化（Playwright）——仍用单测 + 手工验收

---

# 追加 C（2026-09-16）：AI 内容工坊 + 数据回填

> 2026-09-16 用户确认：「**这是这个项目的主体，要做完整的 ai 布局**」。
> 用户对主体的原话：用户输入图片和商户信息 → AI 按 workflow 自动生成推广图片和文案
> → 供用户下载后发到各社交媒体。
>
> **本轮只有文案产出**：图片不做（后端 `models/studio.py:39` 的 `JOB_KINDS` 只有 `copy` / `video`），
> 视频**界面禁用、后端也不接**（03 追加 B：DeepSeek 没有视频模型），故实际只跑 `kind=copy`。
> 其余全做。这是 08 最长的一段，也是**唯一有真实状态机**的一段。
>
> 对话 / 复写 / 文案 / 把关 / 预检 / OCR **全部是真调用**（03 追加 B 真接 DeepSeek），
> 所以**界面上没有一处假数据**，余额**会真的减少**。

## 前置（不满足就不能开工）

- **03 追加 A（素材上传端点）先绿** —— 截图回填要用它
- **03 追加 B（真接 DeepSeek + 计价写死在代码里）先绿** —— 否则点「开始创作」当场 `503`
  （`model_price` 零行种子时后端直接 `503 计价表缺该组合`）
- **追加 B（客户侧外壳）的六条路由先绿** —— 本段全部挂在 `/customer/*` 起手的地方

## 路由表（追加）

| 路径 | 页面 | 说明 |
|---|---|---|
| `/customer/studio/:jobId` | 内容工坊 | 单个 job 全程：预检 → 对话 → 复写 → 生成 → 下载 |
| `/customer/posts` | 我的作品 | 回填列表 + 回填入口 + 追加速据 + 传截图 + 申诉 |

两条都在 `Guard role="customer"` 之内。

---

## `/customer/studio/:jobId` —— 状态机驱动

**进页 = 恰好一次 `GET /api/jobs/{jobId}`**，页面显示什么**完全**由 `job.status` 决定。
`403` → 「这个作品不属于你」；`404` → 「作品不存在」；`:jobId` 非数字 → **不发请求**，
直接显示「作品不存在」。**都不白屏。**

| `status` | 屏幕 | 可用动作 |
|---|---|---|
| `created` / `guarding` | 预检骨架屏 + 「正在检查素材…」 | 轮询（下面的规则） |
| `guard_failed` | `guard.reason` + 素材缩略图 | **只能删除**（「重试」`disabled`——spec 03：素材问题重试无意义） |
| `chatting` | 对话区 + 复写区 + 「开始生成」 | 对话 / 复写 / 生成 / （`created` 才可删） |
| `generating` / `judging` | 进度条 + 阶段文案 | 只读 |
| `ready` | 产物卡 + 下载 | 下载 / 去回填（**不给删除**，后端 `409`） |
| `need_review` | 「已转人工复核」+ 说明 | 只读（**不给下载**） |
| `failed` | `fail_reason` + 「重试」 | 重试 / 删除 |

### 预检轮询（`guarding`）

1. `status ∈ {created, guarding}` → 每 **2 秒**打一次 `GET /api/jobs/{id}/guard`。
2. `409`（「预检尚未完成」）→ **继续轮**，这不是错误。
3. `200` → **停轮询**，按 `passed` 渲染：`true` → 切对话区；`false` → 显示后端给的 `reason`。
4. `404` / 网络失败 → 停轮询，错误态 + 重试按钮。
5. **离开页面必须停**（组件卸载清掉定时器），否则切页后还在打接口。

### 对话（`chatting`）

- 进 `chatting` 后打一次 `GET /api/jobs/{id}/chat` → `{messages[]}`，按 `id` 升序渲染。
- 发消息 `POST /api/jobs/{id}/chat {message}` → **SSE 流**。
  - ⚠️ **必须用 `fetch` + `ReadableStream` 手动解析 `event:` / `data:` 行，不许用 `EventSource`**——
    `EventSource` **带不了 `Authorization` 头**，而这个端点是鉴权的。这是本段最容易写错的一处。
  - 事件三类（`studio_job.py:658-675` 的 `_sse` 格式 `event: X\ndata: {json}\n\n`）：
    `delta`（`{text}`，追加进当前气泡）· `error`（`{message}`，气泡标红，**这一轮不落库**）·
    `done`（`{chars}`，收尾）。
  - 流式进行中：输入框与发送键 `disabled`；`done` / `error` 后恢复。
- 错误映射：`409`（满 20 轮 / 未预检通过）→ 输入框置灰 + **原样**显示后端文案；
  `402` → 就地提示，**不跳页**。
- 20 轮上限**是后端的**（`MAX_CHAT_ROUNDS`），**前端不自己数轮数**。

### 复写提示词

- `POST /api/jobs/{id}/rewrite-prompt {raw_prompt}`，`raw_prompt` 1~2000 字。
- 本地先拦：空 → 按钮 `disabled`；> 2000 字 → 拦，**不发请求**。
- `200` → 显示 `optimized_prompt` + `quality_score`。
  - **分数展示口径**（防编造）：调用前若 `GET /api/jobs/{id}` 的 `prompt_draft.quality_score`
    已有值，显示「上次 N 分 → 本次 M 分」；为 `null` 则**只显示 M 分**，
    页面上**不许出现**「上次」二字。**绝不用 0 或任何数冒充旧分。**
  - `rewrite_failed=true` → 显示「这次没能优化，已用你原来的提示词」。
- `429`（后端 10 次/分钟）→ 「改得太频繁了，稍等一下」，不跳页；`402` → 就地提示。
- 结果落进 `<textarea>` **可编辑**，用户改完再点「开始生成」。

### 开始生成与进度

- `POST /api/jobs/{id}/generate {prompt}` → `202`。`prompt` 取**当前 textarea 的值**
  （可能是用户手改过的，不是 `optimized_prompt`）。
- 错误映射，**全部原样显示后端文案**：`409` 预检未通过 / 状态不允许 · `429` 并发生成数超限 · `402` 额度不足。
- `202` 后打开 `GET /api/jobs/{id}/events`，解析 `stage` 事件（`{id, stage, detail, created_at}`）推进进度条。
  - ⚠️ **这条流会自己收流**：后端空闲 `idle_rounds(5) × poll_seconds(0.2)` ≈ **1 秒**无新事件即结束，
    **这不是错误**。前端须在流结束后**重连**（间隔 1 秒），直到 `status` 进入终态。
  - 每收到 `stage` 顺带打一次 `GET /api/jobs/{id}` 刷新 `status`——
    **终态只认 `GET /api/jobs/{id}`，不认进度流**（进度流是通知，不是真相）。
- 生成中**离开页面**：不发 `DELETE`、不发 `retry`；回来时看到的是 `GET` 出来的真实状态。

### 产物与下载（`ready`）

取 `outputs[]` 里 `is_active=true` 的那条（**恰好一条**；`is_active=false` 的旧产物**不显示**）。

| `kind` | 展示 | 下载 |
|---|---|---|
| `copy` | `content` 全文（可选中、可滚动） | 「复制文案」（剪贴板）+「下载 .txt」（前端 `Blob`，**不调** `GET download`） |

- **默认 `kind=copy`**；用户可选 `video`（08 追加 E）。无视频平台 Key 时生成阶段失败（03 追加 D）；
  有 Key 且 `ready` 后按 `type=video` 展示 url。
- ⚠️ **`outputs[].url` 是存储键（文件名），不是可直接用的地址**——
  后端 `download` 端点把它交给 `presign_url` 才变成 URL（`studio_job.py:1000-1003`）。
  文案下载**不经过它**（前端本地 `Blob`）；视频在本段用上游返回的**可访问 url** 直接展示/打开，
  `presign_url` **仍可不接 MinIO**。
- 同时显示 `judge_score` 与 `judge_detail.reasons`——**这是我们自己算的分，可以显示**。
- `409`（产物未就绪）→ 「还没生成完」+ 刷新一次 `GET /api/jobs/{id}`。
- 「去回填」→ 跳 `/customer/posts?job_id={job_id}`，省用户一次选择。

### 重试与删除

- 「重试」`POST /api/jobs/{id}/retry` → `202`；`409`（`retry_count >= 3` / 状态不允许）→ 原样显示。
- 「删除」`DELETE /api/jobs/{id}` → `204`。**两步确认**：首点只把按钮变「确认删除」，**不发请求**；
  再点才发。成功后跳 `/customer/claims`。
- 只在 `created` / `guard_failed` / `failed` 三态给删除按钮（`ready` / `need_review` / 生成中都没有）。

### 计费展示

- 建 job 时后端已回 `reserved_points` 与 `reimburse_reserved`。页头显示
  「本次预扣 N 点」（`N = reserved_points`）；**`reserved_points = 0` 时整行不出现**。
- `pay_mode=user_pay_reimburse` → 另打 `GET /api/me/reimburse-preview?task_id=`
  显示「我垫付了 X / 已报 Y / 还能报 Z」；`403` → **整块不显示**，不报错。
- ⚠️ **客户侧看不到商户花了多少**（spec 03 明写）。**不许**出现「商户扣了 N 点」这类文案，
  也**一次都不许**打 `GET /api/merchant/*`。

---

## `/customer/posts` —— 我的作品与回填

- `GET /api/me/posts` → `{items, ...}`，每项 = `post_detail`
  （`post_public` + `countdown_seconds` + `latest_snapshot`），见 `post_serializers.py:135`。
- 每行：**任务标题** · 平台 · `post_url` · `status` 徽章 · 互动量 · 倒计时。
  - **任务标题的取法**：`post_detail` 里**只有 `claim_id`，没有 task 信息**。
    故进页时另打**一次** `GET /api/me/claims`（该端点不分页、每项含内嵌 `task`，
    见 `task.py:242-267`），建 `claim_id → task.title` 映射。
    映射里查不到 → 显示 `—`（**不编造、不回显 `job_id`**）。
  - `post_url` 外链必须 `target="_blank" rel="noopener noreferrer"`。
- **倒计时**：`countdown_seconds` 为负 → 「已超时，系统将自动通过」；否则「还有 X 小时 Y 分」。
  取 `countdown_seconds` 由前端**每秒本地重算**，**不轮询后端**。
- 徽章文案：`pending` 待审核 · `approved` 已通过 · `auto_approved` 已自动通过 ·
  `rejected` 已驳回 · `appealed` 申诉中。
- 页面**必须有一句**「奖励按**互动量峰值**结算」——spec 04 明写前端要提示。

### 回填新作品

1. 「回填作品」→ 打 **一次** `GET /api/me/jobs?page=1&size=100`，客户端筛 `status=ready` 的供选。
   - `?job_id=7` 时预选该 job（来自工坊的「去回填」）。
   - 一个 ready 的 job 都没有 → **不弹层**，就地提示「先去创作」+ 跳 `/customer` 的按钮，
     **不发第二个请求**。
2. 弹层字段：平台（5 选 1：小红书 / 抖音 / 快手 / B 站 / 视频号）· 作品链接 · 标题（选填）· 正文（选填）。
3. 本地拦：链接空 → 拦；不以 `http`/`https` 开头 → 拦，**均不发请求**。
4. `POST /api/posts {claim_id, job_id, platform, post_url, post_title?, post_body?, source:"manual"}`
   → `201 {post, review_deadline}`。
   - `claim_id` 与 `job_id` 都取自所选 job 的 `job_public`（`studio_job.py:133-148`）。
     前端**不需要**、也**不许**带 `user_id`。
   - `source` 固定 `"manual"`（本轮没有插件）。
5. 错误映射，**原样显示后端文案**：`409` 同 claim 同链接重复 / 该 claim 已有 approved 作品 ·
   `422` URL 非法 / 平台与域名不匹配 · `403` 非本人 claim · `409` job 未 ready。
   - ⚠️ 平台与域名**必须对得上**（后端白名单见 `post_serializers.py:26-32`；
     `platform=xhs` 但域名是 `douyin.com` → `422`）。前端**不替用户猜平台**，
     但要把那句 `detail` 显示在**平台字段**下方，否则用户不知道该改哪一个。

### 追加速据

- 每行「更新数据」→ 弹层填 `likes` / `collects` / `comments` / `shares`（四个非负整数）。
- `POST /api/posts/{id}/metrics {likes, collects, comments, shares, source:"manual"}` → `201 {snapshot}`。
- ⚠️ **体里绝不带 `engagement`**（后端 `422` 挡的就是它），界面上**也不放**这个输入框。
- 负数 / 小数点 → 本地拦。
- `409`（已审核完成）→ 原样显示 + 刷新该行。
- 成功后用**响应里的** `snapshot.engagement` 就地更新该行，**不重取整个列表**。
- ⚠️ 新值**小于**旧值**是允许的**（社媒数据会因删帖回落），**不拦、不警告**。

### 截图保底（插件拿不到数据时的兜底）

1. 「传截图」→ 本地选图。本地先拦：非 jpeg/png/webp、或 > 20MB → 拦，**不发请求**。
2. `POST /api/uploads`（03 追加 A）→ `{url, mime, size_bytes}`。
3. `POST /api/posts/{id}/screenshot {image_url, mime, size_bytes}` → `202 {ocr_result_id}`。
   - 三个字段**全部取自上传响应**，不自己造。
4. 轮询 `GET /api/ocr/{post_id}`（每 **2 秒**，上限 **30 秒**）：
   - `409`（未完成）→ 继续轮。
   - `200` → **停轮**，显示 `parsed` 的四个数 + `confidence`。
   - 30 秒超时 → 停轮，显示「识别中，稍后刷新」+ 刷新按钮。
5. ⚠️ **`parsed=null` 时一个字都不许填 0**：四个数显示 `—`，并显示
   「没认出来，请重新传一张清晰的截图」。**这是 spec 04 的硬规则**——
   0 是一个合法的互动量，拿它冒充「没认出来」会让商户把糊图当真的批过去。
6. `confidence < 0.70` → 显示「识别置信度低，需要商家人工核对」，
   **不给**「一键通过」之类按钮（spec 04 原话）。
7. `mismatch_flag=true` → 「与链接侧数据不一致，已转人工核对」。
8. `415` / `413` → 原样显示后端文案。

### 申诉（**仅一次**）

只有 `status=rejected` 的行有「申诉」入口；`appealed` 的行入口**置灰**。

弹窗约束（spec 04 明写「需 E2E 测试覆盖」，逐条要测）：

- 文案必须写明「**申诉机会仅有一次，提交后不可撤销**」。
- 「确定申诉」与「取消申诉」**初始 `disabled`**。
- 弹窗出现后**满 2 秒**，两个按钮**同时**变可点。
- 2 秒内点击 → **无任何反应，不发起请求**。
- 「取消申诉」→ 关弹窗，**不消耗次数**，用户可再次进入。
- 只有「确定申诉」且 `201` 才消耗次数。
- `409` → 提示「已申诉过」并刷新该行状态。
- `reason` 10~500 字；9 字 / 501 字 → 本地拦。

---

## 边界（每条之后会变成一条测试）

### SJ 工坊外壳与状态机（12 条）
- `SJ-01` 未登录访问 `/customer/studio/1` → 落 `/login`，登录后**回跳原目标**
- `SJ-02` 别人的 job（`403`）→ 「这个作品不属于你」，不白屏
- `SJ-03` `:jobId = abc` → **不发请求**，显示「作品不存在」
- `SJ-04` `status=guarding` → 出现预检骨架屏，且**开始**轮询 `GET /api/jobs/{id}/guard`
- `SJ-05` `guard` 回 `409` 两次后回 `200{passed:true}` → 轮询**停下**（guard 调用恰好 3 次），切对话区
- `SJ-06` `guard` 回 `200{passed:false, reason:"…"}` → 显示**后端那句** `reason`
- `SJ-07` `guard_failed` → 「重试」`disabled`，「删除」可用
- `SJ-08` `status=ready` → 出现产物卡与下载，**没有**删除按钮
- `SJ-09` `status=need_review` → 「已转人工复核」，**没有**下载按钮
- `SJ-10` `status=failed` → 显示 `fail_reason`，「重试」可用
- `SJ-11` 卸载组件后推进计时器 → `stubApi.calls` **不再增长**（轮询真的停了）
- `SJ-12` 页头「本次预扣 N 点」= 建 job 的 `reserved_points`；`reserved_points=0` → **整行不出现**

### SC 对话（10 条）
- `SC-01` 进 `chatting` → 恰好 1 次 `GET /api/jobs/{id}/chat`，按序渲染，`role` 决定左右
- `SC-02` 发消息 → `POST` 的体是 `{message}`，**不含** `role` / `job_id` / `user_id`
- `SC-03` `delta` 分三段到达 → 气泡文本最终 == 三段拼接
- `SC-04` 收到 `error` 事件 → 气泡标红显示后端文案，**该轮不新增** assistant 气泡
- `SC-05` 流式进行中输入框与发送键 `disabled`；`done` 后恢复可用
- `SC-06` `409`（满 20 轮）→ 输入框置灰 + **原样**显示后端文案，**前端不自己数轮数**
- `SC-07` `402` → 就地提示，**不跳页**（URL 仍是 `/customer/studio/{id}`）
- `SC-08` 空消息点发送 → 拦，**一次 `POST` 都不发**
- `SC-09` 聊天请求带 `Authorization` 头（断言 `stubApi.calls` 的 headers）——**这正是不能用 `EventSource` 的原因**
- `SC-10` 历史 `500` → 错误态 + 重试，不白屏

### SW 复写（8 条）
- `SW-01` 空提示词 → 「复写」`disabled`，**不发请求**
- `SW-02` 2001 字 → 本地拦；2000 字 → 放行
- `SW-03` `200` → 显示 `optimized_prompt` 与 `quality_score`
- `SW-04` 调用前 `prompt_draft.quality_score` 为 `null` → **只显示一个分数**，页面上**不出现**「上次」二字
- `SW-05` 调用前 `quality_score=42`、本次 `88` → 显示「上次 42 分 → 本次 88 分」
- `SW-06` `rewrite_failed=true` → 显示「这次没能优化，已用你原来的提示词」，且文本区 == 传入的 `raw_prompt`
- `SW-07` `429` → 「改得太频繁了，稍等一下」，**不跳页**
- `SW-08` 复写后手改文本再点「开始生成」→ `POST /generate` 的体里是**改过的**文本

### SG 生成与进度（9 条）
- `SG-01` 点「开始生成」→ 恰好 1 次 `POST /api/jobs/{id}/generate`，体是 `{prompt}`
- `SG-02` `202` → 打开 `GET /api/jobs/{id}/events`
- `SG-03` 收到 `stage` 事件 → 进度条文案随阶段变化
- `SG-04` events 流**自然结束**（后端空闲收流）→ 前端**重连**（events 计数 +1），**不报错、不弹错**
- `SG-05` 重连后 `GET /api/jobs/{id}` 回 `ready` → 停止重连并渲染产物（终态只认这个接口）
- `SG-06` `409` 预检未通过 → **原样**显示后端文案
- `SG-07` `429` 并发生成超限 → **原样**显示后端文案
- `SG-08` `402` → 就地提示，**不跳页**
- `SG-09` 生成中离开页面 → 调用记录里 `DELETE` 与 `retry` 各 **0 次**

### SD 下载 / 重试 / 删除（10 条）
- `SD-01` `kind=copy` 且 `ready` → 显示 `is_active=true` 那条的 `content` 全文
- `SD-02` `outputs` 里另有一条 `is_active=false` → **不显示**它
- `SD-03` `kind=copy` 点「下载 .txt」→ **不发** `GET download`（断言调用数 0），本地生成文件
- `SD-04` `kind=copy` 点「复制文案」→ 剪贴板内容 == `content`
- `SD-05` `kind=copy` 进 `ready` → **不发** `GET /api/jobs/{id}/download/*`（断言该端点调用数 **0**）
- `SD-06` **作废口径**：改为 `SD-V1`——仅当 `type=video` 且有 `url` 时才出现 `<video>` / 打开链接；`type=copy` 时仍无视频控件
- `SD-07` 显示 `judge_score` 与 `judge_detail.reasons`——后端给几条就渲染几条，**不截断、不补造**
- `SD-08` 「去回填」→ 跳转 URL 带 `?job_id={job_id}`，且跳转前**不发**任何请求
- `SD-09` 「重试」→ 1 次 `POST /api/jobs/{id}/retry`；`409` → 原样显示
- `SD-10` 「删除」两步：首点只变「确认删除」、**一次 `DELETE` 都不发**；再点才发；`204` 后跳 `/customer/claims`

### SB 计费（5 条）
- `SB-01` `reserved_points=50` → 页头出现「本次预扣 50 点」
- `SB-02` `reserved_points=0` → 页头**不出现**预扣行
- `SB-03` `user_pay_reimburse` → 打 `GET /api/me/reimburse-preview`，显示「垫付 / 已报 / 还能报」三个数
- `SB-04` `reimburse-preview` 回 `403` → 该区块**不显示**，页面其余正常，**不报错**
- `SB-05` 页面**一次都不打** `GET /api/merchant/*`，且不出现「商户扣了」类文案

### PF 回填与快照（14 条）
- `PF-01` 进页 → 1 次 `GET /api/me/posts` **且** 1 次 `GET /api/me/claims`（后者只为拿任务标题）
- `PF-02` 每行六要素：任务标题 · 平台 · 链接 · 状态徽章 · 互动量 · 倒计时
- `PF-03` 倒计时按**北京时间**算；`countdown_seconds` 为负 → 「已超时，系统将自动通过」
- `PF-04` 徽章五态各有不同文案（`pending` / `approved` / `auto_approved` / `rejected` / `appealed`）
- `PF-05` 页面上有「奖励按**互动量峰值**结算」这句提示
- `PF-06` `claim_id` 在 claims 映射里查不到 → 任务标题显示 `—`，**不出现** `job_id` 数字
- `PF-07` 「回填作品」→ 1 次 `GET /api/me/jobs?page=1&size=100`，只列 `status=ready` 的
- `PF-08` 没有 ready 的 job → **不弹层**，提示 + 去创作按钮，**不发第二个请求**
- `PF-09` `/customer/posts?job_id=7` → 弹层预选该 job
- `PF-10` 链接填 `not-a-url` → 本地拦，**不发请求**；`javascript:alert(1)` → 同样拦
- `PF-11` 提交体 = `{claim_id, job_id, platform, post_url, post_title?, post_body?, source:"manual"}`，**不含** `user_id` / `engagement`
- `PF-12` `409` / `422` → **原样**显示后端 `detail`
- `PF-13` `422` 且错在平台与域名不匹配 → 该 `detail` 显示在**平台字段**下方
- `PF-14` 「更新数据」→ 1 次 `POST /api/posts/{id}/metrics`，体里**没有** `engagement` 键；成功后该行数字用**响应里的** `engagement` 更新，**不重取列表**

### PS 截图与 OCR（9 条）
- `PS-01` 选 21MB 的图 → 本地拦，一次 `POST /api/uploads` 都不发
- `PS-02` 选 `.txt` → 本地拦（按扩展名与 `type` 双判）
- `PS-03` 正常流程 → 依次 `POST /api/uploads` → `POST /api/posts/{id}/screenshot`，后者体里 `image_url` == 前者的 `url`
- `PS-04` 上传 `415` → 停住，显示后端文案，**不发** `screenshot`
- `PS-05` `202` → 开始轮询 `GET /api/ocr/{post_id}`
- `PS-06` `ocr` 回 `409` 两次后 `200` → 停止轮询，显示四个数 + 置信度
- `PS-07` **`parsed=null`** → 四个数显示 `—`，出现「没认出来，请重新传一张清晰的截图」，
  且该区块文本**不出现** `0`
- `PS-08` `confidence=0.5` → 出现「置信度低，需人工核对」，且**没有**任何「一键通过」按钮
- `PS-09` `mismatch_flag=true` → 出现「与链接侧数据不一致，已转人工核对」

### PA 申诉（10 条）
- `PA-01` `rejected` 的行有「申诉」入口；`appealed` 的行入口置灰
- `PA-02` 弹窗文案含「申诉机会仅有一次」
- `PA-03` 弹窗打开瞬间「确定申诉」与「取消申诉」**都 `disabled`**
- `PA-04` 1.9 秒时点「确定申诉」→ **无反应**，申诉请求调用数为 0
- `PA-05` 2.1 秒后两个按钮**同时**可点
- `PA-06` 「取消申诉」→ 弹窗关、**不发请求**；再次点「申诉」能重新打开（未消耗次数）
- `PA-07` 「确定申诉」`201` → 该行变 `appealed` 且入口置灰
- `PA-08` 「确定申诉」`409` → 提示「已申诉过」并刷新该行状态
- `PA-09` `reason` 9 字 → 本地拦；10 字 → 放行；501 字 → 本地拦
- `PA-10` 申诉体是 `{reason}`，**不含** `user_id`

### PX 视觉地板与不编造（7 条）
- `PX-01` 320 / 375 / 414 / 768 / 1280 五档**无页面级横向滚动**
- `PX-02` 对话气泡与产物卡在 375 宽下单列、不溢出
- `PX-03` 标题与按钮均为**正体**（`font-style: normal`）
- `PX-04` 组件里**没有**内联 `#hex` / `oklch()` / 裸 `font-family`（全走命名 token）
- `PX-05` 整页文本不出现 `NaN` / `undefined` / `null`
- `PX-06` `prefers-reduced-motion: reduce` 下流式与进度**无位移动画**
- `PX-07` 两页都**不出现**「AI 生成了 N 次」「已为你节省 X 元」这类**后端没给的数**

> 合计 **94 条**（SJ 12 + SC 10 + SW 8 + SG 9 + SD 10 + SB 5 + PF 14 + PS 9 + PA 10 + PX 7）。
> **不改后端出参**：本段全部用既有端点拼出来，`task_title` 走 `GET /api/me/claims` 就近映射，
> 不为一个标题去动 04 的序列化。

## 明确不做（本段）

- ❌ **视频产出 UI 全禁用** —— **作废**（改由 08 追加 E：可选 `kind=video`；无 Key 时在生成阶段失败）
- ❌ **图片产出**（`kind=image`）——后端 `JOB_KINDS` 里没有，本轮不加
- ❌ **桩 provider / 演示产物** —— 文案真接 DeepSeek；视频 Key 空则失败文案清晰，Key 非空则走真 HTTP
- ❌ 生成历史版本对比 / 一键回滚（03 明写旧 `gen_output` 只作审计、前端不展示）
- ❌ 素材图库管理 / 复用
- ❌ 视频剪辑器 / 时间轴 / 图片编辑器
- ❌ **模板选择与套用**（03 有 `prompt-templates` 一组端点，**本轮界面不做**）
- ❌ 插件（Chrome MV3）——插件是独立项目，本轮 `source` 恒为 `manual`
- ❌ 截图伪造检测 / 图片取证
- ❌ 数据趋势图（04 明写只展示数字与倒计时）
- ❌ 二次申诉 / 申诉升级
- ❌ 商户侧申诉入口（方向固定为「用户 → 平台」）
- ❌ **改 04 的出参**（`post_detail` 不加 `task_title`，前端用 claims 映射解决）
- ❌ 券核销 / 积分商城页
- ❌ 深色模式 / 主题切换 / 国际化 / 状态管理库

---

# 追加 D（2026-09-17）：客户壳层定稿 + 本轮交付切片

> 2026-09-17 用户确认意图：「**现在开始做客户页面**；**系统主体放在最显眼的位置**；
> **侧边或下边**放『我的信息』一类入口。」
>
> 这与 **追加 B**（创作台 = 主体；「我领取的任务」「我的奖励」= 信息入口）**同构**。
> 本段不另起一套页面，只把**壳层布局**钉死，并划清**本轮先做哪一块**。

## 一句话目标

> 客户打开 `/customer`，第一眼是**创作台（系统主体）**；「我的信息」类入口永远次要——
> **宽屏在侧栏，窄屏在创作台下方**；绝不做成三张摘要卡抢戏的仪表盘。

## 壳层布局（覆盖追加 B「两个入口卡并排」的摆放方式）

| 视口 | 主体（创作台） | 「我的信息」入口 |
|---|---|---|
| ≥ `40rem`（约 640px） | **主列**居中偏左，`max-width: 720px`，占视觉中心 | **右侧栏**竖排两颗入口：`我领取的任务` · `我的奖励`（可点，带数） |
| < `40rem` | 仍占首屏主位 | **创作台正下方**竖排两颗入口（同追加 B 的窄屏堆叠） |

硬规则：

1. **主体 = 创作台**（标语 + 玻璃卡输入 +「开始创作」）。去掉它，页面必须立刻不像「助小商客户端」。
2. **「我的信息」不是首页主叙事**——只是入口；点进去才是 `/customer/claims`、`/customer/rewards`。
3. 原首页的**三张摘要卡结构废止**（领取 / 积分 / 券）。商户首页三卡**不动**（HM-05 互斥契约改写为：商户轨 vs 客户「创作台+信息入口」）。
4. 顶栏仍是 N9（字标 + 昵称 + 角色徽章 + 退出）；反馈 FAB 保留。
5. 颜色 / 字体继续走 `design.md` + Coral token；追加 B 的玻璃 token 可加，**不许换主题色**。

取数口径仍按追加 B：

| 入口 | 端点 | 卡上显示 |
|---|---|---|
| 我领取的任务 | `GET /api/me/claims` | `total` |
| 我的奖励 | `GET /api/me/points` + `GET /api/me/coupons` | `balance` 积分 · `total` 张券 |

## 本轮交付切片（建议，等你拍板）

后端前置**尚未落地**（仓库现状核对）：

| 前置 | Spec 位置 | 现状 |
|---|---|---|
| `POST/GET /api/uploads` | 03 追加 A | **未挂路由**（`main.py` 无 upload） |
| `GET /api/me/rewards/by-merchant`（及 `/{id}`） | 05 追加 A | **未实现**（仅有 `GET /api/me/rewards`） |

因此本轮建议拆成 **两拍**（仍各走 Spec→测试→实现；拍与拍之间停等确认）：

### 拍 1 · 客户壳 + 找任务 + 我的领取（不依赖上传 / by-merchant）

| 做 | 不做 |
|---|---|
| `/customer` 壳层按上表重做 | ❌ 真·「开始创作」闭环（上传未就绪） |
| `/customer/tasks` · `/customer/tasks/:id` 找任务 / 领取 | ❌ `/customer/rewards*`（等 by-merchant） |
| `/customer/claims` 我的领取 + 去创作深链 | ❌ `/customer/studio/*` · `/customer/posts`（追加 C） |
| 创作台 **UI 可见**；点「开始创作」若尚未领任务 → 引导去找任务；若已领但上传未通 → **就地提示「创作通道准备中」**，**不发** `POST /api/jobs` | ❌ 商户侧任何改版 |

> 「去创作」深链 `/customer?task=` 仍生效：创作台预选任务并显示商户名/标题（读 `GET /api/tasks/{id}`），但提交建 job 被前端闸住，直到拍 2。

### 拍 2 · 后端前置 + 创作闭环 + 我的奖励（再开一轮）

1. 后端：03 追加 A 上传 + 05 追加 A by-merchant（各一组红→绿）
2. 前端：放开「开始创作」→ `uploads` → `POST /api/jobs` → `/customer/studio/{id}`
3. 前端：`/customer/rewards` · `/customer/rewards/:merchantId`
4. 其后才是 **追加 C**（工坊状态机 + 数据回填）

## 边界（拍 1 新增 / 改写，每条 → 一条测试）

### 壳层 `SH`（替换原 CH 里与「三卡并排」冲突的条）

- `SH-01` ≥40rem：创作台在主列；两个信息入口在**右侧栏**（`data-testid="customer-aside"`）
- `SH-02` <40rem：信息入口在创作台**下方**（`data-testid="customer-footer-nav"`）；**不出现** `customer-aside`
- `SH-03` 主列有标语 + 创作台（`data-testid="customer-composer"`）；**没有**旧三卡 `customer-stack` / `customer-card-points` 等
- `SH-04` 「我领取的任务」入口数 = claims.`total`；「我的奖励」同时显示 points.`balance` 与 coupons.`total`
- `SH-05` 单接口 500 → 仅该入口数字变 `—`，入口仍可点
- `SH-06` 点领取入口 → `/customer/claims`；点奖励入口 → `/customer/rewards`（拍 1 奖励页可先做**占位页**「即将开放」，但路由必须在；或拍 1 奖励入口 `disabled`+文案——**默认选：路由进占位页，不 404**）
- `SH-07` 未领任务点「开始创作」→ **不发** job/upload，引导去 `/customer/tasks`
- `SH-08` 已选任务但上传通道未通（拍 1）→ 点「开始创作」**不发** `POST /api/jobs`，就地「创作通道准备中」
- `SH-09` `/customer?task=12` → 创作台显示该任务商户名与标题（1 次 `GET /api/tasks/12`）
- `SH-10` 客户页**零次** `GET /api/merchant/*`

### 找任务 / 详情 / 领取（沿用追加 B 的 CL / CD / CC，拍 1 全做）

条号与断言一字不改；本轮必须全绿。

## 明确不做（拍 1）

- ❌ `POST /api/uploads` / `POST /api/jobs` 真闭环（拍 2）
- ❌ `/customer/studio/*` · `/customer/posts`（追加 C）
- ❌ 积分商城兑换页
- ❌ 换品牌色 / 深色模式
- ❌ 改商户侧页面骨架

## 与既有用例的关系

- 原 `HM-*` 里针对客户「三卡堆叠 / `customer-stack`」的断言 → **按 `SH-*` 改写**（不是静默删绿）。
- 商户 `merchant-rail` 三卡用例**必须继续绿**。
- 追加 B 的 CH/CU 里依赖上传建 job 的条（`CH-11`、`CU-*`）→ **挪到拍 2**，拍 1 不跑。

---

**⛔ 停在这里等确认。** 请回复下面任一即可继续：

1. **「拍 1 可以」** —— 写 `test_plan`（应全红）→ 实现壳 + 找任务 + 领取  
2. **「先做后端前置」** —— 先开 03-A 上传 / 05-A by-merchant 的 Spec→测试→实现，再前端  
3. **「布局改成……」** —— 例如信息入口只要下方不要侧栏，或还要第三颗入口  

未确认前**不写测试、不写实现**。

---

# 追加 E（2026-09-18）：创作台产出类型可选（文案 / 视频）

## 一句话目标

> 「写文案」默认选中；「做视频」可切换；`POST /api/jobs` 带当前 `kind`。
> 缺 Key 时不哑巴失败——弹窗见追加 F。

## 端点 / 接口

改 [`CustomerHome.tsx`](frontend/src/pages/CustomerHome.tsx)：本地 `kind` 状态；建 job 带 `kind`。
可选：创作台展示当前 provider（默认 copy→deepseek，video→jimeng），可进密钥页改。

工坊 `ready`：`type=video` 有 url → `<video>` 或打开链接（`SD-V1`）。

## 边界

- `CU-08` / `CU-11` / `CU-12`（见上）
- `SD-V1`：video 产物可见 url

## 明确不做

- ❌ 不做图片产出 / 视频时间轴编辑器

---

# 追加 F（2026-09-18）：客户密钥页 + 缺 Key 弹窗指引

## 一句话目标

> 客户在站内自行管理所用 **provider 与 API Key**；创作文案/视频时若缺 Key、Key 无效、
> 或后端返回可识别的 Key 类错误，弹出**说明问题 + 如何添加**的引导，并可跳进密钥页。

## 数据模型

无。复用 07 的 `user_model_key` 与 `GET /api/models`。

## 页面与入口

| 路由 | 谁 | 做什么 |
|---|---|---|
| `/customer/keys` | 已登录客户 | 密钥列表 / 新增 / 更换 / 删除 / 校验；只显示 `key_masked` |
| 客户首页信息入口 | 同 | 增加「模型与密钥」入口（与领取/奖励并列或旁链） |
| 创作台 | 同 | 「管理密钥」短链；开始创作前可检查 |

### 密钥页行为（对接既有 API，无新后端）

| 动作 | API | 成功 | 失败展示 |
|---|---|---|---|
| 列表 | `GET /api/me/model-keys` | 表格：provider / 掩码 / status | 401→登录 |
| 价目/可选模型 | `GET /api/models` | 展示 supports_byok、has_my_key | — |
| 新增 | `POST /api/me/model-keys` `{provider, api_key, label?}` | 201，列表刷新 | **原样** detail；**绝不**把 api_key 回显到 UI 日志 |
| 更换 Key | `PATCH /api/me/model-keys/{id}` `{api_key}` | 200 | 同上 |
| 删除 | `DELETE …` | 204 | — |
| 校验 | `POST …/verify` | 更新 last_verified_at | 422 原样 |

Provider 白名单与后端一致：`deepseek`（文案）/ `jimeng`·`kling`（视频）。页面用中文标签：
DeepSeek / 即梦 / 可灵。

### 缺 Key 弹窗（`data-testid="key-guide-dialog"`）

**何时弹出**（任一）：

1. 用户点「开始创作」且选择了 **BYOK**（或系统判定必须 BYOK），但 `has_my_key=false` 对应 provider  
2. 建 job 返回 `422` 且 detail 表明缺 Key / 未配置  
3. 工坊内失败且 `fail_reason` 为 `key_invalid`，或文案含「视频模型尚未接入」且当前无该视频 provider 的用户 Key  

**弹窗必须包含**：

- **问题所在**一句话（例：「还没有 DeepSeek 密钥，无法生成文案」/「即梦密钥未配置或已失效」）  
- **如何添加**步骤（例：①打开「模型与密钥」②选 provider ③粘贴 Key ④保存；可选「去校验」）  
- 主按钮：「去添加密钥」→ `/customer/keys`（可带 `?provider=deepseek`）  
- 次按钮：「关闭」  

**不做什么**：弹窗内不直接贴大段厂商注册教程外链（可一句「Key 在对应厂商控制台创建」）；不收集 Key 进弹窗本身（避免与密钥页双入口互相覆盖——弹窗只指引）。

### 创作台与 BYOK

- 创作台可选「用我的 Key（BYOK）」开关或在有 Key 时默认 BYOK；建 job 体带 `billing_source` + `provider`  
- 无用户 Key 且用户未开 BYOK → 走 `platform`（平台 `.env` 兜底）；若平台也失败，工坊/错误态仍走弹窗指引去自备 Key  

## 边界（测试）

- `CK-01` `/customer/keys` 未登录 → `/login`  
- `CK-02` 进页 → 各 1 次 `GET /api/me/model-keys` 与 `GET /api/models`  
- `CK-03` 新增成功 → 列表出现掩码，**页面 HTML 不含**明文 api_key  
- `CK-04` 首页或创作台有入口能进 `/customer/keys`  
- `KG-01` 无 deepseek Key 且选 BYOK 点开始创作 → 弹出 `key-guide-dialog`，**0 次** `POST /api/jobs`  
- `KG-02` 弹窗主按钮 → `/customer/keys`  
- `KG-03` 建 job `422` 缺 Key → 弹窗展示后端 detail  
- `KG-04` 工坊 `fail_reason=key_invalid` → 可打开同款指引弹窗  
- `KG-05` 旁注/弹窗**不含**笼统「暂未开放」误伤文案能力  

## 明确不做

- ❌ 不在前端存明文 Key（localStorage 等）  
- ❌ 不做自定义 base_url（07 已禁）  
- ❌ 不做商户端密钥页（本段仅客户）  
- ❌ 不在弹窗里完成完整 CRUD（CRUD 只在 `/customer/keys`）

---

# 本轮打包确认（2026-09-18）

确认后写测试再实现，范围：

1. **03 追加 C** — 本地上传 → base64  
2. **03 追加 D** — 平台 `.env.example` + 视频接缝 + BYOK 门闩（含 `merchant_pay`+byok）  
3. **07 追加** — 允许 `merchant_pay` + byok  
4. **08 追加 E** — 创作台可选 copy/video  
5. **08 追加 F** — `/customer/keys` + 缺 Key 弹窗指引  

**⛔ 停在这里等确认。** 回复「可以」或「继续」后再写 `test_plan` 与实现。

---

# 追加 G（2026-09-18）：我的创作列表

## 一句话目标
> 客户能从首页打开自己做过的内容工坊记录，点一条就回到 `/customer/studio/{id}`。现在只有建 job 成功那一次会跳进工坊，离开后没有列表，所以旧作品打不开。

## 数据模型
不建表。列表用已有 `GET /api/me/jobs`，每项是 `job_public`（`id` / `task_id` / `claim_id` / `kind` / `status` / `provider` / `billing_source` / `fail_reason` / `created_at`）。

## 端点 / 接口
| 动作 | 输入 | 成功返回 | 失败情况 |
|---|---|---|---|
| 进列表 | `GET /api/me/jobs?page=1&size=100` | `{items, total, page, size}` | `401` 未登录 |
| 打开一条 | 前端跳 `/customer/studio/{id}` | 工坊页自己再打 `GET /api/jobs/{id}` | 工坊已有 `403` / `404` |

不新增后端端点。

## 页面
- 路径：`/customer/works`（`Guard role=customer`）。标题「我的创作」。
- 首页信息入口（侧栏与底栏同一套）增加第三项之外的一颗：「我的创作」→ `/customer/works`。与「我领取的任务」「我的奖励」「模型与密钥」并列。数字取 `total`；`0` 显示 `0`，请求失败显示 `—`。
- 进页恰好 1 次 `GET /api/me/jobs?page=1&size=100`。
- 每行：`作品 #{id}` · 类型（`copy`→文案，`video`→视频）· 状态中文 · `fail_reason`（有才显示）· 创建时间。整行是链接，目标 `/customer/studio/{id}`。
- 状态中文：`created`/`guarding` 预检中 · `guard_failed` 预检未通过 · `chatting` 对话中 · `generating` 生成中 · `judging` 把关中 · `ready` 已完成 · `need_review` 待人工 · `failed` 失败。未知状态原样显示，不编造。
- 空列表：文案「还没有创作」+ 链接回 `/customer`。
- 页头「返回」→ `/customer`。

## 边界
- `WK-01` 首页出现「我的创作」入口，指向 `/customer/works`。
- `WK-02` 进页恰好 1 次 `GET /api/me/jobs?page=1&size=100`。
- `WK-03` 有一条 `id=1, kind=copy, status=failed` → 行文案含「作品 #1」「文案」「失败」，链接是 `/customer/studio/1`。
- `WK-04` `items=[]` → 「还没有创作」，不渲染列表。
- `WK-05` 列表请求失败 → 失败提示 + 重试，不白屏。

## 明确不做
- ❌ 不改 `/customer/posts`（那是已发布、待回填的作品，标题仍叫「我的作品」；本页不合并进去）
- ❌ 不做分页翻页（一次最多 100 条）
- ❌ 不在列表里删除、重试、下载
- ❌ 不新增后端接口

**⛔ 停在这里等确认。** 回复「可以」后再写测试和页面。

---

# 追加 H（2026-09-18）：客户首页「找任务」入口

## 一句话目标
> 客户在首页一眼能进「找任务」列表，领商户已发布的任务；打通「商户发布 → 客户看见 → 领取 → 创作」不必猜 URL。

## 现状（已有，本段不重做）
| 路径 | 能力 |
|---|---|
| `GET /api/tasks` | 只列 `status=published` 且未软删的任务 |
| `/customer/tasks` | 列表 + 关键词 + 领取 |
| `/customer/tasks/:id` | 详情 + 领取 |
| `/customer/claims` | 我已领取的任务 |

## 本段改动
- 客户首页信息入口（侧栏与底栏同一套）增加「找任务」→ `/customer/tasks`。与「我领取的任务」并列。数字取 `GET /api/tasks` 的 `total`（或 `items.length`）；失败显示 `—`，加载中显示 `…`。
- 商户「直接发布」成功后，该任务必须出现在客户 `GET /api/tasks` 里（既有契约；本段不改后端）。

## 边界
- `FT-01` 首页有 `customer-entry-tasks`，href=`/customer/tasks`，文案含「找任务」
- `FT-02` 进页后出现列表请求 `GET /api/tasks`（既有 CL-01；本段只断言入口能点到）
- `FT-03` 宽屏侧栏与窄屏底栏都有该入口（与 claims 同套 InfoNav）

## 明确不做
- ❌ 不改商户发布表单字段（发布报错可见性另修，不在本段）
- ❌ 不做推荐 / 关注流 / 商户主页
- ❌ 不把草稿任务暴露给客户

**⛔ 停在这里等确认。** 回复「可以」后再写测试和入口。
