/* 人工标注全局状态：按图像隔离的批注列表 + 撤销/重做栈 + 工具/样式/序号/取色浮层。
   全部更新走不可变替换（不原地修改数组），与全局 coding style 一致。 */

import { create } from 'zustand'
import type { AnnotationShape, AnnotationStyle, AnnotationTool } from './annotationTypes'
import { ANNOTATION_COLORS, ANNOTATION_WIDTHS } from './annotationTypes'

export interface AnnotationSnapshot {
  shapes: AnnotationShape[]
  selectedId: string | null
}

/** 取色器采样信息（图像自然坐标 + 屏幕坐标 + 三格式色值） */
export interface PickSample {
  screenX: number
  screenY: number
  nx: number
  ny: number
  hex: string
  rgb: string
  hsl: string
}

/** 点击锁定的取色结果（与 PickSample 相同的三格式色值） */
export type PickedColor = Pick<PickSample, 'hex' | 'rgb' | 'hsl'>

interface AnnotationState {
  /** imageKey → 该图批注列表（追加时生成新数组） */
  shapesByImage: Record<string, AnnotationShape[]>
  /** imageKey → 撤销栈（最近一次操作在栈顶） */
  undoStacks: Record<string, AnnotationSnapshot[]>
  /** imageKey → 重做栈（undo 时压入当前快照；新提交清空） */
  redoStacks: Record<string, AnnotationSnapshot[]>
  /** imageKey → 下一个序号标记的编号（新分组重置为 1） */
  badgeSeqs: Record<string, number>
  /** 当前激活的工具；null=浏览模式 */
  tool: AnnotationTool | null
  style: AnnotationStyle
  selectedId: string | null
  /** 文本标注待输入锚点（图像自然坐标） */
  textDraft: { x: number; y: number } | null
  /** 取色器跟随光标的最新采样（离开画布时清空） */
  pickSample: PickSample | null
  /** 点击锁定的取色结果（离开取色模式清空） */
  pickedColor: PickedColor | null
  /** 标注面板显隐（Viewer 右侧开关控制，页面层在查看器正下方渲染） */
  panelOpen: boolean

  setTool: (tool: AnnotationTool | null) => void
  /** 传已解析的具体色值（hex），由工具栏按明暗主题解析 token key */
  setColor: (color: string) => void
  setStrokeWidth: (strokeWidth: number) => void
  toggleDashed: () => void
  setSelectedId: (id: string | null) => void
  setTextDraft: (point: { x: number; y: number } | null) => void
  setPickSample: (sample: PickSample | null) => void
  /** 点击锁定颜色：写入 pickedColor 并同步为当前笔色；传 null 仅清空锁定 */
  lockPick: (color: PickedColor | null) => void
  clearPick: () => void

  /** 提交一根批注（同一图内形状末尾追加），压入撤销栈并清空重做栈 */
  commitShape: (imageKey: string, shape: AnnotationShape) => void
  /** 拖动/拉伸后的几何写回：替换形状并记录撤销（next 由几何纯函数算出） */
  updateShape: (imageKey: string, id: string, next: AnnotationShape) => void
  /** 删除指定批注（橡皮擦/删除选中共用），压入撤销栈并清空重做栈 */
  removeShape: (imageKey: string, id: string) => void
  /** 删除选中批注 */
  removeSelected: (imageKey: string) => void
  /** 清除该图全部批注 */
  clearImage: (imageKey: string) => void
  /** 撤销一次提交/删除（恢复该图上一次快照），当前状态入重做栈 */
  undo: (imageKey: string) => void
  /** 重做一次撤销 */
  redo: (imageKey: string) => void
  /** 领取下一个序号标记编号（自增） */
  consumeBadgeSeq: (imageKey: string) => number
  /** 新分组：序号从 1 重新开始 */
  resetBadgeSeq: (imageKey: string) => void
  /** 脱敏位图生成完成后回填到形状 */
  applyMaskImage: (imageKey: string, id: string, imageUrl: string) => void
  setPanelOpen: (open: boolean) => void
}

const defaultStyle: AnnotationStyle = {
  color: ANNOTATION_COLORS.dark.danger,
  strokeWidth: ANNOTATION_WIDTHS[1],
  dashed: false,
}

const MAX_HISTORY_DEPTH = 60

