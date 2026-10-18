import { describe, it, expect, vi, beforeEach } from 'vitest'

vi.mock('../api/http', () => {
  const mockHttp = {
    get: vi.fn(),
    post: vi.fn(),
  }
  return {
    default: mockHttp,
    unwrapResponse: (r: unknown) => (r as { data: { result: unknown } }).data.result,
    BizHttpError: class extends Error {},
  }
})

import { useTaskStore } from './useTaskStore'
import type { TaskDetail } from '../api/types'

describe('useTaskStore', () => {
  beforeEach(() => {
    useTaskStore.setState({ tasks: [], detail: null, loading: false, error: null })
  })

  it('fetchDetail 成功写入 detail（mock http）', async () => {
    const http = (await import('../api/http')).default as unknown as { get: ReturnType<typeof vi.fn> }
    const detail: TaskDetail = {
      task_id: 't1',
      input_uris: [],
      scene: null,
      mode: 'single',
      status: 'running',
      capability_used: null,
      created_at: '2026-08-18T10:00:00',
      updated_at: '2026-08-18T10:00:00',
      finished_at: null,
      trace_id: null,
      steps: [],
      standardized: [],
      grade: null,
      knowledge: [],
      review: null,
      report: null,
      error: null,
    }
    http.get.mockResolvedValue({ data: { code: 0, result: detail } })
    await useTaskStore.getState().fetchDetail('t1')
    expect(useTaskStore.getState().detail?.task_id).toBe('t1')
  })
})
