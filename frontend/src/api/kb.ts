import http, { unwrapResponse } from './http'

export type SearchMode = 'semantic' | 'hybrid' | 'keyword'

export interface SearchHit {
  chunk_id: string
  content: string
  score: number
  doc_name: string
  document_id: string | null
  dataset_id: string | null
  image_id: string | null
}

export interface SearchResult {
  query: string
  mode: SearchMode
  elapsed_ms: number
  hits: SearchHit[]
}

export interface DocChunk {
  chunk_id: string
  content: string
  image_id: string | null
}

export interface ChunkDoc {
  document_id: string
  doc_name: string
  chunks: DocChunk[]
}

interface Envelope<T> {
  code: number
  message: string
  trace_id?: string | null
  result?: T | null
}

// 用户主动检索：不传 silent —— 网络级失败照常弹全局提示，页面错误态并行展示
export async function searchKnowledge(query: string, mode: SearchMode): Promise<SearchHit[]> {
  const resp = await http.post<Envelope<SearchResult>>('/ragflow/search', { question: query, mode, top_k: 5 })
  return unwrapResponse(resp).hits
}

export async function fetchDocChunks(datasetId: string, documentId: string): Promise<ChunkDoc> {
  const resp = await http.get<Envelope<ChunkDoc>>(`/ragflow/datasets/${datasetId}/documents/${documentId}/chunks`)
  return unwrapResponse(resp)
}

// 本仓库后端无鉴权，缩略图直接拼代理 URL（失败由 <img> onError 降级占位）
export function buildImageUrl(imageId: string): string {
  return `/api/v1/ragflow/images/${imageId}`
}

