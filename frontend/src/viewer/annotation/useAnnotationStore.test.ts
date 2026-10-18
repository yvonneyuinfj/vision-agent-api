import { beforeEach, describe, expect, it } from 'vitest'
import { useAnnotationStore } from './useAnnotationStore'
import type { AnnotationShape } from './annotationTypes'

function makeShape(overrides: Partial<AnnotationShape> = {}): AnnotationShape {
  return {
    id: 's1', imageKey: 'img-1', tool: 'line',
    start: { x: 0, y: 0 }, end: { x: 50, y: 50 },
    color: '#f0756b', strokeWidth: 4, dashed: false,
    ...overrides,
  }
}

const reset = () => useAnnotationStore.setState({
  shapesByImage: {}, undoStacks: {}, redoStacks: {}, badgeSeqs: {},
  tool: null, selectedId: null, textDraft: null, pickSample: null, pickedColor: null,
  panelOpen: false,
  style: { color: '#f0756b', strokeWidth: 4, dashed: false },
})

beforeEach(reset)

describe('commitShape', () => {
  it('在同一图内不可变追加，并压入撤销栈', () => {
    const store = useAnnotationStore.getState()
    store.commitShape('img-1', makeShape({ id: 's1' }))
    const after = useAnnotationStore.getState()
    expect(after.shapesByImage['img-1']).toHaveLength(1)
    expect(after.undoStacks['img-1']).toHaveLength(1)
    // 追加不修改原数组引用
    const prevSnap = after.undoStacks['img-1'][0]
    expect(prevSnap.shapes).toHaveLength(0)
  })

  it('不同图像之间批注互不干扰', () => {
    const store = useAnnotationStore.getState()
    store.commitShape('img-1', makeShape({ id: 's1' }))
    store.commitShape('img-2', makeShape({ id: 's2', imageKey: 'img-2' }))
    const after = useAnnotationStore.getState()
    expect(after.shapesByImage['img-1']).toHaveLength(1)
    expect(after.shapesByImage['img-2']).toHaveLength(1)
    expect(after.shapesByImage['img-1'][0].id).toBe('s1')
  })
})

describe('undo', () => {
  it('恢复到上一次提交前的形状，但样式（颜色/线宽）不回滚', () => {
    const store = useAnnotationStore.getState()
    store.commitShape('img-1', makeShape({ id: 's1' }))
    useAnnotationStore.getState().setColor('#54c4dc')
    useAnnotationStore.getState().commitShape('img-1', makeShape({ id: 's2', color: '#54c4dc' }))
    useAnnotationStore.getState().undo('img-1')
    const after = useAnnotationStore.getState()
    expect(after.shapesByImage['img-1'].map((s) => s.id)).toEqual(['s1'])
    expect(after.style.color).toBe('#54c4dc')
  })

  it('空栈时 undo 是幂等的', () => {
    useAnnotationStore.getState().undo('img-1')
    expect(useAnnotationStore.getState().shapesByImage['img-1'] ?? []).toHaveLength(0)
  })
})

describe('removeSelected / clearImage', () => {
  it('删除选中批注并清空选区，可被撤销', () => {
    const store = useAnnotationStore.getState()
    store.commitShape('img-1', makeShape({ id: 's1' }))
    store.commitShape('img-1', makeShape({ id: 's2' }))
    useAnnotationStore.getState().setSelectedId('s1')
    useAnnotationStore.getState().removeSelected('img-1')
    const after = useAnnotationStore.getState()
    expect(after.shapesByImage['img-1'].map((s) => s.id)).toEqual(['s2'])
    expect(after.selectedId).toBeNull()
    useAnnotationStore.getState().undo('img-1')
    expect(useAnnotationStore.getState().shapesByImage['img-1']).toHaveLength(2)
  })

  it('无选中时删除不做任何事', () => {
    const store = useAnnotationStore.getState()
    store.commitShape('img-1', makeShape({ id: 's1' }))
    const before = useAnnotationStore.getState().shapesByImage['img-1']
    useAnnotationStore.getState().removeSelected('img-1')
    expect(useAnnotationStore.getState().shapesByImage['img-1']).toBe(before)
  })

  it('清除本图全部批注，且空图清除是幂等的', () => {
    const store = useAnnotationStore.getState()
    store.commitShape('img-1', makeShape({ id: 's1' }))
    useAnnotationStore.getState().clearImage('img-1')
    expect(useAnnotationStore.getState().shapesByImage['img-1']).toHaveLength(0)
    const stackAfterClear = useAnnotationStore.getState().undoStacks['img-1'].length
    useAnnotationStore.getState().clearImage('img-1')
    // 空图再次清除：不产生新的撤销记录
    expect(useAnnotationStore.getState().undoStacks['img-1']).toHaveLength(stackAfterClear)
  })
})

