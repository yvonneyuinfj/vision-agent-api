import { describe, it, expect, beforeEach, vi } from 'vitest'
import { useSystemStore } from './useSystemStore'
import { fetchRagflowStatus, fetchCapabilityHealth } from '../api/system'

vi.mock('../api/system', () => ({
  fetchRagflowStatus: vi.fn(async () => ({
    configured: true,
    reachable: true,
    doc_count: 3,
    latest_doc_time: '2026-08-18',
    message: 'ok',
  })),
  fetchCapabilityHealth: vi.fn(async () => [
    { tool_name: 't1', healthy: true, message: 'ok', latency_ms: 1 },
  ]),
  fetchServerDate: vi.fn(async () => ({ server_date: '2026-08-25', vlm_available: true })),
}))

describe('useSystemStore.probe', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    useSystemStore.setState({ caps: null, ragflow: null, vlmAvailable: null })
  })

  it('并发调用只发一个真实请求（in-flight 去重）', async () => {
    const [a, b] = [useSystemStore.getState().probe(), useSystemStore.getState().probe()]
    await Promise.all([a, b])
    expect(fetchRagflowStatus).toHaveBeenCalledTimes(1)
    expect(fetchCapabilityHealth).toHaveBeenCalledTimes(1)
    expect(useSystemStore.getState().ragflow?.doc_count).toBe(3)
    expect(useSystemStore.getState().caps).toHaveLength(1)
  })

  it('探测失败静默降级为 null（不抛错）', async () => {
    vi.mocked(fetchRagflowStatus).mockRejectedValueOnce(new Error('network down'))
    await expect(useSystemStore.getState().probe()).resolves.not.toThrow()
    expect(useSystemStore.getState().ragflow).toBeNull()
    expect(useSystemStore.getState().caps).toBeNull()
  })

  it('失败后再次探测可恢复成功', async () => {
    vi.mocked(fetchRagflowStatus).mockRejectedValueOnce(new Error('network down'))
    await useSystemStore.getState().probe()
    await useSystemStore.getState().probe()
    expect(useSystemStore.getState().ragflow?.reachable).toBe(true)
  })
})