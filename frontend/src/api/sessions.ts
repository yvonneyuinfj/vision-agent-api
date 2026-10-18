/** 质检助手会话（会话轨）API 客户端：会话 CRUD / 消息收发 / 消息流（SSE）。 */
import http, { unwrapResponse, bizErrorHandler } from './http'
import type { StandardEnvelope, SessionSummary, SessionDetail, SessionMessage } from './types'

const BASE = '/agent/sessions'

export async function createSession(title?: string | null): Promise<SessionSummary> {
  try {
    const res = await http.post<StandardEnvelope<SessionSummary>>(BASE, { title: title ?? null })
    return unwrapResponse(res)
  } catch (e) {
    bizErrorHandler(e)
    throw e
  }
}

export async function fetchSessions(): Promise<SessionSummary[]> {
  try {
    const res = await http.get<StandardEnvelope<SessionSummary[]>>(BASE)
    return unwrapResponse(res) ?? []
  } catch (e) {
    bizErrorHandler(e)
    throw e
  }
}

export async function fetchSession(sessionId: string): Promise<SessionDetail> {
  try {
    const res = await http.get<StandardEnvelope<SessionDetail>>(`${BASE}/${sessionId}`)
    return unwrapResponse(res)
  } catch (e) {
    bizErrorHandler(e)
    throw e
  }
}

export async function renameSession(sessionId: string, title: string): Promise<{ session_id: string; title: string }> {
  try {
    const res = await http.patch<StandardEnvelope<{ session_id: string; title: string }>>(
      `${BASE}/${sessionId}`,
      { title },
    )
    return unwrapResponse(res)
  } catch (e) {
    bizErrorHandler(e)
    throw e
  }
}

export async function deleteSession(sessionId: string): Promise<{ session_id: string; deleted: boolean }> {
  try {
    const res = await http.delete<StandardEnvelope<{ session_id: string; deleted: boolean }>>(`${BASE}/${sessionId}`)
    return unwrapResponse(res)
  } catch (e) {
    bizErrorHandler(e)
    throw e
  }
}

/** 发送消息（可带图）：非阻塞——回合后台执行，回复经消息列表/流取回。 */
export async function sendMessage(
  sessionId: string,
  content: string,
  files: File[] = [],
): Promise<{ session_id: string; accepted: boolean; images: number }> {
  try {
    const fd = new FormData()
    fd.append('content', content)
    for (const f of files) fd.append('files', f)
    const res = await http.post<StandardEnvelope<{ session_id: string; accepted: boolean; images: number }>>(
      `${BASE}/${sessionId}/messages`,
      fd,
    )
    return unwrapResponse(res)
  } catch (e) {
    bizErrorHandler(e)
    throw e
  }
}

export async function fetchMessages(sessionId: string): Promise<SessionMessage[]> {
  try {
    const res = await http.get<StandardEnvelope<SessionMessage[]>>(`${BASE}/${sessionId}/messages`)
    return unwrapResponse(res) ?? []
  } catch (e) {
    bizErrorHandler(e)
    throw e
  }
}

/**
 * 会话列表流（SSE）：首帧全量，此后任何变化（新建/重命名/删除/活跃排序）整表重推。
 * 取代列表轮询；EventSource 断线由浏览器自动重连，重连后服务端重推全量。
 */
export function openSessionsStream(
  onSessions: (sessions: SessionSummary[]) => void,
  onError?: (message: string) => void,
): () => void {
  let source: EventSource | null = null
  try {
    source = new EventSource(`/api/v1${BASE}/stream`)
    source.onmessage = (ev) => {
      try {
        const payload = JSON.parse(ev.data)
        if (payload?.type === 'sessions' && Array.isArray(payload.sessions)) onSessions(payload.sessions as SessionSummary[])
        else if (payload?.type === 'error') onError?.(payload.message ?? 'stream error')
      } catch {
        // 心跳（": ping"）或非法帧：忽略
      }
    }
    source.onerror = () => onError?.('sessions stream disconnected')
  } catch {
    onError?.('EventSource unavailable')
  }
  return () => source?.close()
}

/**
 * 消息流（SSE）：类型分派 + 逐字增量协议（对齐玻璃疏行演绎演示）。
 * 顶层 type 决定渲染器：message / thinking / chain / card；助手文本与思考
 * 逐字（start→delta→end），卡片只推不改。返回关闭函数。
 * `onOpen` 在每次（重）连接建立时触发——调用方应清空重建本地渲染状态，
 * 服务端随后重放全量历史（整帧），断线自动重连自愈。
 */
export function openSessionStream(
  sessionId: string,
  onFrame: (frame: any) => void,
  opts?: { onOpen?: () => void; onError?: (message: string) => void },
): () => void {
  let source: EventSource | null = null
  try {
    source = new EventSource(`/api/v1${BASE}/${sessionId}/stream`)
    source.onopen = () => opts?.onOpen?.()
    source.onmessage = (ev) => {
      try {
        const payload = JSON.parse(ev.data)
        if (payload && typeof payload === 'object' && typeof payload.type === 'string') {
          onFrame(payload)
        }
      } catch {
        // 心跳（": ping"）或非法帧：忽略
      }
    }
    source.onerror = () => opts?.onError?.('stream disconnected')
  } catch {
    opts?.onError?.('EventSource unavailable')
  }
  return () => source?.close()
}
