/* 侧栏偏好读写：localStorage 在隐私模式 / 无存储宿主下会抛错或不存在，
   偏好丢失只影响视觉状态，不该让外壳崩掉，故统一降级为 null。 */

export function readPref(key: string): string | null {
  try {
    return window.localStorage.getItem(key)
  } catch {
    return null
  }
}

export function writePref(key: string, value: string): void {
  try {
    window.localStorage.setItem(key, value)
  } catch {
    /* 忽略：下次进入用默认态 */
  }
}
