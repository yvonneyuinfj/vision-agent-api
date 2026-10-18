import { useMemo, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import {
  buildDocumentPreviewUrl,
  buildImageUrl,
  type ChatReference,
  type ChatReferenceChunk,
} from '../../api/kb'
import { PanelToggleButton } from './PanelToggleButton'

function chunkId(chunk: ChatReferenceChunk, index: number): string {
  return String(chunk.id ?? chunk.chunk_id ?? index)
}

function docName(chunk: ChatReferenceChunk): string {
  return String(chunk.document_name ?? chunk.doc_name ?? '未命名文档')
}

function docId(chunk: ChatReferenceChunk): string | null {
  const value = chunk.document_id ?? chunk.doc_id
  return value ? String(value) : null
}

export function CitationPanel({
  reference,
  selectedChunkId,
  onSelect,
  collapsed,
  onToggleCollapsed,
  datasets = [],
  datasetsNote = null,
}: {
  reference?: ChatReference
  selectedChunkId: string | null
  onSelect: (id: string | null) => void
  collapsed: boolean
  onToggleCollapsed: () => void
  /** 知识库概况：/ragflow/chat/datasets 的真实名称与文档数 */
  datasets?: { name: string; document_count?: number | null }[]
  /** 数据集清单读取失败时的真实原因（接口未就绪时如实说明，不空表糊过去） */
  datasetsNote?: string | null
}) {
  const chunks = reference?.chunks ?? []
  const [preview, setPreview] = useState<{ id: string; name: string } | null>(null)
  const selected = useMemo(() => {
    const index = chunks.findIndex((item, i) => chunkId(item, i) === selectedChunkId)
    return index >= 0 ? chunks[index] : chunks[0]
  }, [chunks, selectedChunkId])

  return (
    <aside className={`citation-panel${collapsed ? ' is-collapsed' : ''}`}>
      <header className="citation-panel__head">
        <div className="citation-panel__heading">
          <PanelToggleButton
            side="right"
            label="知识引用"
            collapsed={collapsed}
            controls="citation-panel-content"
            onToggle={onToggleCollapsed}
          />
          <div className="citation-panel__title" hidden={collapsed}><strong>知识引用</strong><span>KNOWLEDGE TRACE</span></div>
        </div>
        <span className="tag t-accent" hidden={collapsed}>{chunks.length} 条</span>
      </header>
      <div id="citation-panel-content" className="citation-panel__content" hidden={collapsed}>
        {chunks.length === 0 ? (
          <div className="citation-panel__empty">
            <span>⊙</span>
            <p>回答中的知识引用会在这里完整展开</p>
          </div>
        ) : (
          <div className="citation-panel__body">
            <nav className="citation-tabs" aria-label="引用列表">
              {chunks.map((chunk, index) => {
                const id = chunkId(chunk, index)
                return (
                  <button type="button" key={id} className={selected === chunk ? 'is-active' : ''} title={docName(chunk)} onClick={() => onSelect(id)}>
                    {index + 1}
                  </button>
                )
              })}
            </nav>
            {selected && (() => {
              const index = chunks.indexOf(selected)
              const id = chunkId(selected, index)
              const name = docName(selected)
              const documentId = docId(selected)
              const score = selected.similarity
              const imageId = selected.image_id ? String(selected.image_id) : null
              return (
                <article className="citation-detail" key={id}>
                  <div className="citation-detail__meta">
                    <span>引用 {index + 1}</span>
                    {typeof score === 'number' && <span>相似度 {(score * 100).toFixed(1)}%</span>}
                  </div>
                  <h3>{name}</h3>
                  <div className="citation-detail__content"><ReactMarkdown remarkPlugins={[remarkGfm]}>{String(selected.content ?? '')}</ReactMarkdown></div>
                  {imageId && <img src={buildImageUrl(imageId)} alt={`${name} 引用图片`} />}
                  {Array.isArray(selected.positions) && selected.positions.length > 0 && (
                    <details><summary>原文位置信息</summary><pre>{JSON.stringify(selected.positions, null, 2)}</pre></details>
                  )}
                  <div className="citation-detail__actions">
                    {documentId && (
                      <>
                        <button type="button" className="btn btn-primary btn-sm" onClick={() => setPreview({ id: documentId, name })}>查看原始文档</button>
                        <a className="btn btn-sm" href={buildDocumentPreviewUrl(documentId, name)} download={name}>下载</a>
                      </>
                    )}
                  </div>
                </article>
              )
            })()}
          </div>
        )}

        {/* 原型 /knowledge/search 右栏的两张卡并入引用面板：知识库概况（真实数据集与文档数）+ 无依据时的行为 */}
        <section className="citation-panel__kb">
          <div className="citation-panel__kb-head">
            <strong>知识库概况</strong>
            <span className="hint mono">DATASETS</span>
          </div>
          {datasets.length ? (
            <ul className="citation-panel__kb-list">
              {datasets.map((d) => (
                <li key={d.name}>
                  <span className="grow">{d.name}</span>
                  <b className="mono">
                    {typeof d.document_count === 'number' ? `${d.document_count} 篇` : '—'}
                  </b>
                </li>
              ))}
            </ul>
          ) : (
            <p className="hint">{datasetsNote ?? '数据集清单未返回（/ragflow/chat/datasets 无响应）'}</p>
          )}
          <p className="hint">
            来源 <code className="mono">GET /ragflow/chat/datasets</code>（名称与 document_count）；
            文档级解析 / 索引状态无接口，见资料入库页说明。
          </p>
        </section>

        <div className="citation-panel__policy-box">
          <strong>无依据时的行为</strong>
          <p>
            检索不到依据时，AI 解释区必须显式说明「未找到可引用依据」，
            <b>不得生成肯定结论</b>（方案 §7.3 异常处置）。
          </p>
        </div>
      </div>

      {preview && (
        <div className="doc-preview-modal" role="dialog" aria-modal="true" aria-label={`原文预览 ${preview.name}`}>
          <div className="doc-preview-modal__panel">
            <header><strong>{preview.name}</strong><button type="button" onClick={() => setPreview(null)}>×</button></header>
            <iframe title={preview.name} src={buildDocumentPreviewUrl(preview.id, preview.name)} />
          </div>
        </div>
      )}
    </aside>
  )
}
