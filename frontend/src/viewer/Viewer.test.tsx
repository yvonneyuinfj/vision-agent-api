import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, fireEvent, screen, act } from '@testing-library/react'
import Viewer from './Viewer'
import { useViewerStore } from '../store/useViewerStore'
import { useDefectEditStore } from '../store/useDefectEditStore'
import type { ViewerImage } from './Viewer'

/* jsdom 无 canvas 2d：react-konva 组件用透传容器替代；
   缺陷/标注浮层与 FilmStrip 契约无关，stub 掉避免 Konva 依赖蔓延 */
vi.mock('react-konva', () => {
  const passthrough = ({ children }: { children?: React.ReactNode }) => children ?? null
  return { Stage: passthrough, Layer: passthrough, Image: passthrough }
})
vi.mock('./DefectOverlay', () => ({ default: () => null }))
vi.mock('./DefectEditLayer', () => ({
  default: (props: { defects: unknown[]; addMode: boolean }) => (
    <div data-testid="defect-edit-layer" data-add={String(props.addMode)}>{props.defects.length}</div>
  ),
}))
vi.mock('./annotation/AnnotationLayer', () => ({ default: () => null }))
vi.mock('./annotation/AnnotationPanel', () => ({ default: () => null }))
vi.mock('./annotation/TextInputOverlay', () => ({ default: () => null }))
vi.mock('./annotation/ColorPickPanel', () => ({ default: () => null }))

/* Viewer 内 ResizeObserver 监听容器尺寸，jsdom 未实现 */
class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
vi.stubGlobal('ResizeObserver', ResizeObserverStub)

const IMAGES: ViewerImage[] = [
  { key: 'input-0', src: '/files/a.jpg' },
  { key: 'input-1', src: '/files/b.jpg' },
  { key: 'input-2', src: '/files/c.jpg' },
]

const PROPS = {
  taskId: 't1',
  defects: [],
  onSelectDefect: () => {},
  onLayerSwitch: () => {},
  layerLabel: '标注图',
  layerIsArtifact: false,
}

function filmstripImgs(container: HTMLElement): HTMLImageElement[] {
  return [...container.querySelectorAll<HTMLImageElement>('.glass-float img')]
}

describe('Viewer FilmStrip', () => {
  beforeEach(() => {
    useViewerStore.setState({ views: {}, activeImageKey: null, activeDefectId: null })
  })

  it('多图任务渲染缩略图条，点击缩略图切换 activeImageKey', () => {
    const { container } = render(<Viewer {...PROPS} images={IMAGES} />)
    const thumbs = filmstripImgs(container)
    expect(thumbs).toHaveLength(3)
    // 缩略图外层 div 承载 onClick
    fireEvent.click(thumbs[1].parentElement as HTMLElement)
    expect(useViewerStore.getState().activeImageKey).toBe('input-1')
  })

  /* 回归：缩略图 <img> 必须显式关闭原生拖拽。
     浏览器对可拖拽图片按下+移动会发起原生 DnD（dragstart → pointercancel），
     整个指针手势被取消，click 永不派发——表现为「点缩略图没反应」（真实根因） */
  it('缩略图 img 显式 draggable=false，阻止原生图片拖拽吞掉点击', () => {
    const { container } = render(<Viewer {...PROPS} images={IMAGES} />)
    const thumbs = filmstripImgs(container)
    expect(thumbs.length).toBeGreaterThan(1)
    for (const img of thumbs) {
      expect(img.getAttribute('draggable')).toBe('false')
    }
  })

  it('单图任务不渲染缩略图条', () => {
    const { container } = render(<Viewer {...PROPS} images={[IMAGES[0]]} />)
    expect(filmstripImgs(container)).toHaveLength(0)
  })
})

/* 缺陷修订编辑态（决策 Q1/Q8）：store 会话按 taskId 匹配后，只读 DefectOverlay
   换成可编辑 DefectEditLayer（草稿数据驱动）；自由笔迹标注层不受影响 */
describe('Viewer 缺陷修订编辑态', () => {
  const EDIT_DEFECTS = [
    {
      defect_id: 'D001', defect_type: 'BP', defect_name_cn: '模糊的真缺陷',
      confidence: 0.7, severity: 'high' as const,
      location: { type: 'bbox', bbox: [10, 10, 90, 90] as [number, number, number, number] },
    },
  ]

  beforeEach(() => {
    useViewerStore.setState({ views: {}, activeImageKey: null, activeDefectId: null })
  })

  afterEach(() => {
    useDefectEditStore.getState().closeEditor()
  })

  it('会话匹配 taskId → 渲染 DefectEditLayer（草稿条目数）', () => {
    act(() => useDefectEditStore.getState().openEditor('t1', 'input-0', EDIT_DEFECTS))
    render(<Viewer {...PROPS} images={IMAGES} />)
    expect(screen.getByTestId('defect-edit-layer')).toHaveTextContent('1')
  })

  it('会话归属其他任务 → 不激活编辑层', () => {
    act(() => useDefectEditStore.getState().openEditor('other-task', 'input-0', EDIT_DEFECTS))
    render(<Viewer {...PROPS} images={IMAGES} />)
    expect(screen.queryByTestId('defect-edit-layer')).toBeNull()
  })

  it('新增漏检框工具 → 编辑层收到 addMode', () => {
    act(() => useDefectEditStore.getState().openEditor('t1', 'input-0', EDIT_DEFECTS))
    act(() => useDefectEditStore.getState().setTool('add'))
    render(<Viewer {...PROPS} images={IMAGES} />)
    expect(screen.getByTestId('defect-edit-layer')).toHaveAttribute('data-add', 'true')
  })
})
