/* 标注面板：与 Viewer 顶部玻璃工具栏同一可拖动容器（flex 列，间隙 8px），
   始终位于工具栏正下方、随其整体移动。
   显隐由 Viewer 顶部工具栏中「切换图层」后的开关控制（useAnnotationStore.panelOpen），
   图像跟随全局 activeImageKey（FilmStrip 切换联动）。 */

import AnnotationToolbar from './AnnotationToolbar'
import { useAnnotationStore } from './useAnnotationStore'
import { useViewerStore } from '../../store/useViewerStore'

export default function AnnotationPanel() {
  const panelOpen = useAnnotationStore((s) => s.panelOpen)
  const activeImageKey = useViewerStore((s) => s.activeImageKey)

  if (!panelOpen || !activeImageKey) return null
  return (
    <div className="glass-float anno-panel" style={{ padding: '6px 8px', maxWidth: '100%' }}>
      <AnnotationToolbar imageKey={activeImageKey} />
    </div>
  )
}