# 01-auth · 测试计划

> 由 `spec.md` 的「端点」与「边界」逐条转出。**当前状态：全部红**（实现尚未开始）。
> 一个 ID = 一条测试。ID 与测试函数一一对应，见「测试函数」列。
>
> 覆盖：14 个端点的正常路径 + 6 组边界 84 条 + 种子安全 3 条 = **101 条**。

---

## 运行方式

```bash
cd backend
docker compose -f ../docker-compose.yml up -d     # PG:5433 / Redis:6380
.venv/Scripts/python -m pytest -q                 # 全红即为预期
```

前置：Docker Desktop 已启动。若 `docker compose up` 卡在拉镜像，
需在 `~/.docker/daemon.json` 配 `registry-mirrors`（见 memory 记录）。
测试库 `zhuxiaoshang_test` 由 `conftest.py` 自动建，无需手工创建。

测试用**独立库** `zhuxiaoshang_test`（环境变量在 `import app` 之前写入，见 `conftest.py`）。
每个用例前后 `TRUNCATE` 所有表，用例之间零耦合。

---

## A. 注册端点正常路径（`test_auth_register.py`）

| ID | 前置 | 输入 | 期望 | 测试函数 |
|---|---|---|---|---|
| E-01 | 无 | `POST /api/auth/register` 合法 customer | `201` + `access_token`/`refresh_token`/`user` | `test_e01_register_success` |
| E-02 | 无 | 同上但 `role=merchant` + `shop_name`/`category` | `201`，`user.merchant_profile` 存在 | `test_e02_register_merchant_creates_profile` |

## B. 注册与标识符边界（`test_auth_register.py`）

| ID | 前置 | 输入 | 期望 | 测试函数 |
|---|---|---|---|---|
| R-01 | — | `account` 与 `email` 都不填 | `422` | `test_r01_need_account_or_email` |
| R-02 | — | 只填 `account` | `201`，`email IS NULL` | `test_r02_account_only` |
| R-03 | — | 只填 `email` | `201`，`account IS NULL` | `test_r03_email_only` |
| R-04 | — | 两个都填 | `201`，库里两列都有值 | `test_r04_both_identifiers` |
| R-05 | — | `account="abc12"`（5 位） | `422` | `test_r05_account_too_short` |
| R-06 | — | `account` 21 位 | `422` | `test_r06_account_too_long` |
| R-07 | — | `account` 含 `-` | `422` | `test_r07_account_bad_char_dash` |
| R-08 | — | `account` 含中文 | `422` | `test_r08_account_bad_char_cjk` |
| R-09 | — | `account` 含空格 | `422` | `test_r09_account_bad_char_space` |
| R-10 | — | `account="000000"`（纯数字 6 位） | **`201`**（种子账号依赖） | `test_r10_account_pure_digits_allowed` |
| R-11 | 已注册 `Abc123` | 用 `abc123` 登录 | `200`（统一小写归一化） | `test_r11_account_case_insensitive_login` |
| R-12 | 已注册 `Abc123` | 再注册 `abc123` | `409`（归一化后唯一） | `test_r12_account_case_insensitive_unique` |
| R-13 | — | `email="notanemail"`（无 @） | `422` | `test_r13_email_no_at` |
| R-14 | — | `email="a@b"` 之外缺域名形式 `a@` | `422` | `test_r14_email_no_domain` |
| R-15 | 已注册 `A@B.com` | 用 `a@b.com` 登录 | `200` | `test_r15_email_case_insensitive` |
| R-16 | — | `email` 恰好 254 字符 | `201` | `test_r16_email_254_ok` |
| R-17 | — | `email` 255 字符 | `422` | `test_r17_email_255_rejected` |
| R-18 | — | `password` 5 位 | `422` | `test_r18_password_too_short` |
| R-19 | — | `password` 6 位 | `201` | `test_r19_password_6_ok` |
| R-20 | — | `password` 64 位 | `201` | `test_r20_password_64_ok` |
| R-21 | — | `password` 65 位 | `422` | `test_r21_password_65_rejected` |
| R-22 | — | `password` 全空格 | `422` | `test_r22_password_all_spaces` |
| R-23 | 已存在该 `phone` | 再注册同 `phone` | `409` | `test_r23_phone_conflict` |
| R-24 | — | 不传 `phone` | `201` | `test_r24_phone_optional` |
| R-25 | — | `phone` 格式非法（10 位 / 含字母） | `422` | `test_r25_phone_invalid_format` |
| R-26 | — | `role=admin` | `400` | `test_r26_admin_role_rejected` |
| R-27 | — | `role=merchant` 缺 `shop_name` | `422` | `test_r27_merchant_missing_shop_name` |
| R-28 | — | 任意合法注册 | `password_hash` ≠ 明文，且响应体不含 `password` 字段 | `test_r28_password_never_leaks` |