describe('工具与样式状态', () => {
  it('setTool 激活绘制时清空选区', () => {
    useAnnotationStore.getState().setSelectedId('s1')
    useAnnotationStore.getState().setTool('arrow')
    expect(useAnnotationStore.getState().tool).toBe('arrow')
    expect(useAnnotationStore.getState().selectedId).toBeNull()
  })

  it('toggleDashed 翻转虚线开关', () => {
    const before = useAnnotationStore.getState().style.dashed
    useAnnotationStore.getState().toggleDashed()
    expect(useAnnotationStore.getState().style.dashed).toBe(!before)
  })

  it('setStrokeWidth 覆盖线宽', () => {
    useAnnotationStore.getState().setStrokeWidth(6)
    expect(useAnnotationStore.getState().style.strokeWidth).toBe(6)
  })
})

describe('redo', () => {
  it('undo 后可 redo 恢复，redo 后可再 undo', () => {
    const store = useAnnotationStore.getState()
    store.commitShape('img-1', makeShape({ id: 's1' }))
    store.commitShape('img-1', makeShape({ id: 's2' }))
    useAnnotationStore.getState().undo('img-1')
    expect(useAnnotationStore.getState().shapesByImage['img-1'].map((s) => s.id)).toEqual(['s1'])
    useAnnotationStore.getState().redo('img-1')
    expect(useAnnotationStore.getState().shapesByImage['img-1'].map((s) => s.id)).toEqual(['s1', 's2'])
    useAnnotationStore.getState().undo('img-1')
    expect(useAnnotationStore.getState().shapesByImage['img-1'].map((s) => s.id)).toEqual(['s1'])
  })

  it('新提交清空 redo 栈', () => {
    const store = useAnnotationStore.getState()
    store.commitShape('img-1', makeShape({ id: 's1' }))
    useAnnotationStore.getState().undo('img-1')
    useAnnotationStore.getState().commitShape('img-1', makeShape({ id: 's2' }))
    expect(useAnnotationStore.getState().redoStacks['img-1'] ?? []).toHaveLength(0)
    useAnnotationStore.getState().redo('img-1')
    expect(useAnnotationStore.getState().shapesByImage['img-1'].map((s) => s.id)).toEqual(['s2'])
  })

  it('空 redo 栈时幂等', () => {
    useAnnotationStore.getState().redo('img-1')
    expect(useAnnotationStore.getState().shapesByImage['img-1'] ?? []).toHaveLength(0)
  })
})

describe('橡皮擦 removeShape', () => {
  it('删除指定批注、压撤销栈，可被撤销', () => {
    const store = useAnnotationStore.getState()
    store.commitShape('img-1', makeShape({ id: 's1' }))
    store.commitShape('img-1', makeShape({ id: 's2' }))
    useAnnotationStore.getState().removeShape('img-1', 's1')
    expect(useAnnotationStore.getState().shapesByImage['img-1'].map((s) => s.id)).toEqual(['s2'])
    useAnnotationStore.getState().undo('img-1')
    expect(useAnnotationStore.getState().shapesByImage['img-1'].map((s) => s.id)).toEqual(['s1', 's2'])
  })

  it('删除不存在的 id 不做任何事', () => {
    const store = useAnnotationStore.getState()
    store.commitShape('img-1', makeShape({ id: 's1' }))
    const before = useAnnotationStore.getState().shapesByImage['img-1']
    useAnnotationStore.getState().removeShape('img-1', 'ghost')
    expect(useAnnotationStore.getState().shapesByImage['img-1']).toBe(before)
  })
})

describe('序号标记', () => {
  it('consumeBadgeSeq 依次递增，resetBadgeSeq 回到 1（新分组）', () => {
    const store = useAnnotationStore.getState()
    expect(store.consumeBadgeSeq('img-1')).toBe(1)
    expect(store.consumeBadgeSeq('img-1')).toBe(2)
    useAnnotationStore.getState().resetBadgeSeq('img-1')
    expect(useAnnotationStore.getState().consumeBadgeSeq('img-1')).toBe(1)
  })

  it('不同图像的序号计数互不干扰', () => {
    const store = useAnnotationStore.getState()
    store.consumeBadgeSeq('img-1')
    expect(store.consumeBadgeSeq('img-2')).toBe(1)
    expect(store.consumeBadgeSeq('img-1')).toBe(2)
  })
})

