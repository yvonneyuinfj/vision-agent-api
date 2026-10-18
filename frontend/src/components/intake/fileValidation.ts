export type UploadKind = 'image' | 'video' | 'csv' | 'calib'

const KIND_EXTS: Record<UploadKind, string[]> = {
  image: ['.jpg', '.jpeg', '.png', '.tif', '.tiff'],
  video: ['.mp4', '.mov', '.avi', '.webm'],
  csv: ['.csv'],
  calib: ['.json', '.xml'],
}

const KIND_MAX_MB: Record<UploadKind, number> = {
  image: 50,
  video: 1024,
  csv: 10,
  calib: 10,
}

export const FILE_KIND_LABEL: Record<UploadKind, string> = {
  image: '图像',
  video: '视频',
  csv: 'CSV',
  calib: '标定文件',
}

export function kindOfFile(name: string): UploadKind | null {
  const lower = name.toLowerCase()
  for (const kind of Object.keys(KIND_EXTS) as UploadKind[]) {
    if (KIND_EXTS[kind].some((ext) => lower.endsWith(ext))) return kind
  }
  return null
}

export function maxMBOf(kind: UploadKind): number {
  return KIND_MAX_MB[kind]
}

export function validateFile(file: File): string | null {
  const kind = kindOfFile(file.name)
  if (!kind) return `不支持的文件格式：${file.name}`
  if (file.size > KIND_MAX_MB[kind] * 1024 * 1024) return `超过 ${KIND_MAX_MB[kind]}MB 限制：${file.name}`
  return null
}

export function fileSizeText(sizeBytes: number): string {
  const mb = sizeBytes / 1048576
  return mb >= 1024 ? `${(mb / 1024).toFixed(2)} GB` : `${mb.toFixed(1)} MB`
}