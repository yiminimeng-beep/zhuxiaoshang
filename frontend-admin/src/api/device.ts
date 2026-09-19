/**
 * 与 8001 **共用**同一个 `zxs.device` 键，这是有意的：设备指纹描述的是
 * 「这台浏览器」，不是「这个前端」。同一个浏览器在 `/` 与 `/admin` 登录，
 * 后端看到的是同一台设备——那正是事实。
 *
 * 后端登录**要求** `device_fingerprint`（缺失即 422），refresh_token 也挂在
 * 设备行上。
 */
const DEVICE_KEY = 'zxs.device'

export function deviceFingerprint(): string {
  let value = localStorage.getItem(DEVICE_KEY)
  if (!value) {
    value = `web-${Math.random().toString(36).slice(2, 12)}`
    localStorage.setItem(DEVICE_KEY, value)
  }
  return value
}