describe('取色状态', () => {
  const sample = { screenX: 10, screenY: 20, nx: 5, ny: 5, hex: '#112233', rgb: 'rgb(17, 34, 51)', hsl: 'hsl(210, 50%, 13%)' }

  it('lockPick 写入 pickedColor 并同步为当前笔色', () => {
    useAnnotationStore.getState().lockPick({ hex: sample.hex, rgb: sample.rgb, hsl: sample.hsl })
    const after = useAnnotationStore.getState()
    expect(after.pickedColor?.hex).toBe(sample.hex)
    expect(after.style.color).toBe(sample.hex)
  })

  it('clearPick 清空采样与锁定', () => {
    useAnnotationStore.getState().setPickSample(sample)
    useAnnotationStore.getState().lockPick({ hex: sample.hex, rgb: sample.rgb, hsl: sample.hsl })
    useAnnotationStore.getState().clearPick()
    const after = useAnnotationStore.getState()
    expect(after.pickSample).toBeNull()
    expect(after.pickedColor).toBeNull()
  })

  it('离开取色模式（setTool 非 picker）时自动清空取色浮层', () => {
    useAnnotationStore.getState().setTool('picker')
    useAnnotationStore.getState().setPickSample(sample)
    useAnnotationStore.getState().lockPick({ hex: sample.hex, rgb: sample.rgb, hsl: sample.hsl })
    useAnnotationStore.getState().setTool('rect')
    const after = useAnnotationStore.getState()
    expect(after.pickSample).toBeNull()
    expect(after.pickedColor).toBeNull()
  })
})

describe('applyMaskImage', () => {
  it('不可变地回填脱敏位图', () => {
    const store = useAnnotationStore.getState()
    store.commitShape('img-1', makeShape({ id: 's1', tool: 'mosaic' }))
    useAnnotationStore.getState().applyMaskImage('img-1', 's1', 'data:image/png;base64,xx')
    const shape = useAnnotationStore.getState().shapesByImage['img-1'][0]
    expect(shape.imageUrl).toBe('data:image/png;base64,xx')
  })

  it('id 不存在时不改动数组引用', () => {
    const store = useAnnotationStore.getState()
    store.commitShape('img-1', makeShape({ id: 's1', tool: 'blur' }))
    const before = useAnnotationStore.getState().shapesByImage['img-1']
    useAnnotationStore.getState().applyMaskImage('img-1', 'ghost', 'xx')
    expect(useAnnotationStore.getState().shapesByImage['img-1']).toBe(before)
  })
})

describe('updateShape（拖动 / 拉伸几何写回）', () => {
  it('不可变替换形状几何，undo 恢复到移动前位置', () => {
    const store = useAnnotationStore.getState()
    store.commitShape('img-1', makeShape({ id: 's1' }))
    useAnnotationStore.getState().updateShape('img-1', 's1', makeShape({ id: 's1', start: { x: 10, y: 10 }, end: { x: 60, y: 60 } }))
    const after = useAnnotationStore.getState()
    expect(after.shapesByImage['img-1'][0].start).toEqual({ x: 10, y: 10 })
    expect(after.undoStacks['img-1']).toHaveLength(2)
    useAnnotationStore.getState().undo('img-1')
    expect(useAnnotationStore.getState().shapesByImage['img-1'][0].start).toEqual({ x: 0, y: 0 })
  })

  it('updateShape 后重做栈被清空（新变更不可重做）', () => {
    const store = useAnnotationStore.getState()
    store.commitShape('img-1', makeShape({ id: 's1' }))
    useAnnotationStore.getState().updateShape('img-1', 's1', makeShape({ id: 's1', start: { x: 2, y: 2 } }))
    useAnnotationStore.getState().undo('img-1')
    expect(useAnnotationStore.getState().redoStacks['img-1']).toHaveLength(1)
    useAnnotationStore.getState().updateShape('img-1', 's1', makeShape({ id: 's1', start: { x: 3, y: 3 } }))
    expect(useAnnotationStore.getState().redoStacks['img-1'] ?? []).toHaveLength(0)
  })

  it('缺 id 时不改动状态（不产生撤销记录）', () => {
    const store = useAnnotationStore.getState()
    store.commitShape('img-1', makeShape({ id: 's1' }))
    const before = useAnnotationStore.getState().shapesByImage['img-1']
    useAnnotationStore.getState().updateShape('img-1', 'ghost', makeShape({ id: 'ghost' }))
    const after = useAnnotationStore.getState()
    expect(after.shapesByImage['img-1']).toBe(before)
    expect(after.undoStacks['img-1']).toHaveLength(1)
  })

  it('替换几何时保持当前选中不变', () => {
    const store = useAnnotationStore.getState()
    store.commitShape('img-1', makeShape({ id: 's1' }))
    useAnnotationStore.getState().setSelectedId('s1')
    useAnnotationStore.getState().updateShape('img-1', 's1', makeShape({ id: 's1', start: { x: 1, y: 1 } }))
    expect(useAnnotationStore.getState().selectedId).toBe('s1')
  })
})

