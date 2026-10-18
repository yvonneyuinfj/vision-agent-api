import { describe, it, expect } from 'vitest'
import { parseChain, argsSummary, firstLine, latestLine } from './chain'

describe('parseChain', () => {
  it('解析 thought 行', () => {
    const p = parseChain(JSON.stringify({ type: 'thought', turn: 1, text: '先看图' }))
    expect(p?.type).toBe('thought')
    expect((p as any).text).toBe('先看图')
  })

  it('解析 tool_call 行（running）', () => {
    const p = parseChain(JSON.stringify({
      type: 'tool_call', turn: 1, call_id: 'c1', name: 'invoke_capability',
      args: { scene: 'fpi' }, done: false,
    }))
    expect(p?.type).toBe('tool_call')
    expect((p as any).done).toBe(false)
  })

  it('非 JSON / 未知类型返回 null（不渲染）', () => {
    expect(parseChain('不是 json')).toBeNull()
    expect(parseChain(JSON.stringify({ type: 'bogus' }))).toBeNull()
  })
})

describe('argsSummary', () => {
  it('优先场景键', () => {
    expect(argsSummary({ scene: 'fpi' })).toBe('场景 fpi')
  })
  it('查询词', () => {
    expect(argsSummary({ query: '疲劳裂纹判级' })).toBe('查询：疲劳裂纹判级')
  })
  it('空参数为空串', () => {
    expect(argsSummary({})).toBe('')
    expect(argsSummary(undefined)).toBe('')
  })
  it('其余键回退 JSON 截断', () => {
    const s = argsSummary({ a: 1 })
    expect(s).toContain('"a":1')
  })
})

describe('firstLine / latestLine', () => {
  const text = '第一行思考。\n第二行推理。\n最后一行结论。'
  it('首句（落定摘要）', () => {
    expect(firstLine(text)).toBe('第一行思考。')
  })
  it('尾句（运行中摘要，latestLine 语义）', () => {
    expect(latestLine(text)).toBe('最后一行结论。')
  })
  it('单行文本首尾相同', () => {
    expect(firstLine('只有一行')).toBe('只有一行')
    expect(latestLine('只有一行')).toBe('只有一行')
  })
})
