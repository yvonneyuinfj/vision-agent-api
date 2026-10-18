import { describe, it, expect } from 'vitest'
import { defectDiff, diffSummaryText, hasRevision } from './defectDiff'
import type { DefectItem } from '../api/types'

function d(over: Partial<DefectItem> = {}): DefectItem {
  return {
    defect_id: 'D001',
    defect_type: 'BP',
    defect_name_cn: '模糊的真缺陷',
    confidence: 0.7,
    severity: 'high',
    location: { type: 'bbox', bbox: [1, 2, 3, 4] },
    ...over,
  }
}

describe('defectDiff（与后端 _defect_revision_diff 同构）', () => {
  it('未修订 → 全零，hasRevision=false', () => {
    const base = [d(), d({ defect_id: 'D002' })]
    const diff = defectDiff(base, base.map((x) => ({ ...x })))
    expect(diff).toEqual({ added: 0, excluded: 0, reclassified: 0, severity_changed: 0, moved: 0, removed: 0 })
    expect(hasRevision(diff)).toBe(false)
  })

  it('人工新增（source=manual 或 id 不在基线）→ added', () => {
    const base = [d()]
    const draft = [...base, d({ defect_id: 'M001', source: 'manual' })]
    expect(defectDiff(base, draft).added).toBe(1)
  })

  it('标记排除 → excluded；排除优先于改类（一条只计一次）', () => {
    const base = [d(), d({ defect_id: 'D002' })]
    const draft = [
      { ...base[0], human_rejected: true },
      { ...base[1], human_rejected: true, defect_type: 'FI' },
    ]
    const diff = defectDiff(base, draft)
    expect(diff.excluded).toBe(2)
    expect(diff.reclassified).toBe(0)
  })

  it('改类/改严重度/移框分列计数', () => {
    const base = [d(), d({ defect_id: 'D002' }), d({ defect_id: 'D003' })]
    const draft = [
      { ...base[0], defect_type: 'SP' },
      { ...base[1], severity: 'low' as const },
      { ...base[2], location: { type: 'bbox', bbox: [9, 9, 9, 9] as [number, number, number, number] } },
    ]
    const diff = defectDiff(base, draft)
    expect(diff.reclassified).toBe(1)
    expect(diff.severity_changed).toBe(1)
    expect(diff.moved).toBe(1)
  })

  it('bbox 比较只看契约键 location.bbox（bbox_xyxy 变化不计 moved，对齐后端）', () => {
    const base = [d({ location: { bbox_xyxy: [1, 2, 3, 4] } })]
    const draft = [d({ location: { bbox_xyxy: [9, 9, 9, 9] } })]
    expect(defectDiff(base, draft).moved).toBe(0)
  })

  it('硬删基线条目 → removed', () => {
    const base = [d(), d({ defect_id: 'D002' })]
    expect(defectDiff(base, [base[0]]).removed).toBe(1)
  })

  it('存量无 id 数据：索引兜底 key 与补号后的 __idx_i 对齐（不误报 added/removed）', () => {
    const base = [d({ defect_id: null }), d({ defect_id: null, defect_type: 'SP' })]
    const draft = [
      { ...base[0], defect_id: '__idx_0', human_rejected: true },
      { ...base[1], defect_id: '__idx_1' },
    ]
    const diff = defectDiff(base, draft)
    expect(diff.excluded).toBe(1)
    expect(diff.added).toBe(0)
    expect(diff.removed).toBe(0)
  })

  it('diffSummaryText 中文摘要', () => {
    const text = diffSummaryText({ added: 1, excluded: 2, reclassified: 1, severity_changed: 0, moved: 1, removed: 0 })
    expect(text).toBe('新增 1、排除 2、改类 1、移框 1')
  })
})
