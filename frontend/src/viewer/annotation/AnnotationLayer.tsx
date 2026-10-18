/* 批注渲染 + 绘制事务：所有几何都在图像自然坐标系（随 Stage 缩放平移）。
   线条粗细/徽标/文字尺寸按屏幕恒定显示（÷scale 回自然坐标），与 strokeScaleEnabled 语义一致。 */

import { useEffect, useRef, useState } from 'react'
import { Group, Rect, Ellipse, Line, Arrow, Circle, Text, Transformer, Image as KonvaImage } from 'react-konva'
import type Konva from 'konva'
import {
  ANNOTATION_COLORS,
  FREEFORM_TOOLS,
  MASK_TOOLS,
  MARKER_MIN_WIDTH,
  MARKER_OPACITY,
  MARKER_WIDTH_MULTIPLIER,
  canResizeShape,
  nextAnnotationId,
} from './annotationTypes'
import type { AnnotationPoint, AnnotationShape, AnnotationStyle, AnnotationTool } from './annotationTypes'
import { useTheme } from '../../hooks/useTheme'
import {
  isDraftTooSmall,
  normalizeCorners,
  normalizeShape,
  polylineBounds,
  polylineStepForScale,
  resizeShapeToBox,
  translateShape,
  viewportToImage,
} from './annotationGeometry'
import type { ShapeBox, StageTransform } from './annotationGeometry'
import { clamp } from '../../utils/clamp'

export interface PickPointEvent {
  /** 图像自然坐标 */
  nx: number
  ny: number
  /** 视口坐标（放大镜定位用） */
  sx: number
  sy: number
}

interface Props {
  imageKey: string
  imageSize: { width: number; height: number } | null
  /** Stage 画布尺寸（视口像素） */
  stageSize: { width: number; height: number }
  shapes: AnnotationShape[]
  tool: AnnotationTool | null
  style: AnnotationStyle
  selectedId: string | null
  getStageTransform: () => StageTransform
  onCommit: (shape: AnnotationShape) => void
  onSelect: (id: string | null) => void
  /** 橡皮擦命中删除 */
  onErase: (id: string) => void
  /** text 工具：单击锚点（打开输入浮层） */
  onStartText: (point: AnnotationPoint) => void
  /** picker 工具：光标移动采样（null = 离开画布） */
  onPickPoint: (point: PickPointEvent | null) => void
  /** picker 工具：单击锁定颜色 */
  onPickCommit: (point: AnnotationPoint) => void
  /** badge 工具：领取下一个序号 */
  nextBadgeSeq: () => number
  /** 浏览模式拖动/拉伸写回（几何纯函数算好的新形状） */
  onUpdateShape: (shape: AnnotationShape) => void
  /** 脱敏框拉伸后按新区域重新打码（点按类回调，由 Viewer 重建位图） */
  onMaskRegionChange: (id: string, start: AnnotationPoint, end: AnnotationPoint) => void
}

interface Draft {
  start: AnnotationPoint
  current: AnnotationPoint
  /** 自由线轨迹（图像坐标折线） */
  polyline?: number[]
}

function dashPattern(strokeWidth: number): number[] {
  return [strokeWidth * 3.5, strokeWidth * 2]
}

/** 文本工具：屏幕目标字号（随线宽变化，限制在可读范围） */
function textTargetPx(strokeWidth: number): number {
  return clamp(strokeWidth * 5, 16, 48)
}

/** 序号徽标：屏幕目标半径（随线宽变化） */
function badgeTargetRadiusPx(strokeWidth: number): number {
  return clamp(strokeWidth * 2.5, 9, 18)
}

/** 马克笔线宽：半透明荧光按线宽放大，限制最小视觉宽度 */
function markerWidth(strokeWidth: number): number {
  return Math.max(MARKER_MIN_WIDTH, strokeWidth * MARKER_WIDTH_MULTIPLIER)
}

