import http, { unwrapResponse } from './http'
import type { PageResult, StandardEnvelope } from './types'

/** 能力注册中心摘要（GET /capabilities）：与 app/models/capability.py:CapabilitySummary 逐字段对齐。
 *  注意：接口不返回 input_schema / default_params / max_images，参数契约由前端自带（决策 3）。 */
export type CapabilityType = 'defect_detection' | 'measurement' | 'classification'
export type AdapterType = 'http' | 'model_service' | 'local_bridge' | 'mock'
export type CapabilityStatus = 'enabled' | 'disabled' | 'maintenance'

export interface CapabilitySummary {
  capability_id: string
  tool_name: string
  tool_version: string
  model_version: string
  /** 后端取 description or tool_name */
  display_name: string
  scene: string
  capability_type: CapabilityType
  adapter_type: AdapterType
  status: CapabilityStatus
  note: string
}

export async function fetchCapabilities(params?: {
  scene?: string
  enabledOnly?: boolean
}): Promise<CapabilitySummary[]> {
  const res = await http.get<StandardEnvelope<PageResult<CapabilitySummary>>>('/capabilities', {
    params: { scene: params?.scene, enabled_only: params?.enabledOnly },
  })
  return unwrapResponse(res).items
}

/** 热刷新注册表（DB → 内存）：新增/下线工具后手工强制重载（决策 38） */
export async function reloadCapabilities(): Promise<number> {
  const res = await http.post<StandardEnvelope<number>>('/capabilities/reload')
  return unwrapResponse(res)
}

/* ==================== 写侧（批次 3 / 决策 27–50） ==================== */

/** 详情（GET /capabilities/{id}）：headers 只回掩码（决策 43），并下发乐观锁 version 与快照。 */
export interface CapabilityDetail {
  capability_id: string
  tool_name: string
  scene: string
  capability_type: CapabilityType
  adapter_type: AdapterType
  endpoint: string
  health_endpoint: string | null
  tool_version: string
  model_version: string
  api_version: string
  input_schema: Record<string, unknown> | null
  output_schema: Record<string, unknown> | null
  timeout_s: number
  max_retry: number
  status: CapabilityStatus
  description: string | null
  model_weights_sha256: string | null
  /** 后端已剔除 headers 子键（明文不出读方向） */
  meta: Record<string, unknown>
  headers_masked: Record<string, string>
  change_log: ChangeLogEntry[]
  version: number
  snapshot: string
  editable: boolean
}

/** meta_json.change_log 条目（决策 46：零 DDL 的最小留痕，操作人自报、不认证） */
export interface ChangeLogEntry {
  at?: string
  action?: string
  operator?: string
  reason?: string
  fields?: string[]
}

export async function fetchCapabilityDetail(capabilityId: string): Promise<CapabilityDetail> {
  const res = await http.get<StandardEnvelope<CapabilityDetail>>(
    `/capabilities/${encodeURIComponent(capabilityId)}`,
  )
  return unwrapResponse(res)
}

/** 公共可写字段（登记与编辑同源；决策 36：capability_id / tool_name 只在登记时提交） */
export interface CapabilityWritable {
  scene?: string
  capability_type?: CapabilityType
  adapter_type?: AdapterType
  endpoint?: string
  health_endpoint?: string | null
  tool_version?: string
  model_version?: string
  api_version?: string
  input_schema?: Record<string, unknown> | null
  output_schema?: Record<string, unknown> | null
  timeout_s?: number
  max_retry?: number
  description?: string | null
  model_weights_sha256?: string | null
  meta?: Record<string, unknown>
}

export interface CapabilityCreatePayload extends CapabilityWritable {
  capability_id: string
  tool_name: string
  status?: CapabilityStatus
  change_reason: string
  operator?: string | null
  expected_snapshot?: string | null
}

/** 编辑载荷：只提交改动过的键（决策 39 列级 patch：未出现＝保留，显式 null＝清空） */
export interface CapabilityPatchPayload extends CapabilityWritable {
  expected_version: number
  change_reason: string
  operator?: string | null
  expected_snapshot?: string | null
}

export interface WriteAck {
  capability_id: string
  status?: CapabilityStatus
  fields?: string[]
  capabilities: number
  note?: string
}

export async function createCapability(payload: CapabilityCreatePayload): Promise<WriteAck> {
  const res = await http.post<StandardEnvelope<WriteAck>>('/capabilities', payload)
  return unwrapResponse(res)
}

export async function patchCapability(
  capabilityId: string,
  payload: CapabilityPatchPayload,
): Promise<WriteAck> {
  const res = await http.patch<StandardEnvelope<WriteAck>>(
    `/capabilities/${encodeURIComponent(capabilityId)}`,
    payload,
  )
  return unwrapResponse(res)
}

/** 启停（决策 45）：独立端点 + 行内二次确认；启用前置 health 探测 */
export async function setCapabilityStatus(
  capabilityId: string,
  payload: { status: CapabilityStatus; change_reason: string; operator?: string | null },
): Promise<WriteAck> {
  const res = await http.post<StandardEnvelope<WriteAck>>(
    `/capabilities/${encodeURIComponent(capabilityId)}/status`,
    payload,
  )
  return unwrapResponse(res)
}

/** 干跑沙箱（决策 34）：不落库。image_path 留空＝只跑 health 的连通性检查，不发放保存回执。 */
export interface DryRunPayload {
  endpoint: string
  health_path?: string
  inference_path?: string
  headers?: Record<string, string>
  parameters?: Record<string, unknown>
  image_field?: string
  image_path?: string
  capability_type?: CapabilityType
  timeout_s?: number
}

export interface DryRunStep {
  name: string
  ok: boolean
  message: string
  latency_ms: number | null
}

export interface DryRunResult {
  passed: boolean
  steps: DryRunStep[]
  /** 归一后的标准外壳（result/artifacts），可直接作为期望响应样例落 output_schema */
  sample: Record<string, unknown> | null
  gate_errors: string[]
  /** 成功完整试运行才下发；连通性检查为空串，不能充当保存回执（决策 27/41） */
  snapshot: string
}

export async function dryRunCapability(payload: DryRunPayload): Promise<DryRunResult> {
  const res = await http.post<StandardEnvelope<DryRunResult>>('/capabilities/dry-run', payload)
  return unwrapResponse(res)
}

export const CAPABILITY_TYPE_TEXT: Record<CapabilityType, string> = {
  defect_detection: '缺陷检测',
  measurement: '测量',
  classification: '分类',
}

export const ADAPTER_TYPE_TEXT: Record<AdapterType, string> = {
  http: 'HTTP 服务',
  model_service: '模型服务',
  local_bridge: '本地桥接',
  mock: 'Mock（联调用）',
}

export const CAPABILITY_STATUS_TEXT: Record<CapabilityStatus, string> = {
  enabled: '已启用',
  disabled: '已停用',
  maintenance: '维护中',
}

export const CHANGE_LOG_ACTION_TEXT: Record<string, string> = {
  create: '登记',
  update: '编辑',
  status: '启停',
}
