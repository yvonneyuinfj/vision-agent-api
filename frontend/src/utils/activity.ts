/** 活动流语义映射（优化轮 D1）：把 append_step 的 node/message 翻成人类可读行。
 *
 * 后端步骤消息形态（见 node_utils / react/nodes）：
 * - 普通节点：message = "ok" | "business error ..." | "error ..."
 * - react_agent："turn N/M calls=tool,tool" 或 "turn N/M text='...'"
 * - 工具节点：node = "tool:<name>"，message = "args={...} -> result..."
 * - 会话路由：node = "session_routing"，message 已是中文描述
 * - await_children：message = "唤醒：N 个子任务落定（...）"
 */

export type ActivityTone = 'ok' | 'error'

export interface ActivityDesc {
  label: string
  detail: string
  tone: ActivityTone
}

const NODE_LABELS: Record<string, string> = {
  session_routing: '场景路由',
  assistant_thought: '助手思考',
  bootstrap_agent: '智能体初始化',
  react_agent: '推理决策',
  react_tools: '执行工具',
  exit_gate: '出口安检',
  grading: '判级',
  human_review: '人工复核',
  generate_report: '生成报告',
  explain_error: '错误解释',
  explain_metrics: '指标解释',
  validate: '输入校验',
  scene_confirm: '场景确认',
  select_capability: '选择能力',
  invoke_capability: '调用检测能力',
  normalize_result: '结果标准化',
  rag_retrieval: '知识库检索',
  await_children: '等待子任务',
}

export const TOOL_LABELS: Record<string, string> = {
  invoke_capability: '检测能力',
  retrieve_knowledge: '知识库检索',
  submit_final: '提交最终结论',
  launch_inspection: '派发质检任务',
  get_task_detail: '查询任务详情',
  launch_child_task: '派发子任务',
  get_child_status: '查询子任务状态',
  await_children: '等待子任务',
}

const DETAIL_CAP = 64

function truncate(s: string): string {
  return s.length > DETAIL_CAP ? `${s.slice(0, DETAIL_CAP)}…` : s
}

/** 提取工具消息里可读的一段（优先 args 中的场景/查询词，否则截断原文）。 */
function toolDetail(message: string): string {
  const scene = message.match(/['"]?scene['"]?\s*[:=]\s*['"]([^'"]+)['"]/)
  if (scene) return `场景 ${scene[1]}`
  const query = message.match(/['"]?query['"]?\s*[:=]\s*['"]([^'"]+)['"]/)
  if (query) return `查询：${truncate(query[1])}`
  const arrow = message.indexOf(' -> ')
  const head = arrow >= 0 ? message.slice(0, arrow) : message
  return truncate(head.replace(/^args=/, ''))
}

export function describeActivity(node: string, message: string): ActivityDesc {
  const tone: ActivityTone = /business error|^error|\berror\b/i.test(message) ? 'error' : 'ok'

  if (node.startsWith('tool:')) {
    const tool = node.slice('tool:'.length)
    const label = TOOL_LABELS[tool] ?? tool
    return { label: `工具 · ${label}`, detail: toolDetail(message), tone }
  }

  const label = NODE_LABELS[node] ?? node

  if (node === 'react_agent') {
    const m = message.match(/^turn (\d+)\/(\d+)\s+(.*)$/)
    if (m) {
      const rest = m[3]
      if (rest.startsWith('calls=')) return { label: `推理 · 第${m[1]}轮`, detail: `调用 ${rest.slice('calls='.length)}`, tone }
      return { label: `推理 · 第${m[1]}轮`, detail: '生成回复', tone }
    }
  }

  if (message === 'ok') return { label, detail: '', tone }
  return { label, detail: truncate(message), tone }
}
