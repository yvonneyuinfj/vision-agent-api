import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import Md from './Md'

const KNOWLEDGE: Record<string, { content: string; score: number; doc_name: string; chunk_id: string | null; image_uri: string | null }> = {
  c1: { content: '片段', score: 0.9, doc_name: 'd.pdf', chunk_id: 'c1', image_uri: null },
}

describe('Md', () => {
  it('渲染 markdown 基础语法', () => {
    const { container } = render(<Md citations={{}}>{'# 标题\n\n**加粗**文本'}</Md>)
    expect(container.querySelector('h1')).toHaveTextContent('标题')
    expect(container.querySelector('strong')).toHaveTextContent('加粗')
  })

  it('ref:// 链接渲染为引用标记并可点击', () => {
    const onCitationClick = vi.fn()
    render(
      <Md citations={KNOWLEDGE} onCitationClick={onCitationClick}>
        {'判级依据见 [标准 A](ref://c1)'}
      </Md>,
    )
    // a 内还含 📎 后缀文本，需用正则匹配（brief 原文 getByText('标准 A') 会因 textContent 拼接失败）
    fireEvent.click(screen.getByText(/标准 A/))
    expect(onCitationClick).toHaveBeenCalledWith('c1')
  })

  it('非 ref:// 链接保持普通跳转行为', () => {
    const { container } = render(<Md citations={{}}>{'[外部](https://example.com)'}</Md>)
    const a = container.querySelector('a')
    expect(a?.getAttribute('href')).toBe('https://example.com')
  })

  it('行内数学公式 $...$ 渲染为 KaTeX 排版', () => {
    const { container } = render(<Md citations={{}}>{'温度区间 $60^{#}$ 与 $120^{#}$'}</Md>)
    expect(container.querySelectorAll('.katex').length).toBe(2)
    expect(container.textContent).toContain('60')
  })

  it('已转义的 \\# 公式不重复转义且正常渲染', () => {
    // JS 字面量 $60^{\#}$：单反斜杠 + #，表示文档已写好转义的情况
    const { container } = render(<Md citations={{}}>{'$60^{\\#}$'}</Md>)
    expect(container.querySelectorAll('.katex').length).toBe(1)
    expect(container.querySelector('.katex-error')).not.toBeInTheDocument()
  })

  it('原始 HTML 标签 <sub>/<sup> 渲染为上下标', () => {
    const { container } = render(<Md citations={{}}>{'1000<sup>#</sup> 与 CO<sub>2</sub>'}</Md>)
    expect(container.querySelector('sup')).toHaveTextContent('#')
    expect(container.querySelector('sub')).toHaveTextContent('2')
  })
})