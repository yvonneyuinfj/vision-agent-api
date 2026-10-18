import type { ArtifactRef } from '../api/types'

/** 产物是否为图片：mime 优先，缺失时按 URI 扩展名兜底（对齐后端 _is_image_artifact） */
const IMG_URI_RE = /\.(jpe?g|png|tif|tiff)(\?|$)/i

export function isImageArtifact(a: ArtifactRef): boolean {
  return (a.mime ?? '').startsWith('image/') || IMG_URI_RE.test(a.uri)
}

function byName(images: ArtifactRef[], ...keys: string[]): ArtifactRef | undefined {
  return images.find((a) => keys.some((k) => a.name.toLowerCase().includes(k)))
}

/** 从报告产物挑（原图, 缺陷标注图）图像对（对齐后端 _pick_compare_images）：
 * 命名优先（original_image / result_image / annotated，覆盖真实工具与 mock 两套
 * 命名），命名不可辨时按出现顺序取前两张图片兜底。 */
export function pickArtifactImagePair(artifacts: ArtifactRef[] | undefined | null): {
  original: ArtifactRef | null
  annotated: ArtifactRef | null
} {
  const images = (artifacts ?? []).filter(isImageArtifact)
  let original = byName(images, 'original_image', 'original') ?? null
  let annotated = byName(images, 'result_image', 'annotated', 'annotation') ?? null
  const rest = images.filter((a) => a !== original && a !== annotated)
  if (!original && rest.length) original = rest.shift() ?? null
  if (!annotated && rest.length) annotated = rest.shift() ?? null
  return { original, annotated }
}
