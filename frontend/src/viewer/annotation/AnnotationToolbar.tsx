/* 标注工具栏：两行布局。行 1 = 工具（绘图 / 脱敏 / 橡皮擦 / 取色），
   行 2 = 样式（虚实线、线宽含自定义输入、颜色含 hex 输入）、序号分组、编辑（撤销/重做/删除/清空）。
   全部按钮图标化（内联 SVG，见 annotationIcons），文字含义由 aria-label + Tooltip 承载。 */

import { useEffect, useState } from 'react'
import { Button, Tooltip } from 'antd'
import type { ReactNode } from 'react'
import { useAnnotationStore } from './useAnnotationStore'
import { useTheme } from '../../hooks/useTheme'
import { parseHexInput } from './annotationColor'
import {
  IconArrow, IconBadge, IconBlur, IconClear, IconDashToggle, IconDelete,
  IconEllipse, IconEraser, IconGroupNew, IconLine, IconMarker, IconMosaic, IconPencil,
  IconPicker, IconRect, IconRedo, IconStroke, IconText, IconUndo,
} from './annotationIcons'
import {
  ANNOTATION_COLOR_KEYS,
  ANNOTATION_COLORS,
  ANNOTATION_WIDTHS,
  ANNOTATION_WIDTH_LIMIT,
} from './annotationTypes'
import type { AnnotationTool } from './annotationTypes'
import { clamp } from '../../utils/clamp'

interface Props {
  imageKey: string
}

const TOOLS: Array<{ tool: AnnotationTool; label: string; hint: string; icon: ReactNode }> = [
  { tool: 'rect', label: '矩形', hint: '按下拖拽框选矩形', icon: <IconRect /> },
  { tool: 'ellipse', label: '椭圆', hint: '按下拖拽框选椭圆', icon: <IconEllipse /> },
  { tool: 'line', label: '直线', hint: '按下拖拽画出直线', icon: <IconLine /> },
  { tool: 'arrow', label: '箭头', hint: '按下拖拽画出箭头', icon: <IconArrow /> },
  { tool: 'pencil', label: '铅笔', hint: '自由手绘（平滑折线）', icon: <IconPencil /> },
  { tool: 'marker', label: '马克笔', hint: '半透明荧光笔，适合高亮', icon: <IconMarker /> },
  { tool: 'text', label: '文本', hint: '点击放置文字标注', icon: <IconText /> },
  { tool: 'badge', label: '序号', hint: '点击打序号标记，可分组重新计数', icon: <IconBadge /> },
  { tool: 'mosaic', label: '马赛克', hint: '拖拽框选敏感区域打码', icon: <IconMosaic /> },
  { tool: 'blur', label: '模糊', hint: '拖拽框选区域高斯模糊', icon: <IconBlur /> },
  { tool: 'eraser', label: '橡皮擦', hint: '点击批注直接删除', icon: <IconEraser /> },
  { tool: 'picker', label: '取色 (C)', hint: 'C 键进入：放大镜取色，点击锁定并复制', icon: <IconPicker /> },
]

