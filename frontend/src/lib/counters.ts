import { useCallback, useEffect, useState } from 'react'

import { apiFetch } from '../api/client'

export type CounterStatus = 'loading' | 'ok' | 'empty' | 'error'

export interface CounterCell {
  status: CounterStatus
  value: number
}

export interface CounterSpec {
  path: string
  pick: (data: unknown) => number
}

/** 列表类端点取总数：优先 `total`，没有就退回 `items.length` */
export function totalOf(data: unknown): number {
  const box = data as { total?: unknown; items?: unknown } | null
  if (typeof box?.total === 'number') return box.total
  return Array.isArray(box?.items) ? box.items.length : 0
}

export function balanceOf(data: unknown): number {
  const value = (data as { balance?: unknown } | null)?.balance
  return typeof value === 'number' ? value : 0
}

function blank(specs: Record<string, CounterSpec>): Record<string, CounterCell> {
  return Object.fromEntries(
    Object.keys(specs).map((key) => [key, { status: 'loading', value: 0 } satisfies CounterCell]),
  )
}

export interface Counters {
  cells: Record<string, CounterCell>
  /** 三个全挂才算整页错误——一个接口挂不得拖垮另外两张卡 */
  allFailed: boolean
  retry: () => void
}

/**
 * 每张卡**各发各的请求、各记各的成败**。故意不写成「先并行取三个、
 * 任一失败就整体 reject」：那样一个 500 会连带清掉另外两张已经拿到的数据。
 *
 * `key` 只用来标记「换了一套指标」，真正要变的是 `retry` 推的 `attempt`——
 * 直接把 `specs` 放进依赖数组会因为对象字面量每次渲染都是新的而死循环，
 * 所以调用方必须传模块级常量。
 */
export function useCounters(key: string, specs: Record<string, CounterSpec>): Counters {
  const [cells, setCells] = useState<Record<string, CounterCell>>(() => blank(specs))
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    let alive = true
    setCells(blank(specs))
    for (const [name, spec] of Object.entries(specs)) {
      void (async () => {
        let cell: CounterCell
        try {
          const value = spec.pick(await apiFetch(spec.path))
          cell = { status: value > 0 ? 'ok' : 'empty', value }
        } catch {
          cell = { status: 'error', value: 0 }
        }
        if (!alive) return
        setCells((prev) => ({ ...prev, [name]: cell }))
      })()
    }
    return () => {
      alive = false
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, attempt])

  const retry = useCallback(() => setAttempt((n) => n + 1), [])
  const allFailed =
    Object.keys(specs).length > 0 &&
    Object.values(cells).every((cell) => cell.status === 'error')

  return { cells, allFailed, retry }
}
