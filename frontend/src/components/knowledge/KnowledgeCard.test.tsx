import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, fireEvent, cleanup } from '@testing-library/react'
import KnowledgeCard from './KnowledgeCard'
import { fetchKnowledgeContext, fetchKnowledgeSource } from '../../api/tasks'

vi.mock('../../api/tasks', async (importOriginal) => {
  const mod = await importOriginal<typeof import('../../api/tasks')>()
  return {
    ...mod,
    fetchKnowledgeContext: vi.fn(),
    fetchKnowledgeSource: vi.fn(),
  }
})

const mockFetchContext = vi.mocked(fetchKnowledgeContext)
const mockFetchSource = vi.mocked(fetchKnowledgeSource)

const CITATION = {
  content: '判级依据：表面裂纹按 X 标准处理。',
  score: 0.923,
  doc_name: '标准手册.pdf',
  chunk_id: 'c1',
  image_uri: null,
}

describe('KnowledgeCard', () => {
  beforeEach(() => {
    mockFetchContext.mockResolvedValue({
      doc_id: 'd1',
      doc_name: '标准手册.pdf',
      page: 3,
      self: { chunk_id: 'c1', content: '判级依据内容', page: 3, score: 0.923 },
      prev: [{ chunk_id: 'c0', content: '上一条引用片段内容', page: 2 }],
      next: [{ chunk_id: 'c2', content: '下一条引用片段内容', page: 4 }],
    })
    mockFetchSource.mockResolvedValue({ doc_name: '标准手册.pdf', page: 3, content: '原文第 3 页内容段落' })
  })
  afterEach(() => {
    cleanup()
    vi.clearAllMocks()
  })

  it('渲染文档名与相似度得分', () => {
    render(<KnowledgeCard taskId="t1" citation={CITATION} onClose={() => {}} />)
    expect(screen.getByText(/标准手册\.pdf/)).toBeInTheDocument()
    expect(screen.getByText(/相似度 92\.3%/)).toBeInTheDocument()
  })

  it('加载上下文并展示前后片段', async () => {
    render(<KnowledgeCard taskId="t1" citation={CITATION} onClose={() => {}} />)
    expect(mockFetchContext).toHaveBeenCalledWith('t1', 'c1', 2)
    await waitFor(() => expect(screen.getByText(/上一条引用片段内容/)).toBeInTheDocument())
    expect(screen.getByText(/下一条引用片段内容/)).toBeInTheDocument()
  })

  it('点击「查看原文」加载并展示来源内容', async () => {
    render(<KnowledgeCard taskId="t1" citation={CITATION} onClose={() => {}} />)
    fireEvent.click(screen.getByRole('button', { name: /查看原文/ }))
    await waitFor(() => expect(mockFetchSource).toHaveBeenCalledWith('t1', 'c1'))
    expect(screen.getByText(/原文第 3 页内容段落/)).toBeInTheDocument()
  })

  it('上下文接口失败时降级提示，不白屏', async () => {
    mockFetchContext.mockRejectedValue(new Error('network down'))
    render(<KnowledgeCard taskId="t1" citation={CITATION} onClose={() => {}} />)
    await waitFor(() => expect(screen.getByText(/暂无上下文片段/)).toBeInTheDocument())
    expect(screen.getByText('判级依据：表面裂纹按 X 标准处理。')).toBeInTheDocument()
  })

  it('点击「收起」触发 onClose', () => {
    const onClose = vi.fn()
    render(<KnowledgeCard taskId="t1" citation={CITATION} onClose={onClose} />)
    fireEvent.click(screen.getByRole('button', { name: /收起/ }))
    expect(onClose).toHaveBeenCalled()
  })
})