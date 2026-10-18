/* 缺陷修订编辑层（Konva，决策 Q1/Q8）：选中/拖动/Transformer 缩放已有框，
   add 模式下在图上拖拽画新增漏检框。与自由笔迹标注层（AnnotationLayer）
   图层独立、互不提交——笔迹纯内存，缺陷修订随复核提交。
   坐标系与 DefectOverlay 一致：图像自然坐标，随 Stage 变换缩放平移。 */

import { useEffect, useRef, useState } from 'react'
import { Rect, Text, Transformer } from 'react-konva'
import type Konva from 'konva'
import type { DefectItem } from '../api/types'
import type { BBox4 } from './bbox'
import { drawableBBox, SEVERITY_COLORS } from './bbox'
import { viewportToImage, normalizeCorners, isDraftTooSmall } from './annotation/annotationGeometry'
import type { StageTransform } from './annotation/annotationGeometry'

/** 排除项灰（对齐 ReviewImageView）、人工新增绿、画框/选中主题蓝 */
const REJECTED_COLOR = '#9aa0a6'
const MANUAL_COLOR = '#49aa19'
const ADD_COLOR = '#1677ff'

interface Props {
  /** 修订草稿列表（useDefectEditStore.draft） */
  defects: DefectItem[]
  selectedId: string | null
  /** 新增漏检框模式：空白拖拽画框，已有框让出手势 */
  addMode: boolean
  imageSize: { width: number; height: number } | null
  getStageTransform: () => StageTransform
  onSelect: (id: string | null) => void
  onCommitNew: (bbox: BBox4) => void
  /** 拖动/缩放后的几何写回（moveDefect：模型条目自动落 revised 原值快照） */
  onGeometryChange: (id: string, bbox: BBox4) => void
}

interface DraftBox {
  start: { x: number; y: number }
  end: { x: number; y: number }
}

