import { useEffect, useState } from 'react'
import { Card, Button, Space, Spin, Typography, Empty } from 'antd'
import { fetchKnowledgeContext, fetchKnowledgeSource, type KnowledgeContext } from '../../api/tasks'
import type { KnowledgeItem } from '../../api/types'
import Md from '../markdown/Md'

const { Text, Paragraph } = Typography

/* 线性 SVG 文档图标（替代 emoji 📄，保持全线性风格） */
const DOC_ICON = (
  <svg width={14} height={14} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8}>
    <path d="M6 3h9l4 4v14H6z" />
    <path d="M15 3v4h4M9 12h6M9 16h6" />
  </svg>
)

/** 上下文半径：前端配置（.env VITE_KNOWLEDGE_CONTEXT_RADIUS，默认 2） */
const CONTEXT_RADIUS = Number(import.meta.env.VITE_KNOWLEDGE_CONTEXT_RADIUS ?? 2)

interface Props {
  taskId: string
  citation: KnowledgeItem
  onClose: () => void
}

export default function KnowledgeCard({ taskId, citation, onClose }: Props) {
  const [context, setContext] = useState<KnowledgeContext | null>(null)
  const [source, setSource] = useState<{ doc_name: string; page: number | null; content: string } | null>(null)
  const [loading, setLoading] = useState(false)
  const [sourceLoading, setSourceLoading] = useState(false)

  useEffect(() => {
    setLoading(true)
    fetchKnowledgeContext(taskId, citation.chunk_id ?? '', CONTEXT_RADIUS)
      .then(setContext)
      // 后端接口可能未实现（补接口清单），api 层已弹错误提示，这里降级展示
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [taskId, citation.chunk_id])

  async function handleViewSource() {
    setSourceLoading(true)
    try {
      setSource(await fetchKnowledgeSource(taskId, citation.chunk_id ?? ''))
    } catch {
      // api 层已弹错误提示，保持界面可用不重复提示
    } finally {
      setSourceLoading(false)
    }
  }

  return (
    <Card
      className="glass-panel"
      size="small"
      title={
        <Space size={8}>
          <span style={{ color: 'var(--accent)', display: 'inline-flex' }}>{DOC_ICON}</span>
          <span style={{ color: 'var(--fg)' }}>{citation.doc_name || '未知文档'}</span>
          <span className="tag t-accent">相似度 {(citation.score * 100).toFixed(1)}%</span>
        </Space>
      }
      extra={<Button size="small" type="text" onClick={onClose}>收起</Button>}
      style={{ marginTop: 8 }}
    >
      <Paragraph>
        <Md citations={{}}>{citation.content}</Md>
      </Paragraph>

      <Spin spinning={loading}>
        {context ? (
          <div>
            <Text strong style={{ fontSize: 12, color: 'var(--muted)' }}>上下文片段（前后各 {CONTEXT_RADIUS} 块）</Text>
            {[...context.prev, ...context.next].map((c) => (
              <blockquote key={c.chunk_id} className="kbi-context-q">
                {c.content.slice(0, 300)}{c.content.length > 300 ? '…' : ''}
              </blockquote>
            ))}
          </div>
        ) : (
          <Text type="secondary" style={{ fontSize: 12 }}>暂无上下文片段（后端接口未提供）</Text>
        )}
      </Spin>

      <div style={{ marginTop: 8 }}>
        <Button size="small" loading={sourceLoading} onClick={handleViewSource}>查看原文</Button>
        {source && (
          <div style={{ marginTop: 8, maxHeight: 240, overflow: 'auto' }}>
            <Text strong style={{ fontSize: 12 }}>{source.doc_name}{source.page ? ` · 第 ${source.page} 页` : ''}</Text>
            <Md citations={{}}>{source.content}</Md>
          </div>
        )}
      </div>
      {context === null && !loading && (
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="无上下文数据" style={{ margin: '8px 0' }} />
      )}
    </Card>
  )
}