## C. 种子数据安全（`test_auth_seed.py`）

| ID | 前置 | 输入 | 期望 | 测试函数 |
|---|---|---|---|---|
| SD-01 | `APP_ENV=production` | 跑种子脚本 | **非 0 退出 + 库中 0 个默认密码账号 + 有日志** | `test_sd01_seed_refuses_in_production` |
| SD-02 | `APP_ENV=development` | 跑种子脚本 | 三个账号就位，且 `password_hash` 非明文 | `test_sd02_seed_creates_three_accounts` |
| SD-03 | 种子已写入 | — | `000000`/`000001`/`999999` 密码可登录，角色分别为 merchant/customer/admin | `test_sd03_seed_accounts_can_login` |

## D. 登录端点正常路径（`test_auth_login.py`）

| ID | 前置 | 输入 | 期望 | 测试函数 |
|---|---|---|---|---|
| E-03 | 已注册并带 `device_fingerprint` | `POST /api/auth/login` | `200` + `access_token`/`refresh_token`/`user` | `test_e03_login_success` |

## E. 登录与防爆破边界（`test_auth_login.py`）

| ID | 前置 | 输入 | 期望 | 测试函数 |
|---|---|---|---|---|
| L-01 | 已注册 `account` | `identifier=account` | `200` | `test_l01_login_by_account` |
| L-02 | 已注册 `email` | `identifier=email` | `200` | `test_l02_login_by_email` |
| L-03 | — | `identifier` 不存在 | `404` | `test_l03_unknown_identifier_404` |
| L-04 | 已注册 `Abc123` | `identifier=Abc123` / `abc123` | 均 `200` | `test_l04_identifier_case_insensitive` |
| L-05 | 已注册 | 密码错 | `401` | `test_l05_wrong_password_401` |
| L-06 | 用户 `status=banned` | 正确密码 | `403` | `test_l06_banned_user_403` |
| L-07 | 用户 `status=deleted` | 正确密码 | `403` | `test_l07_deleted_user_403` |
| L-08 | — | 连续 5 次密码错后再试 | 第 6 次 `423` | `test_l08_lock_after_5_failures` |
| L-09 | 已锁定 | 15 分钟内正确密码 | 仍 `423` | `test_l09_locked_window_is_15_minutes` |
| L-10 | 已锁定 | 15 分钟后正确密码 | `200` 且 `login_attempt.fail_count=0` | `test_l10_unlock_after_15_minutes` |
| L-11 | `userA.account=X, userB.email=X@y.com` | 把 account 侧锁死 | `userB` 用 email 登录仍 `200`（计数不牵连） | `test_l11_lock_is_per_identifier` |
| L-12 | 已注册 | 登录成功 | `user.last_login_at` 非空且被更新 | `test_l12_last_login_at_updated` |
| L-13 | — | 不传 `device_fingerprint` | `422` | `test_l13_device_fingerprint_required` |
| L-14 | 已登录一次 | 同 `device_fingerprint` 再登录 | `user_device` 行数不变，`last_active_at` 变大 | `test_l14_same_device_no_new_row` |

## F. 令牌与设备端点正常路径（`test_auth_token_device.py`）

| ID | 前置 | 输入 | 期望 | 测试函数 |
|---|---|---|---|---|
| E-04 | 已登录 | `POST /api/auth/refresh` | `200` + 新 access/refresh | `test_e04_refresh_success` |
| E-05 | 已登录 | `POST /api/auth/logout` | `204` | `test_e05_logout_204` |
| E-06 | 已登录 | `GET /api/me` | `200` + `user`（商户含 `merchant_profile`） | `test_e06_get_me` |
| E-07 | 已登录 | `GET /api/me/devices` | `200` + `items` | `test_e07_list_devices` |
| E-08 | 两台设备 | `DELETE /api/me/devices/{id}` | `204` | `test_e08_revoke_one_device` |
| E-09 | 两台设备 | `POST /api/me/devices/revoke-others` | `204` | `test_e09_revoke_others` |

