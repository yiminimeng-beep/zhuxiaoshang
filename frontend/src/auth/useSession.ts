import { useSyncExternalStore } from 'react'

import { readSession, subscribeSession, type Session } from '../api/session'

/**
 * 订阅登录态。清空令牌（例如刷新失败）会触发重渲染，守卫随即把用户送到
 * `/login`——不需要任何命令式跳转。
 */
export function useSession(): Session | null {
  return useSyncExternalStore(subscribeSession, readSession, readSession)
}
