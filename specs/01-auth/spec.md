# 01 · 用户信息管理

> 账号 / 邮箱 + 密码登录，三角色，JWT 鉴权；支持注销、关注商户、登录设备管理。

---

## 一句话目标

> 用户用「账号 + 密码」或「邮箱 + 密码」注册/登录，获得 access_token；系统据此区分 `merchant` / `customer` / `admin` 并做资源归属隔离；用户能关注商户、管理登录设备、注销账号。

---

## 数据模型

### `user`
| 字段 | 类型 | 约束 |
|---|---|---|
| id | bigint | 主键，自增 |
| account | varchar(20) | 可空，**唯一**；登录标识符之一 |
| email | varchar(254) | 可空，**唯一**；登录标识符之一 |
| password_hash | varchar(255) | **必填**，argon2id 或 bcrypt(cost>=12) |
| phone | varchar(11) | 可空，**唯一**，仅作联系方式，**不用于登录** |
| role | enum | **必填**，`merchant` / `customer` / `admin` |
| nickname | varchar(32) | **必填** |
| avatar_url | varchar(512) | 可空 |
| status | enum | **必填**，默认 `active`；`active` / `banned` / `deleted` |
| email_verified | boolean | **必填**，默认 false。**本期不做验证流程**，仅留字段 |
| openid / unionid | varchar(64) | 可空，**本期不写入**，为小程序预留 |
| created_at | timestamptz | **必填**，默认 now() |
| last_login_at | timestamptz | 可空 |
| deleted_at | timestamptz | 可空，注销时间 |

**表级约束**
- `CHECK (account IS NOT NULL OR email IS NOT NULL OR status = 'deleted')` —— 存活账号两者至少有一个；已注销行是匿名墓碑，不受此约束
- `account` 格式：6~20 位，仅 `A-Za-z0-9_`，**允许纯数字**（测试账号 `000000` / `000001` 依赖此项）

### `closed_identifier`（注销墓碑）

> 注销要求把 `account` / `email` / `phone` 全部置 null 以释放占用，但登录又必须把
> 「这个标识符曾属于已注销账号」(→ 403) 与「这个标识符从未存在过」(→ 404) 区分开。
> 标识符已不在 `user` 上，只能单独记一张表。
> 墓碑**不阻碍**重新注册——唯一索引只建在 `user` 上，新用户可照常占用同一标识符。

| 字段 | 类型 | 约束 |
|---|---|---|
| identifier | varchar(254) | **必填，主键**（原 account / email / phone，统一小写） |
| user_id | bigint | **必填**，外键 → user.id |
| closed_at | timestamptz | **必填** |

### `merchant_profile`（role=merchant 时必填）
| 字段 | 类型 | 约束 |
|---|---|---|
| user_id | bigint | 主键，外键 → user.id |
| shop_name | varchar(64) | **必填**，2~64 字符 |
| category | varchar(32) | **必填** |
| address | varchar(256) | 可空 |
| logo_url | varchar(512) | 可空 |
| description | varchar(512) | 可空 |
| contact | varchar(64) | 可空 |

### `refresh_token`
| 字段 | 类型 | 约束 |
|---|---|---|
| id | bigint | 主键 |
| user_id | bigint | **必填**，外键 |
| device_id | bigint | **必填**，外键 → user_device.id |
| token_hash | varchar(128) | **必填，唯一** |
| expires_at | timestamptz | **必填**，签发 + 30 天 |
| revoked | boolean | **必填**，默认 false |
| created_at | timestamptz | 必填 |

### `user_device`（登录设备管理）
| 字段 | 类型 | 约束 |
|---|---|---|
| id | bigint | 主键 |
| user_id | bigint | **必填**，外键 |
| device_fingerprint | varchar(64) | **必填**，客户端上报；`(user_id, device_fingerprint)` 唯一 |
| device_name | varchar(64) | 可空，如 `Chrome / Windows` |
| last_ip | varchar(45) | 可空 |
| last_active_at | timestamptz | **必填** |
| revoked | boolean | **必填**，默认 false（强制下线标记） |
| created_at | timestamptz | 必填 |

### `login_attempt`（防爆破，按标识符）
| 字段 | 类型 | 约束 |
|---|---|---|
| identifier | varchar(254) | **必填，主键**（账号或邮箱，统一小写） |
| fail_count | int | **必填**，默认 0 |
| locked_until | timestamptz | 可空 |

### `user_follow`（客户 → 商户）
| 字段 | 类型 | 约束 |
|---|---|---|
| id | bigint | 主键 |
| follower_id | bigint | **必填**，外键 → user（**必须 role=customer**） |
| followee_id | bigint | **必填**，外键 → user（**必须 role=merchant**） |
| created_at | timestamptz | **必填** |
| **唯一约束** | — | `(follower_id, followee_id)` 唯一 |

