import { Rect, Text, Group } from 'react-konva'
import { bboxToRect, SEVERITY_COLORS } from './bbox'
import { useViewerStore } from '../store/useViewerStore'
import type { DefectItem } from '../api/types'

interface Props {
  defects: DefectItem[]
  /** 点击缺陷回调（详情页滚动联动） */
  onSelect: (defectId: string) => void
  /** false = 关闭缺陷框的悬停/点击（标注绘制模式下接管手势） */
  interactive?: boolean
}

export default function DefectOverlay({ defects, onSelect, interactive = true }: Props) {
  const activeDefectId = useViewerStore((s) => s.activeDefectId)
  const setActiveDefectId = useViewerStore((s) => s.setActiveDefectId)

  return (
    <>
      {defects.map((d) => {
        // 契约形态 {type:'bbox', bbox}；兼容存量 FPI 数据（仅 bbox_xyxy，无 type）
        const bbox = d.location?.bbox ?? d.location?.bbox_xyxy
        const locType = d.location?.type
        if (!bbox || (locType !== undefined && locType !== 'bbox')) return null
        const rect = bboxToRect(bbox)
        const color = SEVERITY_COLORS[d.severity ?? 'low'] ?? '#6ba3ff'
        const isActive = d.defect_id === activeDefectId
        const id = d.defect_id ?? `defect-${d.defect_type}-${rect.x}-${rect.y}`
        return (
          <Group key={id} listening={interactive}>
            <Rect
              {...rect}
              stroke={color}
              strokeWidth={isActive ? 4 : 2}
              strokeScaleEnabled={false}
              dash={[6, 3]}
              listening={interactive}
              onClick={() => { setActiveDefectId(id); onSelect(id) }}
              onMouseEnter={() => setActiveDefectId(id)}
              onMouseLeave={() => setActiveDefectId(null)}
            />
            <Text
              x={rect.x}
              y={Math.max(0, rect.y - 18)}
              text={`${d.defect_name_cn || d.defect_type} ${(d.confidence ?? 0).toFixed(2)}`}
              fontSize={13}
              fill={color}
              stroke="#0b1020"
              strokeWidth={2}
              fillAfterStrokeEnabled
              listening={interactive}
            />
          </Group>
        )
      })}
    </>
  )
}