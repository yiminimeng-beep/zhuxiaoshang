/**
 * 假令牌串。**不造真 JWT**——前端不解码令牌内容，只透传（见 test_plan「测试基建」）。
 * 造真 JWT 只会让测试依赖后端 `pyjwt` 那一侧的实现细节。
 */
export const TOKENS = {
  access: 'fake-access-one',
  refresh: 'fake-refresh-one',
} as const

/** 刷新之后换回来的那一对——TK-04 要断言它被写回了本地 */
export const ROTATED = {
  access: 'fake-access-two',
  refresh: 'fake-refresh-two',
} as const
