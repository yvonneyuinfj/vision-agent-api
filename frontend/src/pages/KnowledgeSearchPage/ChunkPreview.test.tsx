import { describe, expect, it, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { ChunkPreview } from './ChunkPreview'
import type { HitGroup } from './format'
import type { SearchHit } from '../../api/kb'

// vi.mock 工厂被提升至文件顶部，闭包内引用的 mock fn 必须用 vi.hoisted 预声明（否则 TDZ ReferenceError）
const { mockFetchChunks, mockDownload } = vi.hoisted(() => ({
  mockFetchChunks: vi.fn(),
  mockDownload: vi.fn(),
}))

vi.mock('../../api/kb', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/kb')>()
  return { ...actual, fetchDocChunks: mockFetchChunks, downloadDocFile: mockDownload }
})

const hit = (over: Partial<SearchHit>): SearchHit => ({
  chunk_id: 'c1', content: '前缘裂纹属于裂纹类缺陷', score: 0.9,
  doc_name: '手册A', document_id: 'd1', dataset_id: 'ds1', image_id: null, ...over,
})

const group = (over: Partial<HitGroup>): HitGroup => ({
  key: 'ds1:d1', docName: '手册A', datasetId: 'ds1', documentId: 'd1',
  hits: [hit({})], topScore: 0.9, ...over,
})

const CHUNKS = [
  { chunk_id: 'c0', content: '第一段：检查前缘区域。', image_id: null },
  { chunk_id: 'c1', content: '第二段：前缘裂纹属于裂纹类缺陷。', image_id: 'img1' },
  { chunk_id: 'c2', content: '第三段：判级标准见手册。', image_id: null },
  { chunk_id: 'c3', content: '第四段：允许限度 0.3mm。', image_id: null },
  { chunk_id: 'c4', content: '第五段：复查周期。', image_id: null },
  { chunk_id: 'c5', content: '第六段：记录要求。', image_id: null },
]

describe('ChunkPreview', () => {
  beforeEach(() => vi.clearAllMocks())

  it('以中心命中 chunk 取前后 ±2，渲染 5 段且中心带命中标记', async () => {
    mockFetchChunks.mockResolvedValue({ document_id: 'd1', doc_name: '手册A', chunks: CHUNKS })
    render(<ChunkPreview group={group({ documentId: 'd1', hits: [hit({ chunk_id: 'c4' })] })} query="前缘裂纹" />)
    expect(screen.getByText('正在加载原文…')).toBeInTheDocument()
    await waitFor(() => expect(screen.getByText('chunk c4 · 命中段落')).toBeInTheDocument())
    expect(screen.queryByText('chunk c0')).not.toBeInTheDocument() // 超出 ±2 不显示
    // label 实际为「chunk c4 · 命中段落」拼接文本，exact 匹配会失败，改子串匹配
    expect(screen.getByText('chunk c4', { exact: false })).toBeInTheDocument()
    expect(mockFetchChunks).toHaveBeenCalledWith('ds1', 'd1')
  })

  it('中心命中段内容带高亮；邻居段无 highlight', async () => {
    const chunks = CHUNKS.slice(0, 4) // c0..c3，中心 c1
    mockFetchChunks.mockResolvedValue({ document_id: 'd1', doc_name: '手册A', chunks })
    render(<ChunkPreview group={group({ documentId: 'd1' })} query="前缘裂纹" />)
    await waitFor(() => expect(document.querySelectorAll('mark')).toHaveLength(1))
    expect(document.querySelector('mark')?.closest('.chunk-item')?.classList.contains('is-hit')).toBe(true)
    expect(screen.getByText(/第三段：判级标准/)).toBeInTheDocument() // 邻居原文未高亮
    expect(screen.queryByText(/第六段/)).not.toBeInTheDocument() // c5 被 ±2 截掉
  })

  it('满载时中心越界（两侧不足）仅截取可用侧', async () => {
    const chunks = [{ chunk_id: 'c9', content: '仅有一段。', image_id: null }]
    mockFetchChunks.mockResolvedValue({ document_id: 'd1', doc_name: '手册A', chunks })
    render(<ChunkPreview group={group({ documentId: 'd1' })} query="q" />)
    await waitFor(() => expect(screen.getByText('仅有一段。')).toBeInTheDocument())
  })

  it('chunks 拉取失败独立降级：显示错误 + 重试', async () => {
    mockFetchChunks.mockRejectedValueOnce(new Error('preview boom')).mockResolvedValueOnce(
      { document_id: 'd1', doc_name: '手册A', chunks: CHUNKS.slice(0, 2) })
    render(<ChunkPreview group={group({ documentId: 'd1' })} query="q" />)
    await waitFor(() => expect(screen.getByText(/预览加载失败/)).toBeInTheDocument())
    fireEvent.click(screen.getByRole('button', { name: /重试/ }))
    await waitFor(() => expect(screen.getByText('chunk c1', { exact: false })).toBeInTheDocument())
  })

  it('缩略图失败显示占位「图片不可用」，下载失败内联一行', async () => {
    mockFetchChunks.mockResolvedValue({ document_id: 'd1', doc_name: '手册A', chunks: CHUNKS.slice(0, 3) })
    mockDownload.mockRejectedValue(new Error('dl boom'))
    render(<ChunkPreview group={group({ documentId: 'd1' })} query="q" />)
    const img = await screen.findByRole('img') // img1 缩略图
    fireEvent.error(img)
    expect(screen.getByText('图片不可用')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /下载 PDF/ }))
    expect(mockDownload).toHaveBeenCalledWith('ds1', 'd1', '手册A')
    await waitFor(() => expect(screen.getByText(/下载失败/)).toBeInTheDocument())
  })

  it('原文以 markdown 渲染：标题/加粗/<sup> 生效，命中词保留 <mark> 高亮', async () => {
    const chunks = [{
      chunk_id: 'm0',
      content: '### 3.1 疲劳断口形貌分析\n\n**应力集中**区域的判级参考值取 580 MPa<sup>[23-24]</sup>。',
      image_id: null,
    }]
    mockFetchChunks.mockResolvedValue({ document_id: 'd1', doc_name: '手册A', chunks })
    render(<ChunkPreview group={group({ documentId: 'd1', hits: [hit({ chunk_id: 'm0' })] })} query="MPa" />)
    await waitFor(() => expect(screen.getByRole('heading', { name: /3.1 疲劳断口形貌/ })).toBeInTheDocument())
    expect(screen.getByText(/应力集中/, { selector: 'strong' })).toBeInTheDocument()
    expect(screen.getByText(/23-24/, { selector: 'sup' })).toBeInTheDocument()
    expect(document.querySelectorAll('mark')).toHaveLength(1)
    expect(document.querySelector('mark')?.textContent).toBe('MPa')
    // 命中词与 <sup> 邻居布局：mark 在行内 HTML 旁不破坏两侧文本
    expect(document.querySelector('.chunk-item__content p')?.textContent).toContain('580 MPa[23-24]。')
  })
})