/** 将一次变更记录进 undo 栈并清空 redo 栈，返回新的两栈 */
function recordChange(
  snapshot: AnnotationSnapshot,
  undoStacks: Record<string, AnnotationSnapshot[]>,
  redoStacks: Record<string, AnnotationSnapshot[]>,
  imageKey: string,
): { undoStacks: Record<string, AnnotationSnapshot[]>; redoStacks: Record<string, AnnotationSnapshot[]> } {
  return {
    undoStacks: {
      ...undoStacks,
      [imageKey]: [...(undoStacks[imageKey] ?? []), snapshot].slice(-MAX_HISTORY_DEPTH),
    },
    redoStacks: { ...redoStacks, [imageKey]: [] },
  }
}

/** 记录撤销快照后把该图批注替换为 next（提交/删除/清空共用）；next 与当前列表相同时视为无变化 */
function applyChange(
  s: AnnotationState,
  imageKey: string,
  next: AnnotationShape[],
  selectedId: string | null = s.selectedId,
): AnnotationState {
  const prev = s.shapesByImage[imageKey] ?? []
  if (next === prev) return s
  const snap: AnnotationSnapshot = { shapes: prev, selectedId: s.selectedId }
  const stacks = recordChange(snap, s.undoStacks, s.redoStacks, imageKey)
  return {
    ...s,
    shapesByImage: { ...s.shapesByImage, [imageKey]: next },
    selectedId,
    ...stacks,
  }
}

/** 找到选中形状所属的图像 key（selectedId 是全局的，样式写回需要定位到具体图） */
function findShapeImageKey(s: AnnotationState, id: string): string | null {
  for (const [key, list] of Object.entries(s.shapesByImage)) {
    if (list.some((sh) => sh.id === id)) return key
  }
  return null
}

/** 样式修改：未选中时仅改工具样式；选中时同步写回选中形状（记撤销、清重做栈） */
function applyStyleToSelection(s: AnnotationState, patch: Partial<AnnotationStyle>): AnnotationState {
  const nextStyle = { ...s.style, ...patch }
  const id = s.selectedId
  if (!id) return { ...s, style: nextStyle }
  const key = findShapeImageKey(s, id)
  if (!key) return { ...s, style: nextStyle }
  const prev = s.shapesByImage[key] ?? []
  const idx = prev.findIndex((sh) => sh.id === id)
  if (idx < 0) return { ...s, style: nextStyle }
  const next = { ...prev[idx], ...patch }
  const unchanged =
    next.color === prev[idx].color &&
    next.strokeWidth === prev[idx].strokeWidth &&
    !!next.dashed === !!prev[idx].dashed
  if (unchanged) return { ...s, style: nextStyle }
  return { ...applyChange(s, key, prev.map((sh, i) => (i === idx ? next : sh)), id), style: nextStyle }
}

/** 撤销/重做共享实现：从 from 栈顶弹出快照恢复，当前状态压入对向栈 */
function shiftStack(s: AnnotationState, imageKey: string, from: 'undo' | 'redo'): AnnotationState {
  const fromStacks = from === 'undo' ? s.undoStacks : s.redoStacks
  const toStacks = from === 'undo' ? s.redoStacks : s.undoStacks
  const stack = fromStacks[imageKey] ?? []
  if (stack.length === 0) return s
  const last = stack[stack.length - 1]
  const current: AnnotationSnapshot = { shapes: s.shapesByImage[imageKey] ?? [], selectedId: s.selectedId }
  const restored = {
    shapesByImage: { ...s.shapesByImage, [imageKey]: last.shapes },
    selectedId: last.selectedId,
  }
  const pushed = {
    ...toStacks,
    [imageKey]: [...(toStacks[imageKey] ?? []), current].slice(-MAX_HISTORY_DEPTH),
  }
  return from === 'undo'
    ? { ...s, ...restored, undoStacks: { ...fromStacks, [imageKey]: stack.slice(0, -1) }, redoStacks: pushed }
    : { ...s, ...restored, redoStacks: { ...fromStacks, [imageKey]: stack.slice(0, -1) }, undoStacks: pushed }
}

