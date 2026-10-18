import { describe, it, expect } from 'vitest'
import { formatBeijing, beijingTodayStart } from './time'

describe('formatBeijing', () => {
  it('无时区串（后端 SQLite 丢 tz 的真实形态）按 UTC 语义解析为北京墙钟', () => {
    // 库内为 utcnow() 生成的 naive UTC：10:00 UTC = 北京 18:00
    expect(formatBeijing('2026-08-18T10:00:00')).toBe('2026-08-18 18:00:00')
    expect(formatBeijing('2026-08-18T10:00:00.000000')).toBe('2026-08-18 18:00:00')
  })

  it('带时区偏移的绝对时刻同样换算到北京', () => {
    // 2026-08-18T10:00:00Z = 北京 18:00:00
    expect(formatBeijing('2026-08-18T10:00:00Z')).toBe('2026-08-18 18:00:00')
    expect(formatBeijing('2026-08-18T10:00:00+00:00', 'MM-DD HH:mm')).toBe('08-18 18:00')
    // 带其他 offset 的绝对时刻同样换算到北京
    expect(formatBeijing('2026-08-18T02:00:00-08:00')).toBe('2026-08-18 18:00:00')
    // 报告 created_at 是后端显式 Asia/Shanghai 的 +08:00 绝对时刻
    expect(formatBeijing('2026-08-18T18:00:00+08:00')).toBe('2026-08-18 18:00:00')
  })

  it('空值与非法值回退占位符', () => {
    expect(formatBeijing(null)).toBe('—')
    expect(formatBeijing(undefined)).toBe('—')
    expect(formatBeijing('not-a-date')).toBe('—')
  })
})

describe('beijingTodayStart', () => {
  it('返回北京日历日 00:00 对应的 UTC 绝对时刻', () => {
    // UTC+8 前一日 16:00 == 北京当日 00:00
    const start = beijingTodayStart()
    const utc = start.format('HH:mm')
    expect(utc).toBe('16:00')
  })
})