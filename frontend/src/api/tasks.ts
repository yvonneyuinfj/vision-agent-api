import http, { unwrapResponse, bizErrorHandler } from './http'
import type { StandardEnvelope, PageResult, TaskStats, TaskSummary, TaskDetail, TaskTrace } from './types'

export interface SubmitTaskForm {
  scene: string | null
  mode: 'single' | 'joint' | 'agent'
  scenes: string[]
  files: File[]
  /** 工作台参数分区改动过的项；空则不传，由工具侧 default_params 决定 */
  parameters?: Record<string, unknown> | null
}

export function buildSubmitFormData(form: SubmitTaskForm): FormData {
  const fd = new FormData()
  for (const f of form.files) fd.append('files', f)
  // agent 轨由智能体自主路由场景：不传 scene（传了也仅作为提示）；
  // single 轨按选定场景走固定流水线
  if (form.mode === 'single' && form.scene) fd.append('scene', form.scene)
  fd.append('mode', form.mode)
  if (form.mode === 'joint') fd.append('scenes', form.scenes.join(','))
  if (form.parameters && Object.keys(form.parameters).length) fd.append('parameters', JSON.stringify(form.parameters))
  return fd
}

export async function submitTask(form: SubmitTaskForm): Promise<{ task_id: string; mode: string }> {
  try {
    const res = await http.post<StandardEnvelope<{ task_id: string; mode: string }>>('/tasks', buildSubmitFormData(form))
    return unwrapResponse(res)
  } catch (e) {
    bizErrorHandler(e)
    throw e
  }
}

export async function fetchTaskList(limit = 100): Promise<TaskSummary[]> {
  try {
    const res = await http.get<StandardEnvelope<PageResult<TaskSummary>>>('/tasks', { params: { limit } })
    return unwrapResponse(res).items
  } catch (e) {
    bizErrorHandler(e)
    throw e
  }
}

/** 统计横幅数据源：后端权威全量聚合（列表接口受 limit 截断，不可用于计数） */
export async function fetchTaskStats(): Promise<TaskStats> {
  try {
    const res = await http.get<StandardEnvelope<TaskStats>>('/tasks/stats')
    return unwrapResponse(res)
  } catch (e) {
    bizErrorHandler(e)
    throw e
  }
}

export async function fetchTaskDetail(taskId: string): Promise<TaskDetail> {
  try {
    const res = await http.get<StandardEnvelope<TaskDetail>>(`/tasks/${taskId}`)
    return unwrapResponse(res)
  } catch (e) {
    bizErrorHandler(e)
    throw e
  }
}

/** ReAct 思考链（检查点轨迹归一化）；仅 mode=agent 任务有实质条目 */
export async function fetchTaskTrace(taskId: string): Promise<TaskTrace> {
  try {
    const res = await http.get<StandardEnvelope<TaskTrace>>(`/tasks/${taskId}/trace`)
    return unwrapResponse(res)
  } catch (e) {
    bizErrorHandler(e)
    throw e
  }
}

/** 失败任务原地重试：后端原子重置为 pending 后后台重跑流水线 */
export async function retryTask(taskId: string): Promise<{ task_id: string }> {
  try {
    const res = await http.post<StandardEnvelope<{ task_id: string }>>(`/tasks/${taskId}/retry`)
    return unwrapResponse(res)
  } catch (e) {
    bizErrorHandler(e)
    throw e
  }
}

export type DownloadKind = 'inputs' | 'artifacts' | 'reports'

export function downloadTaskFile(taskId: string, name: string, kind: DownloadKind): string {
  return `/api/v1/tasks/${taskId}/files/${name}?kind=${kind}`
}

export interface KnowledgeContext {
  doc_id: string
  doc_name: string
  page: number | null
  self: { chunk_id: string; content: string; page: number | null; score: number }
  prev: Array<{ chunk_id: string; content: string; page: number | null }>
  next: Array<{ chunk_id: string; content: string; page: number | null }>
}

export interface KnowledgeSource {
  doc_name: string
  page: number | null
  content: string
}

/** 后端可能未实现（属补接口清单），失败抛 BizHttpError 由调用方降级提示，不白屏 */
export async function fetchKnowledgeContext(taskId: string, chunkId: string, n: number): Promise<KnowledgeContext> {
  try {
    const res = await http.get<StandardEnvelope<KnowledgeContext>>(`/tasks/${taskId}/knowledge/${chunkId}/context`, { params: { n } })
    return unwrapResponse(res)
  } catch (e) {
    bizErrorHandler(e)
    throw e
  }
}

export async function fetchKnowledgeSource(taskId: string, chunkId: string): Promise<KnowledgeSource> {
  try {
    const res = await http.get<StandardEnvelope<KnowledgeSource>>(`/tasks/${taskId}/knowledge/${chunkId}/source`)
    return unwrapResponse(res)
  } catch (e) {
    bizErrorHandler(e)
    throw e
  }
}