## G. 令牌与设备边界（`test_auth_token_device.py`）

| ID | 前置 | 输入 | 期望 | 测试函数 |
|---|---|---|---|---|
| T-01 | — | 无 `Authorization` 访问 `/api/me` | `401` | `test_t01_no_token_401` |
| T-02 | 已过期 access_token | 访问 `/api/me` | `401` | `test_t02_expired_access_token_401` |
| T-03 | 已刷新一次 | 用**旧的** refresh_token 再刷新 | `401`（旧值立即失效） | `test_t03_old_refresh_token_revoked` |
| T-04 | 设备 `revoked=true` | 用该设备 refresh_token 刷新 | `401` | `test_t04_revoked_device_refresh_401` |
| T-05 | A、B 两设备 | A 登出 | A 的 refresh `revoked=true`，**B 不受影响** | `test_t05_logout_isolated_per_device` |
| T-06 | A、B 两设备 | 在 A 改密码 | 除 A 外全部 `revoked=true`，B 刷新 → `401` | `test_t06_change_password_revokes_others` |
| T-07 | 两台设备 | `DELETE /api/me/devices/{b_id}` | B 的 refresh_token 全部失效 | `test_t07_delete_device_kills_tokens` |
| T-08 | 用户 A、B | A 删 B 的设备 | `403` | `test_t08_cannot_delete_others_device` |
| T-09 | 当前设备 | `DELETE /api/me/devices/{当前}` | `204`（等于登出） | `test_t09_can_delete_current_device` |
| T-10 | 3 台设备 | `revoke-others` | 当前保留，其余全失效 | `test_t10_revoke_others_keeps_current` |
| T-11 | 已 10 台设备 | 第 11 台登录 | `201/200` 成功 + 最老 `last_active_at` 那台被踢 + 有日志 | `test_t11_11th_device_evicts_oldest` |

## H. 个人资料端点正常路径（`test_auth_profile.py`）

| ID | 前置 | 输入 | 期望 | 测试函数 |
|---|---|---|---|---|
| E-10 | 已登录 | `PATCH /api/me` 改 `nickname` | `200` + 新值落库 | `test_e10_patch_me` |
| E-11 | 已登录 | `POST /api/me/password` | `204` + 新密码可登录 | `test_e11_change_password` |

## I. 归属与角色边界（`test_auth_profile.py`）

| ID | 前置 | 输入 | 期望 | 测试函数 |
|---|---|---|---|---|
| O-01 | `status=banned` 且持有有效 token | 访问 `/api/me` | `403` | `test_o01_banned_with_valid_token_403` |
| O-02 | 客户角色 | 访问 `/api/merchant/*` | `403` | `test_o02_customer_blocked_from_merchant_api` |
| O-03 | 商户 A、B | A 访问 B 的数据 | `403`（不是 `404`） | `test_o03_cross_tenant_403_not_404` |
| O-04 | 已登录 | `PATCH /api/me` 传 `account`/`email`/`role`/`status` | `400` 且 DB 值不变 | `test_o04_cannot_patch_protected_fields` |
| O-05 | 已登录 | `nickname` 33 字符 | `422` | `test_o05_nickname_too_long` |
| O-06 | 已登录 | `avatar_url` 非 http/https（如 `javascript:`） | `422` | `test_o06_avatar_url_scheme` |

## J. 关注端点正常路径（`test_auth_follow.py`）

| ID | 前置 | 输入 | 期望 | 测试函数 |
|---|---|---|---|---|
| E-12 | 客户 + 商户 | `POST /api/merchant/{id}/follow` | `201` | `test_e12_follow_201` |
| E-13 | 已关注 | `DELETE /api/merchant/{id}/follow` | `204` | `test_e13_unfollow_204` |
| E-14 | 商户有 1 粉丝 | `GET /api/merchant/{id}/followers/count` | `200` `{count:1}` | `test_e14_follower_count` |
| E-15 | 客户已关注 1 商户 | `GET /api/me/following` | `200` + 1 条 | `test_e15_my_following` |

## K. 关注边界（`test_auth_follow.py`）