---

## 端点 / 接口

| 动作 | 输入 | 成功返回 | 失败情况 |
|---|---|---|---|
| 注册 | `POST /api/auth/register`<br>`{account?, email?, password, role, nickname?, shop_name?, category?, phone?}` | `201`<br>`{access_token, refresh_token, user}` | `422` account/email 都没填<br>`422` account 格式非法<br>`422` password < 6 位<br>`422` role=merchant 缺 shop_name/category<br>`409` account 或 email 或 phone 已存在<br>`400` role=admin |
| 登录 | `POST /api/auth/login`<br>`{identifier, password, device_fingerprint, device_name?}` | `200`<br>`{access_token, refresh_token, user}` | `401` 密码错<br>`404` 用户不存在（且 `closed_identifier` 里也没有）<br>`403` 被封禁/已注销<br>`423` 连续错误锁定 |
| 刷新令牌 | `POST /api/auth/refresh`<br>`{refresh_token}` | `200`<br>`{access_token, refresh_token}` | `401` 无效/过期/已吊销/设备已下线 |
| 登出 | `POST /api/auth/logout` | `204` | `401` |
| 查自己 | `GET /api/me` | `200` `user` + `merchant_profile`（商户时） | `401` |
| 改资料 | `PATCH /api/me` | `200` `user` | `400` 试图改 account/email/role/status<br>`422` 字段超长或格式错<br>`409` 换绑的邮箱已被占用 |
| 改密码 | `POST /api/me/password`<br>`{old_password, new_password}` | `204` **并吊销其他设备** | `401` 旧密码错<br>`422` 新密码 < 6 位或与旧密码相同 |
| 注销 | `POST /api/me/close`<br>`{password, confirm: true}` | `202` | `401` 密码错<br>`422` 缺 `confirm`<br>`409` 商户尚有进行中任务 |
| 我关注的商户 | `GET /api/me/following` | `200` `{items,...}` | `401` |
| 关注 / 取关 | `POST` / `DELETE /api/merchant/{id}/follow` | `201` / `204` | `403` 关注自己<br>`422` 目标非商户/已注销<br>`409` 重复关注 |
| 商户粉丝数 | `GET /api/merchant/{id}/followers/count` | `200` `{count}` | `404` |
| 我的设备 | `GET /api/me/devices` | `200` `{items,...}` | `401` |
| 下线单台设备 | `DELETE /api/me/devices/{id}` | `204` | `403` 非本人设备<br>`404` |
| 下线全部其他设备 | `POST /api/me/devices/revoke-others` | `204` | `401` |

### 种子数据（实现时随建表写入）

| account | password | role | 说明 |
|---|---|---|---|
| `000000` | `000000` | merchant | 测试商户 |
| `000001` | `000001` | customer | 测试用户 |
| `999999` | `999999` | admin | 测试管理员（**已确认要**，用于登录 8002 管理后台） |

**⚠️ 安全约束（必须实现）**
- 种子脚本启动时检查环境变量，**`APP_ENV=production` 时拒绝种入任何默认密码账号**，打日志并退出。
- 否则等于把一个密码可猜的商户账号发到线上。
- 密码 6 位纯数字是**为了满足你给的测试账号**而放宽的，属于已知弱点，生产环境必须另行约束（见「明确不做」）。

---

## 边界（每条之后会变成一条测试）

### 注册与标识符
- `account` 与 `email` 都不填 → `422`
- 只填 `account` → `201`，`email` 为 null
- 只填 `email` → `201`，`account` 为 null
- 两个都填 → `201`，两个都存
- `account` 5 位 / 21 位 / 含 `-` / 含中文 / 含空格 → `422`
- `account="000000"`（纯数字 6 位）→ **通过**（种子账号依赖）
- `account` 大小写：注册 `Abc123`，再用 `abc123` 登录 → **`200`**（统一小写归一化后比对）
- 注册 `Abc123`，再注册 `abc123` → `409`（归一化后唯一）
- `email` 格式非法（无 @、无域名）→ `422`
- `email` 大小写同上，统一小写归一化
- `email` 254 字符 → 通过；255 字符 → `422`
- `password` 5 位 → `422`；6 位 → 通过
- `password` 64 位 → 通过；65 位 → `422`
- `password` 全空格 → `422`
- `phone` 已存在 → `409`；`phone` 不填 → 通过
- `phone` 格式非法 → `422`
- 注册 `role=admin` → `400`
- 注册 `role=merchant` 缺 `shop_name` → `422`
- 注册成功后 `password_hash` 不得等于明文，且**不得进入任何接口响应**

