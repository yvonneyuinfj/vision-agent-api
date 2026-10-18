/** 思考链行内容解析（共识轮：对话内显示模型思考链）。
 *
 * 后端 kind=chain 消息 content 为 JSON：
 * - {"type":"thought","turn":n,"text":...}            思考行
 * - {"type":"tool_call","turn":n,"call_id","name","args",
 *    "done":false}                                     工具调用行（running）
 * - 同上 + {"content","is_error","done":true}          结果原地更新后的完成行
 */

export interface ThoughtPayload {
  type: 'thought'
  turn: number
  text: string
}

export interface ToolCallPayload {
  type: 'tool_call'
  turn: number
  call_id: string
  name: string
  args: Record<string, unknown>
  done: boolean
  content?: string
  is_error?: boolean
  task_id?: string | null
}

export type ChainPayload = ThoughtPayload | ToolCallPayload

export function parseChain(content: string): ChainPayload | null {
  try {
    const p = JSON.parse(content) as Partial<ChainPayload>
    if (p && (p.type === 'thought' || p.type === 'tool_call')) {
      return p as ChainPayload
    }
  } catch { /* 非 JSON：不渲染 */ }
  return null
}

/** 收起态一行摘要：优先场景/查询词等可读键，否则 JSON 截断。 */
export function argsSummary(args: Record<string, unknown> | undefined): string {
  if (!args || Object.keys(args).length === 0) return ''
  const scene = args.scene
  if (typeof scene === 'string' && scene) return `场景 ${scene}`
  const query = args.query
  if (typeof query === 'string' && query) return `查询：${query.length > 48 ? `${query.slice(0, 48)}…` : query}`
  const taskId = args.task_id
  if (typeof taskId === 'string' && taskId) return taskId
  const raw = JSON.stringify(args)
  return raw.length > 64 ? `${raw.slice(0, 64)}…` : raw
}

/** 思考文本的首句（落定摘要）与尾句（运行中摘要，latestLine 语义）。 */
export function firstLine(text: string): string {
  const i = text.indexOf('\n')
  return (i === -1 ? text : text.slice(0, i)).trim()
}

export function latestLine(text: string): string {
  const visible = text.trimEnd()
  const i = visible.lastIndexOf('\n')
  return (i === -1 ? visible : visible.slice(i + 1)).trim()
}
