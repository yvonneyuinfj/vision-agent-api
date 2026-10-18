import { useEffect, useRef, useState, useCallback, useMemo } from 'react'
import { Stage, Layer, Image as KonvaImage } from 'react-konva'
import type Konva from 'konva'
import { Button, Space, Tooltip, Spin } from 'antd'
import { ZoomInOutlined, ZoomOutOutlined, ExpandOutlined, OneToOneOutlined, PictureOutlined } from '@ant-design/icons'
import { useKonvaImage } from './useKonvaImage'
import DefectOverlay from './DefectOverlay'
import DefectEditLayer from './DefectEditLayer'
import { resolveAnnotatedLayerSrc } from './annotatedLayer'
import { defectsForImage, annotatedArtifactForImage } from './taskImages'
import AnnotationLayer from './annotation/AnnotationLayer'
import AnnotationPanel from './annotation/AnnotationPanel'
import TextInputOverlay from './annotation/TextInputOverlay'
import ColorPickPanel from './annotation/ColorPickPanel'
import { IconPanelToggle } from './annotation/annotationIcons'
import { useAnnotationStore } from './annotation/useAnnotationStore'
import { buildMaskDataUrl, buildPixelSource, samplePixel } from './annotation/annotationPixels'
import type { PixelSource, MaskKind } from './annotation/annotationPixels'
import { rgbToHex, rgbString, rgbToHsl, hslString } from './annotation/annotationColor'
import { imageToViewport } from './annotation/annotationGeometry'
import { nextAnnotationId, MASK_TOOLS } from './annotation/annotationTypes'
import type { AnnotationShape } from './annotation/annotationTypes'
import { useViewerStore } from '../store/useViewerStore'
import { useDefectEditStore } from '../store/useDefectEditStore'
import { useDraggable } from '../hooks/useDraggable'
import type { ArtifactRef, DefectItem } from '../api/types'

export interface ViewerImage { key: string; src: string }

interface Props {
  taskId: string
  images: ViewerImage[]
  defects: DefectItem[]
  onSelectDefect: (defectId: string) => void
  onLayerSwitch: () => void
  layerLabel: string
  layerIsArtifact: boolean
  /** 是否叠加缺陷 bbox（「算法标注图」= 原图 + 8002 返回坐标绘制框） */
  showDefects?: boolean
  /** 检测模型渲染的标注图产物 URI：缺陷条目无可绘制 bbox 时作为「算法标注图」层
   * （agent 轨条目常缺 location，见 resolveAnnotatedLayerSrc） */
  annotatedSrc?: string | null
  /** 多图任务：传产物全量列表，按当前激活图配对标注图产物
   *  （fan-out __<stem> 后缀；优于 annotatedSrc 单值形态） */
  artifacts?: ArtifactRef[] | null
}

const MIN_SCALE = 0.05
const MAX_SCALE = 32

/** 空批注列表常量：selector 需返回稳定引用，避免每次求值生成新数组导致整页重渲染 */
const NO_SHAPES: AnnotationShape[] = []

