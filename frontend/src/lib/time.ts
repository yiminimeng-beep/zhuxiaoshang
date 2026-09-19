/**
 * 时间只有两种形态，中间**没有第三种**：
 *
 * - 存储/传输：带偏移的 ISO 字符串（后端给的是 `+00:00`）
 * - 屏幕/输入框：**北京时间（UTC+8）墙上时钟**
 *
 * 机器时区在这里被完全绕开——先给时刻加 8 小时，再用 `getUTC*` 读，
 * 于是同一个时刻在任何时区的机器上都渲染成同一个北京钟点。
 * 用 `toLocaleString('zh-CN', {timeZone:'Asia/Shanghai'})` 也能得到同样的
 * 结果，但它跨 ICU 版本会变（不同 Node 的小时制、空格形态不一样），
 * 测试里就成了看环境脸色的断言。
 */

const CST_OFFSET_MS = 8 * 60 * 60 * 1000
const CST_SUFFIX = '+08:00'

/** 取不到就是取不到，**不编时间** */
const MISSING = '—'

function pad(value: number): string {
  return String(value).padStart(2, '0')
}

function parse(iso: string | null | undefined): Date | null {
  if (!iso) return null
  const date = new Date(iso)
  return Number.isNaN(date.getTime()) ? null : date
}

/** 把时刻挪成北京墙上时钟（返回的 Date 只用它的 UTC 读数） */
function shifted(date: Date): Date {
  return new Date(date.getTime() + CST_OFFSET_MS)
}

/** `2026-09-20 10:00`（北京时间） */
export function formatCst(iso: string | null | undefined): string {
  const date = parse(iso)
  if (!date) return MISSING
  const t = shifted(date)
  return (
    `${t.getUTCFullYear()}-${pad(t.getUTCMonth() + 1)}-${pad(t.getUTCDate())}` +
    ` ${pad(t.getUTCHours())}:${pad(t.getUTCMinutes())}`
  )
}

/** `18:20`（北京时间）。自动保存回执只关心钟点 */
export function formatCstClock(iso: string | null | undefined): string {
  const date = parse(iso)
  if (!date) return MISSING
  const t = shifted(date)
  return `${pad(t.getUTCHours())}:${pad(t.getUTCMinutes())}`
}

/** ISO → `<input type="datetime-local">` 的值（北京墙上时钟，不带偏移） */
export function toInputValue(iso: string | null | undefined): string {
  const date = parse(iso)
  if (!date) return ''
  const t = shifted(date)
  return (
    `${t.getUTCFullYear()}-${pad(t.getUTCMonth() + 1)}-${pad(t.getUTCDate())}` +
    `T${pad(t.getUTCHours())}:${pad(t.getUTCMinutes())}`
  )
}

/**
 * `<input type="datetime-local">` 的值 → 带偏移的 ISO。
 *
 * 输入框吐出来的 `2026-09-20T10:00` 是**裸串**：直接送后端，Pydantic 会
 * 解出一个 naive datetime，后端拿它跟 aware 的 `now()` 比较当场 `TypeError`
 * → 500。所以这里必须补上 `+08:00`——用户填的是北京钟点，这是已知事实，
 * 不是猜测。
 */
export function toIso(local: string): string | null {
  if (!local) return null
  const date = new Date(`${local}${CST_SUFFIX}`)
  if (Number.isNaN(date.getTime())) return null
  return date.toISOString()
}

/** 列表里的时间窗：`2026-09-20 10:00 → 2026-09-30 10:00` */
export function windowLabel(
  start: string | null | undefined,
  end: string | null | undefined,
): string {
  return `${formatCst(start)} → ${formatCst(end)}`
}
