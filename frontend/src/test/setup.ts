import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterEach } from 'vitest'

afterEach(() => {
  cleanup()
  // localStorage 是登录态的唯一真相（spec「鉴权与令牌」）。
  // 不在这里清，「上一个用例写进去的令牌」会漏进下一个用例，
  // 而那种绿是假绿——测的其实是上一条用例的残留。
  localStorage.clear()
  // 回跳目标在 sessionStorage；不清算会让「回跳到哪」跨用例串味
  sessionStorage.clear()
})
