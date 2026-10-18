/** ReAct 思考链面板（仅 mode=agent 任务）：检查点完整轨迹归一化展示。
 *
 * 条目：任务简报 → 逐轮思考/工具调用/工具结果。数据来源 /tasks/{id}/trace。
 */
import { useEffect, useState } from 'react'
import { fetchTaskTrace } from '../../api/tasks'
import type { TaskTrace, TraceEntry } from '../../api/types'

const TOOL_LABELS: Record<string, string> = {
  invoke_capability: '调用检测能力',
  retrieve_knowledge: '检索知识库',
  submit_final: '提交最终结论',
}

function EntryView({ entry }: { entry: TraceEntry }) {
  switch (entry.type) {
    case 'briefing':
      return (
        <div className="tc-entry tc-briefing">
          <div className="tc-label">任务简报</div>
          <div className="tc-text">{entry.text}</div>
        </div>
      )
    case 'thought':
      return (
        <div className="tc-entry tc-thought">
          <div className="tc-label">
            思考 <span className="mono">· 第 {entry.turn} 轮</span>
          </div>
          <div className="tc-text">{entry.text}</div>
        </div>
      )
    case 'tool_call':
      return (
        <div className="tc-entry tc-call">
          <div className="tc-label">
            工具调用 <span className="mono">· 第 {entry.turn} 轮</span>
          </div>
          <div className="tc-tool">
            <span className="tag t-accent">{TOOL_LABELS[entry.name] ?? entry.name}</span>
            <span className="mono tc-args">
              {Object.entries(entry.args).map(([k, v]) => `${k}=${v}`).join('  ') || '—'}
            </span>
          </div>
        </div>
      )
    case 'tool_result':
      return (
        <div className="tc-entry tc-result">
          <div className="tc-label">工具结果{entry.name ? <span className="mono"> · {entry.name}</span> : null}</div>
          <pre className="tc-pre" title={entry.content}>{entry.content}</pre>
        </div>
      )
    default:
      return null
  }
}

export default function ThoughtChainPanel({ taskId, running = false }: { taskId: string; running?: boolean }) {
  const [trace, setTrace] = useState<TaskTrace | null>(null)
  const [error, setError] = useState<string | null>(null)

  // 优化轮 E3：任务运行中 3s 轮询追踪思考链；落定后由父级传 running=false 停轮询
  useEffect(() => {
    let cancelled = false
    let timer: number | null = null

    const load = () => {
      fetchTaskTrace(taskId)
        .then((t) => { if (!cancelled) { setTrace(t); setError(null) } })
        .catch((e) => { if (!cancelled) setError(e instanceof Error ? e.message : '加载失败') })
    }
    load()
    if (running) timer = window.setInterval(load, 3000)
    return () => { cancelled = true; if (timer !== null) window.clearInterval(timer) }
  }, [taskId, running])

  if (error) {
    return (
      <section className="card">
        <div className="card-h">
          <div className="card-title">ReAct 思考链 <span className="en">AGENT TRACE</span></div>
        </div>
        <div className="card-b"><p className="faint">思考链加载失败：{error}</p></div>
      </section>
    )
  }

  if (!trace || trace.entries.length === 0) {
    return null
  }

  return (
    <section className="card">
      <div className="card-h">
        <div className="card-title">ReAct 思考链 <span className="en">AGENT TRACE</span></div>
        <span className="hint mono">
          {running && <span className="tc-live">● 实时跟随 </span>}
          循环 {trace.loop_count ?? '—'} 轮 · 能力调用 {trace.capability_calls ?? '—'} 次
          {trace.submitted ? ' · 已提交' : ''}
        </span>
      </div>
      <div className="card-b tc-body">
        {trace.entries.map((entry, i) => (
          <EntryView key={i} entry={entry} />
        ))}
      </div>
    </section>
  )
}
