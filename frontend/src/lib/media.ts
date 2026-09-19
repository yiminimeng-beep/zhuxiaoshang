/**
 * 宽屏门槛与 CSS `min-width: 40rem` 对齐。
 * 测试里用 `mockMatchMedia(true/false)` 驱动，不依赖 jsdom 排版。
 */

import { useEffect, useState } from 'react'

const QUERY = '(min-width: 40rem)'

export function useWideViewport(): boolean {
  const [wide, setWide] = useState(() => readMatch(QUERY))

  useEffect(() => {
    if (typeof window.matchMedia !== 'function') return
    const mql = window.matchMedia(QUERY)
    const sync = () => setWide(mql.matches)
    sync()
    mql.addEventListener('change', sync)
    return () => mql.removeEventListener('change', sync)
  }, [])

  return wide
}

function readMatch(query: string): boolean {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') {
    return false
  }
  return window.matchMedia(query).matches
}
