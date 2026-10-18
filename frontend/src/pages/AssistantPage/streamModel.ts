/** 会话流式协议（对齐 design-demos/思维链_玻璃疏行演绎.html）。
 *
 * 顶层 `type` 决定渲染器：
 * - `message`  文本消息（助手文本/引导逐字 start→delta→end；用户/事件/路由整帧）
 * - `thinking` 思考行（逐字流式）
 * - `chain`    工具调用行（tool_call running → tool_result 原地更新，唯一允许的原地更新）
 * - `card`     生命周期卡片（task/review/done/report/scene，只推不改）
 *
 * 本模块是纯函数渲染模型：把帧序列折叠成有序渲染项列表，供 AssistantPage
 * 消费。历史回放与实时增量走同一入口（按 message_id 幂等归并），断线重连
 * 由调用方在 SSE onopen 时清空重建。
 */
import type { ToolCallPayload } from '../../components/chain/chain'

export interface ImageRef { uri: string; name?: string }

// ---------- 帧 ----------
export type Phase = 'start' | 'delta' | 'end'

export interface MessageFrame {
  type: 'message'
  message_id: string
  phase: Phase
  delta?: string
  message?: { role: string; kind: string; content?: string; images?: ImageRef[] }
  task_id?: string | null
}
export interface ThinkingFrame {
  type: 'thinking'
  message_id: string
  task_id?: string | null
  turn?: number
  phase: Phase
  delta?: string
  text?: string
}
export interface ChainFrame {
  type: 'chain'
  event: 'tool_call' | 'tool_result'
  message_id: string
  task_id?: string | null
  turn?: number
  call_id?: string
  name?: string
  args?: Record<string, unknown>
  done?: boolean
  content?: string
  is_error?: boolean
}
export type CardType = 'task_card' | 'review_card' | 'done_card' | 'report_card' | 'scene_card'
export interface CardFrame {
  type: 'card'
  card_type: CardType
  message_id: string
  task_id?: string | null
  payload: Record<string, any>
}
export type StreamFrame = MessageFrame | ThinkingFrame | ChainFrame | CardFrame

// ---------- 渲染项 ----------
export type RenderItem =
  | {
      kind: 'message'
      id: string
      role: string
      mkind: string
      content: string
      images: ImageRef[]
      streaming: boolean
      taskId: string | null
    }
  | {
      kind: 'thinking'
      id: string
      text: string
      taskId: string | null
      turn: number | null
      streaming: boolean
    }
  | {
      kind: 'tool'
      id: string
      name: string
      args: Record<string, unknown>
      content: string | undefined
      isError: boolean
      running: boolean
      taskId: string | null
    }
  | {
      kind: 'card'
      id: string
      cardType: CardType
      payload: Record<string, any>
      taskId: string | null
    }

export interface StreamState {
  order: string[]
  items: Record<string, RenderItem>
}

export const emptyStreamState: StreamState = { order: [], items: {} }

function ensureOrder(order: string[], id: string): string[] {
  return order.includes(id) ? order : [...order, id]
}

/** 把单帧折叠进渲染状态（按 message_id 幂等；delta 逐字追加）。
 * 未知类型 / 缺 message_id 的帧（如 error 帧）安全忽略。 */
export function applyFrame(state: StreamState, frame: StreamFrame): StreamState {
  const known =
    frame &&
    (frame.type === 'message' || frame.type === 'thinking' || frame.type === 'chain' || frame.type === 'card')
  if (!known || !frame.message_id) return state
  const id = frame.message_id
  const items = { ...state.items }
  let order = state.order

  if (frame.type === 'message') {
    const prev = items[id]
    const base =
      prev && prev.kind === 'message'
        ? prev
        : {
            kind: 'message' as const,
            id,
            role: frame.message?.role ?? 'assistant',
            mkind: frame.message?.kind ?? 'text',
            content: '',
            images: [] as ImageRef[],
            streaming: false,
            taskId: frame.task_id ?? null,
          }
    if (frame.phase === 'start') {
      items[id] = { ...base, content: '', streaming: true, role: frame.message?.role ?? base.role, mkind: frame.message?.kind ?? base.mkind }
    } else if (frame.phase === 'delta') {
      items[id] = { ...base, content: base.content + (frame.delta ?? ''), streaming: true }
    } else {
      // end / 历史整帧
      items[id] = {
        ...base,
        content: frame.message?.content ?? base.content,
        images: frame.message?.images ?? base.images,
        role: frame.message?.role ?? base.role,
        mkind: frame.message?.kind ?? base.mkind,
        streaming: false,
      }
    }
    order = ensureOrder(order, id)
    return { order, items }
  }

  if (frame.type === 'thinking') {
    const prev = items[id]
    const base =
      prev && prev.kind === 'thinking'
        ? prev
        : { kind: 'thinking' as const, id, text: '', taskId: frame.task_id ?? null, turn: frame.turn ?? null, streaming: false }
    if (frame.phase === 'start') {
      items[id] = { ...base, text: '', streaming: true, taskId: frame.task_id ?? base.taskId, turn: frame.turn ?? base.turn }
    } else if (frame.phase === 'delta') {
      items[id] = { ...base, text: base.text + (frame.delta ?? ''), streaming: true }
    } else {
      items[id] = { ...base, text: frame.text ?? base.text, streaming: false }
    }
    order = ensureOrder(order, id)
    return { order, items }
  }

  if (frame.type === 'chain') {
    const prev = items[id]
    if (frame.event === 'tool_call') {
      items[id] = {
        kind: 'tool',
        id,
        name: frame.name ?? '',
        args: frame.args ?? {},
        content: undefined,
        isError: false,
        running: true,
        taskId: frame.task_id ?? (prev && prev.kind === 'tool' ? prev.taskId : null),
      }
    } else {
      // tool_result：原地更新同一行（协议唯一允许的原地更新）
      const base =
        prev && prev.kind === 'tool'
          ? prev
          : {
              kind: 'tool' as const,
              id,
              name: frame.name ?? '',
              args: frame.args ?? {},
              content: undefined,
              isError: false,
              running: true,
              taskId: frame.task_id ?? null,
            }
      items[id] = {
        ...base,
        name: frame.name ?? base.name,
        args: frame.args ?? base.args,
        content: frame.content,
        isError: Boolean(frame.is_error),
        running: false,
        taskId: frame.task_id ?? base.taskId,
      }
    }
    order = ensureOrder(order, id)
    return { order, items }
  }

  // card：只推不改，落一张新卡
  items[id] = {
    kind: 'card',
    id,
    cardType: frame.card_type,
    payload: frame.payload ?? {},
    taskId: frame.task_id ?? null,
  }
  order = ensureOrder(order, id)
  return { order, items }
}

/** 有序渲染项列表（渲染层消费）。 */
export function renderItems(state: StreamState): RenderItem[] {
  return state.order.map((id) => state.items[id]).filter(Boolean)
}

/** 思考链工具行的标准载荷（供 ToolCallRow 复用）。 */
export function toolPayload(item: Extract<RenderItem, { kind: 'tool' }>): ToolCallPayload {
  return {
    type: 'tool_call',
    turn: 0,
    call_id: item.id,
    name: item.name,
    args: item.args,
    done: !item.running,
    content: item.content,
    is_error: item.isError,
    task_id: item.taskId,
  }
}
