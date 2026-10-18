import http, { unwrapResponse } from './http'
import type { StandardEnvelope } from './types'

/* GET /tasks/review-matrix：人工复核留痕的聚合矩阵（后端已实现，本轮首次被前端消费）。
   注意：返回的是聚合计数，不含逐样本行——样本明细只能来自 /tasks 窗口。 */

export interface MatrixBucket {
  tasks: number
  actions: { confirmed: number; revised: number; rejected: number }
  agent_requests: number
  defects: {
    added: number
    excluded: number
    reclassified: number
    severity_changed: number
    moved: number
    removed: number
  }
  /** revised / 已决总数；无样本为 null */
  revised_rate: number | null
}

export interface ReviewMatrix {
  sample_count: number
  confidence_threshold: number
  total: MatrixBucket
  by_scene: Record<string, MatrixBucket>
  low_confidence_band: MatrixBucket
}

export async function fetchReviewMatrix(limit = 500): Promise<ReviewMatrix> {
  const res = await http.get<StandardEnvelope<ReviewMatrix>>('/tasks/review-matrix', {
    params: { limit },
    silent: true,
  })
  return unwrapResponse(res)
}

export const DEFECT_DIFF_TEXT: Record<keyof MatrixBucket['defects'], string> = {
  added: '人工补检（漏检）',
  excluded: '人工排除（误检）',
  reclassified: '改缺陷类别',
  severity_changed: '改严重度',
  moved: '移框',
  removed: '删除条目',
}

export const ACTION_TEXT: Record<keyof MatrixBucket['actions'], string> = {
  confirmed: '确认',
  revised: '改判',
  rejected: '驳回',
}
