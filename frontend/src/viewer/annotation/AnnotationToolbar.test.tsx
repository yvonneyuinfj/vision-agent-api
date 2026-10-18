import { beforeEach, describe, expect, it } from 'vitest'
import { act, fireEvent, render, screen } from '@testing-library/react'
import AnnotationToolbar from './AnnotationToolbar'
import { useAnnotationStore } from './useAnnotationStore'
import type { AnnotationShape } from './annotationTypes'

function makeShape(overrides: Partial<AnnotationShape> = {}): AnnotationShape {
  return {
    id: 's1', imageKey: 'img-1', tool: 'line',
    start: { x: 0, y: 0 }, end: { x: 50, y: 50 },
    color: '#f0756b', strokeWidth: 4, dashed: false,
    ...overrides,
  }
}

const reset = () => useAnnotationStore.setState({
  shapesByImage: {}, undoStacks: {}, redoStacks: {}, badgeSeqs: {},
  tool: null, selectedId: null, textDraft: null, pickSample: null, pickedColor: null,
  panelOpen: false,
  style: { color: '#f0756b', strokeWidth: 4, dashed: false },
})

beforeEach(reset)

const TOOL_LABELS = ['矩形', '椭圆', '直线', '箭头', '铅笔', '马克笔', '文本', '序号', '马赛克', '模糊', '橡皮擦', '取色 (C)']

describe('AnnotationToolbar（图标化）', () => {
  it('12 个工具按钮每个都以图标呈现（按钮内含 svg，用 aria-label 承载名称）', () => {
    render(<AnnotationToolbar imageKey="img-1" />)
    for (const label of TOOL_LABELS) {
      const btn = screen.getByRole('button', { name: label })
      expect(btn.querySelector('svg')).toBeTruthy()
    }
  })

  it('点击工具激活，再次点击退出（回到浏览模式）', () => {
    render(<AnnotationToolbar imageKey="img-1" />)
    fireEvent.click(screen.getByRole('button', { name: '矩形' }))
    expect(useAnnotationStore.getState().tool).toBe('rect')
    fireEvent.click(screen.getByRole('button', { name: '矩形' }))
    expect(useAnnotationStore.getState().tool).toBeNull()
  })

  it('虚实线切换按钮为图标，点击翻转 dashed 样式', () => {
    render(<AnnotationToolbar imageKey="img-1" />)
    const toggle = screen.getByRole('button', { name: /实线|虚线/ })
    expect(toggle.querySelector('svg')).toBeTruthy()
    fireEvent.click(toggle)
    expect(useAnnotationStore.getState().style.dashed).toBe(true)
    fireEvent.click(screen.getByRole('button', { name: /实线|虚线/ }))
    expect(useAnnotationStore.getState().style.dashed).toBe(false)
  })

  it('自定义线宽与 hex 输入框保留', () => {
    render(<AnnotationToolbar imageKey="img-1" />)
    expect(screen.getByLabelText('annotation-width-input')).toBeTruthy()
    expect(screen.getByLabelText('annotation-hex-input')).toBeTruthy()
  })

  it('无历史时撤销/重做禁用，提交后撤销可用并真正回退', () => {
    render(<AnnotationToolbar imageKey="img-1" />)
    expect(screen.getByRole('button', { name: '撤销' }).hasAttribute('disabled')).toBe(true)
    expect(screen.getByRole('button', { name: '重做' }).hasAttribute('disabled')).toBe(true)
    act(() => {
      useAnnotationStore.getState().commitShape('img-1', makeShape())
    })
    expect(screen.getByRole('button', { name: '撤销' }).hasAttribute('disabled')).toBe(false)
    fireEvent.click(screen.getByRole('button', { name: '撤销' }))
    expect(useAnnotationStore.getState().shapesByImage['img-1'] ?? []).toHaveLength(0)
  })

  it('「新分组」为图标按钮，点击后下一个序号回到 1', () => {
    render(<AnnotationToolbar imageKey="img-1" />)
    expect(useAnnotationStore.getState().consumeBadgeSeq('img-1')).toBe(1)
    expect(useAnnotationStore.getState().consumeBadgeSeq('img-1')).toBe(2)
    const groupBtn = screen.getByRole('button', { name: /新分组/ })
    expect(groupBtn.querySelector('svg')).toBeTruthy()
    fireEvent.click(groupBtn)
    expect(useAnnotationStore.getState().consumeBadgeSeq('img-1')).toBe(1)
  })
})