import type { DefectItem } from '../api/types'

/** 缺陷条目是否携带可绘制 bbox（平台契约 bbox，或存量 FPI 数据的 bbox_xyxy） */
export function hasDrawableDefectBoxes(defects: DefectItem[]): boolean {
  return defects.some((d) => {
    const loc = d.location
    if (!loc) return false
    const bbox = loc.bbox ?? loc.bbox_xyxy
    const typeOk = loc.type === undefined || loc.type === 'bbox'
    return typeOk && Array.isArray(bbox) && bbox.length === 4
  })
}

/** 「算法标注图」层图像源决策。
 *
 * 条目携带可绘制 bbox → 返回 null（维持原图 + Konva 绘框：可点击联动缺陷卡）；
 * 条目无 bbox（agent 轨常见：智能体提交条目缺 location）而模型标注产物存在 →
 * 返回产物 URI，直接呈现检测模型自己渲染的标注图。
 * 多图任务的 annotatedSrc 由调用方按当前激活图配对（fan-out __<stem>
 * 后缀，见 Viewer 内 annotatedArtifactForImage）——配得上就呈现本图产物，
 * 配不上返回 null 不猜测。 */
export function resolveAnnotatedLayerSrc(opts: {
  annotatedSrc?: string | null
  showDefects?: boolean
  imageCount: number
  defects: DefectItem[]
  /** 多图任务按图配对的产物形态（区别于单图任务的单值 annotatedSrc 透传） */
  perImage?: boolean
}): string | null {
  const { annotatedSrc, showDefects, imageCount, defects, perImage } = opts
  if (!annotatedSrc || !showDefects) return null
  if (imageCount !== 1 && !perImage) return null
  return hasDrawableDefectBoxes(defects) ? null : annotatedSrc
}
