import { describe, expect, it } from 'vitest'
import {
  imageToViewport, isDraftTooSmall, normalizeCorners, normalizeShape, polylineBounds,
  polylineStepForScale, resizeShapeToBox, shapeBounds, translateShape, viewportToImage,
} from './annotationGeometry'
import { canResizeShape } from './annotationTypes'
import type { AnnotationShape } from './annotationTypes'

const TRANSFORM = { scale: 2, x: 100, y: -50 }

describe('viewportToImage / imageToViewport', () => {
  it('视口坐标按 scale/x/y 换算为图像自然坐标', () => {
    expect(viewportToImage({ x: 300, y: 150 }, TRANSFORM)).toEqual({ x: 100, y: 100 })
  })

  it('往返换算回到原点', () => {
    const viewport = { x: 733.5, y: 241.25 }
    const image = viewportToImage(viewport, TRANSFORM)
    expect(imageToViewport(image, TRANSFORM)).toEqual(viewport)
  })

  it('scale=1 且无平移时恒等', () => {
    expect(viewportToImage({ x: 55, y: 66 }, { scale: 1, x: 0, y: 0 })).toEqual({ x: 55, y: 66 })
  })
})

describe('normalizeCorners', () => {
  it('任意拖拽方向都归一为 左上→右下', () => {
    expect(normalizeCorners({ x: 50, y: 90 }, { x: 10, y: 20 })).toEqual({
      start: { x: 10, y: 20 },
      end: { x: 50, y: 90 },
    })
  })

  it('已有序时保持原值', () => {
    expect(normalizeCorners({ x: 1, y: 1 }, { x: 9, y: 9 })).toEqual({
      start: { x: 1, y: 1 },
      end: { x: 9, y: 9 },
    })
  })
})

describe('isDraftTooSmall', () => {
  it('小于 3px 的抖动视为无效草稿', () => {
    expect(isDraftTooSmall({ x: 0, y: 0 }, { x: 2, y: 0 })).toBe(true)
    expect(isDraftTooSmall({ x: 10, y: 10 }, { x: 11.5, y: 11.5 })).toBe(true)
  })

  it('达到阈值构成有效批注', () => {
    expect(isDraftTooSmall({ x: 0, y: 0 }, { x: 5, y: 0 })).toBe(false)
  })
})

describe('normalizeShape', () => {
  const base: AnnotationShape = {
    id: 'a1', imageKey: 'img-1', tool: 'rect',
    start: { x: 40, y: 40 }, end: { x: 10, y: 10 },
    color: '#f0756b', strokeWidth: 4, dashed: false,
  }

  it('矩形角点归一并用不可变方式返回新对象', () => {
    const out = normalizeShape(base)
    expect(out).not.toBe(base)
    expect(out.start).toEqual({ x: 10, y: 10 })
    expect(out.end).toEqual({ x: 40, y: 40 })
  })

  it('箭头/直线不改变端点方向', () => {
    const line: AnnotationShape = { ...base, tool: 'arrow' }
    expect(normalizeShape(line)).toBe(line)
  })

  it('脱敏区（mosaic/blur）同矩形一样归一角点', () => {
    const mosaic = normalizeShape({ ...base, tool: 'mosaic' })
    expect(mosaic.start).toEqual({ x: 10, y: 10 })
    expect(mosaic.end).toEqual({ x: 40, y: 40 })
    const blur = normalizeShape({ ...base, tool: 'blur' })
    expect(blur.start).toEqual({ x: 10, y: 10 })
  })

  it('自由线/文本/序号保持原样（不归一化）', () => {
    const pencil = { ...base, tool: 'pencil' as const }
    expect(normalizeShape(pencil)).toBe(pencil)
    const text = { ...base, tool: 'text' as const }
    expect(normalizeShape(text)).toBe(text)
    const badge = { ...base, tool: 'badge' as const }
    expect(normalizeShape(badge)).toBe(badge)
  })
})

describe('polyline 工具', () => {
  it('polylineBounds 计算折线包围盒', () => {
    expect(polylineBounds([0, 10, 50, 5, 30, 80])).toEqual({
      start: { x: 0, y: 5 },
      end: { x: 50, y: 80 },
    })
  })

  it('不足两个点时返回 null', () => {
    expect(polylineBounds([])).toBeNull()
    expect(polylineBounds([1, 2])).toBeNull()
  })

  it('polylineStepForScale 随缩放反向调整（100% 时 3 图像像素）', () => {
    expect(polylineStepForScale(1)).toBe(3)
    expect(polylineStepForScale(1.4)).toBeCloseTo(2.142857, 4)
  })
})

