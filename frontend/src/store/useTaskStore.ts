import { create } from 'zustand'
import http, { unwrapResponse, bizErrorHandler } from '../api/http'
import type { TaskDetail, TaskSummary, ReviewRequest, StandardEnvelope, PageResult } from '../api/types'

interface TaskState {
  tasks: TaskSummary[]
  detail: TaskDetail | null
  loading: boolean
  error: string | null
  fetchList: () => Promise<void>
  fetchDetail: (taskId: string) => Promise<void>
  submitReview: (taskId: string, req: ReviewRequest) => Promise<{ task_id: string }>
}

export const useTaskStore = create<TaskState>((set) => ({
  tasks: [],
  detail: null,
  loading: false,
  error: null,

  async fetchList() {
    set({ loading: true, error: null })
    try {
      const res = await http.get<StandardEnvelope<PageResult<TaskSummary>>>('/tasks', { params: { limit: 100 } })
      set({ tasks: unwrapResponse(res).items, loading: false })
    } catch (e) {
      set({ error: e instanceof Error ? e.message : '加载失败', loading: false })
    }
  },

  async fetchDetail(taskId) {
    set({ loading: true, error: null })
    try {
      const res = await http.get<StandardEnvelope<TaskDetail>>(`/tasks/${taskId}`)
      set({ detail: unwrapResponse(res), loading: false })
    } catch (e) {
      set({ error: e instanceof Error ? e.message : '加载失败', loading: false })
    }
  },

  async submitReview(taskId, req) {
    try {
      const res = await http.post<StandardEnvelope<{ task_id: string }>>(`/tasks/${taskId}/review`, req)
      return unwrapResponse(res)
    } catch (e) {
      // 业务错误弹提示后继续上抛；组件层 catch 为空（防重复 toast）
      bizErrorHandler(e)
    }
  },
}))
