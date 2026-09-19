import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterEach } from 'vitest'

afterEach(() => {
  cleanup()
  // 登录态只存在 localStorage 里。不在这里清，上一条用例写进去的 admin 令牌
  // 会漏进下一条——那种绿测的是残留，不是本事。
  localStorage.clear()
  sessionStorage.clear()
})
