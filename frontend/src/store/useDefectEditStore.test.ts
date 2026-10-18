import { describe, it, expect, beforeEach } from 'vitest'
import {
  NEW_DEFECT_TYPE,
  hasUntypedManual,
  selectDiff,
  useDefectEditStore,
} from './useDefectEditStore'
import type { DefectItem } from '../api/types'

function d(over: Partial<DefectItem> = {}): DefectItem {
  return {
    defect_id: 'D001',
    defect_type: 'BP',
    defect_name_cn: '模糊的真缺陷',
    confidence: 0.7,
    severity: 'high',
    location: { type: 'bbox', bbox: [10, 10, 90, 90] },
    ...over,
  }
}

const store = () => useDefectEditStore.getState()

beforeEach(() => {
  store().closeEditor()
})

describe('useDefectEditStore（缺陷修订编辑会话）', () => {
  it('openEditor 播种 baseline/draft（深拷贝隔离），存量空 id 补 __idx_i', () => {
    store().openEditor('T1', 'input-0', [d(), d({ defect_id: null, defect_type: 'SP' })])
    const s = store()
    expect(s.open).toBe(true)
    expect(s.taskId).toBe('T1')
    expect(s.draft[1].defect_id).toBe('__idx_1')
    // draft 与 baseline 隔离：改 draft 不污染 baseline
    s.patchDefect('D001', { defect_type: 'FI' })
    expect(store().baseline[0].defect_type).toBe('BP')
  })

  it('同任务重开保留未提交草稿；换任务重建基线', () => {
    store().openEditor('T1', 'input-0', [d()])
    store().toggleReject('D001')
    store().openEditor('T1', 'input-0', [d()])
    expect(store().draft[0].human_rejected).toBe(true)
    store().openEditor('T2', 'input-0', [d()])
    expect(store().draft[0].human_rejected).toBeUndefined()
    expect(store().taskId).toBe('T2')
  })

  it('addDefect：人工新增条目（source=manual、占位类型、置信度 null、框坐标归一）', () => {
    store().openEditor('T1', 'input-0', [d()])
    store().addDefect([200, 100, 50, 30]) // 任意方向拖拽 → clamp 归一
    const added = store().draft[1]
    expect(added.source).toBe('manual')
    expect(added.defect_type).toBe(NEW_DEFECT_TYPE)
    expect(added.confidence).toBeNull()
    expect(added.location).toEqual({ type: 'bbox', bbox: [50, 30, 200, 100] })
    expect(added.defect_id).toMatch(/^M/)
    expect(store().selectedId).toBe(added.defect_id)
    // 新增后自动回到选择工具
    expect(store().tool).toBe('select')
    expect(hasUntypedManual(store())).toBe(true)
  })

  it('patchDefect：模型条目首次修改落 revised 原值快照；人工条目不落', () => {
    store().openEditor('T1', 'input-0', [d()])
    store().patchDefect('D001', { defect_type: 'SP' })
    expect(store().draft[0].revised).toEqual({ defect_type: 'BP', severity: 'high' })
    // 二次修改不覆盖最初快照
    store().patchDefect('D001', { severity: 'low' })
    expect(store().draft[0].revised).toEqual({ defect_type: 'BP', severity: 'high' })

    store().addDefect([1, 2, 3, 4])
    const mid = store().draft[1].defect_id!
    store().patchDefect(mid, { defect_type: 'FI' })
    expect(store().draft[1].revised).toBeUndefined()
    expect(hasUntypedManual(store())).toBe(false)
  })

  it('moveDefect：写回 bbox 并为模型条目落 location 原值快照', () => {
    store().openEditor('T1', 'input-0', [d()])
    store().moveDefect('D001', [300, 200, 100, 50])
    expect(store().draft[0].location).toEqual({ type: 'bbox', bbox: [100, 50, 300, 200] })
    expect(store().draft[0].revised).toMatchObject({ location: { type: 'bbox', bbox: [10, 10, 90, 90] } })
  })

  it('toggleReject 翻转排除标记（条目保留不硬删）', () => {
    store().openEditor('T1', 'input-0', [d()])
    store().toggleReject('D001')
    expect(store().draft[0].human_rejected).toBe(true)
    store().toggleReject('D001')
    expect(store().draft[0].human_rejected).toBe(false)
  })

  it('removeDefect：仅人工新增条目可硬删，模型条目忽略', () => {
    store().openEditor('T1', 'input-0', [d()])
    store().removeDefect('D001')
    expect(store().draft.length).toBe(1)
    store().addDefect([1, 2, 3, 4])
    const mid = store().draft[1].defect_id!
    store().removeDefect(mid)
    expect(store().draft.length).toBe(1)
  })

  it('resetDraft 回到基线；closeEditor 清空会话', () => {
    store().openEditor('T1', 'input-0', [d()])
    store().toggleReject('D001')
    store().resetDraft()
    expect(store().draft[0].human_rejected).toBeUndefined()
    store().toggleReject('D001')
    store().closeEditor()
    expect(store().open).toBe(false)
    expect(store().taskId).toBeNull()
    expect(store().draft).toEqual([])
  })

  it('selectDiff 汇总修订（排除1+新增1）', () => {
    store().openEditor('T1', 'input-0', [d()])
    store().toggleReject('D001')
    store().addDefect([1, 2, 30, 40])
    const diff = selectDiff(store())
    expect(diff.excluded).toBe(1)
    expect(diff.added).toBe(1)
  })
})
