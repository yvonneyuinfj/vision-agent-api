import { create } from 'zustand'
import type { DefectItem } from '../api/types'
import { defectDiff } from '../utils/defectDiff'

/** 缺陷编辑工具：浏览/选中 与 新增漏检框（拖拽画框）两态。
 *  改类/改严重度/标记排除/移框均针对已选条目，无需独立工具。 */
export type DefectEditTool = 'select' | 'add'

/** 人工新增条目占位类型：提交前须改为真实类别（面板拦截，见 DefectEditPanel） */
export const NEW_DEFECT_TYPE = '__new__'

export const NEW_DEFECT_NAME_CN = '人工补框'

const clampBox = (b: [number, number, number, number]): [number, number, number, number] => [
  Math.min(b[0], b[2]),
  Math.min(b[1], b[3]),
  Math.max(b[0], b[2]),
  Math.max(b[1], b[3]),
]

/** 人工新增条目本地 id 生成：M + 时间戳36进制 + 计数，避免与模型 D00x 冲突 */
let seq = 0
function nextManualId(): string {
  seq += 1
  return `M${Date.now().toString(36).slice(-5)}${seq}`
}

interface DefectEditState {
  open: boolean
  /** 编辑会话归属任务：Viewer 以 taskId 匹配后才渲染编辑层（页面/Modal 双实例隔离） */
  taskId: string | null
  /** 编辑目标（缺陷框叠加图层所用底图，人工新增框归属此图） */
  imageKey: string | null
  baseline: DefectItem[]
  draft: DefectItem[]
  tool: DefectEditTool
  selectedId: string | null

  openEditor: (taskId: string, imageKey: string, defects: DefectItem[]) => void
  closeEditor: () => void
  setTool: (tool: DefectEditTool) => void
  setSelectedId: (id: string | null) => void
  /** 画框落定：追加一条人工新增缺陷（source=manual，置信度 null）；
   *  imageName = 画框时所在输入图文件名（多图任务 image_name 溯源，防 bbox 混叠） */
  addDefect: (bbox: [number, number, number, number], imageName?: string) => void
  /** 改类/改严重度：写入原值快照 revised 并打修订标记 */
  patchDefect: (id: string, patch: Partial<Pick<DefectItem, 'defect_type' | 'defect_name_cn' | 'severity'>>) => void
  /** 移框：仅人工新增或已修订条目允许（模型框移动同样计入修订） */
  moveDefect: (id: string, bbox: [number, number, number, number]) => void
  /** 标记排除误检：保留条目不硬删（human_rejected 语义） */
  toggleReject: (id: string) => void
  /** 硬删：仅人工新增条目可删（模型条目只能标记排除） */
  removeDefect: (id: string) => void
  resetDraft: () => void
}

/** 取条目的稳定 key：defect_id 优先，缺失时用索引兜底。
 *  兜底格式 `__idx_{i}` 与后端 _defect_revision_diff 完全一致——补号后的
 *  draft 提交上去，存量无 id 数据也能按位对齐（不误报 added/removed）。 */
function keyOf(d: DefectItem, i: number): string {
  return d.defect_id ?? `__idx_${i}`
}

/** 为 baseline 补齐稳定 id，保证 draft 每条可寻址（老数据 defect_id 可能为 null） */
function withIds(defects: DefectItem[]): DefectItem[] {
  return defects.map((d, i) => ({ ...d, defect_id: d.defect_id ?? keyOf(d, i) }))
}

export const useDefectEditStore = create<DefectEditState>((set, get) => ({
  open: false,
  taskId: null,
  imageKey: null,
  baseline: [],
  draft: [],
  tool: 'select',
  selectedId: null,

  openEditor: (taskId, imageKey, defects) => {
    const s = get()
    // 同一任务重开编辑器：保留未提交草稿（继续修订）；换任务才重建基线
    if (s.open && s.taskId === taskId) {
      set({ imageKey, tool: 'select' })
      return
    }
    const seeded = withIds(defects)
    set({
      open: true,
      taskId,
      imageKey,
      baseline: seeded,
      draft: seeded.map((d) => ({ ...d })),
      tool: 'select',
      selectedId: null,
    })
  },

  closeEditor: () =>
    set({ open: false, taskId: null, imageKey: null, baseline: [], draft: [], tool: 'select', selectedId: null }),

  setTool: (tool) => set({ tool, selectedId: tool === 'add' ? null : get().selectedId }),
  setSelectedId: (selectedId) => set({ selectedId, tool: selectedId ? 'select' : get().tool }),

  addDefect: (bbox, imageName) =>
    set((s) => {
      if (!s.open) return s
      const entry: DefectItem = {
        defect_id: nextManualId(),
        defect_type: NEW_DEFECT_TYPE,
        defect_name_cn: NEW_DEFECT_NAME_CN,
        confidence: null,
        severity: null,
        location: { type: 'bbox', bbox: clampBox(bbox) },
        source: 'manual',
        ...(imageName ? { image_name: imageName } : {}),
      }
      return { draft: [...s.draft, entry], selectedId: entry.defect_id, tool: 'select' }
    }),

  patchDefect: (id, patch) =>
    set((s) => ({
      draft: s.draft.map((d) => {
        if (d.defect_id !== id) return d
        const base = s.baseline.find((b) => b.defect_id === id)
        // 人工新增条目无“原值”，不打 revised 快照；模型条目记录改判前原值
        const revised = d.source === 'manual'
          ? d.revised
          : { ...(base ? { defect_type: base.defect_type, severity: base.severity } : {}), ...(d.revised ?? {}) }
        return { ...d, ...patch, revised }
      }),
    })),

  moveDefect: (id, bbox) =>
    set((s) => ({
      draft: s.draft.map((d) => {
        if (d.defect_id !== id) return d
        const base = s.baseline.find((b) => b.defect_id === id)
        const revised = d.source === 'manual'
          ? d.revised
          : { ...(base ? { location: base.location } : {}), ...(d.revised ?? {}) }
        return { ...d, location: { type: 'bbox', bbox: clampBox(bbox) }, revised }
      }),
    })),

  toggleReject: (id) =>
    set((s) => ({
      draft: s.draft.map((d) => (d.defect_id === id ? { ...d, human_rejected: !d.human_rejected } : d)),
    })),

  removeDefect: (id) =>
    set((s) => {
      const target = s.draft.find((d) => d.defect_id === id)
      // 模型条目不硬删（保留审计），只允许标记排除
      if (!target || target.source !== 'manual') return s
      return {
        draft: s.draft.filter((d) => d.defect_id !== id),
        selectedId: s.selectedId === id ? null : s.selectedId,
      }
    }),

  resetDraft: () => set((s) => ({ draft: s.baseline.map((d) => ({ ...d })), selectedId: null })),
}))

/** 选择器：当前 draft 相对 baseline 的修订摘要（复用后端同构 diff 口径） */
export function selectDiff(s: Pick<DefectEditState, 'baseline' | 'draft'>) {
  return defectDiff(s.baseline, s.draft)
}

/** 提交拦截：存在未指定真实类别的人工新增条目 */
export function hasUntypedManual(s: Pick<DefectEditState, 'draft'>): boolean {
  return s.draft.some((d) => d.source === 'manual' && d.defect_type === NEW_DEFECT_TYPE)
}

if (import.meta.env.DEV) {
  ;(window as unknown as { __defectEditStore: typeof useDefectEditStore }).__defectEditStore = useDefectEditStore
}
