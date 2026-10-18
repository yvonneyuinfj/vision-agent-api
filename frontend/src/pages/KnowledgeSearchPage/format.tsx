import type { ReactNode } from 'react'
import type { SearchHit } from '../../api/kb'

export interface HitGroup {
  key: string
  docName: string
  datasetId: string | null
  documentId: string | null
  hits: SearchHit[]
  topScore: number
}

function groupKey(hit: SearchHit): string {
  return `${hit.dataset_id ?? ''}:${hit.document_id ?? hit.doc_name}`
}

export function groupHits(hits: SearchHit[]): HitGroup[] {
  const buckets = new Map<string, SearchHit[]>()
  for (const hit of hits) {
    const key = groupKey(hit)
    const bucket = buckets.get(key)
    if (bucket) bucket.push(hit)
    else buckets.set(key, [hit])
  }
  return [...buckets.entries()]
    .map(([key, bucket]) => {
      bucket.sort((a, b) => b.score - a.score)
      const first = bucket[0]
      return {
        key,
        docName: first.doc_name,
        datasetId: first.dataset_id,
        documentId: first.document_id,
        hits: bucket,
        topScore: first.score,
      }
    })
    .sort((a, b) => b.topScore - a.topScore)
}

export function highlightSentence(content: string, query: string): ReactNode {
  const terms = query.trim().split(/\s+/).filter(Boolean)
  if (terms.length === 0) return content
  const term = terms[0]
  const index = content.indexOf(term)
  if (index < 0) return content
  const before = content.slice(0, index)
  const after = content.slice(index + term.length)
  return (
    <>
      {before}
      <mark>{term}</mark>
      {after}
    </>
  )
}

/**
 * markdown 预览用高亮：命中处注入 <mark>，其余内容逐字保留（交给 react-markdown 解析）。
 * 与 highlightSentence 的区别：返回 string 而非 ReactNode，保真 markdown 语法与行内 HTML。
 */
export function highlightMarkdown(content: string, query: string): string {
  const terms = query.trim().split(/\s+/).filter(Boolean)
  if (terms.length === 0) return content
  const term = terms[0]
  const index = content.indexOf(term)
  if (index < 0) return content
  const before = content.slice(0, index)
  const after = content.slice(index + term.length)
  return `${before}<mark>${term}</mark>${after}`
}

export function truncateSnippet(content: string, maxLength = 180): string {
  if (content.length <= maxLength) return content
  return `${content.slice(0, maxLength)}…`
}