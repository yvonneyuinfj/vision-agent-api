import http, { unwrapResponse } from './http'

export interface RagflowStatus {
  configured: boolean
  reachable: boolean
  doc_count: number
  latest_doc_time: string | null
  message?: string
}

export interface CapabilityHealth {
  tool_name: string
  healthy: boolean
  message: string
  latency_ms: number | null
}

export interface ServerDate {
  server_date: string
  vlm_available?: boolean
}

// 状态探测为静默降级语义：不抛业务错误弹窗，由调用方按失败渲染"未知"
export async function fetchRagflowStatus(): Promise<RagflowStatus> {
  const res = await http.get('/ragflow/status', { silent: true })
  return unwrapResponse(res)
}

export async function fetchCapabilityHealth(): Promise<CapabilityHealth[]> {
  const res = await http.get('/capabilities/health', { silent: true })
  return unwrapResponse(res)
}

// 服务器当日日期（北京时区）+ VLM 可用性（agent 轨入口置灰依据）；失败由调用方降级
export async function fetchServerDate(): Promise<ServerDate> {
  const res = await http.get('/health', { silent: true })
  return unwrapResponse(res)
}