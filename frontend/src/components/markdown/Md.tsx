import ReactMarkdown, { defaultUrlTransform } from 'react-markdown'
import remarkGfm from 'remark-gfm'
import remarkMath from 'remark-math'
import rehypeRaw from 'rehype-raw'
import rehypeKatex from 'rehype-katex'
import 'katex/dist/katex.min.css'
import normalizeMath from './normalizeMath'
import type { KnowledgeItem } from '../../api/types'

interface Props {
  children: string
  citations?: Record<string, KnowledgeItem>
  onCitationClick?: (chunkId: string) => void
}

/** 从 href 中提取 ref:// 协议的 chunk_id，不是则返回 null */
export function parseRefHref(href: string | undefined): string | null {
  if (!href) return null
  if (href.startsWith('ref://')) return decodeURIComponent(href.slice('ref://'.length))
  return null
}

// react-markdown 默认过滤非 http(s) 协议（ref:// 会被改写为空串），
// 放行 ref 协议供引用标记识别，其余交给默认安全过滤
function transformUrl(href: string): string {
  if (/^ref:/i.test(href)) return href
  return defaultUrlTransform(href)
}

/* 线性 SVG：引用角标（替代 emoji 📎）与外部链接箭头 */
const CITATION_GLYPH = (
  <svg width={11} height={11} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
    <path d="M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6Z" />
    <path d="M14.8 9.2 20 4M16 4h4v4" />
  </svg>
)
const EXTERNAL_GLYPH = (
  <svg width={10} height={10} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
    <path d="M14 4h6v6M20 4l-9 9M20 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V5a1 1 0 0 1 1-1h5" />
  </svg>
)

export default function Md({ children, citations = {}, onCitationClick }: Props) {
  // citations 供外部传入引用映射，链接点击时由回调消费，此处保持接口
  void citations
  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm, remarkMath, normalizeMath]}
      // rehype-raw 渲染报告/引用中的原始 HTML（<sub>/<sup> 上下标等）；
      // 须置于首位，remark-math 产出的数学节点才交给 rehype-katex 排版
      rehypePlugins={[rehypeRaw, rehypeKatex]}
      urlTransform={transformUrl}
      components={{
        a: ({ href, children: text }) => {
          const chunkId = parseRefHref(href)
          if (chunkId) {
            return (
              <a
                href={href}
                style={{ color: 'var(--accent)', textDecoration: 'underline', cursor: 'pointer' }}
                onClick={(e) => { e.preventDefault(); onCitationClick?.(chunkId) }}
              >
                {text} {CITATION_GLYPH}
              </a>
            )
          }
          return (
            <a href={href} target="_blank" rel="noreferrer" style={{ color: 'var(--accent)' }}>
              {text} {EXTERNAL_GLYPH}
            </a>
          )
        },
      }}
    >
      {children}
    </ReactMarkdown>
  )
}
