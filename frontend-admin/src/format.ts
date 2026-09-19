const CST_OFFSET_MS = 8 * 60 * 60 * 1000

/**
 * 按**北京时间**显示。不用 `toLocaleString()`：那会跟着运行机器的时区变，
 * 运营在北京看到的是「今天」，同事在 CI 上跑出的是昨天。这条口径与
 * 06 的「今日使用量按 UTC+8 切日」是同一套。
 */
export function formatCst(iso: string): string {
  const at = new Date(iso)
  if (Number.isNaN(at.getTime())) return iso
  const shifted = new Date(at.getTime() + CST_OFFSET_MS)
  const pad = (n: number) => String(n).padStart(2, '0')
  return (
    `${shifted.getUTCFullYear()}-${pad(shifted.getUTCMonth() + 1)}-${pad(shifted.getUTCDate())}` +
    ` ${pad(shifted.getUTCHours())}:${pad(shifted.getUTCMinutes())}`
  )
}
