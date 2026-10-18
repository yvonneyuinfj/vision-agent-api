import { describe, it, expect } from 'vitest'
import { bboxToRect, SEVERITY_COLORS } from './bbox'

describe('bboxToRect', () => {
  it('转换 [x1,y1,x2,y2] 为宽高', () => {
    expect(bboxToRect([10, 20, 110, 70])).toEqual({ x: 10, y: 20, width: 100, height: 50 })
  })
})

describe('SEVERITY_COLORS', () => {
  it('high 危险红 / medium 警告琥珀 / low 信息蓝', () => {
    expect(SEVERITY_COLORS.high).toBe('#f0756b')
    expect(SEVERITY_COLORS.medium).toBe('#f0b75e')
    expect(SEVERITY_COLORS.low).toBe('#6ba3ff')
  })
})