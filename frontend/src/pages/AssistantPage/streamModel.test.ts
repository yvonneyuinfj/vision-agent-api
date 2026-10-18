import { describe, it, expect } from 'vitest'
import { applyFrame, emptyStreamState, renderItems, toolPayload, type StreamState } from './streamModel'

function feed(state: StreamState, frames: any[]): StreamState {
  return frames.reduce((s, f) => applyFrame(s, f), state)
}

describe('streamModel（类型分派 + 逐字增量渲染模型）', () => {
  it('message：start → delta×N → end 逐字累积，end 落定全文', () => {
    const s = feed(emptyStreamState, [
      { type: 'message', message_id: 'M1', phase: 'start', message: { role: 'assistant', kind: 'text' } },
      { type: 'message', message_id: 'M1', phase: 'delta', delta: '你好' },
      { type: 'message', message_id: 'M1', phase: 'delta', delta: '，世界' },
      { type: 'message', message_id: 'M1', phase: 'end', message: { role: 'assistant', kind: 'text', content: '你好，世界' } },
    ])
    const items = renderItems(s)
    expect(items).toHaveLength(1)
    const it = items[0]
    expect(it.kind).toBe('message')
    if (it.kind !== 'message') return
    expect(it.content).toBe('你好，世界')
    expect(it.streaming).toBe(false)

    // 中间态：delta 累积且 streaming
    const mid = feed(emptyStreamState, [
      { type: 'message', message_id: 'M1', phase: 'start', message: { role: 'assistant', kind: 'text' } },
      { type: 'message', message_id: 'M1', phase: 'delta', delta: '你好' },
    ])
    const m = renderItems(mid)[0]
    if (m.kind === 'message') {
      expect(m.content).toBe('你好')
      expect(m.streaming).toBe(true)
    }
  })

  it('message：历史整帧（仅 end）直接落定，不残留 streaming', () => {
    const s = feed(emptyStreamState, [
      { type: 'message', message_id: 'M1', phase: 'end', message: { role: 'user', kind: 'text', content: '查一下', images: [{ uri: '/i/1', name: 'a.jpg' }] } },
    ])
    const it = renderItems(s)[0]
    if (it.kind !== 'message') throw new Error('expected message')
    expect(it.role).toBe('user')
    expect(it.content).toBe('查一下')
    expect(it.images).toHaveLength(1)
    expect(it.streaming).toBe(false)
  })

  it('thinking：逐字流式并落定', () => {
    const s = feed(emptyStreamState, [
      { type: 'thinking', message_id: 'C1', task_id: 'T1', turn: 1, phase: 'start' },
      { type: 'thinking', message_id: 'C1', phase: 'delta', delta: '先拆解' },
      { type: 'thinking', message_id: 'C1', phase: 'delta', delta: '场景。' },
      { type: 'thinking', message_id: 'C1', phase: 'end', text: '先拆解场景。' },
    ])
    const it = renderItems(s)[0]
    if (it.kind !== 'thinking') throw new Error('expected thinking')
    expect(it.text).toBe('先拆解场景。')
    expect(it.streaming).toBe(false)
    expect(it.taskId).toBe('T1')
  })

  it('chain：tool_call 落 running 行，tool_result 原地更新同一行', () => {
    const s = feed(emptyStreamState, [
      { type: 'chain', event: 'tool_call', message_id: 'MC1', task_id: 'T1', name: 'launch_child_task', args: { scene: 'borescope' } },
    ])
    let it = renderItems(s)[0]
    if (it.kind !== 'tool') throw new Error('expected tool')
    expect(it.running).toBe(true)
    expect(it.name).toBe('launch_child_task')

    const s2 = feed(s, [
      { type: 'chain', event: 'tool_result', message_id: 'MC1', name: 'launch_child_task', content: '已派发', is_error: false },
    ])
    // 原地更新：仍是 1 行
    expect(renderItems(s2)).toHaveLength(1)
    it = renderItems(s2)[0]
    if (it.kind !== 'tool') throw new Error('expected tool')
    expect(it.running).toBe(false)
    expect(it.content).toBe('已派发')
    expect(it.isError).toBe(false)

    const payload = toolPayload(it)
    expect(payload.done).toBe(true)
    expect(payload.content).toBe('已派发')
  })

  it('card：四种生命周期卡各自落一张新卡（只推不改）', () => {
    const s = feed(emptyStreamState, [
      { type: 'card', card_type: 'task_card', message_id: 'K1', task_id: 'T1', payload: { status: 'running', scene: 'borescope' } },
      { type: 'card', card_type: 'review_card', message_id: 'K2', task_id: 'T1', payload: { grade_level: 'B' } },
      { type: 'card', card_type: 'done_card', message_id: 'K3', task_id: 'T1', payload: { status: 'completed', report: { status: 'ready' } } },
      { type: 'card', card_type: 'report_card', message_id: 'K4', task_id: 'T1', payload: { report: { status: 'ready', uri: '/r/T1' } } },
    ])
    const items = renderItems(s)
    expect(items).toHaveLength(4)
    expect(items.map((i) => (i.kind === 'card' ? i.cardType : ''))).toEqual([
      'task_card', 'review_card', 'done_card', 'report_card',
    ])
    // 同一 message_id 重放（断线重连）幂等：不新增
    const s2 = feed(s, [
      { type: 'card', card_type: 'task_card', message_id: 'K1', task_id: 'T1', payload: { status: 'running', scene: 'borescope' } },
    ])
    expect(renderItems(s2)).toHaveLength(4)
  })

  it('顺序保持插入序：user → routing → thinking → tool → card → assistant', () => {
    const s = feed(emptyStreamState, [
      { type: 'message', message_id: 'M1', phase: 'end', message: { role: 'user', kind: 'image', content: '帮我看看' } },
      { type: 'message', message_id: 'M2', phase: 'end', message: { role: 'event', kind: 'routing', content: '{"message":"borescope"}' } },
      { type: 'thinking', message_id: 'C1', phase: 'end', text: '先拆解场景。' },
      { type: 'chain', event: 'tool_call', message_id: 'MC1', name: 'launch_inspection', args: {} },
      { type: 'card', card_type: 'task_card', message_id: 'K1', task_id: 'T1', payload: {} },
      { type: 'message', message_id: 'M3', phase: 'end', message: { role: 'assistant', kind: 'text', content: '已派发' } },
    ])
    const items = renderItems(s)
    expect(items.map((i) => i.id)).toEqual(['M1', 'M2', 'C1', 'MC1', 'K1', 'M3'])
  })

  it('未知/非法帧安全忽略（不崩溃、不落项）', () => {
    const s = feed(emptyStreamState, [
      { type: 'error', message: 'boom' } as any,
      { type: 'weird' } as any,
    ])
    expect(renderItems(s)).toHaveLength(0)
  })

  it('同 id 变形：直播气泡在工具轮收尾被 thinking end 替换为思考行', () => {
    // 会话智能体直播协议：文本先按 message 帧推（气泡逐字生长），
    // 回合若以工具调用收尾 → 同 message_id 的 thinking end 帧整体替换为思考行
    const s = feed(emptyStreamState, [
      { type: 'message', message_id: 'MX', phase: 'start', message: { role: 'assistant', kind: 'text' } },
      { type: 'message', message_id: 'MX', phase: 'delta', delta: '置信度足够，' },
      { type: 'message', message_id: 'MX', phase: 'delta', delta: '直接派发。' },
      { type: 'thinking', message_id: 'MX', turn: 1, phase: 'end', text: '置信度足够，直接派发。' },
    ])
    const items = renderItems(s)
    expect(items).toHaveLength(1)
    const it = items[0]
    expect(it.kind).toBe('thinking')
    if (it.kind === 'thinking') {
      expect(it.text).toBe('置信度足够，直接派发。')
      expect(it.streaming).toBe(false)
    }
  })

  it('同 id 反向变形：thinking 行被 message end 提升为助手气泡', () => {
    const s = feed(emptyStreamState, [
      { type: 'thinking', message_id: 'MY', phase: 'start' },
      { type: 'thinking', message_id: 'MY', phase: 'delta', delta: '分析中' },
      { type: 'message', message_id: 'MY', phase: 'end', message: { role: 'assistant', kind: 'text', content: '分析中，已完成。' } },
    ])
    const items = renderItems(s)
    expect(items).toHaveLength(1)
    const it = items[0]
    expect(it.kind).toBe('message')
    if (it.kind === 'message') {
      expect(it.content).toBe('分析中，已完成。')
      expect(it.streaming).toBe(false)
    }
  })

  it('直播帧与历史回放同 id 幂等：重放整帧覆盖已流式渲染的项，不新增', () => {
    const live = feed(emptyStreamState, [
      { type: 'message', message_id: 'MZ', phase: 'start', message: { role: 'assistant', kind: 'text' } },
      { type: 'message', message_id: 'MZ', phase: 'delta', delta: '你好' },
    ])
    // 断线重连：历史回放整帧（同 id）→ 覆盖为落定态，仍只有一项
    const s = feed(live, [
      { type: 'message', message_id: 'MZ', phase: 'end', message: { role: 'assistant', kind: 'text', content: '你好，世界' } },
    ])
    const items = renderItems(s)
    expect(items).toHaveLength(1)
    const it = items[0]
    if (it.kind === 'message') {
      expect(it.content).toBe('你好，世界')
      expect(it.streaming).toBe(false)
    }
  })
})
