import { useCallback, useEffect, useRef, useState } from 'react'
import {
  createChatSession,
  deleteChatMessage,
  deleteChatSession,
  fetchChatDatasets,
  fetchChatInfo,
  fetchChatSession,
  listChatSessions,
  renameChatSession,
  streamChatCompletion,
  updateChatConfig,
  type ChatConfig,
  type ChatDataset,
  type ChatInfo,
  type ChatReference,
  type ChatSession,
} from '../../api/kb'

export interface UiChatMessage {
  key: string
  id?: string
  role: 'user' | 'assistant'
  content: string
  reference?: ChatReference
  state?: 'streaming' | 'error' | 'stopped'
  error?: string
}

function errorMessage(error: unknown): string {
  if (error instanceof Error && error.name === 'AbortError') return '已停止生成'
  return error instanceof Error && error.message ? error.message : '知识库对话失败'
}

function sessionMessages(session: ChatSession): UiChatMessage[] {
  const refs = session.reference ?? []
  let refIndex = 0
  return (session.messages ?? [])
    .filter((item) => item.role === 'user' || item.role === 'assistant')
    .map((item, index) => {
      const reference = item.role === 'assistant' && item.id ? refs[refIndex++] : undefined
      return {
        key: `${item.id ?? 'opening'}:${item.role}:${index}`,
        id: item.id,
        role: item.role as 'user' | 'assistant',
        content: item.content ?? '',
        reference,
      }
    })
}

function newMessageId(): string {
  return globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(16).slice(2)}`
}

const DEFAULT_SESSION_NAME = /^新会话(?:\s|$)/
const SESSION_TITLE_MAX_LENGTH = 28

/** 从首条问题提取可扫描的会话标题，不增加一次模型调用。 */
export function deriveSessionTitle(question: string): string {
  const compact = question
    .replace(/[`*_>#]+/g, ' ')
    .replace(/[\r\n\t]+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
    .replace(/^(?:请问|请帮我|帮我|麻烦(?:你)?|我想知道)(?:一下)?[，,：:\s]*/u, '')
    .replace(/[？?！!。；;，,]+$/u, '')
    .trim()
  const source = compact || question.trim() || '新会话'
  const characters = Array.from(source)
  return characters.length <= SESSION_TITLE_MAX_LENGTH
    ? source
    : `${characters.slice(0, SESSION_TITLE_MAX_LENGTH - 1).join('')}…`
}

