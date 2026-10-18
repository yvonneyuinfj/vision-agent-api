/* 坐标换算与形状工具：纯函数，独立于 Konva，便于单测 */

import type { AnnotationPoint, AnnotationShape } from './annotationTypes'

/** Stage 变换参数的最小抽象（避免依赖 Konva 实例做测试） */
export interface StageTransform {
  scale: number
  x: number
  y: number
}

/** 视口坐标 → 图像自然坐标 */
export function viewportToImage(point: AnnotationPoint, transform: StageTransform): AnnotationPoint {
  return {
    x: (point.x - transform.x) / transform.scale,
    y: (point.y - transform.y) / transform.scale,
  }
}

/** 图像自然坐标 → 视口坐标 */
export function imageToViewport(point: AnnotationPoint, transform: StageTransform): AnnotationPoint {
  return {
    x: point.x * transform.scale + transform.x,
    y: point.y * transform.scale + transform.y,
  }
}

/** 归一化角点：start=左上、end=右下（支持任意方向的拖拽） */
export function normalizeCorners(start: AnnotationPoint, end: AnnotationPoint): { start: AnnotationPoint; end: AnnotationPoint } {
  return {
    start: { x: Math.min(start.x, end.x), y: Math.min(start.y, end.y) },
    end: { x: Math.max(start.x, end.x), y: Math.max(start.y, end.y) },
  }
}

/** 手势最小感知量（屏幕像素）：拖拽抖动阈值与自由线采样步进共用 */
const MIN_GESTURE_PX = 3

export function isDraftTooSmall(start: AnnotationPoint, end: AnnotationPoint): boolean {
  const dx = Math.abs(end.x - start.x)
  const dy = Math.abs(end.y - start.y)
  return Math.hypot(dx, dy) < MIN_GESTURE_PX
}

/** 需要角点归一化的工具（点按类/自由线类保持原始坐标） */
const CORNER_NORMALIZED_TOOLS = new Set<AnnotationShape['tool']>(['rect', 'ellipse', 'mosaic', 'blur'])

/** 批注的归一化拷贝（矩形/椭圆/脱敏区角点排序后，start 永远是左上） */
export function normalizeShape(shape: AnnotationShape): AnnotationShape {
  if (!CORNER_NORMALIZED_TOOLS.has(shape.tool)) return shape
  return { ...shape, ...normalizeCorners(shape.start, shape.end) }
}

/** 自由线采样最小步进（图像自然坐标）：约 3 个屏幕像素（MIN_GESTURE_PX），随缩放调整 */
export function polylineStepForScale(scale: number): number {
  return MIN_GESTURE_PX / Math.max(scale, 0.01)
}

/** 折线包围盒（自由线上报 start/end 用）；不足两个点返回 null */
export function polylineBounds(polyline: number[]): { start: AnnotationPoint; end: AnnotationPoint } | null {
  if (polyline.length < 4) return null
  let minX = Infinity
  let minY = Infinity
  let maxX = -Infinity
  let maxY = -Infinity
  for (let i = 0; i < polyline.length; i += 2) {
    const x = polyline[i]
    const y = polyline[i + 1]
    minX = Math.min(minX, x)
    maxX = Math.max(maxX, x)
    minY = Math.min(minY, y)
    maxY = Math.max(maxY, y)
  }
  return { start: { x: minX, y: minY }, end: { x: maxX, y: maxY } }
}

/** 形状包围盒（图像自然坐标；单点形状宽高为 0） */
export interface ShapeBox {
  x: number
  y: number
  width: number
  height: number
}

/** 批注包围盒：自由线取折线包围盒，其余取 start/end 归一角点 */
export function shapeBounds(shape: AnnotationShape): ShapeBox | null {
  if (shape.polyline) {
    const b = polylineBounds(shape.polyline)
    if (!b) return null
    return { x: b.start.x, y: b.start.y, width: b.end.x - b.start.x, height: b.end.y - b.start.y }
  }
  const { start, end } = normalizeCorners(shape.start, shape.end)
  return { x: start.x, y: start.y, width: end.x - start.x, height: end.y - start.y }
}

/** 整体平移：平移 start/end 与折线，返回新形状（原形状不变；坐标收敛到 0.01 保落库数据干净） */
export function translateShape(shape: AnnotationShape, dx: number, dy: number): AnnotationShape {
  const r2 = (v: number) => Math.round(v * 100) / 100
  const next: AnnotationShape = {
    ...shape,
    start: { x: r2(shape.start.x + dx), y: r2(shape.start.y + dy) },
    end: { x: r2(shape.end.x + dx), y: r2(shape.end.y + dy) },
  }
  if (shape.polyline) {
    next.polyline = shape.polyline.map((v, i) => r2(v + (i % 2 === 0 ? dx : dy)))
  }
  return next
}

const round2 = (v: number) => Math.round(v * 100) / 100

/** 拉伸到新盒（Transformer 写回）：
   - 矩形/椭圆/脱敏框：直接以新盒为角点；
   - 直线/箭头/自由线：各点按旧盒→新盒仿射映射，退化维度（零宽/零高）只平移不缩放；
   - 文本/序号：不支持拉伸，原对象原样返回（由 canResizeShape 挡在入口外）。
   全部坐标收敛到 0.01，避免手笔浮点噪声污染落库几何。 */
export function resizeShapeToBox(shape: AnnotationShape, box: ShapeBox): AnnotationShape {
  if (shape.tool === 'text' || shape.tool === 'badge') return shape
  const old = shapeBounds(shape)
  if (!old) return shape

  const mapPoint = (p: AnnotationPoint): AnnotationPoint => ({
    x: round2(old.width > 0 ? box.x + ((p.x - old.x) * box.width) / old.width : p.x),
    y: round2(old.height > 0 ? box.y + ((p.y - old.y) * box.height) / old.height : p.y),
  })

  if (shape.polyline) {
    return {
      ...shape,
      polyline: shape.polyline.map((v, i) => (i % 2 === 0
        ? mapPoint({ x: v, y: 0 }).x
        : mapPoint({ x: 0, y: v }).y)),
      start: { x: round2(box.x), y: round2(box.y) },
      end: { x: round2(box.x + box.width), y: round2(box.y + box.height) },
    }
  }
  if (shape.tool === 'line' || shape.tool === 'arrow') {
    return { ...shape, start: mapPoint(shape.start), end: mapPoint(shape.end) }
  }
  return {
    ...shape,
    start: { x: round2(box.x), y: round2(box.y) },
    end: { x: round2(box.x + box.width), y: round2(box.y + box.height) },
  }
}