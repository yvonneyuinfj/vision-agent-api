import { useState, type CSSProperties, type ReactNode } from 'react'
import { kindOfFile, validateFile, fileSizeText, type UploadKind } from './fileValidation'

export type FileGroup = 'main' | 'ir' | 'xray-ref' | 'xray-calib'

export interface UploadedFile {
  file: File
  group: FileGroup
}

const FILE_ICONS: Record<UploadKind, ReactNode> = {
  image: (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="var(--accent)" strokeWidth="1.7">
      <rect x="4" y="4" width="16" height="16" rx="2.5" />
      <path d="M4 14.5 9 10l5 4.5 3-2.5 3 2.5" />
    </svg>
  ),
  video: (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="var(--info)" strokeWidth="1.7">
      <rect x="3.5" y="6" width="12" height="12" rx="2.2" />
      <path d="M15.5 10.5 20.5 8v8l-5-2.5" />
    </svg>
  ),
  csv: (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="var(--warn)" strokeWidth="1.7">
      <path d="M6 3h9l4 4v14H6z" />
      <path d="M14 3v4h4" />
    </svg>
  ),
  calib: (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="var(--warn)" strokeWidth="1.7">
      <path d="M6 3h9l4 4v14H6z" />
      <path d="M14 3v4h4" />
    </svg>
  ),
}

const OK_TEXT: Record<UploadKind, string> = {
  image: '格式与大小校验通过',
  video: '时长与码率校验通过',
  csv: '与图像分辨率一致',
  calib: '格式校验通过',
}

export function Dropzone({
  accept, multiple = true, className, style, onFiles, children,
}: {
  accept: string
  multiple?: boolean
  className?: string
  style?: CSSProperties
  onFiles: (files: File[]) => void
  children: ReactNode
}) {
  const [drag, setDrag] = useState(false)
  return (
    <label
      style={style}
      className={className ? `dropzone ${className}${drag ? ' dragover' : ''}` : `dropzone${drag ? ' dragover' : ''}`}
      onDragOver={(e) => { e.preventDefault(); setDrag(true) }}
      onDragLeave={() => setDrag(false)}
      onDrop={(e) => {
        e.preventDefault()
        setDrag(false)
        onFiles(Array.from(e.dataTransfer?.files ?? []))
      }}
    >
      <input
        type="file"
        className="dz-inp"
        accept={accept}
        multiple={multiple}
        hidden
        onChange={(e) => {
          onFiles(Array.from(e.currentTarget.files ?? []))
          e.currentTarget.value = ''
        }}
      />
      {children}
    </label>
  )
}

export function FileRow({ item, onRemove }: { item: UploadedFile; onRemove: () => void }) {
  const kind = kindOfFile(item.file.name)
  const err = kind ? validateFile(item.file) : `不支持的文件格式：${item.file.name}`
  const ext = (item.file.name.split('.').pop() ?? '').toUpperCase()
  return (
    <div className="file-row frow">
      {FILE_ICONS[kind ?? 'image']}
      <div>
        <div className="fname">{item.file.name}</div>
        <div className="fmeta">{ext} · {fileSizeText(item.file.size)}</div>
      </div>
      <span className={err ? 'ok err' : 'ok'}>
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2">
          {err ? (
            <path d="m6 6 12 12M18 6 6 18" />
          ) : (
            <path d="m5 12.5 4.5 4.5L19 7.5" />
          )}
        </svg>
        {err ?? (kind ? OK_TEXT[kind] : '')}
      </span>
      <button className="fdel" title="移除文件" onClick={onRemove}>
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <path d="m6 6 12 12M18 6 6 18" />
        </svg>
      </button>
    </div>
  )
}
