import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, cleanup, act } from '@testing-library/react'
import ThoughtChainPanel from './ThoughtChainPanel'

const mocks = vi.hoisted(() => ({ fetchTaskTrace: vi.fn() }))

vi.mock('../../api/tasks', async (importOriginal) => {
  const mod = await importOriginal<typeof import('../../api/tasks')>()
  return { ...mod, fetchTaskTrace: mocks.fetchTaskTrace }
})

const TRACE = {
  task_id: 't1',
  mode: 'agent',
  status: 'running',
  loop_count: 2,
  capability_calls: 1,
  submitted: false,
  entries: [{ type: 'thought', turn: 1, text: '先看图像再选能力' }],
}

beforeEach(() => {
  vi.clearAllMocks()
  mocks.fetchTaskTrace.mockResolvedValue(TRACE)
})

afterEach(() => {
  cleanup()
  vi.useRealTimers()
})

describe('ThoughtChainPanel（优化轮 E3 运行中轮询）', () => {
  it('落定任务只拉取一次，不轮询', async () => {
    render(<ThoughtChainPanel taskId="t1" running={false} />)
    await screen.findByText('先看图像再选能力')
    expect(mocks.fetchTaskTrace).toHaveBeenCalledTimes(1)
    vi.useFakeTimers()
    vi.advanceTimersByTime(10000)
    expect(mocks.fetchTaskTrace).toHaveBeenCalledTimes(1)
  })

  it('运行中任务每 3s 轮询并展示实时跟随指示', async () => {
    vi.useFakeTimers()
    render(<ThoughtChainPanel taskId="t1" running />)
    await act(async () => { await Promise.resolve() })
    expect(mocks.fetchTaskTrace).toHaveBeenCalledTimes(1)
    expect(screen.getByText(/实时跟随/)).toBeInTheDocument()
    await act(async () => { vi.advanceTimersByTime(3000) })
    expect(mocks.fetchTaskTrace).toHaveBeenCalledTimes(2)
    await act(async () => { vi.advanceTimersByTime(3000) })
    expect(mocks.fetchTaskTrace).toHaveBeenCalledTimes(3)
  })

  it('running 由 true 变 false 时停止轮询', async () => {
    vi.useFakeTimers()
    const { rerender } = render(<ThoughtChainPanel taskId="t1" running />)
    await act(async () => { await Promise.resolve() })
    expect(mocks.fetchTaskTrace).toHaveBeenCalledTimes(1)
    rerender(<ThoughtChainPanel taskId="t1" running={false} />)
    // effect 重跑：清掉轮询定时器并立即新拉一次（第 2 次）
    await act(async () => { await Promise.resolve() })
    expect(mocks.fetchTaskTrace).toHaveBeenCalledTimes(2)
    await act(async () => { vi.advanceTimersByTime(9000) })
    // 之后不再有轮询
    expect(mocks.fetchTaskTrace).toHaveBeenCalledTimes(2)
  })
})
