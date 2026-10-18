import { describe, expect, it, vi, beforeEach } from 'vitest'
import { AxiosError } from 'axios'
import { renderHook, act } from '@testing-library/react'
import { useKbSearch } from './useKbSearch'
import { searchKnowledge } from '../../api/kb'
import type { SearchHit } from '../../api/kb'

vi.mock('../../api/kb', () => ({
  searchKnowledge: vi.fn(),
}))

const mockSearch = vi.mocked(searchKnowledge)

describe('useKbSearch', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('初始 idle；search 后 loading → success 且 hits 落盘', async () => {
    // mock 保持 pending，让 loading 中间态可观测（immediate resolve 会被 act flush 掉）
    let resolve!: (v: SearchHit[]) => void
    mockSearch.mockImplementationOnce(() => new Promise((r) => { resolve = r }))
    const { result } = renderHook(() => useKbSearch())
    expect(result.current.state).toBe('idle')
    await act(async () => { result.current.setInput('孔探'); result.current.search() })
    expect(result.current.state).toBe('loading')
    await act(async () => { resolve([{ chunk_id: 'c1' } as never]) })
    await act(async () => {})
    expect(result.current.state).toBe('success')
    expect(result.current.hits).toEqual([{ chunk_id: 'c1' }])
  })

  it('失败进入 error 并带一行原因', async () => {
    mockSearch.mockRejectedValue(new Error('知识库检索暂不可用'))
    const { result } = renderHook(() => useKbSearch())
    await act(async () => { result.current.setInput('q'); result.current.search() })
    await act(async () => {})
    expect(result.current.state).toBe('error')
    expect(result.current.errorMsg).toBe('知识库检索暂不可用')
  })

  it('AxiosError（真实网络失败）回落中文文案，不显示英文原文', async () => {
    mockSearch.mockRejectedValue(new AxiosError('Network Error'))
    const { result } = renderHook(() => useKbSearch())
    await act(async () => { result.current.setInput('q'); result.current.search() })
    await act(async () => {})
    expect(result.current.state).toBe('error')
    expect(result.current.errorMsg).toBe('网络错误，请稍后重试')
  })

  it('rejected null 也进入 error 态（messageOf null 防御）', async () => {
    mockSearch.mockRejectedValue(null)
    const { result } = renderHook(() => useKbSearch())
    await act(async () => { result.current.setInput('q'); result.current.search() })
    await act(async () => {})
    expect(result.current.state).toBe('error')
    expect(result.current.errorMsg).toBe('网络错误，请稍后重试')
  })

  it('空 query 不触发请求', async () => {
    const { result } = renderHook(() => useKbSearch())
    await act(async () => { result.current.search() })
    expect(mockSearch).not.toHaveBeenCalled()
  })

  it('请求未返回时再次检索，旧的慢响应被丢弃（reqSeq 防乱序）', async () => {
    let resolveFirst!: (v: SearchHit[]) => void
    mockSearch.mockImplementationOnce(() => new Promise((r) => { resolveFirst = r }))
      .mockResolvedValueOnce([{ chunk_id: 'new' }] as never)
    const { result } = renderHook(() => useKbSearch())
    await act(async () => { result.current.setInput('旧'); result.current.search() })
    await act(async () => { result.current.setInput('新'); result.current.search() })
    await act(async () => { resolveFirst([{ chunk_id: 'old' } as SearchHit]); })
    await act(async () => {})
    expect(result.current.state).toBe('success')
    expect(result.current.hits).toEqual([{ chunk_id: 'new' }])
    expect(mockSearch).toHaveBeenCalledTimes(2)
  })

  it('模式切换时已输入关键词自动重查', async () => {
    mockSearch.mockResolvedValue([{ chunk_id: 'c1' }] as never)
    const { result } = renderHook(() => useKbSearch())
    await act(async () => { result.current.setInput('孔探'); result.current.search() })
    await act(async () => {})
    mockSearch.mockClear()
    await act(async () => { result.current.setMode('keyword') })
    await act(async () => {})
    expect(mockSearch).toHaveBeenCalledWith('孔探', 'keyword')
    expect(result.current.mode).toBe('keyword')
  })

  it('未输入关键词时切换模式不触发请求', async () => {
    const { result } = renderHook(() => useKbSearch())
    await act(async () => { result.current.setMode('semantic') })
    expect(mockSearch).not.toHaveBeenCalled()
  })
})
