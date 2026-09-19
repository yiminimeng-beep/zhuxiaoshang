const DEVICE_KEY = 'zxs.device'

/**
 * 后端登录**要求** `device_fingerprint`（缺失即 422），refresh_token 也挂在
 * 设备行上。按浏览器持久化一台设备：同一浏览器始终是同一台，
 * 换浏览器才是新设备（那正是 `max_devices_per_user` 想管的事）。
 */
export function deviceFingerprint(): string {
  let value = localStorage.getItem(DEVICE_KEY)
  if (!value) {
    value = `web-${Math.random().toString(36).slice(2, 12)}`
    localStorage.setItem(DEVICE_KEY, value)
  }
  return value
}