export default function DefectEditLayer({
  defects,
  selectedId,
  addMode,
  imageSize,
  getStageTransform,
  onSelect,
  onCommitNew,
  onGeometryChange,
}: Props) {
  /* 画框手势：ref 为同步真相（高频 move 下 up 事件闭包可能滞后一拍，
     与 AnnotationLayer 同策略），state 仅驱动预览渲染 */
  const [draft, setDraft] = useState<DraftBox | null>(null)
  const draftRef = useRef<DraftBox | null>(null)
  const trRef = useRef<Konva.Transformer>(null)
  /** defect id → Konva 节点（Transformer 挂载目标） */
  const nodeRefs = useRef(new Map<string, Konva.Rect>())

  const selected = defects.find((d) => d.defect_id === selectedId) ?? null
  const selectedRejected = Boolean(selected?.human_rejected)

  // Transformer 挂载：选中且未被排除的框显示 8 向手柄（排除项只读展示）
  useEffect(() => {
    const tr = trRef.current
    if (!tr) return
    const node = selected && !selectedRejected ? nodeRefs.current.get(selected.defect_id ?? '') : undefined
    tr.nodes(node ? [node] : [])
    tr.getLayer()?.batchDraw()
  }, [selected, selectedRejected, defects])

  function toImage(p: { x: number; y: number }) {
    return viewportToImage(p, getStageTransform())
  }

  function pointerOf(e: Konva.KonvaEventObject<MouseEvent | TouchEvent>) {
    const stage = e.target.getStage()
    return stage?.getPointerPosition() ?? null
  }

  function handleDown(e: Konva.KonvaEventObject<MouseEvent | TouchEvent>) {
    const pointer = pointerOf(e)
    if (!pointer) return
    if (!addMode) {
      // 浏览态点空白 = 取消选中
      onSelect(null)
      return
    }
    const p = toImage(pointer)
    draftRef.current = { start: p, end: p }
    setDraft(draftRef.current)
  }

  function handleMove(e: Konva.KonvaEventObject<MouseEvent | TouchEvent>) {
    if (!draftRef.current) return
    const pointer = pointerOf(e)
    if (!pointer) return
    draftRef.current = { ...draftRef.current, end: toImage(pointer) }
    setDraft(draftRef.current)
  }

  function handleUp() {
    const d = draftRef.current
    draftRef.current = null
    setDraft(null)
    if (!d || isDraftTooSmall(d.start, d.end)) return
    const { start, end } = normalizeCorners(d.start, d.end)
    onCommitNew([start.x, start.y, end.x, end.y])
  }

  const captureW = imageSize?.width ?? 10000
  const captureH = imageSize?.height ?? 10000

  return (
    <>
      {/* 捕获层：铺满图像自然尺寸，承接画框手势与空白点击（置于框下层，
          已有框在浏览态优先命中；add 态框不 listening，手势全部落到捕获层） */}
      <Rect
        x={0}
        y={0}
        width={captureW}
        height={captureH}
        fill="transparent"
        onMouseDown={handleDown}
        onTouchStart={handleDown}
        onMouseMove={handleMove}
        onTouchMove={handleMove}
        onMouseUp={handleUp}
        onTouchEnd={handleUp}
      />

      {defects.map((d, i) => {
        const bbox = drawableBBox(d)
        if (!bbox) return null
        const id = d.defect_id ?? `__idx_${i}`
        const [x1, y1, x2, y2] = bbox
        const rejected = Boolean(d.human_rejected)
        const manual = d.source === 'manual'
        const color = rejected
          ? REJECTED_COLOR
          : manual
            ? MANUAL_COLOR
            : SEVERITY_COLORS[d.severity ?? 'low'] ?? '#6ba3ff'
        const w = Math.max(1, x2 - x1)
        const h = Math.max(1, y2 - y1)
        return (
          <Rect
            key={id}
            ref={(node) => {
              if (node) nodeRefs.current.set(id, node)
              else nodeRefs.current.delete(id)
            }}
            x={x1}
            y={y1}
            width={w}
            height={h}
            stroke={color}
            strokeWidth={selectedId === id ? 3 : 2}
            strokeScaleEnabled={false}
            dash={rejected ? [6, 3] : undefined}
            opacity={rejected ? 0.8 : 1}
            draggable={!addMode && !rejected}
            listening={!addMode}
            onClick={() => onSelect(id)}
            onTap={() => onSelect(id)}
            onDragEnd={(e) => {
              const node = e.target
              onGeometryChange(id, [node.x(), node.y(), node.x() + w, node.y() + h])
            }}
          />
        )
      })}

      {/* 标签层独立于拖动框：文字不参与 Transformer 缩放 */}
      {defects.map((d, i) => {
        const bbox = drawableBBox(d)
        if (!bbox) return null
        const rejected = Boolean(d.human_rejected)
        const manual = d.source === 'manual'
        const color = rejected
          ? REJECTED_COLOR
          : manual
            ? MANUAL_COLOR
            : SEVERITY_COLORS[d.severity ?? 'low'] ?? '#6ba3ff'
        const label = `A${i + 1}${rejected ? ' 已排除' : manual ? ' 人工' : ''}`
        return (
          <Text
            key={`t-${d.defect_id ?? i}`}
            x={bbox[0]}
            y={Math.max(0, bbox[1] - 18)}
            text={label}
            fontSize={13}
            fill={color}
            stroke="#0b1020"
            strokeWidth={2}
            fillAfterStrokeEnabled
            listening={false}
          />
        )
      })}

      {/* 画框预览 */}
      {draft &&
        (() => {
          const { start, end } = normalizeCorners(draft.start, draft.end)
          return (
            <Rect
              x={start.x}
              y={start.y}
              width={Math.max(0, end.x - start.x)}
              height={Math.max(0, end.y - start.y)}
              stroke={ADD_COLOR}
              strokeWidth={2}
              strokeScaleEnabled={false}
              dash={[6, 3]}
              listening={false}
            />
          )
        })()}

      <Transformer
        ref={trRef}
        rotateEnabled={false}
        keepRatio={false}
        anchorSize={8}
        borderStroke={ADD_COLOR}
        anchorStroke={ADD_COLOR}
        anchorFill="#ffffff"
        onTransformEnd={(e) => {
          if (!selected?.defect_id) return
          const node = e.target as Konva.Rect
          const scaleX = node.scaleX()
          const scaleY = node.scaleY()
          const w = Math.max(4, node.width() * scaleX)
          const h = Math.max(4, node.height() * scaleY)
          // 归零 Transformer 写入的 scale：几何真相始终落在 bbox 上
          node.scaleX(1)
          node.scaleY(1)
          onGeometryChange(selected.defect_id, [node.x(), node.y(), node.x() + w, node.y() + h])
        }}
      />
    </>
  )
}
