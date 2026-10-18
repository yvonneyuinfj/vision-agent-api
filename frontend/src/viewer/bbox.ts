export type BBox4 = [number, number, number, number]

export function bboxToRect(bbox: BBox4) {
  return { x: bbox[0], y: bbox[1], width: bbox[2] - bbox[0], height: bbox[3] - bbox[1] }
}

/** 可绘制 bbox 提取（平台契约 bbox / 存量 FPI 数据 bbox_xyxy 兼容，
 *  对齐 DefectOverlay）：ReviewImageView 只读叠加与 DefectEditLayer 编辑共用 */
export function drawableBBox(d: { location?: { type?: string; bbox?: BBox4; bbox_xyxy?: BBox4 } | null }): BBox4 | null {
  const loc = d.location
  if (!loc) return null
  if (loc.type !== undefined && loc.type !== 'bbox') return null
  const bbox = loc.bbox ?? loc.bbox_xyxy
  return Array.isArray(bbox) && bbox.length === 4 ? bbox : null
}

// konva canvas 绘制不解析 CSS var()/oklch，需显式 hex。
// 对齐 DESIGN.md 严重度映射：high→危险红 / medium→警告琥珀 / low→信息蓝（近似 oklch）。
export const SEVERITY_COLORS: Record<string, string> = {
  high: '#f0756b',
  medium: '#f0b75e',
  low: '#6ba3ff',
}