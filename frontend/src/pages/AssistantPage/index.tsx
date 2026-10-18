/** 质检助手（会话轨）：会话列表 + 对话面板 + 类型分派渲染 + 逐字流式。
 *
 * 流式协议（对齐 design-demos/思维链_玻璃疏行演绎.html）：
 * - SSE 顶层 type 决定渲染器：message（气泡 · 助手文本逐字 start→delta→end）、
 *   thinking（思考行 · 逐字）、chain（工具调用行 · tool_result 原地更新同行，
 *   协议唯一允许的原地更新）、card（创建/复核/完成/报告卡 · 只推不改）；
 * - 历史回放与实时增量同一入口折叠（streamModel.applyFrame，按 message_id 幂等）；
 * - EventSource 断线浏览器自动重连，onopen 清空重建，服务端重放全量历史自愈。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link, useLocation } from 'react-router-dom'
import {
  createSession,
  deleteSession,
  fetchSessions,
  openSessionStream,
  openSessionsStream,
  renameSession,
  sendMessage,
} from '../../api/sessions'
import { useSystemStore } from '../../store/useSystemStore'
import type { SceneCardPayload, SessionSummary } from '../../api/types'
import { SCENE_FINGERPRINT, SCENE_MAP, SCENE_OPTIONS } from '../../constants/scene'
import Md from '../../components/markdown/Md'
import { ThoughtRow } from '../../components/chain/ThoughtRow'
import { ToolCallRow, type ToolRowState } from '../../components/chain/ToolCallRow'
import '../../components/chain/ChainRow.css'
import ReviewCard from './ReviewCard'
import { TaskCard, DoneCard, ReportCard } from './cards'
import {
  applyFrame,
  emptyStreamState,
  renderItems,
  toolPayload,
  type RenderItem,
  type StreamState,
} from './streamModel'
import { formatBeijing } from '../../utils/time'
import './index.css'

const IMG_ACCEPT = '.jpg,.jpeg,.png,.tif,.tiff'

function sceneLabel(code: string | null | undefined): string {
  return code ? (SCENE_MAP[code as keyof typeof SCENE_MAP] ?? code) : '—'
}

/** 解析 routing 轻量行内容（后端 content 为 JSON {node,message}） */
function parseRoutingText(content: string): string {
  try {
    const p = JSON.parse(content) as { message?: unknown }
    if (typeof p.message === 'string') return p.message
  } catch { /* 非 JSON：按原文 */ }
  return content
}

/* ---------------- 场景确认交互卡片（payload 版） ---------------- */

function SceneCard({
  payload,
  active,
  sending,
  onPick,
  onGuide,
}: {
  payload: SceneCardPayload
  active: boolean
  sending: boolean
  onPick: (scene: string) => void
  onGuide: () => void
}) {
  const fingerprint = payload.mode === 'fingerprint'
  return (
    <div className="msg msg-assistant">
      <div className="bubble assistant scene-card">
        <div className="sc-head">
          <span className="sc-badge">请确认检测场景</span>
          {payload.image && (
            <a className="sc-thumb" href={payload.image.uri} target="_blank" rel="noreferrer">
              <img src={payload.image.uri} alt={payload.image.name} />
            </a>
          )}
        </div>
        {payload.reasoning && <div className="sc-reasoning">{payload.reasoning}</div>}
        <div className={`sc-list${fingerprint ? ' sc-fingerprint' : ''}`}>
          {payload.candidates.map((c) => {
            const code = c.scene as keyof typeof SCENE_MAP
            const label = SCENE_MAP[code] ?? c.scene
            return (
              <div className="sc-item" key={c.scene}>
                <div className="sc-item-main">
                  <div className="sc-item-top">
                    <b>{label}</b>
                    <span className="mono faint">({c.scene})</span>
                    {c.confidence !== null && (
                      <span className="sc-conf">
                        <span className="sc-conf-bar"><span style={{ width: `${Math.round(c.confidence * 100)}%` }} /></span>
                        <span className="mono">{Math.round(c.confidence * 100)}%</span>
                      </span>
                    )}
                  </div>
                  {fingerprint && SCENE_FINGERPRINT[code] && (
                    <div className="sc-item-hint">{SCENE_FINGERPRINT[code]}</div>
                  )}
                </div>
                <button
                  type="button"
                  className="btn btn-sm btn-primary"
                  disabled={!active || sending}
                  onClick={() => onPick(c.scene)}
                >
                  按此送检
                </button>
              </div>
            )
          })}
        </div>
        {/* 决策 12：需要专业参数时不造参数表单，渲染「去 X 工作台补齐参数」引导卡。
            场景取自本卡真实候选（candidates[0] = 置信度最高），接口无参数通道故引导转向工作台。 */}
        {payload.candidates.length > 0 && (
          <div className="sc-param-guide">
            <div className="notice info">
              <span>
                <b>需要专业参数才能给出可采信结论。</b>助手通道不传参数（接口无该字段）。请在
                {sceneLabel(payload.candidates[0].scene)}工作台补齐检测标准与阈值后发起，结果仍会回流到本会话。
              </span>
            </div>
            <Link
              className="btn btn-primary btn-sm"
              to={`/detect/${payload.candidates[0].scene}`}
            >
              前往{sceneLabel(payload.candidates[0].scene)}工作台补齐参数
            </Link>
          </div>
        )}
        <div className="sc-foot">
          <button type="button" className="btn btn-sm" disabled={!active || sending} onClick={onGuide}>
            都不是，给我采集指引
          </button>
        </div>
      </div>
    </div>
  )
}

