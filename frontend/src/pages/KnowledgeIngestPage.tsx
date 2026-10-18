import { useEffect, useMemo, useState } from 'react'
import { fetchChatDatasets, type ChatDataset } from '../api/kb'
import { fetchRagflowStatus, type RagflowStatus } from '../api/system'
import { LoadError, Notice } from '../components/common/ModuleShell'
import { Table } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import dayjs from 'dayjs'

/* 资料入库（方案 §7.1）：本轮只做**数据集级 + 总量级只读视图**。
   文档级的标题 / 版本 / 专业 / 权限 / 解析状态无读取端点，故不提供上传表单与文档清单。 */

export default function KnowledgeIngestPage() {
  const [status, setStatus] = useState<RagflowStatus | null>(null)
  const [datasets, setDatasets] = useState<ChatDataset[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const load = async () => {
    setLoading(true)
    setError(null)
    try {
      const [s, d] = await Promise.all([fetchRagflowStatus(), fetchChatDatasets({ silent: true }).catch(() => [])])
      setStatus(s)
      setDatasets(d)
    } catch (e) {
      setError(e instanceof Error ? e.message : '知识库状态读取失败')
    } finally {
      setLoading(false)
    }
  }
  useEffect(() => {
    void load()
  }, [])

  const columns: ColumnsType<ChatDataset> = [
    { title: '数据集', dataIndex: 'name' },
    {
      title: '文档数',
      dataIndex: 'document_count',
      render: (v?: number | null) => <span className="num">{v ?? '—'}</span>,
    },
    {
      title: '片段数',
      dataIndex: 'chunk_count',
      render: (v?: number | null) => <span className="num">{v ?? '—'}</span>,
    },
    {
      title: '最近更新',
      dataIndex: 'update_time',
      render: (v?: number | null) =>
        v ? <span className="mono">{dayjs.unix(v).utcOffset(8).format('YYYY-MM-DD HH:mm')}</span> : '—',
    },
    { title: '说明', dataIndex: 'description', ellipsis: true, render: (v?: string | null) => v ?? '—' },
  ]

  const docTotal = datasets.reduce((sum, d) => sum + (d.document_count ?? 0), 0)
  const chunkTotal = useMemo(() => datasets.reduce((sum, d) => sum + (d.chunk_count ?? 0), 0), [datasets])

  return (
    <>
      <div className="page-head">
        <div>
          <div className="page-title">资料入库</div>
          <div className="page-sub">
            规程 / 标准知识库的数据集级只读视图 · 数据源 <span className="mono">GET /ragflow/status</span> +{' '}
            <span className="mono">/ragflow/chat/datasets</span>
          </div>
        </div>
        <button type="button" className="btn btn-sm" disabled={loading} onClick={() => void load()}>
          {loading ? <span className="spin" /> : null} 重新读取
        </button>
      </div>

      {error && (
        <div style={{ marginBottom: 16 }}>
          <LoadError message={error} onRetry={() => void load()} />
        </div>
      )}

      <div className="kpi-grid">
        <div className="card kpi">
          <div className="kpi-label">已索引文档</div>
          <div className="kpi-value num">{status ? status.doc_count : '—'}</div>
          <div className="kpi-delta">
            {status?.configured ? (status.reachable ? 'RAGFlow 可达' : 'RAGFlow 不可达') : '未配置 RAGFlow'}
          </div>
        </div>
        <div className="card kpi">
          <div className="kpi-label">接入数据集</div>
          <div className="kpi-value num">{datasets.length || '—'}</div>
          <div className="kpi-delta">助手与检索共用同一批数据集</div>
        </div>
        <div className="card kpi">
          <div className="kpi-label">文档数合计</div>
          <div className="kpi-value num">{docTotal || '—'}</div>
          <div className="kpi-delta">按数据集返回值累加</div>
        </div>
        <div className="card kpi">
          <div className="kpi-label">片段数合计</div>
          <div className="kpi-value num">{chunkTotal || '—'}</div>
          <div className="kpi-delta">
            最近入库 {status?.latest_doc_time ? <span className="mono">{status.latest_doc_time}</span> : '—'}
          </div>
        </div>
      </div>

      <section className="card mt-16">
        <div className="card-h">
          <div className="card-title">
            数据集清单 <span className="en">DATASETS</span>
          </div>
          <span className="hint">只读：解析与索引状态在 RAGFlow 侧完成，本页不回写</span>
        </div>
        <div className="card-b table-pad">
          <Table<ChatDataset>
            size="small"
            rowKey="id"
            loading={loading}
            dataSource={datasets}
            columns={columns}
            pagination={false}
            locale={{ emptyText: '未接入数据集或 RAGFlow 不可达' }}
          />
        </div>
      </section>

      <div className="mt-16">
        <Notice tone="gray">
          入库流程为：RAGFlow 侧上传并解析 → 平台按数据集读取统计 → 检索与引用即时可用（无需重启）。
          按专业拆分数据集、文档级版本与权限映射属知识中心后续工作，当前接口不提供文档清单。
        </Notice>
      </div>
    </>
  )
}