export default function Viewer({ taskId, images, defects, onSelectDefect, onLayerSwitch, layerLabel, layerIsArtifact, showDefects = true, annotatedSrc = null, artifacts = null }: Props) {
  const containerRef = useRef<HTMLDivElement>(null)
  const stageRef = useRef<Konva.Stage>(null)
  const [size, setSize] = useState({ width: 800, height: 600 })
  const activeImageKey = useViewerStore((s) => s.activeImageKey)
  const setActiveImageKey = useViewerStore((s) => s.setActiveImageKey)
  const views = useViewerStore((s) => s.views)
  const updateView = useViewerStore((s) => s.updateView)

  /* 缺陷修订编辑会话（决策 Q1/Q8）：store 按 taskId 匹配后，只读 DefectOverlay
     换成可编辑 DefectEditLayer（选中/拖动/缩放/画新增框）；自由笔迹标注层
     独立共存——笔迹纯内存不提交，修订随复核 revise 上送 */
  const editDraft = useDefectEditStore((s) => (s.open && s.taskId === taskId ? s.draft : null))
  const editTool = useDefectEditStore((s) => s.tool)
  const editSelectedId = useDefectEditStore((s) => s.selectedId)
  const setEditSelectedId = useDefectEditStore((s) => s.setSelectedId)
  const setEditTool = useDefectEditStore((s) => s.setTool)
  const addDefect = useDefectEditStore((s) => s.addDefect)
  const moveDefect = useDefectEditStore((s) => s.moveDefect)
  const defectEditActive = editDraft !== null

  // 两个可拖动 overlay：工具栏 + 缩略图条，各自独立位移
  const toolbar = useDraggable()
  const filmstrip = useDraggable()

  const activeImage = images.find((i) => i.key === activeImageKey) ?? images[0]
  const base = useKonvaImage(activeImage?.src ?? null)
  // 多图 bbox 混叠修复：当前激活图只画本图的缺陷框（image_name 溯源）
  const activeDefects = useMemo(
    () => defectsForImage(defects, activeImage?.src, images[0]?.src),
    [defects, activeImage?.src, images],
  )
  // 人工新增框归属当前激活图（image_name 溯源，多图任务防混叠）
  const activeImageFileName = useMemo(() => {
    const src = activeImage?.src
    if (!src) return undefined
    return src.split('/files/')[1]?.split('?')[0] ?? undefined
  }, [activeImage?.src])
  // 标注层图像源：条目无可绘制 bbox 且有模型标注产物时，直接呈现产物图。
  // 多图任务按当前激活图配对产物（fan-out __<stem> 后缀）——别图的标注图
  // 绝不当成本图的图层
  const multiImage = images.length > 1
  const layerArtifactSrc = resolveAnnotatedLayerSrc({
    annotatedSrc:
      multiImage && artifacts
        ? annotatedArtifactForImage(artifacts, activeImage?.src)?.uri ?? null
        : annotatedSrc,
    showDefects,
    imageCount: images.length,
    defects: activeDefects,
    perImage: multiImage,
  })
  const artifactLayer = useKonvaImage(layerArtifactSrc)
  // 产物图加载完成前先显示原图（图层切换不闪白）；产物加载失败且原图可用时静默降级
  const image = artifactLayer.image ?? base.image
  const failed = layerArtifactSrc ? artifactLayer.failed && !base.image : base.failed
  const view = (activeImage && views[activeImage.key]) || { scale: 1, x: 0, y: 0 }

  /* 标注像素源：取色放大镜与脱敏位图共用（等比缩放到封顶边长） */
  const [pixelSource, setPixelSource] = useState<PixelSource | null>(null)
  useEffect(() => {
    setPixelSource(image ? buildPixelSource(image) : null)
  }, [image])

  // 人工标注（Snipaste 式：箭头/矩形/椭圆/直线 + 虚线/线宽/颜色/撤销）
  const annotationTool = useAnnotationStore((s) => s.tool)
  const annotationStyle = useAnnotationStore((s) => s.style)
  const annotationShapes = useAnnotationStore((s) => (activeImage ? s.shapesByImage[activeImage.key] ?? NO_SHAPES : NO_SHAPES))
  const selectedId = useAnnotationStore((s) => s.selectedId)
  const setSelectedId = useAnnotationStore((s) => s.setSelectedId)
  const commitShape = useAnnotationStore((s) => s.commitShape)
  const undoAnnotation = useAnnotationStore((s) => s.undo)
  const removeSelected = useAnnotationStore((s) => s.removeSelected)
  const setTool = useAnnotationStore((s) => s.setTool)
  const redoAnnotation = useAnnotationStore((s) => s.redo)
  const removeShape = useAnnotationStore((s) => s.removeShape)
  const setColor = useAnnotationStore((s) => s.setColor)
  const setPickSample = useAnnotationStore((s) => s.setPickSample)
  const lockPick = useAnnotationStore((s) => s.lockPick)
  const textDraft = useAnnotationStore((s) => s.textDraft)
  const setTextDraft = useAnnotationStore((s) => s.setTextDraft)
  const consumeBadgeSeq = useAnnotationStore((s) => s.consumeBadgeSeq)
  const applyMaskImage = useAnnotationStore((s) => s.applyMaskImage)
  const updateShape = useAnnotationStore((s) => s.updateShape)
  const annotationPanelOpen = useAnnotationStore((s) => s.panelOpen)
  const setPanelOpen = useAnnotationStore((s) => s.setPanelOpen)

  useEffect(() => {
    if (!activeImageKey && images[0]) setActiveImageKey(images[0].key)
  }, [images, activeImageKey, setActiveImageKey])

  useEffect(() => {
    if (!containerRef.current) return
    const ro = new ResizeObserver((entries) => {
      const { width, height } = entries[0].contentRect
      setSize({ width, height })
    })
    ro.observe(containerRef.current)
    return () => ro.disconnect()
  }, [])

  const fitToWindow = useCallback(() => {
    if (!image || !activeImage) return
    const scale = Math.min(size.width / image.width, size.height / image.height)
    const x = (size.width - image.width * scale) / 2
    const y = (size.height - image.height * scale) / 2
    updateView(activeImage.key, { scale, x, y })
  }, [image, activeImage, size, updateView])

  // 首次加载/换图后自适应窗口
  useEffect(() => {
    if (image && activeImage && !views[activeImage.key]) fitToWindow()
  }, [image, activeImage, views, fitToWindow])

  const clampScale = (s: number) => Math.min(MAX_SCALE, Math.max(MIN_SCALE, s))

  function handleWheel(e: Konva.KonvaEventObject<WheelEvent>) {
    e.evt.preventDefault()
    if (!activeImage) return
    const stage = stageRef.current
    if (!stage) return
    const pointer = stage.getPointerPosition()
    if (!pointer) return
    const oldScale = view.scale
    const direction = e.evt.deltaY > 0 ? 0.9 : 1.1
    const newScale = clampScale(oldScale * direction)
    const scaleRatio = newScale / oldScale
    const newX = pointer.x - (pointer.x - view.x) * scaleRatio
    const newY = pointer.y - (pointer.y - view.y) * scaleRatio
    updateView(activeImage.key, { scale: newScale, x: newX, y: newY })
  }

  function handleKeyDown(e: React.KeyboardEvent) {
    // 输入控件（文本标注、线宽、hex 颜色）内按键不触发查看器快捷键
    const target = e.target as HTMLElement | null
    if (target && (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA' || target.isContentEditable)) return
    if (!activeImage) return
    const STEP = 40
    const v = views[activeImage.key] ?? { scale: 1, x: 0, y: 0 }
    const key = e.key.toLowerCase()
    if (key === 'w') updateView(activeImage.key, { ...v, y: v.y + STEP })
    else if (key === 's') updateView(activeImage.key, { ...v, y: v.y - STEP })
    else if (key === 'a') updateView(activeImage.key, { ...v, x: v.x + STEP })
    else if (key === 'd') updateView(activeImage.key, { ...v, x: v.x - STEP })
    else if (key === '+' || key === '=') updateView(activeImage.key, { ...v, scale: clampScale(v.scale * 1.1) })
    else if (key === '-') updateView(activeImage.key, { ...v, scale: clampScale(v.scale / 1.1) })
    else if (key === 'f') fitToWindow()
    else if (key === ' ') { e.preventDefault(); onLayerSwitch() }
    else if (key === '1') {
      if (!image) return
      updateView(activeImage.key, { scale: 1, x: (size.width - image.width) / 2, y: (size.height - image.height) / 2 })
    }
    // ---- 标注快捷键 ----
    else if (key === 'delete' || key === 'backspace') {
      if (!annotationTool) {
        e.preventDefault()
        removeSelected(activeImage.key)
      }
    }
    else if (key === 'z' && (e.ctrlKey || e.metaKey)) {
      if (!annotationTool) {
        e.preventDefault()
        if (e.shiftKey) redoAnnotation(activeImage.key)
        else undoAnnotation(activeImage.key)
      }
    }
    else if (key === 'y' && (e.ctrlKey || e.metaKey)) {
      if (!annotationTool) {
        e.preventDefault()
        redoAnnotation(activeImage.key)
      }
    }
    else if (key === 'c') setTool(annotationTool === 'picker' ? null : 'picker')
    else if (key === 'escape') {
      if (textDraft) setTextDraft(null)
      else if (defectEditActive && editTool === 'add') setEditTool('select')
      else setTool(null)
    }
  }

  function handleDragEnd(e: Konva.KonvaEventObject<DragEvent>) {
    if (!activeImage) return
    /* Konva 的 dragend 会从任意被拖节点冒泡到 Stage（形状的拖动结束也会触发这里），
       若用 e.target 的坐标写 view，拖形状时图片会被「带回」形状的坐标。
       只有舞台自身拖动结束才更新视图 */
    if (e.target !== stageRef.current) return
    updateView(activeImage.key, { ...view, x: e.target.x(), y: e.target.y() })
  }

  return (
    <div
      ref={containerRef}
      tabIndex={0}
      onKeyDown={handleKeyDown}
      style={{ outline: 'none', width: '100%', height: '100%', position: 'relative', cursor: annotationTool === 'eraser' ? 'cell' : annotationTool || (defectEditActive && editTool === 'add') ? 'crosshair' : undefined }}
      className="viewer"
    >
      {/* 玻璃浮层列（同一可拖动容器）：顶部工具栏 + 标注面板。拖动把手（data-drag-handle）
          会整体移动，标注面板始终在工具栏正下方、间隙 8px */}
      <div
        {...toolbar.handlers}
        style={{
          ...toolbar.style,
          position: 'absolute', top: 8, left: 8, zIndex: 10,
          display: 'flex', flexDirection: 'column', alignItems: 'flex-start', gap: 8,
          maxWidth: 'calc(100% - 16px)',
          transform: `translate(${toolbar.offset.x}px, ${toolbar.offset.y}px)`,
        }}
      >
        <div className="glass-float" style={{ display: 'flex', gap: 4, borderRadius: 10, padding: '4px 8px' }}>
          <Space size={4}>
            <Tooltip title="放大 (+)"><Button size="small" type="text" icon={<ZoomInOutlined />} onClick={() => { if (activeImage) updateView(activeImage.key, { ...view, scale: clampScale(view.scale * 1.2) }) }} /></Tooltip>
            <Tooltip title="缩小 (-)"><Button size="small" type="text" icon={<ZoomOutOutlined />} onClick={() => { if (activeImage) updateView(activeImage.key, { ...view, scale: clampScale(view.scale / 1.2) }) }} /></Tooltip>
            <Tooltip title="适应窗口 (F)"><Button size="small" type="text" icon={<ExpandOutlined />} onClick={fitToWindow} /></Tooltip>
            <Tooltip title="1:1 原始尺寸 (1)"><Button size="small" type="text" icon={<OneToOneOutlined />} onClick={() => { if (image && activeImage) updateView(activeImage.key, { scale: 1, x: (size.width - image.width) / 2, y: (size.height - image.height) / 2 }) }} /></Tooltip>
            <Tooltip title="切换图层 (Space)"><Button size="small" type="text" icon={<PictureOutlined />} onClick={onLayerSwitch}>{layerLabel}</Button></Tooltip>
            <Tooltip title={annotationPanelOpen ? '隐藏标注面板' : '显示标注面板'}>
              <Button
                size="small"
                type={annotationPanelOpen ? 'primary' : 'text'}
                aria-label="toggle-annotation-panel"
                aria-pressed={annotationPanelOpen}
                icon={<IconPanelToggle />}
                onClick={() => setPanelOpen(!annotationPanelOpen)}
              />
            </Tooltip>
            <Tooltip title="图层信息">
              {/* data-drag-handle：悬浮其上可拖动整条工具栏（含下方标注面板），百分比随缩放实时变化 */}
              <span data-drag-handle className="drag-handle" style={{ userSelect: 'none' }}>
                {layerIsArtifact ? 'RAW' : 'ANNOTATED'} / {Math.round(view.scale * 100)}%
              </span>
            </Tooltip>
          </Space>
        </div>

        {/* 标注面板：与工具栏同容器，始终在其正下方；显隐由右侧开关控制 */}
        <AnnotationPanel />
      </div>

      {failed && (
        <div style={{ position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--danger)', zIndex: 5 }}>
          图像解码失败，请切换标注图或检查文件
        </div>
      )}

      {!image && !failed && (
        <div style={{ position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 5 }}>
          <Spin tip="图像加载中" />
        </div>
      )}

      <Stage
        ref={stageRef}
        width={size.width}
        height={size.height}
        scaleX={view.scale}
        scaleY={view.scale}
        x={view.x}
        y={view.y}
        draggable={annotationTool === null && !(defectEditActive && editTool === 'add')}
        onWheel={handleWheel}
        onDragEnd={handleDragEnd}
      >
        <Layer>
          {image && <KonvaImage image={image} />}
          {/* 多图 bbox 混叠修复：缺陷框按当前激活图过滤（image_name 溯源），
              单图/存量数据不过滤——别图的框绝不画到本图上 */}
          {showDefects && !defectEditActive && <DefectOverlay defects={activeDefects} onSelect={onSelectDefect} interactive={annotationTool === null} />}
          {defectEditActive && (
            <DefectEditLayer
              defects={defectsForImage(editDraft, activeImage?.src, images[0]?.src)}
              selectedId={editSelectedId}
              addMode={editTool === 'add'}
              imageSize={image ? { width: image.width, height: image.height } : null}
              getStageTransform={() => ({ scale: view.scale, x: view.x, y: view.y })}
              onSelect={setEditSelectedId}
              onCommitNew={(bbox) => addDefect(bbox, activeImageFileName)}
              onGeometryChange={(id, bbox) => moveDefect(id, bbox)}
            />
          )}
          {activeImage?.key && (
            <AnnotationLayer
              imageKey={activeImage.key}
              imageSize={image ? { width: image.width, height: image.height } : null}
              stageSize={size}
              shapes={annotationShapes}
              tool={annotationTool}
              style={annotationStyle}
              selectedId={selectedId}
              getStageTransform={() => ({
                scale: view.scale,
                x: view.x,
                y: view.y,
              })}
              onCommit={(shape) => {
                if (!activeImage) return
                commitShape(activeImage.key, shape)
                /* 拖拽类工具绘制提交后自动回到浏览模式（Snipaste 行为）：形状立即可 hover 拖动/选中拉伸。
                   badge / text 保持激活以便连续放置（再点一次工具或 Esc 退出） */
                if (shape.tool !== 'badge' && shape.tool !== 'text') setTool(null)
                // 脱敏形状：先落占位遮罩，再回填处理后的位图
                if (MASK_TOOLS.includes(shape.tool)) {
                  const dataUrl = buildMaskDataUrl(pixelSource, shape.start, shape.end, shape.tool as MaskKind, view.scale)
                  if (dataUrl) applyMaskImage(activeImage.key, shape.id, dataUrl)
                }
              }}
              onUpdateShape={(shape) => {
                if (!activeImage) return
                updateShape(activeImage.key, shape.id, shape)
              }}
              onMaskRegionChange={(id, start, end) => {
                if (!activeImage || !pixelSource) return
                const shape = annotationShapes.find((sh) => sh.id === id)
                if (!shape) return
                const dataUrl = buildMaskDataUrl(pixelSource, start, end, shape.tool as MaskKind, view.scale)
                if (dataUrl) applyMaskImage(activeImage.key, id, dataUrl)
              }}
              onSelect={setSelectedId}
              onErase={(id) => removeShape(activeImage.key, id)}
              onStartText={(p) => setTextDraft(p)}
              onPickPoint={(p) => {
                if (!p || !pixelSource) return
                const px = samplePixel(pixelSource, { x: p.nx, y: p.ny })
                if (!px) return
                setPickSample({
                  screenX: p.sx, screenY: p.sy, nx: p.nx, ny: p.ny,
                  hex: rgbToHex(px), rgb: rgbString(px), hsl: hslString(rgbToHsl(px)),
                })
              }}
              onPickCommit={(p) => {
                if (!pixelSource) return
                const px = samplePixel(pixelSource, p)
                if (!px) return
                const hex = rgbToHex(px)
                const rgb = rgbString(px)
                const hsl = hslString(rgbToHsl(px))
                /* store setter 只接受值、不接受 updater 函数——先读当前采样再合并 */
                const prevSample = useAnnotationStore.getState().pickSample
                if (prevSample) {
                  setPickSample({ ...prevSample, hex, rgb, hsl })
                }
                lockPick({ hex, rgb, hsl })
                setColor(hex)
              }}
              nextBadgeSeq={() => consumeBadgeSeq(activeImage.key)}
            />
          )}
        </Layer>
      </Stage>

      {/* 文本标注输入浮层（锚定图像自然坐标，视口位置随缩放平移同步） */}
      <TextInputOverlay
        viewport={textDraft ? imageToViewport(textDraft, { scale: view.scale, x: view.x, y: view.y }) : null}
        onSubmit={(text) => {
          if (!textDraft || !activeImage) return
          setTextDraft(null)
          commitShape(activeImage.key, {
            id: nextAnnotationId(),
            imageKey: activeImage.key,
            tool: 'text',
            start: textDraft,
            end: textDraft,
            color: annotationStyle.color,
            strokeWidth: annotationStyle.strokeWidth,
            dashed: false,
            text,
          })
        }}
        onCancel={() => setTextDraft(null)}
      />

      {/* 取色器：放大镜预览 + HEX/RGB/HSL 一键复制 */}
      {annotationTool === 'picker' && pixelSource && <ColorPickPanel source={pixelSource} />}

      {/* FilmStrip 缩略图条 */}
      {images.length > 1 && (
        <div className="glass-float" {...filmstrip.handlers} style={{ ...filmstrip.style, position: 'absolute', bottom: 8, left: '50%', transform: `translateX(-50%) translate(${filmstrip.offset.x}px, ${filmstrip.offset.y}px)`, zIndex: 10, borderRadius: 10, padding: 6, display: 'flex', gap: 6 }}>
          {images.map((img) => (
            <div
              key={img.key}
              onClick={() => setActiveImageKey(img.key)}
              style={{
                width: 64, height: 48, cursor: 'pointer', overflow: 'hidden', borderRadius: 6,
                border: img.key === (activeImage?.key ?? images[0].key) ? '2px solid var(--accent)' : '2px solid transparent',
                background: '#05070f',
              }}
            >
              {/* draggable=false：图片默认可被浏览器原生拖拽，按下+轻微移动即触发
                  dragstart → pointercancel，整个点击手势被取消、onClick 永不派发
                  （缩略图点不动的真凶）；CSS 侧 -webkit-user-drag 双重兜底 */}
              <img src={img.src} alt="" draggable={false} style={{ width: '100%', height: '100%', objectFit: 'cover', userSelect: 'none' }} />
            </div>
          ))}
        </div>
      )}
    </div>
  )
}