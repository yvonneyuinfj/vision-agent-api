import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import Viewer from './Viewer'
import { useViewerStore } from '../store/useViewerStore'
import type { DefectItem } from '../api/types'

/* 与 Viewer.test.tsx 同策略：Konva 组件透传；Image 额外把 image.__src 透出为
   data-src，用于断言「算法标注图」层图像源选择（原图 vs 模型标注产物） */
vi.mock('react-konva', () => {
  const passthrough = ({ children }: { children?: React.ReactNode }) => children ?? null
  const Image = (p: { image?: { __src?: string } | null }) => (
    <div data-testid="konva-image" data-src={p.image?.__src ?? ''} />
  )
  return { Stage: passthrough, Layer: passthrough, Image }
})
vi.mock('./DefectOverlay', () => ({ default: () => null }))
vi.mock('./annotation/AnnotationLayer', () => ({ default: () => null }))
vi.mock('./annotation/AnnotationPanel', () => ({ default: () => null }))
vi.mock('./annotation/TextInputOverlay', () => ({ default: () => null }))
vi.mock('./annotation/ColorPickPanel', () => ({ default: () => null }))
// 图像加载桩：按 src 返回携带 __src 标记的对象，绕过 jsdom 无 canvas 的限制
vi.mock('./useKonvaImage', () => ({
  useKonvaImage: (src: string | null) => ({
    image: src ? ({ width: 100, height: 80, __src: src } as unknown as HTMLCanvasElement) : null,
    failed: false,
  }),
}))
vi.mock('./annotation/annotationPixels', async (importOriginal) => {
  const mod = await importOriginal<typeof import('./annotation/annotationPixels')>()
  return { ...mod, buildPixelSource: () => null, samplePixel: () => null }
})

class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
vi.stubGlobal('ResizeObserver', ResizeObserverStub)

const ANN = '/api/v1/tasks/t1/files/artifact_result_image?kind=artifacts'
const BASE = '/api/v1/tasks/t1/files/original.jpg?kind=inputs'
const IMAGES = [{ key: 'input-0', src: BASE }]

function defect(location: DefectItem['location']): DefectItem {
  return {
    defect_id: 'd1',
    defect_type: 'BP',
    defect_name_cn: '模糊的真缺陷',
    confidence: 0.8,
    severity: null,
    location,
    description: '',
  }
}

const PROPS = {
  taskId: 't1',
  onSelectDefect: () => {},
  onLayerSwitch: () => {},
  layerLabel: '标注图',
  layerIsArtifact: false,
}

function renderedSrc(): string {
  return screen.getByTestId('konva-image').getAttribute('data-src') ?? ''
}

describe('Viewer 标注层图像源选择', () => {
  beforeEach(() => {
    useViewerStore.setState({ views: {}, activeImageKey: null, activeDefectId: null })
  })

  it('缺陷条目无 bbox 时，「算法标注图」层渲染模型标注产物图（标注图==原图回归）', () => {
    render(<Viewer {...PROPS} images={IMAGES} defects={[defect(null)]} showDefects annotatedSrc={ANN} />)
    expect(renderedSrc()).toBe(ANN)
  })

  it('条目携带可绘制 bbox 时维持原图（Konva 绘框由 DefectOverlay 承担，保留点击联动）', () => {
    render(
      <Viewer {...PROPS} images={IMAGES} defects={[defect({ type: 'bbox', bbox: [1, 2, 3, 4] })]} showDefects annotatedSrc={ANN} />,
    )
    expect(renderedSrc()).toBe(BASE)
  })

  it('原图层（showDefects=false）恒渲染原图', () => {
    render(
      <Viewer
        {...PROPS}
        images={IMAGES}
        defects={[defect(null)]}
        showDefects={false}
        annotatedSrc={ANN}
        layerIsArtifact
        layerLabel="原图"
      />,
    )
    expect(renderedSrc()).toBe(BASE)
  })

  it('无标注产物时退回原图（不臆造图源）', () => {
    render(<Viewer {...PROPS} images={IMAGES} defects={[defect(null)]} showDefects />)
    expect(renderedSrc()).toBe(BASE)
  })
})
