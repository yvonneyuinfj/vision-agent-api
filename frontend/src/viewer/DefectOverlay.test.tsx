import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, cleanup } from '@testing-library/react'
import DefectOverlay from './DefectOverlay'
import type { DefectItem } from '../api/types'

// Konva 图元 props 捕获为 DOM 属性（jsdom 无 canvas，与 Viewer.test 同策略）
vi.mock('react-konva', () => {
  const Group = ({ children }: { children?: React.ReactNode }) => <>{children}</>
  const Rect = (p: Record<string, unknown>) => (
    <div
      data-testid="konva-rect"
      data-x={String(p.x)}
      data-y={String(p.y)}
      data-width={String(p.width)}
      data-height={String(p.height)}
    />
  )
  const Text = (p: Record<string, unknown>) => <div data-testid="konva-text">{String(p.text)}</div>
  return { Group, Rect, Text }
})

function defect(over: Partial<DefectItem> = {}): DefectItem {
  return {
    defect_id: 'D001',
    defect_type: 'BP',
    defect_name_cn: '模糊的真缺陷',
    confidence: 0.672,
    severity: null,
    location: null,
    description: '',
    ...over,
  }
}

afterEach(cleanup)

describe('DefectOverlay bbox 兼容', () => {
  it('契约形态 {type:bbox, bbox} 绘制：坐标换算为 x,y,width,height', () => {
    render(
      <DefectOverlay defects={[defect({ location: { type: 'bbox', bbox: [464, 43, 774, 558] } })]} onSelect={() => {}} />,
    )
    const rect = screen.getByTestId('konva-rect')
    expect(rect).toHaveAttribute('data-x', '464')
    expect(rect).toHaveAttribute('data-y', '43')
    expect(rect).toHaveAttribute('data-width', '310')
    expect(rect).toHaveAttribute('data-height', '515')
  })

  it('存量 FPI 数据（仅 bbox_xyxy、无 type）也绘制——「标注图==原图」回归', () => {
    render(
      <DefectOverlay
        defects={[defect({ location: { bbox_xyxy: [1311.2, 2742.2, 1665.7, 2838.8] } })]}
        onSelect={() => {}}
      />,
    )
    const rect = screen.getByTestId('konva-rect')
    expect(rect).toHaveAttribute('data-x', '1311.2')
    expect(rect).toHaveAttribute('data-width', '354.5')
  })

  it('非 bbox 类型与缺坐标条目不绘框（不臆造脏框）', () => {
    render(
      <DefectOverlay
        defects={[defect({ defect_id: 'a', location: { type: 'mask' } }), defect({ defect_id: 'b' })]}
        onSelect={() => {}}
      />,
    )
    expect(screen.queryByTestId('konva-rect')).not.toBeInTheDocument()
  })

  it('标签文本包含类型与置信度', () => {
    render(
      <DefectOverlay defects={[defect({ location: { type: 'bbox', bbox: [0, 0, 10, 10] } })]} onSelect={() => {}} />,
    )
    expect(screen.getByTestId('konva-text')).toHaveTextContent('模糊的真缺陷 0.67')
  })
})