function normalizeScenePayload(payload: Record<string, any>): SceneCardPayload | null {
  if (!payload || !Array.isArray(payload.candidates)) return null
  return {
    mode: payload.mode === 'fingerprint' ? 'fingerprint' : 'candidates',
    candidates: payload.candidates,
    reasoning: typeof payload.reasoning === 'string' ? payload.reasoning : '',
    image: payload.image ?? null,
  }
}

/* ---------------- 渲染单项 ---------------- */

function ItemView({
  item,
  isLast,
  sending,
  onPickScene,
  onGuide,
}: {
  item: RenderItem
  isLast: boolean
  sending: boolean
  onPickScene: (scene: string) => void
  onGuide: () => void
}) {
  if (item.kind === 'thinking') {
    return (
      <div className="msg msg-activity">
        <div className="act-solo">
          <ThoughtRow text={item.text} running={item.streaming} />
        </div>
      </div>
    )
  }

  if (item.kind === 'tool') {
    let state: ToolRowState
    if (item.running) state = 'running'
    else state = item.isError ? 'error' : 'ok'
    return (
      <div className="msg msg-activity">
        <div className="act-solo">
          <ToolCallRow data={toolPayload(item)} state={state} />
        </div>
      </div>
    )
  }

  if (item.kind === 'card') {
    if (item.cardType === 'task_card') {
      return <TaskCard key={item.id} taskId={item.taskId} payload={item.payload} />
    }
    if (item.cardType === 'review_card') {
      return (
        <ReviewCard
          key={item.id}
          taskId={item.taskId ?? item.payload?.task_id ?? ''}
          payload={item.payload}
          onSubmitted={() => { /* 本地即时落定；后续完成/报告卡经流推送 */ }}
        />
      )
    }
    if (item.cardType === 'done_card') {
      return <DoneCard key={item.id} taskId={item.taskId} payload={item.payload} />
    }
    if (item.cardType === 'report_card') {
      return <ReportCard key={item.id} taskId={item.taskId} payload={item.payload} />
    }
    if (item.cardType === 'scene_card') {
      const sp = normalizeScenePayload(item.payload)
      if (!sp) return null
      return (
        <SceneCard
          key={item.id}
          payload={sp}
          active={isLast}
          sending={sending}
          onPick={onPickScene}
          onGuide={onGuide}
        />
      )
    }
    return null
  }

  // message 族
  if (item.role === 'user') {
    return (
      <div className="msg msg-user" key={item.id}>
        <div className="bubble user">
          {item.content && <div className="msg-text">{item.content}</div>}
          {item.images && item.images.length > 0 && (
            <div className="msg-imgs">
              {item.images.map((img, i) => (
                <a key={i} href={img.uri} target="_blank" rel="noreferrer">
                  <img src={img.uri} alt={img.name ?? ''} />
                </a>
              ))}
            </div>
          )}
        </div>
      </div>
    )
  }
  if (item.mkind === 'routing') {
    return (
      <div className="msg msg-activity" key={item.id}>
        <div className="act-solo"><span className="routing-line">{parseRoutingText(item.content)}</span></div>
      </div>
    )
  }
  if (item.role === 'event') {
    return (
      <div className="msg msg-event" key={item.id}>
        <span className="event-pill">⚙ {item.content}</span>
      </div>
    )
  }
  // 助手文本 / 场景引导：逐字流式（未完成时纯文本 + 光标，落定后 Markdown 渲染）
  return (
    <div className="msg msg-assistant" key={item.id}>
      <div className={`bubble assistant${item.mkind === 'guidance' ? ' guidance' : ''}`}>
        {item.mkind === 'guidance' && <div className="guidance-badge">场景引导</div>}
        {item.streaming ? (
          <span className="msg-text">{item.content}<span className="stream-caret" /></span>
        ) : (
          <Md>{item.content}</Md>
        )}
      </div>
    </div>
  )
}