### 登录与防爆破
- `identifier` 传 account → `200`；传 email → `200`
- `identifier` 传不存在的值 → `404`
- `identifier` 大小写混用 → `200`
- 密码错 → `401`
- 被封禁用户登录 → `403`；已注销用户登录 → `403`
- 连续 5 次密码错 → 第 6 次 `423`，且后续 15 分钟内一律 `423`
- 15 分钟后正确密码 → `200`，`fail_count` 归零
- **同一个 account 的错误计数不牵连同 email 的其他账号**（反之亦然）
- 登录成功 → `last_login_at` 被更新
- 登录必须带 `device_fingerprint`，缺失 → `422`
- 同一 `device_fingerprint` 再次登录 → `user_device` 不新增行，只更新 `last_active_at`

### 令牌与设备
- 无 `Authorization` 头访问 `/api/me` → `401`
- access_token 过期 → `401`
- 刷新成功后**旧 refresh_token 立即失效**，再用旧的 → `401`
- refresh_token 对应设备被 `revoked=true` → `401`
- 在 A 设备登出 → A 的 refresh_token `revoked=true`；**B 设备不受影响**
- 改密码 → **除当前设备外所有设备的 refresh_token 全部 `revoked=true`**，这些设备再刷新 → `401`
- `DELETE /api/me/devices/{id}` 下线设备 → 该设备 refresh_token 全部失效
- 下线**别人**的设备 → `403`
- 下线当前正在用的设备 → `204`（允许，等于登出）
- `revoke-others` → 保留当前设备，其余全部失效
- 一个用户最多 10 台设备，第 11 台 → 自动踢掉 `last_active_at` 最老的一台（并记日志）

### 关注（客户 → 商户）
- 客户关注商户 → `201`
- 重复关注 → `409`
- 取关 → `204`；再取关 → `404`
- 商户关注客户 → **`422`**（方向被约束）
- 客户关注客户 → `422`
- 商户关注商户 → `422`
- 关注自己 → `403`
- 关注已注销商户 → `422`
- 关注不存在的 id → `404`
- 客户关注后被封禁 → 关注关系保留（不自动清理）
- 商户被封禁 → 其粉丝列表仍可查，但商户主页不可访问

### 注销（软删 + 匿名化）
- 注销需同时提供**正确密码**与 `confirm: true` → 缺任一 → `422` / `401`
- 注销后：
  - `status='deleted'`，`deleted_at` 非空
  - `account` / `email` / `phone` 置 null（**释放占用，可被重新注册**）
  - 三个标识符的**原值写入 `closed_identifier` 墓碑**（小写），供登录侧区分 403 / 404
  - `nickname` → `已注销用户`，`avatar_url` → null
  - `password_hash` → 替换为不可登录的随机值
- 注销后用原账号密码登录 → `403`（命中墓碑；不是 `404`）
- 注销后原 `account` **可被新用户注册** → `201`（墓碑不占唯一索引）
- 注销后 audit 表（`reward_grant` / `point_ledger` / `review_log` / `cash_payout`）**必须全部保留**，且能通过 `user_id` 查到
- 注销用户的历史 `social_post` / `content_job` 走回收站软删（见各模块），**不物理删除**
- 商户尚有 `published` 任务未结束 → `409`，不允许注销
- 重复注销 → `403`
- 未登录注销 → `401`

### 归属与角色
- `status=banned` 用户带**有效** token 访问 → `403`
- 客户访问 `/api/merchant/*` → `403`
- 商户 A 访问商户 B 的数据 → `403`（不是 404）
- 改资料时传 `account` / `email` / `role` / `status` → `400`，数据库不得变更
- `PATCH /api/me` 试图把 `nickname` 改成 33 字符 → `422`
- `avatar_url` 传非 http/https → `422`

---

## 明确不做

- ❌ 不做手机号登录、不做短信验证码（**整个 `verify_code` 表与发码接口已删除**）
- ❌ 不做邮箱验证流程（`email_verified` 字段已留，本期不写入 true）⚠️ **已知风险：可抢注他人邮箱**
- ❌ 不做密码强度策略（允许 6 位纯数字，为测试账号让路）⚠️ **生产环境上线前必须补**
- ❌ 不做找回密码 / 重置密码（忘记只能联系管理员）
- ❌ 不做 OAuth（微信 / QQ / 支付宝 / GitHub）—— `openid` / `unionid` 字段预留不写入
- ❌ 不做 MFA / 二次验证
- ❌ 不做管理员注册接口（`admin` 只由种子脚本写入）
- ❌ 不做多角色（一个 user 只有一个 role）
- ❌ 不做关注后的通知推送（关注了不会收到短信/推送）
- ❌ 不做商户之间的关注关系
- ❌ 不做用户间私信 / 聊天
- ❌ 不做设备指纹的强校验（客户端可伪造，仅作设备管理用，**不作为安全边界**）
- ❌ 不做注销后的数据导出（GDPR 式导出）
- ❌ 不做账号冻结 / 自助解封