export const useAnnotationStore = create<AnnotationState>((set, get) => ({
  shapesByImage: {},
  undoStacks: {},
  redoStacks: {},
  badgeSeqs: {},
  tool: null,
  style: defaultStyle,
  selectedId: null,
  textDraft: null,
  pickSample: null,
  pickedColor: null,
  panelOpen: false,

  setTool: (tool) =>
    set((s) => ({
      tool,
      selectedId: tool ? null : s.selectedId,
      // 离开取色模式时清理取色浮层
      ...(tool !== 'picker' ? { pickSample: null, pickedColor: null } : {}),
    })),
  setColor: (color) => set((s) => applyStyleToSelection(s, { color })),
  setStrokeWidth: (strokeWidth) => set((s) => applyStyleToSelection(s, { strokeWidth })),
  toggleDashed: () => set((s) => applyStyleToSelection(s, { dashed: !s.style.dashed })),
  setSelectedId: (selectedId) =>
    set((s) => {
      if (!selectedId || selectedId === s.selectedId) return { ...s, selectedId }
      /* 选中即同步工具属性：工具栏展示该形状当前颜色/线宽/线型，后续修改直接写回形状 */
      const key = findShapeImageKey(s, selectedId)
      const shape = key ? (s.shapesByImage[key] ?? []).find((sh) => sh.id === selectedId) : undefined
      if (!shape) return { ...s, selectedId }
      return {
        ...s,
        selectedId,
        style: { ...s.style, color: shape.color, strokeWidth: shape.strokeWidth, dashed: !!shape.dashed },
      }
    }),
  setTextDraft: (point) => set({ textDraft: point }),
  setPickSample: (pickSample) => set({ pickSample }),
  lockPick: (color) =>
    set((s) => ({
      ...(color ? applyStyleToSelection(s, { color: color.hex }) : s),
      pickedColor: color,
    })),
  clearPick: () => set({ pickSample: null, pickedColor: null }),

  commitShape: (imageKey, shape) =>
    set((s) => applyChange(s, imageKey, [...(s.shapesByImage[imageKey] ?? []), shape])),

  updateShape: (imageKey, id, next) =>
    set((s) => {
      const prev = s.shapesByImage[imageKey] ?? []
      if (!prev.some((sh) => sh.id === id)) return s
      return applyChange(s, imageKey, prev.map((sh) => (sh.id === id ? next : sh)), s.selectedId)
    }),

  removeShape: (imageKey, id) =>
    set((s) => {
      const prev = s.shapesByImage[imageKey] ?? []
      if (!prev.some((sh) => sh.id === id)) return s
      return applyChange(s, imageKey, prev.filter((sh) => sh.id !== id), s.selectedId === id ? null : s.selectedId)
    }),

  removeSelected: (imageKey) => {
    const id = get().selectedId
    if (!id) return
    get().removeShape(imageKey, id)
  },

  clearImage: (imageKey) =>
    set((s) => {
      if ((s.shapesByImage[imageKey] ?? []).length === 0) return s
      return applyChange(s, imageKey, [], null)
    }),

  undo: (imageKey) => set((s) => shiftStack(s, imageKey, 'undo')),

  redo: (imageKey) => set((s) => shiftStack(s, imageKey, 'redo')),

  consumeBadgeSeq: (imageKey) => {
    const n = get().badgeSeqs[imageKey] ?? 1
    set((s) => ({ badgeSeqs: { ...s.badgeSeqs, [imageKey]: n + 1 } }))
    return n
  },
  resetBadgeSeq: (imageKey) => set((s) => ({ badgeSeqs: { ...s.badgeSeqs, [imageKey]: 1 } })),

  applyMaskImage: (imageKey, id, imageUrl) =>
    set((s) => {
      const prev = s.shapesByImage[imageKey] ?? []
      if (!prev.some((sh) => sh.id === id)) return s
      return {
        shapesByImage: {
          ...s.shapesByImage,
          [imageKey]: prev.map((sh) => (sh.id === id ? { ...sh, imageUrl } : sh)),
        },
      }
    }),

  setPanelOpen: (open) => set({ panelOpen: open }),
}))

/* 仅开发环境：暴露 store 给浏览器调试/Playwright 验证（生产包不包含） */
if (import.meta.env.DEV) {
  ;(window as unknown as { __annotationStore: typeof useAnnotationStore }).__annotationStore = useAnnotationStore
}