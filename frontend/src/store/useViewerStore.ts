import { create } from 'zustand'

export interface ViewState { scale: number; x: number; y: number }

interface ViewerState {
  /** imageKey → 视图状态（FilmStrip 每图独立缩放/平移） */
  views: Record<string, ViewState>
  activeImageKey: string | null
  activeDefectId: string | null
  setActiveImageKey: (key: string) => void
  setActiveDefectId: (id: string | null) => void
  updateView: (key: string, view: ViewState) => void
}

export const useViewerStore = create<ViewerState>((set) => ({
  views: {},
  activeImageKey: null,
  activeDefectId: null,
  setActiveImageKey: (key) => set({ activeImageKey: key }),
  setActiveDefectId: (id) => set({ activeDefectId: id }),
  updateView: (key, view) => set((s) => ({ views: { ...s.views, [key]: view } })),
}))

/* 仅开发环境：暴露 store 给浏览器调试/Playwright 验证（生产包不包含） */
if (import.meta.env.DEV) {
  ;(window as unknown as { __viewerStore: typeof useViewerStore }).__viewerStore = useViewerStore
}
