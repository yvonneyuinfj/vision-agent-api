import { useEffect, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import rehypeRaw from 'rehype-raw'
import { buildImageUrl, downloadDocFile, fetchDocChunks, type DocChunk } from '../../api/kb'
import type { HitGroup } from './format'
import { highlightMarkdown } from './format'

interface ChunkPreviewProps {
  group: HitGroup
  query: string
}

type LoadState = 'loading' | 'ok' | 'error'

const NEIGHBOR = 2

function windowChunks(chunks: DocChunk[], centerId: string): DocChunk[] {
  const center = chunks.findIndex((c) => c.chunk_id === centerId)
  if (center < 0) return chunks.slice(0, NEIGHBOR * 2 + 1) // 中心丢失则取前 5 段兜底（代码侧裁定，见 report）
  const start = Math.max(0, center - NEIGHBOR)
  return chunks.slice(start, center + NEIGHBOR + 1)
}

export function ChunkPreview({ group, query }: ChunkPreviewProps) {
  const [state, setState] = useState<LoadState>('loading')
  const [chunks, setChunks] = useState<DocChunk[]>([])
  const [retry, setRetry] = useState(0)
  const [imgFailed, setImgFailed] = useState<string | null>(null)
  const [dlError, setDlError] = useState<string | null>(null)

  const { datasetId, documentId, docName, hits } = group
  const centerChunkId = hits[0].chunk_id

  useEffect(() => {
    if (!datasetId || !documentId) return
    let cancelled = false
    setState('loading')
    setImgFailed(null)
    setDlError(null)
    fetchDocChunks(datasetId, documentId)
      .then((doc) => {
        if (cancelled) return
        setChunks(doc.chunks)
        setState('ok')
      })
      .catch(() => {
        if (cancelled) return
        setState('error') // 预览独立降级，不拖垮结果列表
      })
    return () => { cancelled = true }
  }, [datasetId, documentId, retry])

  if (state === 'loading') return <div className="kb-preview__loading">正在加载原文…</div>
  if (state === 'error') {
    return (
      <div className="kb-preview__error">
        <p>预览加载失败，请稍后重试</p>
        <button type="button" onClick={() => setRetry((n) => n + 1)}>重试</button>
      </div>
    )
  }

  const visible = windowChunks(chunks, centerChunkId)
  return (
    <div className="kb-preview">
      <header className="kb-preview__head">
        <h3>{docName}</h3>
        <button
          type="button"
          className="kb-preview__download"
          onClick={() => {
            if (!datasetId || !documentId) return
            setDlError(null)
            downloadDocFile(datasetId, documentId, docName)
              .catch(() => setDlError('下载失败，请重试')) // catch 吞掉，内联行展示，不让 Promise 悬空
          }}
        >
          下载 PDF
        </button>
      </header>
      {dlError && <p className="kb-preview__errline">{dlError}</p>}
      <ol className="kb-preview__chunks">
        {visible.map((chunk) => {
          const isHit = chunk.chunk_id === centerChunkId
          return (
            <li key={chunk.chunk_id} className={`chunk-item${isHit ? ' is-hit' : ''}`}>
              <span className="chunk-item__label">
                chunk {chunk.chunk_id}
                {isHit ? ' · 命中段落' : ''}
              </span>
              <div className="chunk-item__content">
                <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeRaw]}>
                  {highlightMarkdown(chunk.content, query)}
                </ReactMarkdown>
              </div>
              {isHit && chunk.image_id && (
                imgFailed === chunk.image_id ? (
                  <span className="chunk-item__img-placeholder">图片不可用</span>
                ) : (
                  <img
                    className="chunk-item__img"
                    src={buildImageUrl(chunk.image_id)}
                    alt={`${docName} 示意图`}
                    onError={() => setImgFailed(chunk.image_id ?? null)}
                  />
                )
              )}
            </li>
          )
        })}
      </ol>
    </div>
  )
}