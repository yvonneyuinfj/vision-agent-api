import { describe, expect, it } from 'vitest'
import { describeActivity } from './activity'

describe('describeActivity（活动流语义映射，优化轮 D1）', () => {
  it('普通节点 ok：只给标签、无冗余 detail', () => {
    const d = describeActivity('grading', 'ok')
    expect(d.label).toBe('判级')
    expect(d.detail).toBe('')
    expect(d.tone).toBe('ok')
  })

  it('错误消息：标记 error 色调', () => {
    const d = describeActivity('invoke_capability', 'business error 4001 missing tool')
    expect(d.tone).toBe('error')
    const d2 = describeActivity('react_tools', 'error RuntimeError("boom")')
    expect(d2.tone).toBe('error')
  })

  it('工具节点：tool: 前缀解析为「工具 · 名称」并提取场景', () => {
    const d = describeActivity('tool:invoke_capability', "args={'scene': 'borescope'} -> normal")
    expect(d.label).toBe('工具 · 检测能力')
    expect(d.detail).toBe('场景 borescope')
  })

  it('工具节点：提取检索查询词', () => {
    const d = describeActivity('tool:retrieve_knowledge', "args={'query': '疲劳裂纹判级'} -> 命中3条")
    expect(d.label).toBe('工具 · 知识库检索')
    expect(d.detail).toContain('疲劳裂纹判级')
  })

  it('react_agent 轮次：calls 与文本两种形态', () => {
    const calls = describeActivity('react_agent', 'turn 2/10 calls=invoke_capability')
    expect(calls.label).toBe('推理 · 第2轮')
    expect(calls.detail).toContain('invoke_capability')
    const text = describeActivity('react_agent', "turn 3/10 text='我先观察图像'")
    expect(text.detail).toBe('生成回复')
  })

  it('会话路由：保留中文描述', () => {
    const d = describeActivity('session_routing', '场景识别：fracture（置信度 0.95）')
    expect(d.label).toBe('场景路由')
    expect(d.detail).toContain('fracture')
  })

  it('未知节点：回退为节点名，长消息截断', () => {
    const long = 'x'.repeat(200)
    const d = describeActivity('some_future_node', long)
    expect(d.label).toBe('some_future_node')
    expect(d.detail.length).toBeLessThanOrEqual(65)
    expect(d.detail.endsWith('…')).toBe(true)
  })

  it('await_children 唤醒消息不触发 error 色调', () => {
    const d = describeActivity('await_children', '唤醒：2 个子任务落定（fracture:completed/pass）')
    expect(d.tone).toBe('ok')
  })
})
