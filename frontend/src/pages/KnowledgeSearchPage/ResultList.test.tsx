import { describe, expect, it, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { ResultList } from './ResultList'
import type { HitGroup } from './format'
import type { SearchHit } from '../../api/kb'

const hit = (over: Partial<SearchHit>): SearchHit => ({
  chunk_id: 'c1', content: '前缘裂纹属于裂纹类缺陷，判级标准见手册。', score: 0.9,
  doc_name: '手册A', document_id: 'd1', dataset_id: 'ds1', image_id: null, ...over,
})

const group = (over: Partial<HitGroup>): HitGroup => ({
  key: 'ds1:d1', docName: '手册A', datasetId: 'ds1', documentId: 'd1',
  hits: [hit({})], topScore: 0.9, ...over,
})

describe('ResultList', () => {
  it('渲染真实文档名 / 相似度 toFixed(2) / 「命中 N 段」真实计数', () => {
    const groups = [
      group({ key: 'a', docName: '孔探手册', documentId: 'd1', topScore: 0.873,
             hits: [hit({ chunk_id: 'x' }), hit({ chunk_id: 'y' })] }),
      group({ key: 'b', docName: '裂纹图谱', documentId: 'd2', topScore: 0.5,
             hits: [hit({ chunk_id: 'z' })] }),
    ]
    render(<ResultList groups={groups} activeKey={null} query="裂纹" onPreview={vi.fn()} />)
    expect(screen.getByText('孔探手册')).toBeInTheDocument()
    expect(screen.getByText('命中 2 段')).toBeInTheDocument()
    expect(screen.getByText('命中 1 段')).toBeInTheDocument()
    expect(screen.getByText('0.87')).toBeInTheDocument()
    expect(screen.getByText('0.50')).toBeInTheDocument()
    // 两个测试卡片 snippet 均含该词（hits[0].content 相同），getByText 会多匹配抛错，改 getAllByText 断言至少一处
    expect(screen.getAllByText('裂纹类缺陷', { exact: false }).length).toBeGreaterThan(0)
    expect(screen.queryByText('命中 4 条', { exact: false })).toBeNull() // 静态装饰字串必须消失
  })

  it('点击卡片触发 onPreview(group)', () => {
    const onPreview = vi.fn()
    const g = group({ key: 'ds1:d1' })
    render(<ResultList groups={[g]} activeKey={null} query="q" onPreview={onPreview} />)
    fireEvent.click(screen.getByText('手册A'))
    expect(onPreview).toHaveBeenCalledWith(g)
  })

  it('active 卡片标记 is-active 类', () => {
    render(<ResultList groups={[group({ key: 'k' })]} activeKey="k" query="q" onPreview={vi.fn()} />)
    expect(screen.getByText('手册A').closest('.result-card')?.classList.contains('is-active')).toBe(true)
  })

  it('query 命中处渲染 <mark>', () => {
    render(<ResultList groups={[group({})]} activeKey={null} query="前缘裂纹" onPreview={vi.fn()} />)
    expect(document.querySelector('mark')?.textContent).toBe('前缘裂纹')
  })
})