describe('panelOpen（标注面板显隐）', () => {
  it('默认关闭，setPanelOpen 全局切换', () => {
    expect(useAnnotationStore.getState().panelOpen).toBe(false)
    useAnnotationStore.getState().setPanelOpen(true)
    expect(useAnnotationStore.getState().panelOpen).toBe(true)
    useAnnotationStore.getState().setPanelOpen(false)
    expect(useAnnotationStore.getState().panelOpen).toBe(false)
  })
})

describe('选中形状的样式编辑（点选后改颜色/粗细/虚实线）', () => {
  it('setSelectedId 把当前样式同步为选中形状的属性', () => {
    const store = useAnnotationStore.getState()
    store.commitShape('img-1', makeShape({ id: 's1', color: '#f5c542', strokeWidth: 6, dashed: true }))
    useAnnotationStore.getState().setSelectedId('s1')
    const after = useAnnotationStore.getState()
    expect(after.style.color).toBe('#f5c542')
    expect(after.style.strokeWidth).toBe(6)
    expect(after.style.dashed).toBe(true)
  })

  it('选中形状时 setColor 写回形状颜色并记录撤销', () => {
    const store = useAnnotationStore.getState()
    store.commitShape('img-1', makeShape({ id: 's1', color: '#f0756b' }))
    useAnnotationStore.getState().setSelectedId('s1')
    useAnnotationStore.getState().setColor('#54c4dc')
    expect(useAnnotationStore.getState().shapesByImage['img-1'][0].color).toBe('#54c4dc')
    expect(useAnnotationStore.getState().undoStacks['img-1']).toHaveLength(2)
    useAnnotationStore.getState().undo('img-1')
    expect(useAnnotationStore.getState().shapesByImage['img-1'][0].color).toBe('#f0756b')
  })

  it('选中形状时 setStrokeWidth / toggleDashed 同样写回形状', () => {
    const store = useAnnotationStore.getState()
    store.commitShape('img-1', makeShape({ id: 's1', strokeWidth: 4, dashed: false }))
    useAnnotationStore.getState().setSelectedId('s1')
    useAnnotationStore.getState().setStrokeWidth(6)
    useAnnotationStore.getState().toggleDashed()
    const shape = useAnnotationStore.getState().shapesByImage['img-1'][0]
    expect(shape.strokeWidth).toBe(6)
    expect(shape.dashed).toBe(true)
  })

  it('未选中时 setColor 只改工具样式，不动形状、不产生撤销记录', () => {
    const store = useAnnotationStore.getState()
    store.commitShape('img-1', makeShape({ id: 's1' }))
    useAnnotationStore.getState().setColor('#54c4dc')
    expect(useAnnotationStore.getState().shapesByImage['img-1'][0].color).toBe('#f0756b')
    expect(useAnnotationStore.getState().style.color).toBe('#54c4dc')
    expect(useAnnotationStore.getState().undoStacks['img-1']).toHaveLength(1)
  })

  it('样式写回清空重做栈且不丢选中', () => {
    const store = useAnnotationStore.getState()
    store.commitShape('img-1', makeShape({ id: 's1' }))
    useAnnotationStore.getState().setSelectedId('s1')
    useAnnotationStore.getState().setColor('#54c4dc')
    useAnnotationStore.getState().undo('img-1')
    expect(useAnnotationStore.getState().redoStacks['img-1']).toHaveLength(1)
    useAnnotationStore.getState().setColor('#f5c542')
    expect(useAnnotationStore.getState().redoStacks['img-1'] ?? []).toHaveLength(0)
    expect(useAnnotationStore.getState().selectedId).toBe('s1')
  })

  it('选中形状不存在时 setColor 退化为仅改样式', () => {
    useAnnotationStore.getState().setSelectedId('ghost')
    useAnnotationStore.getState().setColor('#54c4dc')
    expect(useAnnotationStore.getState().style.color).toBe('#54c4dc')
    expect(useAnnotationStore.getState().undoStacks['img-1'] ?? []).toHaveLength(0)
  })
})