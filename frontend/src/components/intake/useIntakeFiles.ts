import { useMemo, useState } from 'react'
import { kindOfFile, validateFile, fileSizeText } from './fileValidation'
import { missingLinkage } from './sceneInput'
import type { FileGroup, UploadedFile } from './Dropzone'

/** 送检输入的公共状态：文件分组、格式/大小校验、场景联动齐备性、提交摘要。
 *  新建任务页与各专业工作台共用（决策 11），两处规则因此不会分叉。 */
export function useIntakeFiles(scenes: readonly string[], withLinkage = true) {
  const [files, setFiles] = useState<UploadedFile[]>([])

  const addFiles = (group: FileGroup, list: File[]) =>
    setFiles((prev) => [...prev, ...list.map((file) => ({ file, group }))])

  const removeFile = (item: UploadedFile) => setFiles((prev) => prev.filter((f) => f !== item))

  const resetFiles = () => setFiles([])

  const countOf = (group: FileGroup) => files.filter((f) => f.group === group).length

  const fileErrors = useMemo(
    () => files.map((f) => validateFile(f.file)).filter((e): e is string => e !== null),
    [files],
  )

  const linkageMiss = useMemo(
    () => (withLinkage ? missingLinkage(scenes, countOf) : []),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [scenes, files, withLinkage],
  )

  const summary = useMemo(() => {
    let img = 0, vid = 0, csv = 0, calib = 0, total = 0
    for (const f of files) {
      const kind = kindOfFile(f.file.name)
      total += f.file.size
      if (kind === 'video') vid++
      else if (kind === 'csv') csv++
      else if (kind === 'calib') calib++
      else img++
    }
    const parts: string[] = []
    if (img) parts.push(`${img} 张图像`)
    if (vid) parts.push(`${vid} 段视频`)
    if (csv) parts.push(`${csv} 个 CSV`)
    if (calib) parts.push(`${calib} 个标定文件`)
    return parts.length ? `${parts.join(' + ')} · ${fileSizeText(total)}` : '未上传文件'
  }, [files])

  return { files, addFiles, removeFile, resetFiles, countOf, fileErrors, linkageMiss, summary }
}

export type IntakeFiles = ReturnType<typeof useIntakeFiles>