| ID | 前置 | 输入 | 期望 | 测试函数 |
|---|---|---|---|---|
| F-01 | 已关注 | 再关注同一商户 | `409` | `test_f01_duplicate_follow_409` |
| F-02 | 已取关 | 再取关 | `404` | `test_f02_unfollow_twice_404` |
| F-03 | 客户 | 关注另一个客户 | `422`（方向被约束） | `test_f03_customer_follow_customer_422` |
| F-04 | 商户 | 关注客户 | `422` | `test_f04_merchant_follow_customer_422` |
| F-05 | 商户 | 关注另一个商户 | `422` | `test_f05_merchant_follow_merchant_422` |
| F-06 | 客户 | 关注自己 | `403` | `test_f06_follow_self_403` |
| F-07 | 商户 `status=deleted` | 关注它 | `422` | `test_f07_follow_deleted_merchant_422` |
| F-08 | 客户 | 关注不存在的 id | `404` | `test_f08_follow_nonexistent_404` |
| F-09 | 已建立关注 | 把**客户**封禁 | 关注关系仍在库里 | `test_f09_follow_survives_customer_ban` |
| F-10 | 已建立关注 | 把**商户**封禁 | `followers/count` 仍 `200`；商户主页访问被拒 | `test_f10_banned_merchant_profile_blocked` |

## L. 注销端点正常路径（`test_auth_close.py`）

| ID | 前置 | 输入 | 期望 | 测试函数 |
|---|---|---|---|---|
| E-16 | 已登录 | `POST /api/me/close` `{password, confirm:true}` | `202` | `test_e16_close_202` |

## M. 注销边界（`test_auth_close.py`）

| ID | 前置 | 输入 | 期望 | 测试函数 |
|---|---|---|---|---|
| C-01 | 已登录 | 缺 `confirm` | `422` | `test_c01_missing_confirm_422` |
| C-02 | 已登录 | 密码错 | `401` | `test_c02_wrong_password_401` |
| C-03 | 注销完成 | — | `status='deleted'` 且 `deleted_at` 非空 | `test_c03_soft_deleted` |
| C-04 | 注销完成 | — | `account`/`email`/`phone` 全为 `NULL` | `test_c04_identifiers_freed` |
| C-05 | 注销完成 | — | `nickname='已注销用户'`，`avatar_url IS NULL` | `test_c05_anonymized_profile` |
| C-06 | 注销完成 | — | `password_hash` 不等于原哈希（不可登录） | `test_c06_password_hash_replaced` |
| C-07 | 注销完成 | 原账号密码登录 | `403` | `test_c07_login_after_close_403` |
| C-08 | 注销完成 | 用原 `account` 新注册 | `201` | `test_c08_account_reusable` |
| C-09 | 有 `reward_grant`/`point_ledger`/`review_log`/`cash_payout` 记录 | 注销 | 四表记录全在，且可按 `user_id` 查到 | `test_c09_audit_tables_retained` |
| C-10 | 商户有 `published` 任务 | 注销 | `409` | `test_c10_merchant_with_published_task_409` |
| C-11 | 已注销 | 再注销 | `403` | `test_c11_double_close_403` |
| C-12 | 未登录 | 注销 | `401` | `test_c12_close_without_token_401` |

---

## 已知取舍

1. **`argon2id` 由测试侧选定** —— `seed_accounts` fixture 与 `SD-*` 用 `argon2-cffi` 生成哈希直插，
   因此**实现必须支持 argon2id 校验**（spec 允许 argon2id 或 bcrypt(cost≥12)，这里锁定前者）。
2. **`T-02` 造过期 token** —— 通过 `app.core.security.create_access_token(..., expires_delta=-1s)` 直造；
   该函数尚未实现，故这条在红阶段会因 import 失败而红，属预期。
3. **`T-11` 的"记日志"** —— 断言 `admin_action_log` 之外，另断言存在一条踢设备日志行；
   日志落库表名在 06 实现时才定，**此条暂标 `xfail` 并加 TODO**，避免测试与未定 schema 死绑。
4. **`C-09` 依赖 05/06 的表** —— 红阶段会因表不存在而红；实现 01 时可先用最小建表脚本支撑。
5. **`O-03`** —— spec 要求跨租户 `403` 而非 `404`，故断言必须严格区分这两个码，不得写成 `in (403,404)`。

## 下一步

全部红之后，按 `E-01 → R-01 → ...` 顺序补实现，一个端点跑完红绿再下一个。
每补一块就跑一次 `pytest -q`，确认**新绿的没有把别的搞红**。