/** 箭头头部尺寸（屏幕像素）：随线宽缩放，正式形状与草稿共用 */
function arrowHeadPx(strokeWidth: number): { pointerLength: number; pointerWidth: number } {
  return { pointerLength: 10 + strokeWidth * 2, pointerWidth: 6 + strokeWidth * 1.5 }
}

/** dataURL → 已解码位图：同一位图多形状/重复切换图像时避免反复解码 */
const maskImageCache = new Map<string, HTMLImageElement>()

/** 脱敏形状：位图加载完成后显示，加载中/无位图显示占位遮罩。
   浏览模式支持整体拖动（矩形区域也参与 Transformer 拉伸，写回后由 Viewer 重打码） */
function MaskImage({
  shape,
  listening,
  draggable,
  onPointerDown,
  onDragEnd,
  onMouseEnter,
  onMouseLeave,
  nodeRef,
}: {
  shape: AnnotationShape
  listening: boolean
  draggable: boolean
  onPointerDown: (e: Konva.KonvaEventObject<PointerEvent>) => void
  onDragEnd: (e: Konva.KonvaEventObject<DragEvent>) => void
  onMouseEnter: (e: Konva.KonvaEventObject<MouseEvent>) => void
  onMouseLeave: (e: Konva.KonvaEventObject<MouseEvent>) => void
  nodeRef: (node: Konva.Node | null) => void
}) {
  const [img, setImg] = useState<HTMLImageElement | null>(null)
  const imageUrl = shape.imageUrl

  useEffect(() => {
    if (!imageUrl) {
      setImg(null)
      return
    }
    const cached = maskImageCache.get(imageUrl)
    if (cached) {
      setImg(cached)
      return
    }
    let dead = false
    const el = new window.Image()
    el.onload = () => {
      maskImageCache.set(imageUrl, el)
      if (!dead) setImg(el)
    }
    el.onerror = () => {
      dead = true
    }
    el.src = imageUrl
    return () => {
      dead = true
    }
  }, [imageUrl])

  const x = shape.start.x
  const y = shape.start.y
  const w = shape.end.x - shape.start.x
  const h = shape.end.y - shape.start.y

  if (img) {
    return (
      <KonvaImage
        key={shape.id}
        image={img}
        x={x} y={y} width={w} height={h}
        listening={listening}
        draggable={draggable}
        ref={nodeRef}
        onPointerDown={onPointerDown}
        onDragEnd={onDragEnd}
        onMouseEnter={onMouseEnter}
        onMouseLeave={onMouseLeave}
      />
    )
  }
  return (
    <Rect
      key={shape.id}
      x={x} y={y} width={w} height={h}
      fill="rgba(0,0,0,0.32)"
      stroke={shape.color}
      strokeWidth={2}
      dash={shape.dashed ? dashPattern(2) : undefined}
      strokeScaleEnabled={false}
      listening={listening}
      draggable={draggable}
      ref={nodeRef}
      onPointerDown={onPointerDown}
      onDragEnd={onDragEnd}
      onMouseEnter={onMouseEnter}
      onMouseLeave={onMouseLeave}
    />
  )
}