/* ---------------- 页面 ---------------- */

export default function AssistantPage() {
  const [sessions, setSessions] = useState<SessionSummary[]>([])
  const [activeId, setActiveId] = useState<string | null>(null)
  const [stream, setStream] = useState<StreamState>(emptyStreamState)
  const [text, setText] = useState('')
  const [files, setFiles] = useState<File[]>([])
  const [sending, setSending] = useState(false)
  const [forceScene, setForceScene] = useState<string>('')
  const scrollRef = useRef<HTMLDivElement>(null)
  const fileRef = useRef<HTMLInputElement>(null)

  const vlmAvailable = useSystemStore((s) => s.vlmAvailable)
  const probe = useSystemStore((s) => s.probe)
  useEffect(() => { void probe() }, [probe])
  const agentDisabled = vlmAvailable === false

  const items = useMemo(() => renderItems(stream), [stream])
  const activeSession = sessions.find((s) => s.session_id === activeId)

  // ---------- 数据加载 ----------
  const location = useLocation()

  /* 首页「开始分析」跳转而来：直接定位到指定会话。
     消费后清掉 history state，避免刷新/回退时重复抢占用户手动选择的会话。 */
  useEffect(() => {
    const st = location.state as { sessionId?: string } | null
    if (st?.sessionId) {
      setActiveId(st.sessionId)
      window.history.replaceState({}, '')
    }
  }, [location.state])

  const loadSessions = useCallback(async () => {
    try { setSessions(await fetchSessions()) } catch { /* 静默：下次重试 */ }
  }, [])

  useEffect(() => { void loadSessions() }, [loadSessions])

  // 会话列表：SSE 列表流取代轮询——首帧全量，任何变化整表重推
  useEffect(() => {
    const close = openSessionsStream((list) => setSessions(list))
    return close
  }, [])

  // 当前会话消息流：类型分派 + 逐字增量协议。
  // onopen（含断线重连）清空重建，服务端随后重放全量历史自愈。
  useEffect(() => {
    if (!activeId) { setStream(emptyStreamState); return }
    const close = openSessionStream(
      activeId,
      (frame) => {
        if (!frame || typeof frame !== 'object') return
        if (frame.type === 'error') return // 流内错误帧：静默（HTTP 层已有提示）
        setStream((prev) => applyFrame(prev, frame))
      },
      { onOpen: () => setStream(emptyStreamState) },
    )
    return close
  }, [activeId])

  // 新帧到达自动滚动到底部（含逐字增长）
  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight })
  }, [stream])

  // ---------- 会话操作 ----------
  async function handleNewSession() {
    try {
      const s = await createSession()
      setActiveId(s.session_id)
      setStream(emptyStreamState)
      void loadSessions()
    } catch { /* 拦截器已提示 */ }
  }

  async function handleRename(s: SessionSummary) {
    const next = window.prompt('重命名会话', s.title ?? '')
    if (next === null || !next.trim()) return
    try { await renameSession(s.session_id, next.trim()); void loadSessions() } catch { /* 已提示 */ }
  }

  async function handleDelete(s: SessionSummary) {
    if (!window.confirm(`删除会话「${s.title ?? s.session_id}」？历史消息将一并移除。`)) return
    try {
      await deleteSession(s.session_id)
      if (activeId === s.session_id) { setActiveId(null); setStream(emptyStreamState) }
      void loadSessions()
    } catch { /* 已提示 */ }
  }

  // ---------- 发送 ----------
  const lastAssistant = useMemo(() => {
    for (let i = items.length - 1; i >= 0; i--) {
      const it = items[i]
      if (it.kind === 'message' && it.role === 'assistant') return it
    }
    return null
  }, [items])
  const showForceSpecify = lastAssistant?.kind === 'message' && lastAssistant.mkind === 'guidance'

  const pending = useMemo(() => {
    // 发送后到助手首帧到达前："最后一条为用户消息且无后续" → 思考中指示
    if (!items.length) return false
    const last = items[items.length - 1]
    return last.kind === 'message' && last.role === 'user'
  }, [items])

  async function doSend(content: string, attachments: File[]) {
    if (!activeId) {
      // 无会话时先建一个
      try {
        const s = await createSession()
        setActiveId(s.session_id)
        await sendMessage(s.session_id, content, attachments)
      } catch { /* 已提示 */ }
      return
    }
    await sendMessage(activeId, content, attachments)
  }

  async function handleSend() {
    const content = text.trim()
    if (!content && files.length === 0) return
    setSending(true)
    try {
      await doSend(content, files)
      setText('')
      setFiles([])
      void loadSessions()
    } catch { /* 拦截器已提示 */ } finally {
      setSending(false)
    }
  }

  async function handleForceSpecify() {
    if (!forceScene || !activeId) return
    const zh = sceneLabel(forceScene)
    setSending(true)
    try {
      await sendMessage(activeId, `这张图片属于「${zh}（${forceScene}）」场景，请按用户指定直接派发质检任务。`)
      setText('')
      setFiles([])
    } catch { /* 已提示 */ } finally {
      setSending(false)
    }
  }

  /** 场景卡片：按某场景送检（复用强制指定话术，触发派发回合）。 */
  async function handlePickScene(scene: string) {
    if (!activeId) return
    const zh = sceneLabel(scene)
    setSending(true)
    try {
      await sendMessage(activeId, `这张图片属于「${zh}（${scene}）」场景，已由用户确认，请直接派发质检任务。`)
    } catch { /* 已提示 */ } finally {
      setSending(false)
    }
  }

  /** 场景卡片：都不是 → 请求采集指引（进入引导分支）。 */
  async function handleGuide() {
    if (!activeId) return
    setSending(true)
    try {
      await sendMessage(activeId, '这张图片不属于上述任何场景。请给我图像采集指引，说明如何拍摄/准备一张可用于质检的图像。')
    } catch { /* 已提示 */ } finally {
      setSending(false)
    }
  }

  // ---------- 渲染 ----------
  return (
    <>
      <div className="page-head">
        <div>
          <div className="page-title">智能助手</div>
          <div className="page-sub">上传检测图像，助手自动识别场景并派发质检任务；识别不了会引导你，任务落定主动回报</div>
        </div>
        <button className="btn btn-primary" type="button" onClick={handleNewSession}>+ 新建会话</button>
      </div>

      {agentDisabled && (
        <div className="agent-off-banner">
          未检测到 VLM 多模态模型：智能助手暂不可用，输入已置灰。请配置 VLM_MODEL 后刷新，或前往专业工作台补齐参数后发起检测。
        </div>
      )}

      <div className="assistant-grid">
        {/* 左：对话面板（2/3）—— 对齐原型 grid-23 列序 */}
        <section className="card chat-panel">
          <div className="card-h chat-head">
            <div className="card-title">
              会话 {activeSession && <span className="en">{activeSession.title ?? '未命名会话'}</span>}
            </div>
            <span className="hint">思考与工具调用与对话同流呈现</span>
          </div>

          {!activeId ? (
            <div className="chat-empty">
              <div className="chat-empty-title">开始一段质检对话</div>
              <p>新建会话后，直接上传检测图像（孔探 / 红外 / X射线 / 荧光渗透 / 断口 / 金相），
                助手会识别场景、与你确认并派发质检任务；图像不属于任何场景时，会给出采集指引。</p>
            </div>
          ) : (
            <>
              <div className="chat-scroll" ref={scrollRef}>
                {items.length === 0 && (
                  <div className="chat-empty-hint">
                    发送消息或上传检测图像开始 · 助手将自动识别场景
                  </div>
                )}
                {items.map((item, idx) => (
                  <ItemView
                    key={item.id}
                    item={item}
                    isLast={idx === items.length - 1}
                    sending={sending}
                    onPickScene={(scene) => void handlePickScene(scene)}
                    onGuide={() => void handleGuide()}
                  />
                ))}
                {pending && !sending && (
                  <div className="msg msg-assistant">
                    <div className="bubble assistant typing">
                      <span className="spin" /> 助手正在处理…
                    </div>
                  </div>
                )}
              </div>

              {/* 引导分支的强制指定场景入口（决策 14：引导而非拒绝） */}
              {showForceSpecify && (
                <div className="force-specify">
                  <span className="fs-label">确定属于某个场景？可直接指定：</span>
                  <select value={forceScene} onChange={(e) => setForceScene(e.target.value)}>
                    <option value="">选择场景…</option>
                    {SCENE_OPTIONS.map((o) => (
                      <option key={o.value} value={o.value}>{o.label}（{o.value}）</option>
                    ))}
                  </select>
                  <button type="button" className="btn btn-sm btn-primary" disabled={!forceScene || sending}
                    onClick={handleForceSpecify}>按此场景送检</button>
                </div>
              )}

              <div className="chat-input">
                {/* 决策 12 降级：VLM 不可用时输入框可见置灰，并给出指向专业工作台的提示 */}
                {agentDisabled && (
                  <div className="notice gray vl-degrade">
                    <span>
                      大模型（VLM）不可用：助手通道已置灰，暂不能受理。请前往专业工作台补齐检测参数后发起：
                      {SCENE_OPTIONS.map((o) => (
                        <Link key={o.value} className="vl-link" to={`/detect/${o.value}`}>{o.label}工作台</Link>
                      ))}
                    </span>
                  </div>
                )}
                {files.length > 0 && (
                  <div className="attach-row">
                    {files.map((f, i) => (
                      <span className="attach-chip" key={`${f.name}-${i}`}>
                        {f.name}
                        <button type="button" onClick={() => setFiles(files.filter((_, j) => j !== i))}>✕</button>
                      </span>
                    ))}
                  </div>
                )}
                <div className="chat-input-row">
                  <input ref={fileRef} type="file" hidden accept={IMG_ACCEPT} multiple
                    onChange={(e) => {
                      const list = Array.from(e.currentTarget.files ?? [])
                      if (list.length) setFiles((prev) => [...prev, ...list])
                      e.currentTarget.value = ''
                    }} />
                  <button type="button" className="btn btn-sm" title="附加检测图像" disabled={agentDisabled}
                    onClick={() => fileRef.current?.click()}>+ 图片</button>
                  <input
                    className="chat-text"
                    placeholder={agentDisabled ? 'VLM 未配置，请前往专业工作台发起' : '描述检测需求，或上传图像后提问…'}
                    value={text}
                    disabled={agentDisabled || sending}
                    onChange={(e) => setText(e.target.value)}
                    onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); void handleSend() } }}
                  />
                  <button type="button" className="btn btn-primary" disabled={agentDisabled || sending || (!text.trim() && files.length === 0)}
                    onClick={handleSend}>{sending ? '发送中…' : '发送'}</button>
                </div>
              </div>
            </>
          )}
        </section>

        {/* 右：会话列表 + 能力边界说明（1/3） */}
        <aside className="assistant-side">
          <div className="card session-list">
            <div className="card-h"><div className="card-title">会话列表 <span className="en">SESSIONS</span></div></div>
            <div className="card-b session-items">
              {sessions.length === 0 && <div className="list-empty">暂无会话，点击右上角新建</div>}
              {sessions.map((s) => (
                <div
                  key={s.session_id}
                  className={`session-item${activeId === s.session_id ? ' active' : ''}`}
                  onClick={() => setActiveId(s.session_id)}
                >
                  <div className="si-top">
                    <span className="si-title">{s.title ?? '未命名会话'}</span>
                    <span className="si-actions">
                      <button type="button" className="icon-btn sm" title="重命名" onClick={(e) => { e.stopPropagation(); void handleRename(s) }}>✎</button>
                      <button type="button" className="icon-btn sm" title="删除" onClick={(e) => { e.stopPropagation(); void handleDelete(s) }}>✕</button>
                    </span>
                  </div>
                  <div className="si-preview">{s.last_message ?? '（暂无消息）'}</div>
                  <div className="si-time mono">{formatBeijing(s.updated_at)}</div>
                </div>
              ))}
            </div>
          </div>

          {/* 能力边界说明（决策：诚实边界）—— 三行文案自原型逐字搬运 */}
          <div className="card honest-limits">
            <div className="card-h"><div className="card-title">能力边界说明 <span className="en">HONEST LIMITS</span></div></div>
            <div className="card-b stack">
              <div className="row hl-row">
                <span className="tag t-success">能</span>
                <span>自然语言描述需求 → 路由到场景与能力；返回结果卡、思维链、报告卡</span>
              </div>
              <div className="row hl-row">
                <span className="tag t-failed">不能</span>
                <span>代替专业参数确认；也不能给出跨任务（同零件多专业）的综合质量分</span>
              </div>
              <div className="row hl-row">
                <span className="tag t-review">降级</span>
                <span>大模型不可用时输入框置灰并提示改走专业工作台，不静默失败</span>
              </div>
            </div>
          </div>
        </aside>
      </div>
    </>
  )
}
