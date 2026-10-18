import { describe, it, expect, vi, beforeEach } from 'vitest'
import type { AxiosResponse } from 'axios'
import { fetchRagflowStatus, fetchCapabilityHealth, fetchServerDate } from './system'

vi.mock('./http', () => ({
  default: {
    get: vi.fn(),
  },
  unwrapResponse: (res: { data: { code: number; result?: unknown } }) => {
    if (res.data.code !== 0) throw new Error(`biz code ${res.data.code}`)
    return res.data.result
  },
}))

import http from './http'

const envelope = <T>(result: T): AxiosResponse => ({
  data: { code: 0, message: 'ok', result },
  status: 200,
  statusText: 'OK',
  headers: {},
  config: {} as never,
})

describe('system api', () => {
  beforeEach(() => {
    vi.mocked(http.get).mockReset()
  })

  it('fetchRagflowStatus 解包 result', async () => {
    vi.mocked(http.get).mockResolvedValue(
      envelope({ configured: true, reachable: true, doc_count: 5, latest_doc_time: '2026-08-18', message: 'ok' }) as never,
    )
    const status = await fetchRagflowStatus()
    expect(status.doc_count).toBe(5)
    expect(http.get).toHaveBeenCalledWith('/ragflow/status', { silent: true })
  })

  it('fetchCapabilityHealth 拉取并解包数组', async () => {
    vi.mocked(http.get).mockResolvedValue(
      envelope([{ tool_name: 'borescope_defect_detection', healthy: true, message: 'ok', latency_ms: null }]) as never,
    )
    const list = await fetchCapabilityHealth()
    expect(list).toHaveLength(1)
    expect(list[0].healthy).toBe(true)
    expect(http.get).toHaveBeenCalledWith('/capabilities/health', { silent: true })
  })

  it('fetchServerDate 静默拉取服务器日期', async () => {
    vi.mocked(http.get).mockResolvedValue(
      envelope({ status: 'healthy', database: 'ok', server_date: '2026-08-19', server_datetime: '2026-08-19 08:00:00 CST' }) as never,
    )
    const date = await fetchServerDate()
    expect(date.server_date).toBe('2026-08-19')
    expect(http.get).toHaveBeenCalledWith('/health', { silent: true })
  })
})