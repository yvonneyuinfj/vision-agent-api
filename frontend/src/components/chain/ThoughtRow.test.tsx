import { describe, it, expect } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { ThoughtRow } from './ThoughtRow'

const TEXT = '图像呈荧光网状裂纹。\n先调用检测能力核实。\n若矛盾则复查。'

describe('ThoughtRow（ReasoningRow 语义）', () => {
  it('默认收起，落定摘要 = 思考首句', () => {
    const { container } = render(<ThoughtRow text={TEXT} />)
    expect(screen.getByText('思考')).toBeInTheDocument()
    expect(screen.getByText('图像呈荧光网状裂纹。')).toBeInTheDocument()
    // 全文不外显
    expect(screen.queryByText(TEXT)).toBeNull()
    expect(container.querySelector('[data-open]')).toBeNull()
  })

  it('运行中摘要 = 思考尾句', () => {
    render(<ThoughtRow text={TEXT} running />)
    expect(screen.getByText('若矛盾则复查。')).toBeInTheDocument()
  })

  it('点击展开后显示思考全文，再点收起', () => {
    const { container } = render(<ThoughtRow text={TEXT} />)
    fireEvent.click(screen.getByText('思考'))
    expect(container.querySelector('.cr-think-body')?.textContent).toBe(TEXT)
    fireEvent.click(screen.getByText('思考'))
    expect(container.querySelector('.cr-think-body')).toBeNull()
  })

  it('键盘可展开（Enter）', () => {
    const { container } = render(<ThoughtRow text={TEXT} />)
    const head = screen.getByRole('button')
    fireEvent.keyDown(head, { key: 'Enter' })
    expect(container.querySelector('.cr-think-body')?.textContent).toBe(TEXT)
  })
})