describe('shapeBounds / translateShape / resizeShapeToBox（拖动与拉伸写回）', () => {
  const base: AnnotationShape = {
    id: 'b1', imageKey: 'img-1', tool: 'rect',
    start: { x: 10, y: 20 }, end: { x: 50, y: 60 },
    color: '#f0756b', strokeWidth: 4, dashed: false,
  }

  it('shapeBounds：矩形/椭圆/线从 start/end 取包围盒', () => {
    expect(shapeBounds(base)).toEqual({ x: 10, y: 20, width: 40, height: 40 })
  })

  it('shapeBounds：自由线从折线取包围盒', () => {
    const pencil: AnnotationShape = { ...base, tool: 'pencil', polyline: [0, 0, 50, 50, 100, 50] }
    expect(shapeBounds(pencil)).toEqual({ x: 0, y: 0, width: 100, height: 50 })
  })

  it('shapeBounds：单点形状（文本/序号）包围盒为零面积点', () => {
    const text: AnnotationShape = { ...base, tool: 'text', end: { x: 10, y: 20 } }
    expect(shapeBounds(text)).toEqual({ x: 10, y: 20, width: 0, height: 0 })
  })

  it('translateShape：平移矩形同时移动 start/end，不改动原形状', () => {
    const out = translateShape(base, 5, -5)
    expect(out).not.toBe(base)
    expect(out.start).toEqual({ x: 15, y: 15 })
    expect(out.end).toEqual({ x: 55, y: 55 })
    expect(base.start).toEqual({ x: 10, y: 20 })
  })

  it('translateShape：平移自由线整条折线', () => {
    const pencil: AnnotationShape = { ...base, tool: 'pencil', polyline: [0, 0, 10, 10] }
    const out = translateShape(pencil, 5, 3)
    expect(out.polyline).toEqual([5, 3, 15, 13])
    expect(out.start).toEqual({ x: 15, y: 23 })
    expect(out.end).toEqual({ x: 55, y: 63 })
  })

  it('translateShape：文本仅平移锚点', () => {
    const text: AnnotationShape = { ...base, tool: 'text', end: { x: 10, y: 20 } }
    const out = translateShape(text, -2, 4)
    expect(out.start).toEqual({ x: 8, y: 24 })
  })

  it('resizeShapeToBox：矩形拉伸到新盒并归一角点，原形状不变', () => {
    const rect: AnnotationShape = { ...base, start: { x: 0, y: 0 }, end: { x: 100, y: 50 } }
    const out = resizeShapeToBox(rect, { x: 10, y: 20, width: 60, height: 40 })
    expect(out.start).toEqual({ x: 10, y: 20 })
    expect(out.end).toEqual({ x: 70, y: 60 })
    expect(rect.end).toEqual({ x: 100, y: 50 })
  })

  it('resizeShapeToBox：椭圆按盒重算角点（渲染时折算中心与半径）', () => {
    const ellipse: AnnotationShape = { ...base, tool: 'ellipse', start: { x: 0, y: 0 }, end: { x: 100, y: 50 } }
    const out = resizeShapeToBox(ellipse, { x: 10, y: 20, width: 60, height: 40 })
    expect(out.start).toEqual({ x: 10, y: 20 })
    expect(out.end).toEqual({ x: 70, y: 60 })
  })

  it('resizeShapeToBox：直线/箭头端点按旧盒到新盒仿射映射', () => {
    const line: AnnotationShape = { ...base, tool: 'line', start: { x: 0, y: 0 }, end: { x: 100, y: 50 } }
    const out = resizeShapeToBox(line, { x: 0, y: 0, width: 200, height: 100 })
    expect(out.end).toEqual({ x: 200, y: 100 })
  })

  it('resizeShapeToBox：自由线所有点按盒仿射映射', () => {
    const pencil: AnnotationShape = {
      ...base, tool: 'marker', start: { x: 0, y: 0 }, end: { x: 100, y: 50 }, polyline: [0, 0, 50, 50, 100, 50],
    }
    const out = resizeShapeToBox(pencil, { x: 0, y: 0, width: 100, height: 100 })
    expect(out.polyline).toEqual([0, 0, 50, 100, 100, 100])
  })

  it('resizeShapeToBox：旧盒退化维度（零宽/零高）该维只平移不缩放', () => {
    const vline: AnnotationShape = { ...base, tool: 'line', start: { x: 50, y: 0 }, end: { x: 50, y: 80 } }
    const out = resizeShapeToBox(vline, { x: 10, y: 0, width: 40, height: 160 })
    expect(out.start).toEqual({ x: 50, y: 0 })
    expect(out.end).toEqual({ x: 50, y: 160 })
  })

  it('resizeShapeToBox：文本/序号不做拉伸，原对象返回', () => {
    const text: AnnotationShape = { ...base, tool: 'text', end: { x: 10, y: 20 } }
    expect(resizeShapeToBox(text, { x: 5, y: 5, width: 80, height: 80 })).toBe(text)
    const badge: AnnotationShape = { ...base, tool: 'badge', end: { x: 10, y: 20 } }
    expect(resizeShapeToBox(badge, { x: 5, y: 5, width: 80, height: 80 })).toBe(badge)
  })

  it('resizeShapeToBox：脱敏框同矩形处理（新区域供重新打码）', () => {
    const mosaic: AnnotationShape = { ...base, tool: 'mosaic', start: { x: 0, y: 0 }, end: { x: 100, y: 50 } }
    const out = resizeShapeToBox(mosaic, { x: 10, y: 20, width: 60, height: 40 })
    expect(out.start).toEqual({ x: 10, y: 20 })
    expect(out.end).toEqual({ x: 70, y: 60 })
  })

  it('canResizeShape：几何图形可拉伸，文本/序号仅可拖动', () => {
    for (const tool of ['rect', 'ellipse', 'line', 'arrow', 'pencil', 'marker', 'mosaic', 'blur'] as const) {
      expect(canResizeShape(tool)).toBe(true)
    }
    expect(canResizeShape('text')).toBe(false)
    expect(canResizeShape('badge')).toBe(false)
  })
})