export function parseFilenameFromDisposition(header: string | undefined, fallback: string): string {
  if (!header) return fallback
  const starred = header.match(/filename\*=[^']*''([^;]+)/i)
  if (starred) {
    try {
      return decodeURIComponent(starred[1].trim())
    } catch {
      return fallback
    }
  }
  const plain = header.match(/filename="?([^";]+)"?/i)
  return plain ? plain[1].trim() : fallback
}

export async function downloadDocFile(datasetId: string, documentId: string, fallbackName: string): Promise<void> {
  const resp = await http.get<Blob>(`/ragflow/datasets/${datasetId}/documents/${documentId}/file`, {
    responseType: 'blob',
  })
  const name = parseFilenameFromDisposition(resp.headers?.['content-disposition'], fallbackName)
  const url = URL.createObjectURL(resp.data as Blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = name
  anchor.click()
  URL.revokeObjectURL(url)
}

// ===== RAGFlow Chat（原检索 API 继续保留给 Agent/兼容调用） =====

export interface ChatConfig {
  name: string
  description?: string
  icon?: string
  dataset_ids: string[]
  language?: string
  llm_id?: string
  llm_setting: Record<string, unknown>
  prompt_config: Record<string, unknown>
  prompt_type?: string
  rerank_id?: string
  similarity_threshold: number
  vector_similarity_weight: number
  top_n: number
  top_k: number
  do_refer?: string
}

export interface ChatInfo {
  ready: boolean
  chat_id: string | null
  error: string | null
  config: ChatConfig | null
}

export interface ChatDataset {
  id: string
  name: string
  description?: string | null
  document_count?: number | null
  chunk_count?: number | null
  update_time?: number | null
}

export interface ChatReferenceChunk {
  id?: string
  chunk_id?: string
  content?: string
  document_id?: string
  doc_id?: string
  document_name?: string
  doc_name?: string
  dataset_id?: string
  image_id?: string
  similarity?: number
  vector_similarity?: number
  term_similarity?: number
  positions?: unknown[]
  [key: string]: unknown
}

export interface ChatReference {
  chunks?: ChatReferenceChunk[]
  doc_aggs?: unknown[] | Record<string, unknown>
  [key: string]: unknown
}

export interface RawChatMessage {
  id?: string
  role: 'user' | 'assistant' | 'system'
  content: string
  created_at?: number
}

export interface ChatSession {
  id: string
  chat_id: string
  name: string
  messages: RawChatMessage[]
  reference?: ChatReference[]
  create_time?: number
  update_time?: number
}

export interface ChatStreamData {
  answer?: string
  reference?: ChatReference
  id?: string
  session_id?: string
  chat_id?: string
  final?: boolean
  start_to_think?: boolean
  end_to_think?: boolean
}

export async function fetchChatInfo(): Promise<ChatInfo> {
  const resp = await http.get<Envelope<ChatInfo>>('/ragflow/chat')
  return unwrapResponse(resp)
}

export async function fetchChatDatasets(opts?: { silent?: boolean }): Promise<ChatDataset[]> {
  // 静默档给只读状态视图用；默认档保持原调用签名（不传 config）
  const resp = opts?.silent
    ? await http.get<Envelope<ChatDataset[]>>('/ragflow/chat/datasets', { silent: true })
    : await http.get<Envelope<ChatDataset[]>>('/ragflow/chat/datasets')
  return unwrapResponse(resp)
}

export async function updateChatConfig(config: ChatConfig): Promise<ChatInfo> {
  const resp = await http.put<Envelope<{ chat_id: string; config: ChatConfig }>>('/ragflow/chat/config', config)
  const result = unwrapResponse(resp)
  return { ready: true, chat_id: result.chat_id, config: result.config, error: null }
}

export async function listChatSessions(): Promise<ChatSession[]> {
  const resp = await http.get<Envelope<ChatSession[]>>('/ragflow/chat/sessions')
  return unwrapResponse(resp)
}

export async function createChatSession(name = '新会话'): Promise<ChatSession> {
  const resp = await http.post<Envelope<ChatSession>>('/ragflow/chat/sessions', { name })
  return unwrapResponse(resp)
}

export async function fetchChatSession(sessionId: string): Promise<ChatSession> {
  const resp = await http.get<Envelope<ChatSession>>(`/ragflow/chat/sessions/${sessionId}`)
  return unwrapResponse(resp)
}

export async function renameChatSession(sessionId: string, name: string): Promise<ChatSession> {
  const resp = await http.patch<Envelope<ChatSession>>(
    `/ragflow/chat/sessions/${encodeURIComponent(sessionId)}`,
    { name },
  )
  return unwrapResponse(resp)
}

export async function deleteChatSession(sessionId: string): Promise<void> {
  const resp = await http.delete<Envelope<boolean>>(`/ragflow/chat/sessions/${sessionId}`)
  unwrapResponse(resp)
}

export async function deleteChatMessage(sessionId: string, messageId: string): Promise<ChatSession> {
  const resp = await http.delete<Envelope<ChatSession>>(`/ragflow/chat/sessions/${sessionId}/messages/${messageId}`)
  return unwrapResponse(resp)
}

export function buildDocumentPreviewUrl(documentId: string, name: string): string {
  return `/api/v1/ragflow/documents/${encodeURIComponent(documentId)}/content?name=${encodeURIComponent(name)}`
}

function parseSseBlock(block: string): string | null {
  const data = block
    .split('\n')
    .filter((line) => line.startsWith('data:'))
    .map((line) => line.slice(5).trimStart())
    .join('\n')
  return data || null
}

export async function streamChatCompletion(
  sessionId: string,
  question: string,
  messageId: string,
  onChunk: (chunk: ChatStreamData) => void,
  signal?: AbortSignal,
): Promise<void> {
  const response = await fetch(`/api/v1/ragflow/chat/sessions/${encodeURIComponent(sessionId)}/completions`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
    body: JSON.stringify({ question, message_id: messageId }),
    signal,
  })
  if (!response.ok || !response.body) {
    let message = `知识库对话失败（HTTP ${response.status}）`
    try {
      const body = await response.json() as { message?: string }
      if (body.message) message = body.message
    } catch { /* 非 JSON 错误页 */ }
    throw new Error(message)
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  const consume = (block: string): void => {
    const raw = parseSseBlock(block)
    if (!raw || raw === '[DONE]') return
    const event = JSON.parse(raw) as { code?: number; message?: string; data?: ChatStreamData | boolean }
    if ((event.code ?? 0) !== 0) throw new Error(event.message || '知识库对话失败')
    if (event.data && typeof event.data === 'object') onChunk(event.data)
  }

  try {
    while (true) {
      const { value, done } = await reader.read()
      buffer += decoder.decode(value, { stream: !done }).replace(/\r\n/g, '\n')
      const blocks = buffer.split('\n\n')
      buffer = blocks.pop() ?? ''
      blocks.forEach(consume)
      if (done) break
    }
    if (buffer.trim()) consume(buffer)
  } finally {
    reader.releaseLock()
  }
}