export default function AnnotationToolbar({ imageKey }: Props) {
  const tool = useAnnotationStore((s) => s.tool)
  const setTool = useAnnotationStore((s) => s.setTool)
  const style = useAnnotationStore((s) => s.style)
  const setColor = useAnnotationStore((s) => s.setColor)
  const setStrokeWidth = useAnnotationStore((s) => s.setStrokeWidth)
  const toggleDashed = useAnnotationStore((s) => s.toggleDashed)
  const undo = useAnnotationStore((s) => s.undo)
  const redo = useAnnotationStore((s) => s.redo)
  const removeSelected = useAnnotationStore((s) => s.removeSelected)
  const clearImage = useAnnotationStore((s) => s.clearImage)
  const resetBadgeSeq = useAnnotationStore((s) => s.resetBadgeSeq)
  const canUndo = useAnnotationStore((s) => (s.undoStacks[imageKey]?.length ?? 0) > 0)
  const canRedo = useAnnotationStore((s) => (s.redoStacks[imageKey]?.length ?? 0) > 0)
  const selectedId = useAnnotationStore((s) => s.selectedId)
  const shapeCount = useAnnotationStore((s) => s.shapesByImage[imageKey]?.length ?? 0)
  const nextBadgeSeq = useAnnotationStore((s) => s.badgeSeqs[imageKey] ?? 1)
  const { theme } = useTheme()

  /* 主题色板：跟随明暗主题（canvas 无法解析 var(--danger)，故用 hex 等价色） */
  const palette = ANNOTATION_COLORS[theme]

  /* 自定义线宽输入（1-60px） */
  const [widthText, setWidthText] = useState(String(style.strokeWidth))
  useEffect(() => setWidthText(String(style.strokeWidth)), [style.strokeWidth])
  function applyWidthText() {
    const n = Math.round(Number(widthText))
    if (!Number.isFinite(n)) {
      setWidthText(String(style.strokeWidth))
      return
    }
    setStrokeWidth(clamp(n, ANNOTATION_WIDTH_LIMIT.min, ANNOTATION_WIDTH_LIMIT.max))
  }

  /* 自定义颜色输入（hex，支持 # 与 3/6 位缩写） */
  const [hexText, setHexText] = useState(style.color)
  useEffect(() => setHexText(style.color), [style.color])
  function applyHexText() {
    const parsed = parseHexInput(hexText)
    if (!parsed) {
      setHexText(style.color)
      return
    }
    setColor(parsed)
  }

  const baseBtn: React.CSSProperties = { fontSize: 12, padding: '0 7px', height: 24, minWidth: 24 }
  const inputStyle: React.CSSProperties = {
    width: 46, height: 24, fontSize: 12, padding: '0 4px',
    color: 'var(--fg)', background: 'var(--surface)',
    border: '1px solid var(--border-strong)', borderRadius: 6, outline: 'none',
  }
  /** 竖分隔线（三处重复：宽度 / 颜色 / 序号前） */
  const dividerStyle: React.CSSProperties = { width: 1, height: 14, background: 'var(--border-strong)', margin: '0 4px' }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
      {/* 行 1：全部工具（图标） */}
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 2, alignItems: 'center' }}>
        {TOOLS.map(({ tool: t, label, hint, icon }) => (
          <Tooltip key={t} title={t === 'badge' ? `${hint}（下一个 ${nextBadgeSeq}）` : hint}>
            <Button
              size="small"
              type={tool === t ? 'primary' : 'text'}
              style={baseBtn}
              aria-label={label}
              icon={icon}
              onClick={() => setTool(tool === t ? null : t)}
            />
          </Tooltip>
        ))}
      </div>

      {/* 行 2：样式 + 序号分组 + 编辑（图标 + 输入框） */}
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 2, alignItems: 'center' }}>
        <Tooltip title={style.dashed ? '切换为实线' : '切换为虚线'}>
          <Button
            size="small" type="text" style={baseBtn}
            aria-label={style.dashed ? '虚线' : '实线'}
            icon={<IconDashToggle dashed={style.dashed} />}
            onClick={toggleDashed}
          />
        </Tooltip>

        <Tooltip title={`线宽 ${style.strokeWidth}px：点击循环 ${ANNOTATION_WIDTHS.join(' → ')}，右侧输入自定义 px`}>
          <Button
            size="small" type="text" style={baseBtn}
            aria-label={`线宽 ${style.strokeWidth}px`}
            icon={<IconStroke />}
            onClick={() => {
              const idx = ANNOTATION_WIDTHS.indexOf(style.strokeWidth as (typeof ANNOTATION_WIDTHS)[number])
              setStrokeWidth(ANNOTATION_WIDTHS[(idx + 1) % ANNOTATION_WIDTHS.length])
            }}
          />
        </Tooltip>
        <input
          type="number"
          aria-label="annotation-width-input"
          min={ANNOTATION_WIDTH_LIMIT.min}
          max={ANNOTATION_WIDTH_LIMIT.max}
          value={widthText}
          style={inputStyle}
          onChange={(e) => setWidthText(e.target.value)}
          onBlur={applyWidthText}
          onKeyDown={(e) => {
            if (e.key === 'Enter') (e.target as HTMLInputElement).blur()
          }}
        />

        <span style={dividerStyle} />

        {ANNOTATION_COLOR_KEYS.map((key) => (
          <Tooltip key={key} title={`颜色 · ${key}`}>
            <button
              type="button"
              aria-label={`color-${key}`}
              onClick={() => setColor(palette[key])}
              style={{
                width: 14, height: 14, borderRadius: '50%', cursor: 'pointer',
                background: palette[key],
                border: style.color === palette[key] ? '2px solid var(--fg)' : '1px solid var(--border-strong)',
                padding: 0,
              }}
            />
          </Tooltip>
        ))}
        <Tooltip title="取色板选择自定义颜色">
          <input
            type="color"
            aria-label="annotation-color-picker"
            value={style.color}
            onChange={(e) => setColor(e.target.value)}
            style={{ width: 22, height: 20, padding: 0, border: '1px solid var(--border-strong)', borderRadius: 6, background: 'transparent', cursor: 'pointer' }}
          />
        </Tooltip>
        <input
          type="text"
          aria-label="annotation-hex-input"
          maxLength={7}
          value={hexText}
          style={{ ...inputStyle, width: 62, fontFamily: 'ui-monospace, Menlo, monospace', textTransform: 'lowercase' }}
          onChange={(e) => setHexText(e.target.value)}
          onBlur={applyHexText}
          onKeyDown={(e) => {
            if (e.key === 'Enter') (e.target as HTMLInputElement).blur()
          }}
          placeholder="#rrggbb"
        />

        <span style={dividerStyle} />

        <Tooltip title={`序号重新从 1 开始（新分组，下一个 ${nextBadgeSeq}）`}>
          <Button size="small" type="text" style={baseBtn} aria-label="新分组" icon={<IconGroupNew />} onClick={() => resetBadgeSeq(imageKey)} />
        </Tooltip>

        <span style={dividerStyle} />

        <Tooltip title="撤销上一次操作（Ctrl/Cmd+Z）">
          <Button size="small" type="text" style={baseBtn} aria-label="撤销" icon={<IconUndo />} disabled={!canUndo} onClick={() => undo(imageKey)} />
        </Tooltip>
        <Tooltip title="重做（Ctrl/Cmd+Shift+Z 或 Ctrl+Y）">
          <Button size="small" type="text" style={baseBtn} aria-label="重做" icon={<IconRedo />} disabled={!canRedo} onClick={() => redo(imageKey)} />
        </Tooltip>
        <Tooltip title="删除选中批注（Del）">
          <Button size="small" type="text" style={baseBtn} aria-label="删除选中" icon={<IconDelete />} disabled={!selectedId} onClick={() => removeSelected(imageKey)} />
        </Tooltip>
        <Tooltip title="清除本图全部批注">
          <Button size="small" type="text" style={baseBtn} aria-label="清除批注" icon={<IconClear />} disabled={shapeCount === 0} onClick={() => clearImage(imageKey)} />
        </Tooltip>
      </div>
    </div>
  )
}