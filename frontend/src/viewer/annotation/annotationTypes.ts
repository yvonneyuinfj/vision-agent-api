/* 人工标注：类型与常量（图像自然坐标，随 Konva Stage 变换自动缩放平移）。
   eraser / picker 是模式工具，不落成形状，从 AnnotationShape['tool'] 中排除。 */

import { ANTD_THEME } from '../../constants/theme'

export type AnnotationTool =
  | 'arrow' | 'rect' | 'ellipse' | 'line'
  | 'pencil' | 'marker'
  | 'text' | 'badge'
  | 'mosaic' | 'blur'
  | 'eraser' | 'picker'

export type ShapeTool = Exclude<AnnotationTool, 'eraser' | 'picker'>

/** 自由手绘类：移动轨迹折线 polyline */
export const FREEFORM_TOOLS: readonly AnnotationTool[] = ['pencil', 'marker']
/** 脱敏类：拖拽矩形区域后生成位图遮盖 */
export const MASK_TOOLS: readonly AnnotationTool[] = ['mosaic', 'blur']

/** 可拉伸的形状（text/badge 仅可拖动，不参与拉伸） */
export function canResizeShape(tool: ShapeTool): boolean {
  return tool !== 'text' && tool !== 'badge'
}

export interface AnnotationPoint {
  x: number
  y: number
}

export interface AnnotationShape {
  id: string
  /** 归属图像（FilmStrip 每图独立一套批注） */
  imageKey: string
  tool: ShapeTool
  start: AnnotationPoint
  end: AnnotationPoint
  color: string
  strokeWidth: number
  dashed: boolean
  /** 自由线（铅笔/马克笔）：图像坐标折线 [x1,y1,x2,y2,...] */
  polyline?: number[]
  /** 文本内容（text 工具） */
  text?: string
  /** 序号标记编号（badge 工具；分组由工具栏重置计数） */
  seq?: number
  /** 脱敏位图 dataURL（mosaic/blur 提交后异步填上，之前显示占位遮罩） */
  imageUrl?: string
}

export interface AnnotationStyle {
  color: string
  strokeWidth: number
  dashed: boolean
}

/** 标注色板 key（运行时按明暗主题解析为 hex，canvas 无法解析 var(--...)） */
export const ANNOTATION_COLOR_KEYS = ['danger', 'warn', 'accent'] as const
export type AnnotationColorKey = (typeof ANNOTATION_COLOR_KEYS)[number]

export const ANNOTATION_WIDTHS = [2, 4, 6] as const

/** 自定义线宽输入范围（px） */
export const ANNOTATION_WIDTH_LIMIT = { min: 1, max: 60 } as const

/** 明暗主题下与 base.css token 视觉等价的标注色（canvas 无法解析 var(--...)，直接取 theme token 的 hex 近似值） */
export const ANNOTATION_COLORS: Record<'dark' | 'light', Record<AnnotationColorKey, string>> = {
  dark: {
    danger: ANTD_THEME.dark.token!.colorError!,
    warn: ANTD_THEME.dark.token!.colorWarning!,
    accent: ANTD_THEME.dark.token!.colorPrimary!,
  },
  light: {
    danger: ANTD_THEME.light.token!.colorError!,
    warn: ANTD_THEME.light.token!.colorWarning!,
    accent: ANTD_THEME.light.token!.colorPrimary!,
  },
}

/** 马克笔：半透明荧光，描边按用户线宽放大 */
export const MARKER_OPACITY = 0.45
export const MARKER_WIDTH_MULTIPLIER = 2.5
export const MARKER_MIN_WIDTH = 10

export function nextAnnotationId(): string {
  return `anno-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 7)}`
}