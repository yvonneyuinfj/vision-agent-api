import http, { unwrapResponse, bizErrorHandler } from './http'
import type { StandardEnvelope } from './types'

/** `GET /reports/{task_id}`（app/services/report_service.py:19-41）：
 *  报告未生成或 uri 缺失时后端返回 TASK_NOT_FOUND，调用方须降级而非白屏。 */
export interface ReportMeta {
  task_id: string
  report_uri: string
  format: string
  generated_at: string
}

export interface ReportDoc {
  meta: ReportMeta
  content: string
}

export async function fetchReport(taskId: string, opts?: { silent?: boolean }): Promise<ReportDoc> {
  try {
    const resp = opts?.silent
      ? await http.get<StandardEnvelope<ReportDoc>>(`/reports/${taskId}`, { silent: true })
      : await http.get<StandardEnvelope<ReportDoc>>(`/reports/${taskId}`)
    return unwrapResponse(resp)
  } catch (e) {
    if (!opts?.silent) bizErrorHandler(e)
    throw e
  }
}
