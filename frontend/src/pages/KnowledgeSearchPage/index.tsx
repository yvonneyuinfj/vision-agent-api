import { useEffect, useMemo, useRef, useState } from 'react'
import Md from '../../components/markdown/Md'
import { Icon } from '../../components/ui/Icon'
import type { ChatConfig, ChatReference } from '../../api/kb'
import { useKbChat, type UiChatMessage } from './useKbChat'
import { CitationPanel } from './CitationPanel'
import { PanelToggleButton } from './PanelToggleButton'
import { AssistantSettingsDialog } from './AssistantSettingsDialog'
import { SessionTitleEditor } from './SessionTitleEditor'
import './index.css'

const SESSIONS_PANEL_KEY = 'aeroqc:kbChatSessionsCollapsed'
const CITATIONS_PANEL_KEY = 'aeroqc:kbChatCitationsCollapsed'

function readCollapsedPreference(key: string): boolean {
  try {
    return localStorage.getItem(key) === '1'
  } catch {
    return false
  }
}

function writeCollapsedPreference(key: string, collapsed: boolean): void {
  try {
    localStorage.setItem(key, collapsed ? '1' : '0')
  } catch {
    // 浏览器禁用持久化时仍保留当前页面内的折叠状态。
  }
}

function citationId(reference: ChatReference | undefined, index: number): string {
  const chunk = reference?.chunks?.[index]
  return String(chunk?.id ?? chunk?.chunk_id ?? index)
}