export default function AnnotationLayer({
  imageKey,
  imageSize,
  stageSize,
  shapes,
  tool,
  style,
  selectedId,
  getStageTransform,
  onCommit,
  onSelect,
  onErase,
  onStartText,
  onPickPoint,
  onPickCommit,
  nextBadgeSeq,
  onUpdateShape,
  onMaskRegionChange,
}: Props) {
  const [draft, setDraft] = useState<Draft | null>(null)
  /* React 事件的 setState 是异步批处理：move 高频更新时，up 事件可能仍在旧渲染
     闭包里。绘制逻辑读 draftRef（同步真相），渲染展示读 draft state。 */
  const draftRef = useRef<Draft | null>(null)

  const stroke = style.color
  /* 拉伸手柄：主题 accent 色（区别于白色选中线），明暗主题自动跟随 */
  const { theme } = useTheme()
  const anchorColor = ANNOTATION_COLORS[theme].accent

  /* 橡皮擦模式需要形状参与命中检测（Konva hit 图只含 listening 节点） */
  const shapeListening = !tool || tool === 'eraser'
  /* 渲染体内只取一次 Stage 变换（renderShape/renderDraft/捕获层共用，避免每形状重复闭包调用） */
  const stageT = getStageTransform()

  /* 拖动/拉伸：浏览模式下形状可拖动，选中可拉伸形状挂 Transformer */
  const isBrowse = tool === null
  const groupRef = useRef<Konva.Group>(null)
  const trRef = useRef<Konva.Transformer>(null)
  /** shape id → Konva 节点（Transformer 挂载目标 + 变换写回） */
  const nodeRefs = useRef(new Map<string, Konva.Node>())

  /* 选中框形态：文本/序号不可拉伸 → 只显示描边框（无手柄），其余形状 8 向手柄 */
  const selectedShape = shapes.find((sh) => sh.id === selectedId) ?? null
  const borderOnlyAnchor = !selectedShape || !canResizeShape(selectedShape.tool)

  /* 拖动写回：拖动距离换算成图像坐标平移量，几何纯函数产出新形状 */
  function dragDelta(shape: AnnotationShape, node: Konva.Node): { dx: number; dy: number } {
    if (shape.tool === 'line' || shape.tool === 'arrow' || FREEFORM_TOOLS.includes(shape.tool)) {
      /* 点列形状：节点位置即平移量（几何点存绝对坐标，节点本体恒在原点） */
      return { dx: node.x(), dy: node.y() }
    }
    /* 盒式形状：节点位置是左上角/中心/锚点，减原位置得平移量 */
    const baseX = shape.tool === 'ellipse' ? (shape.start.x + shape.end.x) / 2 : shape.start.x
    const baseY = shape.tool === 'ellipse' ? (shape.start.y + shape.end.y) / 2 : shape.start.y
    return { dx: node.x() - baseX, dy: node.y() - baseY }
  }

  function handleDragEnd(shape: AnnotationShape, e: Konva.KonvaEventObject<DragEvent>) {
    const node = e.target as Konva.Node
    const { dx, dy } = dragDelta(shape, node)
    /* 点列形状的节点位置需先归零（几何已吸收平移），否则下次渲染残留偏移 */
    if (shape.tool === 'line' || shape.tool === 'arrow' || FREEFORM_TOOLS.includes(shape.tool)) {
      node.position({ x: 0, y: 0 })
    }
    if (dx === 0 && dy === 0) return
    onUpdateShape(translateShape(shape, dx, dy))
  }

  /* 拉伸写回：读取缩放后的最终包围盒（相对 Group = 图像自然坐标，跳过描边），
     几何纯函数折算回 start/end/折线，脱敏框另行通知重建位图 */
  function handleTransformEnd() {
    const shape = shapes.find((sh) => sh.id === selectedId)
    const node = nodeRefs.current.get(selectedId ?? '')
    if (!shape || !node || !groupRef.current) return
    const rect = node.getClientRect({ relativeTo: groupRef.current, skipStroke: true })
    const box: ShapeBox = { x: rect.x, y: rect.y, width: rect.width, height: rect.height }
    if (box.width < 0.5 && box.height < 0.5) return
    const next = resizeShapeToBox(shape, box)
    /* 归零节点自身的变换：scale/offset（Transformer 缩放通过 scale+offset 实现）/
       rotation 全部复位，重渲染按新几何重建，残留会造成双重缩放与顶点漂移 */
    node.scale({ x: 1, y: 1 })
    node.offset({ x: 0, y: 0 })
    node.rotation(0)
    if (shape.tool === 'line' || shape.tool === 'arrow' || FREEFORM_TOOLS.includes(shape.tool)) {
      node.position({ x: 0, y: 0 })
    }
    onUpdateShape(next)
    if (MASK_TOOLS.includes(shape.tool)) onMaskRegionChange(shape.id, next.start, next.end)
  }

  /* Transformer 挂载：浏览模式下任意选中形状（文本/序号仅拖动 → 只显示描边框，不显示手柄） */
  useEffect(() => {
    const tr = trRef.current
    if (!tr) return
    const shape = shapes.find((sh) => sh.id === selectedId)
    if (!isBrowse || !shape) {
      tr.nodes([])
      return
    }
    const node = nodeRefs.current.get(shape.id)
    tr.nodes(node ? [node] : [])
  }, [selectedId, isBrowse, shapes])

  /* 工具模式开始绘制时清掉形状 hover 遗留的 move 光标（否则十字/橡皮光标被盖住） */
  useEffect(() => {
    if (tool) {
      groupRef.current?.getStage()?.container().style.setProperty('cursor', '')
    }
  }, [tool])

  function handleShapeHover(e: Konva.KonvaEventObject<MouseEvent>, enter: boolean) {
    if (!isBrowse) return
    e.cancelBubble = true
    e.target.getStage()?.container().style.setProperty('cursor', enter ? 'move' : '')
  }

  function pointerToImage(e: Konva.KonvaEventObject<PointerEvent>): AnnotationPoint {
    const stage = e.target.getStage()
    if (!stage) return { x: 0, y: 0 }
    const p = stage.getPointerPosition()
    if (!p) return { x: 0, y: 0 }
    return viewportToImage({ x: p.x, y: p.y }, getStageTransform())
  }

  function screenPoint(e: Konva.KonvaEventObject<PointerEvent>): AnnotationPoint | null {
    return e.target.getStage()?.getPointerPosition() ?? null
  }

  const clip = (p: AnnotationPoint): AnnotationPoint => {
    if (!imageSize) return p
    return {
      x: clamp(p.x, 0, imageSize.width),
      y: clamp(p.y, 0, imageSize.height),
    }
  }

  function beginDrag(p: AnnotationPoint) {
    if (FREEFORM_TOOLS.includes(tool as AnnotationTool)) {
      draftRef.current = { start: p, current: p, polyline: [p.x, p.y] }
    } else {
      draftRef.current = { start: p, current: p }
    }
    setDraft(draftRef.current)
  }

  function handlePointerDown(e: Konva.KonvaEventObject<PointerEvent>) {
    if (!tool || !imageSize) return
    e.cancelBubble = true
    const p = clip(pointerToImage(e))
    if (tool === 'badge') {
      onCommit({
        id: nextAnnotationId(),
        imageKey,
        tool: 'badge',
        start: p,
        end: p,
        color: style.color,
        strokeWidth: style.strokeWidth,
        dashed: false,
        seq: nextBadgeSeq(),
      })
      return
    }
    if (tool === 'text') {
      onStartText(p)
      return
    }
    if (tool === 'eraser' || tool === 'picker') return
    beginDrag(p)
  }

  function handlePointerMove(e: Konva.KonvaEventObject<PointerEvent>) {
    if (!tool || !imageSize) return
    if (tool === 'picker') {
      const vp = screenPoint(e)
      const p = pointerToImage(e)
      onPickPoint(vp ? { nx: p.x, ny: p.y, sx: vp.x, sy: vp.y } : null)
      return
    }
    if (!draftRef.current) return
    e.cancelBubble = true
    const p = clip(pointerToImage(e))
    const cur = draftRef.current
    if (cur.polyline) {
      const lastX = cur.polyline[cur.polyline.length - 2]
      const lastY = cur.polyline[cur.polyline.length - 1]
      const minStep = polylineStepForScale(getStageTransform().scale)
      if (Math.hypot(p.x - lastX, p.y - lastY) < minStep) return
      draftRef.current = { ...cur, current: p, polyline: [...cur.polyline, p.x, p.y] }
      setDraft(draftRef.current)
      return
    }
    draftRef.current = { ...cur, current: p }
    setDraft(draftRef.current)
  }

  function commitDraft() {
    const d = draftRef.current
    if (!d || !tool) return
    draftRef.current = null
    setDraft(null)

    if (d.polyline) {
      if (d.polyline.length < 4) return
      const bounds = polylineBounds(d.polyline)
      if (!bounds) return
      const start = clip(bounds.start)
      const end = clip(bounds.end)
      if (isDraftTooSmall(start, end)) return
      onCommit({
        id: nextAnnotationId(),
        imageKey,
        tool: tool as AnnotationShape['tool'],
        start,
        end,
        color: style.color,
        strokeWidth: style.strokeWidth,
        dashed: false,
        polyline: d.polyline,
      })
      return
    }

    const start = clip(d.start)
    const end = clip(d.current)
    if (isDraftTooSmall(start, end)) return
    const normalized = normalizeShape({
      id: nextAnnotationId(),
      imageKey,
      tool: tool as AnnotationShape['tool'],
      start,
      end,
      color: style.color,
      strokeWidth: style.strokeWidth,
      dashed: style.dashed,
    })
    onCommit(normalized)
  }

  function handlePointerUp(e: Konva.KonvaEventObject<PointerEvent>) {
    if (tool === 'picker') {
      e.cancelBubble = true
      onPickCommit(clip(pointerToImage(e)))
      return
    }
    if (!draftRef.current) return
    e.cancelBubble = true
    commitDraft()
  }

  function handlePointerLeave(e: Konva.KonvaEventObject<PointerEvent>) {
    if (tool === 'picker') {
      onPickPoint(null)
      return
    }
    if (!draftRef.current) return
    e.cancelBubble = true
    commitDraft()
  }

  function handleShapePointerDown(shape: AnnotationShape, e: Konva.KonvaEventObject<PointerEvent>) {
    /* 阻断 pointerdown 冒泡到 draggable Stage：否则拖动形状时舞台平移会同时启动（图片跟着动） */
    e.cancelBubble = true
    if (tool === 'eraser') {
      onErase(shape.id)
      return
    }
    onSelect(shape.id)
  }

  function renderShape(shape: AnnotationShape) {
    /* 选中不改变形状自身颜色/粗细：选中态由 Transformer（描边框 + 主题色手柄）表达 */
    const s = shape.color
    const sw = shape.strokeWidth
    const d = shape.dashed ? dashPattern(sw) : undefined
    const scale = stageT.scale
    const down = (e: Konva.KonvaEventObject<PointerEvent>) => handleShapePointerDown(shape, e)
    /* 浏览模式：拖动 + hover 光标 + 注册节点引用（Transformer 挂载与写回） */
    const nodeRef = (n: Konva.Node | null) => {
      if (n) nodeRefs.current.set(shape.id, n)
      else nodeRefs.current.delete(shape.id)
    }
    const browse = isBrowse
      ? {
          draggable: true,
          onDragEnd: (e: Konva.KonvaEventObject<DragEvent>) => handleDragEnd(shape, e),
          onMouseEnter: (e: Konva.KonvaEventObject<MouseEvent>) => handleShapeHover(e, true),
          onMouseLeave: (e: Konva.KonvaEventObject<MouseEvent>) => handleShapeHover(e, false),
        }
      : {}

    if (shape.tool === 'rect') {
      return (
        <Rect
          key={shape.id}
          x={shape.start.x} y={shape.start.y}
          width={shape.end.x - shape.start.x} height={shape.end.y - shape.start.y}
          stroke={s} strokeWidth={sw} dash={d} strokeScaleEnabled={false}
          listening={shapeListening}
          onPointerDown={down}
          ref={nodeRef}
          {...browse}
        />
      )
    }
    if (shape.tool === 'ellipse') {
      return (
        <Ellipse
          key={shape.id}
          x={(shape.start.x + shape.end.x) / 2} y={(shape.start.y + shape.end.y) / 2}
          radiusX={(shape.end.x - shape.start.x) / 2} radiusY={(shape.end.y - shape.start.y) / 2}
          stroke={s} strokeWidth={sw} dash={d} strokeScaleEnabled={false}
          listening={shapeListening}
          onPointerDown={down}
          ref={nodeRef}
          {...browse}
        />
      )
    }
    if (shape.tool === 'arrow') {
      return (
        <Arrow
          key={shape.id}
          points={[shape.start.x, shape.start.y, shape.end.x, shape.end.y]}
          stroke={s} strokeWidth={sw} dash={d} strokeScaleEnabled={false}
          {...arrowHeadPx(sw)}
          hitStrokeWidth={14}
          listening={shapeListening}
          onPointerDown={down}
          ref={nodeRef}
          {...browse}
        />
      )
    }
    if (FREEFORM_TOOLS.includes(shape.tool)) {
      const isMarker = shape.tool === 'marker'
      const width = isMarker ? markerWidth(shape.strokeWidth) : shape.strokeWidth
      return (
        <Line
          key={shape.id}
          points={shape.polyline ?? [shape.start.x, shape.start.y, shape.end.x, shape.end.y]}
          stroke={s}
          strokeWidth={width}
          opacity={isMarker ? MARKER_OPACITY : 1}
          tension={0.35}
          lineCap="round"
          lineJoin="round"
          hitStrokeWidth={14}
          strokeScaleEnabled={false}
          listening={shapeListening}
          onPointerDown={down}
          ref={nodeRef}
          {...browse}
        />
      )
    }
    if (shape.tool === 'text') {
      const fontSize = textTargetPx(shape.strokeWidth) / scale
      return (
        <Text
          key={shape.id}
          x={shape.start.x} y={shape.start.y}
          text={shape.text ?? ''}
          fill={s}
          fontSize={fontSize}
          fontStyle="600"
          fontFamily='-apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif'
          listening={shapeListening}
          onPointerDown={down}
          ref={nodeRef}
          {...browse}
        />
      )
    }
    if (shape.tool === 'badge') {
      const radius = badgeTargetRadiusPx(shape.strokeWidth) / scale
      const seq = String(shape.seq ?? 1)
      return (
        <Group key={shape.id} x={shape.start.x} y={shape.start.y} listening={shapeListening} onPointerDown={down} ref={nodeRef} {...browse}>
          <Circle
            radius={radius}
            fill={s}
            stroke="#ffffff"
            strokeWidth={1.5}
            strokeScaleEnabled={false}
            listening={shapeListening}
            onPointerDown={down}
          />
          <Text
            text={seq}
            x={-radius} y={-radius}
            width={radius * 2} height={radius * 2}
            fill="#ffffff"
            fontSize={radius * 1.15}
            fontStyle="700"
            fontFamily='-apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif'
            align="center"
            verticalAlign="middle"
            listening={false}
          />
        </Group>
      )
    }
    if (MASK_TOOLS.includes(shape.tool)) {
      return (
        <MaskImage
          key={shape.id}
          shape={shape}
          listening={shapeListening}
          draggable={isBrowse}
          onPointerDown={down}
          onDragEnd={(e) => handleDragEnd(shape, e)}
          onMouseEnter={(e) => handleShapeHover(e, true)}
          onMouseLeave={(e) => handleShapeHover(e, false)}
          nodeRef={nodeRef}
        />
      )
    }
    return (
      <Line
        key={shape.id}
        points={[shape.start.x, shape.start.y, shape.end.x, shape.end.y]}
        stroke={s} strokeWidth={sw} dash={d} strokeScaleEnabled={false}
        lineCap="round"
        hitStrokeWidth={14}
        listening={shapeListening}
        onPointerDown={down}
        ref={nodeRef}
        {...browse}
      />
    )
  }

  function renderDraft() {
    if (!draft) return null
    const sw = style.strokeWidth
    const d = style.dashed ? dashPattern(sw) : undefined

    if (draft.polyline && tool && FREEFORM_TOOLS.includes(tool)) {
      const isMarker = tool === 'marker'
      const width = isMarker ? markerWidth(sw) : sw
      return (
        <Line
          points={draft.polyline}
          stroke={stroke}
          strokeWidth={width}
          opacity={isMarker ? MARKER_OPACITY : 1}
          tension={0.35}
          lineCap="round"
          lineJoin="round"
          strokeScaleEnabled={false}
          listening={false}
        />
      )
    }

    const start = clip(draft.start)
    const end = clip(draft.current)
    // 与正式形状一致：矩形/椭圆/脱敏区角点归一化为 左上→右下
    const { start: s, end: en } = normalizeCorners(start, end)

    if (tool === 'rect') {
      return (
        <Rect
          x={s.x} y={s.y} width={en.x - s.x} height={en.y - s.y}
          stroke={stroke} strokeWidth={sw} dash={d} strokeScaleEnabled={false}
          listening={false}
        />
      )
    }
    if (tool === 'ellipse') {
      return (
        <Ellipse
          x={(start.x + end.x) / 2} y={(start.y + end.y) / 2}
          radiusX={Math.abs(end.x - start.x) / 2} radiusY={Math.abs(end.y - start.y) / 2}
          stroke={stroke} strokeWidth={sw} dash={d} strokeScaleEnabled={false}
          listening={false}
        />
      )
    }
    if (tool && MASK_TOOLS.includes(tool)) {
      return (
        <Rect
          x={s.x} y={s.y} width={en.x - s.x} height={en.y - s.y}
          fill={tool === 'blur' ? 'rgba(120, 170, 210, 0.28)' : 'rgba(0, 0, 0, 0.45)'}
          stroke={stroke} strokeWidth={sw} dash={d} strokeScaleEnabled={false}
          listening={false}
        />
      )
    }
    const arrowProps = tool === 'arrow' ? arrowHeadPx(sw) : {}
    const LineCls = tool === 'arrow' ? Arrow : Line
    return (
      <LineCls
        points={[start.x, start.y, end.x, end.y]}
        stroke={stroke} strokeWidth={sw} dash={d} strokeScaleEnabled={false}
        lineCap="round" listening={false} {...arrowProps}
      />
    )
  }

  return (
    <Group listening={true} ref={groupRef}>
      {/* 工具模式下的全视口事件捕获层（把手势从缺陷框/Stage 平移中接管过来）。
          覆盖与视口等大的图像坐标矩形：指针拖到画布任意角落都不会丢失 move/up 事件；
          最终端点在 commitDraft 里被 clip 回图像范围内 */}
      {tool && imageSize && (
        <Rect
          x={-stageT.x / stageT.scale}
          y={-stageT.y / stageT.scale}
          width={stageSize.width / stageT.scale}
          height={stageSize.height / stageT.scale}
          fill="rgba(0,0,0,0.01)" listening={true}
          onPointerDown={handlePointerDown}
          onPointerMove={handlePointerMove}
          onPointerUp={handlePointerUp}
          onPointerLeave={handlePointerLeave}
        />
      )}
      {shapes.map(renderShape)}
      {/* 选中框：可拉伸形状（矩形/椭圆/直线/箭头/手绘/脱敏框）显示 8 向拉伸手柄，
          文本/序号只拖动 → 仅描边框（无手柄）。手柄用主题 accent 色，拖动/拉伸结果写回形状几何。
          按下时阻断冒泡，避免 Stage 平移同时启动 */}
      <Transformer
        ref={trRef}
        rotateEnabled={false}
        flipEnabled={false}
        keepRatio={false}
        enabledAnchors={borderOnlyAnchor ? [] : undefined}
        anchorSize={8}
        anchorCornerRadius={4}
        anchorStroke={anchorColor}
        anchorFill={anchorColor}
        borderStroke="#ffffff"
        borderDash={[4, 3]}
        onPointerDown={(e) => e.cancelBubble = true}
        onTransformEnd={handleTransformEnd}
      />
      {renderDraft()}
    </Group>
  )
}