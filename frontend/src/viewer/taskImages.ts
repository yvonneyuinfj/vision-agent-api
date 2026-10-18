import { isImageArtifact, pickArtifactImagePair } from '../utils/artifacts'
import type { ArtifactRef, DefectItem } from '../api/types'
import type { ViewerImage } from './Viewer'

/** 由任务详情构造查看器图像列表。
 *
 * 原图 src 直接采用后端契约 input_uris（URI 本身即下载端点
 * `/api/v1/tasks/{id}/files/{name}?kind=inputs`）。历史实现曾臆造
 * `input_${i}` 与 `report.name` 替换出的标注图名，文件名不存在且
 * 无扩展名会被后端路径白名单拒绝，导致查看器恒报「图像解码失败」。
 */
export function buildTaskImages(
  detail: { input_uris?: string[] } | null | undefined,
): ViewerImage[] {
  if (!detail) return []
  return (detail.input_uris ?? []).map((src, i) => ({ key: `input-${i}`, src }))
}

/** 多图任务缺陷-图像配对（2026-09-16 生产事故：多图 bbox 混叠）。

 *  fan-out 合并的缺陷带 image_name 溯源；查看器把缺陷框画到某张输入图
 *  上时必须按图过滤——否则每张图都叠上全部图的框（坐标体系不同，
 *  位置错乱 + 「凭空多出标注」）。
 *
 *  匹配规则：缺陷 image_name == 图 URI 末段文件名。存量数据
 *  （单图任务 / 无 image_name）不过滤，维持全量兼容。
 */
export function defectsForImage(
  defects: DefectItem[],
  imageUri: string | null | undefined,
  firstImageUri?: string | null,
): DefectItem[] {
  if (!imageUri) return defects
  const fileName = imageUri.split('/files/')[1]?.split('?')[0] ?? imageUri.split('/').pop() ?? ''
  if (!fileName) return defects
  const tagged = defects.filter((d) => d.image_name)
  if (tagged.length === 0) return defects // 存量单图数据：无溯源标记，不过滤
  const isFirst =
    !firstImageUri ||
    (firstImageUri.split('/files/')[1]?.split('?')[0] ?? firstImageUri) === fileName
  // 带标记按图精确匹配；无标记条目（智能体手写未注明图名/存量）只归属
  // 第一张图——绝不把不确定归属的框画到每张图上（坐标体系不同必然错位）
  return defects.filter((d) => (d.image_name ? d.image_name === fileName : isFirst))
}

/** 多图任务算法标注图配对：按来源图名（fan-out 后缀 __<stem>）挑当前图
 *  的模型渲染标注图产物；无标记产物时回退第一张（单图行为兼容）。 */
export function annotatedArtifactForImage(
  artifacts: ArtifactRef[] | undefined | null,
  imageUri: string | null | undefined,
): ArtifactRef | null {
  if (!imageUri) return pickArtifactImagePair(artifacts).annotated
  const fileName = imageUri.split('/files/')[1]?.split('?')[0] ?? ''
  const stem = fileName.replace(/\.[^.]+$/, '')
  if (!stem) return pickArtifactImagePair(artifacts).annotated
  const images = (artifacts ?? []).filter(isImageArtifact)
  const lower = (a: ArtifactRef) => (a.name ?? '').toLowerCase()
  const match =
    images.find((a) => lower(a).includes(`__${stem.toLowerCase()}`) && !lower(a).includes('original')) ??
    images.find((a) => lower(a).startsWith(`${stem.toLowerCase()}_annotated`))
  return match ?? pickArtifactImagePair(artifacts).annotated
}