export function useKbChat() {
  const [info, setInfo] = useState<ChatInfo | null>(null)
  const [sessions, setSessions] = useState<ChatSession[]>([])
  const [activeId, setActiveId] = useState<string | null>(null)
  const [messages, setMessages] = useState<UiChatMessage[]>([])
  const [loading, setLoading] = useState(true)
  const [streaming, setStreaming] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [datasets, setDatasets] = useState<ChatDataset[]>([])
  const [datasetsLoading, setDatasetsLoading] = useState(false)
  const [datasetsError, setDatasetsError] = useState<string | null>(null)
  const abortRef = useRef<AbortController | null>(null)
  const activeRef = useRef<string | null>(null)

  useEffect(() => { activeRef.current = activeId }, [activeId])

  const refreshDatasets = useCallback(async () => {
    setDatasetsLoading(true)
    setDatasetsError(null)
    try {
      setDatasets(await fetchChatDatasets())
    } catch (err) {
      setDatasetsError(errorMessage(err))
    } finally {
      setDatasetsLoading(false)
    }
  }, [])

  const renameSession = useCallback(async (sessionId: string, rawName: string) => {
    const name = rawName.trim()
    if (!name) throw new Error('请输入会话名称')
    const renamed = await renameChatSession(sessionId, name)
    const persistedName = renamed.name?.trim() || name
    setSessions((rows) => rows.map((item) => (
      item.id === sessionId ? { ...item, ...renamed, name: persistedName } : item
    )))
    return { ...renamed, name: persistedName }
  }, [])

  const openSession = useCallback(async (sessionId: string) => {
    abortRef.current?.abort()
    setLoading(true)
    setError(null)
    try {
      const session = await fetchChatSession(sessionId)
      setActiveId(session.id)
      activeRef.current = session.id
      const restoredMessages = sessionMessages(session)
      setMessages(restoredMessages)
      const firstQuestion = restoredMessages.find((message) => message.role === 'user')?.content
      if (DEFAULT_SESSION_NAME.test(session.name) && firstQuestion) {
        void renameSession(session.id, deriveSessionTitle(firstQuestion)).catch(() => {
          // 旧会话迁移失败不影响历史记录读取，仍可由用户手动重命名。
        })
      }
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setLoading(false)
    }
  }, [renameSession])

  const newSession = useCallback(async (name?: string) => {
    setError(null)
    const session = await createChatSession(name ?? `新会话 ${new Date().toLocaleString('zh-CN', { hour: '2-digit', minute: '2-digit' })}`)
    setSessions((current) => [session, ...current.filter((item) => item.id !== session.id)])
    setActiveId(session.id)
    activeRef.current = session.id
    setMessages(sessionMessages(session))
    return session
  }, [])

  const bootstrap = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const chatInfo = await fetchChatInfo()
      setInfo(chatInfo)
      void refreshDatasets()
      if (!chatInfo.ready) {
        setError(chatInfo.error || 'RAGFlow chat 尚未就绪')
        return
      }
      const rows = await listChatSessions()
      setSessions(rows)
      if (rows.length > 0) await openSession(rows[0].id)
      else await newSession()
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setLoading(false)
    }
  }, [newSession, openSession, refreshDatasets])

  useEffect(() => {
    void bootstrap()
    return () => abortRef.current?.abort()
  }, [bootstrap])

  const send = useCallback(async (raw: string) => {
    const question = raw.trim()
    let sessionId = activeRef.current
    let currentSession = sessions.find((item) => item.id === sessionId)
    if (!question || streaming) return
    if (!sessionId) {
      currentSession = await newSession()
      sessionId = currentSession.id
    }
    const id = newMessageId()
    const user: UiChatMessage = { key: `${id}:user`, id, role: 'user', content: question }
    const assistant: UiChatMessage = { key: `${id}:assistant`, id, role: 'assistant', content: '', state: 'streaming' }
    const shouldAutoName = Boolean(
      currentSession
      && DEFAULT_SESSION_NAME.test(currentSession.name)
      && !messages.some((message) => message.role === 'user'),
    )
    setMessages((current) => [...current, user, assistant])
    setStreaming(true)
    setError(null)
    const autoRename = shouldAutoName
      ? renameSession(sessionId, deriveSessionTitle(question)).catch(() => {
        // 自动命名失败不应打断主问答；保留默认名称，用户仍可手动重试。
      })
      : Promise.resolve()
    const controller = new AbortController()
    abortRef.current = controller
    try {
      await streamChatCompletion(sessionId, question, id, (chunk) => {
        setMessages((current) => current.map((message) => {
          if (message.key !== assistant.key) return message
          const hasRefs = (chunk.reference?.chunks?.length ?? 0) > 0
          return {
            ...message,
            content: chunk.final && chunk.answer ? chunk.answer : message.content + (chunk.answer ?? ''),
            reference: hasRefs ? chunk.reference : message.reference,
          }
        }))
      }, controller.signal)
      setMessages((current) => current.map((message) => (
        message.key === assistant.key ? { ...message, state: undefined } : message
      )))
      void autoRename
        .then(listChatSessions)
        .then(setSessions)
        .catch(() => {
          // 回答已完成，后台刷新失败时保留当前会话列表，不覆盖主流程状态。
        })
    } catch (err) {
      const stopped = err instanceof Error && err.name === 'AbortError'
      setMessages((current) => current.map((message) => (
        message.key === assistant.key
          ? { ...message, state: stopped ? 'stopped' : 'error', error: errorMessage(err) }
          : message
      )))
    } finally {
      if (abortRef.current === controller) abortRef.current = null
      setStreaming(false)
    }
  }, [messages, newSession, renameSession, sessions, streaming])

  const retry = useCallback(async (messageId: string) => {
    if (!activeRef.current || streaming) return
    const question = messages.find((message) => message.id === messageId && message.role === 'user')?.content
    if (!question) return
    await deleteChatMessage(activeRef.current, messageId)
    setMessages((current) => current.filter((message) => message.id !== messageId))
    await send(question)
  }, [messages, send, streaming])

  const clear = useCallback(async () => {
    const currentId = activeRef.current
    if (!currentId || streaming) return
    const currentName = sessions.find((item) => item.id === currentId)?.name ?? '新会话'
    await deleteChatSession(currentId)
    setSessions((rows) => rows.filter((item) => item.id !== currentId))
    setActiveId(null)
    activeRef.current = null
    setMessages([])
    await newSession(currentName)
  }, [newSession, sessions, streaming])

  const removeSession = useCallback(async (sessionId: string) => {
    if (streaming) return
    await deleteChatSession(sessionId)
    const remain = sessions.filter((item) => item.id !== sessionId)
    setSessions(remain)
    if (sessionId === activeRef.current) {
      if (remain[0]) await openSession(remain[0].id)
      else await newSession()
    }
  }, [newSession, openSession, sessions, streaming])

  const saveConfig = useCallback(async (config: ChatConfig) => {
    const next = await updateChatConfig(config)
    setInfo(next)
  }, [])

  return {
    info, sessions, activeId, messages, loading, streaming, error,
    datasets, datasetsLoading, datasetsError, refreshDatasets,
    openSession, newSession, renameSession, send, retry, clear, removeSession, saveConfig,
    stop: () => abortRef.current?.abort(), refresh: bootstrap,
  }
}
