import dayjs from 'dayjs'
import utc from 'dayjs/plugin/utc'
import type { Dayjs } from 'dayjs'

// 平台面向北京时区业务：后端以 utcnow() 生成时刻，但 SQLite DATETIME 方言写库
// 时会丢弃时区（API 吐出的是无时区 naive UTC 串）；展示层必须把无时区串按
// **UTC 语义**解析（dayjs.utc 恰好如此）再固定换算为北京时间墙钟（UTC+8），
// 不随浏览器/机器时区漂移（否则差 8 小时且离开 Beijing 时区的机器会显示成 UTC）。
// 兼容带 Z/+offset 后缀的绝对时刻字符串：dayjs.utc 同样解析为正确 UTC 时刻。
dayjs.extend(utc)

const BEIJING_OFFSET_MIN = 8 * 60

/** 后端 UTC ISO 串 → 北京时间展示；非法/空值回退占位符 */
export function formatBeijing(ts?: string | null, pattern = 'YYYY-MM-DD HH:mm:ss'): string {
  if (!ts) return '—'
  const d = dayjs.utc(ts)
  if (!d.isValid()) return '—'
  return d.utcOffset(BEIJING_OFFSET_MIN).format(pattern)
}

/** 北京时间「今天 00:00」对应的绝对时刻（UTC 模式），用于跨日统计边界比较 */
export function beijingTodayStart(): Dayjs {
  return dayjs.utc().utcOffset(BEIJING_OFFSET_MIN).startOf('day').utc()
}