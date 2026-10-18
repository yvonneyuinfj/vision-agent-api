import { describe, it, expect } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { ToolCallRow } from './ToolCallRow'
import type { ToolCallPayload } from './chain'

function data(over: Partial<ToolCallPayload> = {}): ToolCallPayload {
  return {
    type: 'tool_call', turn: 1, call_id: 'c1', name: 'invoke_capability',
    args: { scene: 'fpi' }, done: false, ...over,
  }
}

describe('ToolCallRow（ToolRow 语义）', () => {
  it('收起态 = 工具标题 + 参数摘要', () => {
    render(<ToolCallRow data={data()} state="running" />)
    expect(screen.getByText('检测能力')).toBeInTheDocument() // TOOL_LABELS
    expect(screen.getByText('场景 fpi')).toBeInTheDocument()
  })

  it('running 态带扫光与读屏文案', () => {
    const { container } = render(<ToolCallRow data={data()} state="running" />)
    expect(container.querySelector('[data-state="running"]')).toBeInTheDocument()
    expect(screen.getByText('执行中')).toBeInTheDocument()
  })

  it('结果原地更新后（done）显示完成态', () => {
    const { container } = render(
      <ToolCallRow data={data({ done: true, content: '检出 2 项缺陷' })} state="ok" />,
    )
    expect(container.querySelector('[data-state="ok"]')).toBeInTheDocument()
    expect(screen.queryByText('执行中')).toBeNull()
  })

  it('错误行摘要 = 失败首行（红）', () => {
    render(<ToolCallRow data={data({ done: true, content: '工具执行异常：boom\n详情' })} state="error" />)
    expect(screen.getByText('工具执行异常：boom')).toBeInTheDocument()
  })

  it('展开后显示 IN/OUT 分节，再点收起', () => {
    render(<ToolCallRow data={data({ done: true, content: '检出 2 项' })} state="ok" />)
    fireEvent.click(screen.getByText('检测能力'))
    expect(screen.getByText('IN')).toBeInTheDocument()
    expect(screen.getByText('OUT')).toBeInTheDocument()
    expect(screen.getByText('检出 2 项')).toBeInTheDocument()
    expect(screen.getByText(/"scene": "fpi"/)).toBeInTheDocument()
    fireEvent.click(screen.getByText('检测能力'))
    expect(screen.queryByText('OUT')).toBeNull()
  })

  it('中断态（任务落定仍未完成）= stopped', () => {
    const { container } = render(<ToolCallRow data={data()} state="stopped" />)
    expect(container.querySelector('[data-state="stopped"]')).toBeInTheDocument()
  })
})