function withCitationLinks(content: string, reference?: ChatReference): string {
  const replaceCitation = (marker: string, raw: string): string => {
    const index = Number(raw)
    if (!Number.isSafeInteger(index) || index < 0 || !reference?.chunks?.[index]) return marker
    return `[${index + 1}](ref://${encodeURIComponent(citationId(reference, index))})`
  }

  return content
    .replace(/##(\d+)\$\$/g, replaceCitation)
    .replace(/\[\s*ID\s*:\s*(\d+)\s*\]/gi, replaceCitation)
}

function citationIndexes(content: string): number[] {
  return [...content.matchAll(/##(\d+)\$\$|\[\s*ID\s*:\s*(\d+)\s*\]/gi)]
    .map((match) => Number(match[1] ?? match[2]))
    .filter(Number.isSafeInteger)
}

/**
 * RAGFlow 的多轮追问有时只在答案中返回引用标记，不重复发送 reference。
 * 仅当所有标记都能落到最近一次引用数组中时才继承，避免把未知引用错误链接到旧资料。
 */
export function withInheritedReferences(messages: UiChatMessage[]): UiChatMessage[] {
  let latestReference: ChatReference | undefined
  return messages.map((message) => {
    if ((message.reference?.chunks?.length ?? 0) > 0) {
      latestReference = message.reference
      return message
    }
    if (message.role !== 'assistant' || !latestReference?.chunks?.length) return message
    const indexes = citationIndexes(message.content)
    if (indexes.length === 0 || indexes.some((index) => index < 0 || !latestReference?.chunks?.[index])) return message
    return { ...message, reference: latestReference }
  })
}

function MessageBubble({
  message,
  onRetry,
  onShowReferences,
  onCitation,
}: {
  message: UiChatMessage
  onRetry: () => void
  onShowReferences: () => void
  onCitation: (chunkId: string) => void
}) {
  const references = message.reference?.chunks ?? []
  return (
    <article className={`chat-message chat-message--${message.role}`}>
      <div className="chat-message__avatar">{message.role === 'assistant' ? 'AI' : '我'}</div>
      <div className="chat-message__body">
        <div className="chat-message__role">{message.role === 'assistant' ? '知识助手' : '你'}</div>
        <div className="chat-message__content">
          {message.role === 'assistant' ? (
            message.content
              ? <Md onCitationClick={onCitation}>{withCitationLinks(message.content, message.reference)}</Md>
              : <span className="chat-thinking"><i /><i /><i /></span>
          ) : <p>{message.content}</p>}
        </div>
        {message.state && message.state !== 'streaming' && (
          <div className={`chat-message__state chat-message__state--${message.state}`}>{message.error}</div>
        )}
        {message.role === 'assistant' && (
          <div className="chat-message__actions">
            {references.length > 0 && (
              <button type="button" className="chat-message__reference-action" onClick={onShowReferences}>完整引用 · {references.length}</button>
            )}
            {message.id && message.state !== 'streaming' && (
              <button
                type="button"
                className="chat-message__icon-action"
                aria-label="重新生成"
                title="重新生成"
                onClick={onRetry}
              >
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true">
                  <path d="M20 6v5h-5" />
                  <path d="M4 18v-5h5" />
                  <path d="M6.1 9a7 7 0 0 1 11.7-2.6L20 11" />
                  <path d="M17.9 15a7 7 0 0 1-11.7 2.6L4 13" />
                </svg>
              </button>
            )}
          </div>
        )}
      </div>
    </article>
  )
}

export default function KnowledgeSearchPage() {
  const chat = useKbChat()
  const [input, setInput] = useState('')
  const [selectedMessageKey, setSelectedMessageKey] = useState<string | null>(null)
  const [selectedChunkId, setSelectedChunkId] = useState<string | null>(null)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [sessionsCollapsed, setSessionsCollapsed] = useState(() => readCollapsedPreference(SESSIONS_PANEL_KEY))
  const [citationsCollapsed, setCitationsCollapsed] = useState(() => readCollapsedPreference(CITATIONS_PANEL_KEY))
  const transcriptRef = useRef<HTMLElement>(null)

  useEffect(() => {
    writeCollapsedPreference(SESSIONS_PANEL_KEY, sessionsCollapsed)
  }, [sessionsCollapsed])

  useEffect(() => {
    writeCollapsedPreference(CITATIONS_PANEL_KEY, citationsCollapsed)
  }, [citationsCollapsed])

  useEffect(() => {
    setSelectedMessageKey(null)
    setSelectedChunkId(null)
  }, [chat.activeId])

  useEffect(() => {
    if (chat.streaming && transcriptRef.current) {
      transcriptRef.current.scrollTop = transcriptRef.current.scrollHeight
    }
  }, [chat.messages, chat.streaming])

  const displayMessages = useMemo(() => withInheritedReferences(chat.messages), [chat.messages])
  const latestReferenced = useMemo(
    () => [...displayMessages].reverse().find((item) => (item.reference?.chunks?.length ?? 0) > 0),
    [displayMessages],
  )
  const selectedMessage = displayMessages.find((item) => item.key === selectedMessageKey)
  const activeReference = selectedMessage?.reference ?? latestReferenced?.reference
  const activeSession = chat.sessions.find((item) => item.id === chat.activeId)

  const submit = (): void => {
    if (!input.trim() || chat.streaming) return
    const question = input
    setInput('')
    void chat.send(question)
  }

  const openSettings = (): void => {
    setSettingsOpen(true)
  }

  const statusText = chat.info?.ready
    ? `${chat.info.config?.dataset_ids.length ?? 0} 个知识库 · ${chat.info.config?.llm_id || '默认模型'}`
    : chat.error || '正在连接 RAGFlow…'

  const configuredDatasets = chat.info?.config?.dataset_ids ?? []

  return (
    <div className="kb-chat-page">
      <div className="page-head kb-chat-head">
        <div>
          <div className="page-title">知识库检索</div>
          <div className="page-sub">基于 RAGFlow 的多轮问答，回答、引用、图片与原始文档全程可溯源</div>
        </div>
        <div className="kb-chat-head__actions">
          <span className={`tag ${chat.info?.ready ? 't-success' : 't-failed'}`}>{statusText}</span>
          <button type="button" className="btn btn-sm" onClick={openSettings}>助手配置</button>
        </div>
      </div>

      {/* 方案 §7.3 要求「先权限过滤、再检索」。当前后端无权限字段，页面如实声明全库可见；
          检索范围取 GET /ragflow/chat/datasets 的真实名称与文档数 */}
      <div className="kb-chat-scope">
        <span className="kb-chat-scope__notice">
          <Icon name="warn" size={13} />
          未启用知识 ACL，检索结果为<b>全库</b>，未做权限过滤 · 接口无 page/offset，会话按轮次续写而非翻页重检
        </span>
        <span className="grow" />
        {chat.info?.config && (
          <span className="hint mono">
            取回深度 top_k {chat.info.config.top_k} · 引用上限 top_n {chat.info.config.top_n}
          </span>
        )}
        {configuredDatasets.map((id) => {
          const ds = chat.datasets.find((item) => item.id === id)
          return (
            <span className="chip" key={id} title={ds ? undefined : chat.datasetsError ?? undefined}>
              {ds?.name ?? '清单未返回'}
              {typeof ds?.document_count === 'number' && <b className="mono"> · {ds.document_count} 篇</b>}
            </span>
          )
        })}
      </div>

      <div className={`kb-chat-shell card${sessionsCollapsed ? ' is-sessions-collapsed' : ''}${citationsCollapsed ? ' is-citations-collapsed' : ''}`}>
        <aside className={`chat-sessions${sessionsCollapsed ? ' is-collapsed' : ''}`}>
          <div className="chat-sessions__head">
            <strong hidden={sessionsCollapsed}>历史会话</strong>
            <div className="chat-sessions__actions">
              <button type="button" className="chat-sessions__new" aria-label="新建会话" hidden={sessionsCollapsed} onClick={() => void chat.newSession()}>+</button>
              <PanelToggleButton
                side="left"
                label="历史会话"
                collapsed={sessionsCollapsed}
                controls="chat-sessions-list"
                onToggle={() => setSessionsCollapsed((value) => !value)}
              />
            </div>
          </div>
          <div id="chat-sessions-list" className="chat-sessions__list" hidden={sessionsCollapsed}>
            {chat.sessions.map((session) => (
              <div key={session.id} className={`chat-session${session.id === chat.activeId ? ' is-active' : ''}`}>
                <button type="button" className="chat-session__main" onClick={() => void chat.openSession(session.id)}>
                  <span>{session.name}</span>
                  <small>{session.update_time ? new Date(session.update_time).toLocaleDateString('zh-CN') : '刚刚'}</small>
                </button>
                <button type="button" className="chat-session__delete" aria-label={`删除${session.name}`} onClick={() => void chat.removeSession(session.id)}>×</button>
              </div>
            ))}
          </div>
        </aside>

        <main className="chat-main">
          <header className="chat-main__toolbar">
            <SessionTitleEditor
              sessionId={activeSession?.id}
              name={activeSession?.name}
              streaming={chat.streaming}
              onRename={chat.renameSession}
            />
            <button type="button" className="btn btn-sm" disabled={!chat.activeId || chat.streaming} onClick={() => void chat.clear()}>清空会话</button>
          </header>

          <section ref={transcriptRef} className="chat-transcript" aria-live="polite">
            {chat.loading && <div className="chat-empty">正在加载会话…</div>}
            {!chat.loading && chat.error && (
              <div className="chat-empty chat-empty--error"><p>{chat.error}</p><button type="button" className="btn btn-sm" onClick={() => void chat.refresh()}>重试</button></div>
            )}
            {!chat.loading && !chat.error && chat.messages.length === 0 && (
              <div className="chat-empty">可以询问判级标准、历史案例或文档中的任何技术问题</div>
            )}
            {displayMessages.map((message) => (
              <MessageBubble
                key={message.key}
                message={message}
                onRetry={() => message.id && void chat.retry(message.id)}
                onShowReferences={() => { setSelectedMessageKey(message.key); setSelectedChunkId(null); setCitationsCollapsed(false) }}
                onCitation={(chunkId) => { setSelectedMessageKey(message.key); setSelectedChunkId(chunkId); setCitationsCollapsed(false) }}
              />
            ))}
          </section>

          <footer className="chat-composer">
            <textarea
              value={input}
              rows={2}
              maxLength={8000}
              placeholder="输入问题，Enter 发送，Shift + Enter 换行"
              disabled={!chat.info?.ready}
              onChange={(event) => setInput(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); submit() }
              }}
            />
            <div className="chat-composer__foot">
              <span>RAGFlow 可能产生错误，重要结论请核对原文</span>
              {chat.streaming
                ? <button type="button" className="btn btn-danger" onClick={chat.stop}>停止</button>
                : <button type="button" className="btn btn-primary" disabled={!input.trim() || !chat.info?.ready} onClick={submit}>发送</button>}
            </div>
          </footer>
        </main>

        <CitationPanel
          reference={activeReference}
          selectedChunkId={selectedChunkId}
          onSelect={setSelectedChunkId}
          collapsed={citationsCollapsed}
          onToggleCollapsed={() => setCitationsCollapsed((value) => !value)}
          datasets={chat.datasets.map((d) => ({ name: d.name, document_count: d.document_count }))}
          datasetsNote={chat.datasetsError}
        />
      </div>

      <AssistantSettingsDialog
        open={settingsOpen}
        config={chat.info?.config}
        datasets={chat.datasets}
        datasetsLoading={chat.datasetsLoading}
        datasetsError={chat.datasetsError}
        onRetryDatasets={() => void chat.refreshDatasets()}
        onCancel={() => setSettingsOpen(false)}
        onSave={async (nextConfig: ChatConfig) => {
          await chat.saveConfig(nextConfig)
          setSettingsOpen(false)
        }}
      />
    </div>
  )
}
