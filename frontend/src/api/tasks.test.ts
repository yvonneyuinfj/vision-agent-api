import { describe, it, expect, vi, beforeEach } from 'vitest'
import { BizHttpError } from './http'

vi.mock('./http', async (importOriginal) => {
  const actual = await importOriginal<typeof import('./http')>()
  const mockHttp = { get: vi.fn(), post: vi.fn() }
  return {
    ...actual,
    default: mockHttp,
    bizErrorHandler: vi.fn((e: unknown) => {
      throw e
    }),
  }
})

import http from './http'
import { bizErrorHandler } from './http'
import {
  buildSubmitFormData,
  downloadTaskFile,
  submitTask,
  fetchTaskList,
  fetchTaskDetail,
  fetchKnowledgeContext,
  fetchKnowledgeSource,
  retryTask,
} from './tasks'
import type { TaskDetail } from './types'
import type { KnowledgeContext } from './tasks'

const httpMock = http as unknown as {
  get: ReturnType<typeof vi.fn>
  post: ReturnType<typeof vi.fn>
}

function envelope<T>(result: T) {
  return {
    data: {
      code: 0,
      message: 'ok',
      task_id: null,
      trace_id: null,
      tool: { tool_name: 't', tool_version: 'v', api_version: 'a' },
      result,
    },
  }
}

const minimalDetail: TaskDetail = {
  task_id: 't1',
  scene: null,
  mode: 'single',
  status: 'running',
  capability_used: null,
  created_at: '2026-08-18T10:00:00',
  updated_at: '2026-08-18T10:00:00',
  finished_at: null,
  trace_id: null,
  input_uris: [],
  steps: [],
  standardized: [],
  grade: null,
  knowledge: [],
  review: null,
  report: null,
  error: null,
}

describe('buildSubmitFormData', () => {
  it('单场景仅携带 files/scene/mode，不带业务元数据与 parameters', () => {
    const fd = buildSubmitFormData({
      scene: 'borescope',
      mode: 'single',
      scenes: ['borescope'],
      files: [new File(['x'], 'a.jpg')],
    })
    expect(fd.get('scene')).toBe('borescope')
    expect(fd.get('mode')).toBe('single')
    expect(fd.get('files')).toBeInstanceOf(File)
    expect(fd.get('parameters')).toBeNull()
    expect(fd.get('engine_id')).toBeNull()
    expect(fd.get('component_id')).toBeNull()
    expect(fd.get('image_id')).toBeNull()
  })

  it('joint 模式拼接 scenes 逗号串', () => {
    const fd = buildSubmitFormData({
      scene: null,
      mode: 'joint',
      scenes: ['borescope', 'infrared'],
      files: [new File(['x'], 'a.jpg')],
    })
    expect(fd.get('mode')).toBe('joint')
    expect(fd.get('scenes')).toBe('borescope,infrared')
  })

  it('改动过的参数以 JSON 对象下发（后端 app/api/v1/task.py 要求 parameters 为合法 JSON 对象）', () => {
    const fd = buildSubmitFormData({
      scene: 'fpi',
      mode: 'single',
      scenes: ['fpi'],
      files: [new File(['x'], 'a.jpg')],
      parameters: { confidence_threshold: 0.9 },
    })
    expect(JSON.parse(String(fd.get('parameters')))).toEqual({ confidence_threshold: 0.9 })

    const empty = buildSubmitFormData({
      scene: 'fpi',
      mode: 'single',
      scenes: ['fpi'],
      files: [new File(['x'], 'a.jpg')],
      parameters: {},
    })
    expect(empty.get('parameters')).toBeNull()
  })
})

describe('downloadTaskFile', () => {
  it('生成 kind 白名单内的下载 URL', () => {
    const url = downloadTaskFile('t1', 'annotated.png', 'artifacts')
    expect(url).toBe('/api/v1/tasks/t1/files/annotated.png?kind=artifacts')
  })
})

describe('统一错误包装（bizErrorHandler 接线）', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('fetchTaskList 成功返回 items，URL 直接写 /tasks', async () => {
    httpMock.get.mockResolvedValue(
      envelope({ items: [minimalDetail], meta: { total: 1, page: 1, page_size: 20 } }),
    )
    const list = await fetchTaskList()
    expect(list).toHaveLength(1)
    expect(list[0].task_id).toBe('t1')
    expect(httpMock.get).toHaveBeenCalledWith('/tasks', { params: { limit: 100 } })
  })

  it('fetchTaskList 业务错误走 bizErrorHandler 并上抛', async () => {
    const err = new BizHttpError(400, '参数错误', 'tr-1')
    httpMock.get.mockRejectedValue(err)
    await expect(fetchTaskList()).rejects.toBe(err)
    expect(bizErrorHandler).toHaveBeenCalledWith(err)
  })

  it('submitTask 成功以 FormData 提交并返回任务 ID', async () => {
    httpMock.post.mockResolvedValue(envelope({ task_id: 't9', mode: 'single' }))
    const res = await submitTask({
      scene: 'borescope',
      mode: 'single',
      scenes: ['borescope'],
      files: [new File(['x'], 'a.jpg')],
    })
    expect(res).toEqual({ task_id: 't9', mode: 'single' })
    const sent = httpMock.post.mock.calls[0][1] as FormData
    expect(sent.get('mode')).toBe('single')
  })

  it('submitTask 业务错误走 bizErrorHandler 并上抛', async () => {
    const err = new BizHttpError(500, '服务异常', 'tr-2')
    httpMock.post.mockRejectedValue(err)
    await expect(
      submitTask({
        scene: 'borescope',
        mode: 'single',
        scenes: ['borescope'],
        files: [],
      }),
    ).rejects.toBe(err)
    expect(bizErrorHandler).toHaveBeenCalledWith(err)
  })

  it('fetchTaskDetail 成功返回详情', async () => {
    httpMock.get.mockResolvedValue(envelope(minimalDetail))
    const detail = await fetchTaskDetail('t1')
    expect(detail.task_id).toBe('t1')
    expect(httpMock.get).toHaveBeenCalledWith('/tasks/t1')
  })

  it('fetchTaskDetail 业务错误走 bizErrorHandler 并上抛', async () => {
    const err = new BizHttpError(404, '任务不存在', 'tr-3')
    httpMock.get.mockRejectedValue(err)
    await expect(fetchTaskDetail('t1')).rejects.toBe(err)
    expect(bizErrorHandler).toHaveBeenCalledWith(err)
  })

  it('retryTask 成功调用 POST /tasks/:id/retry 并返回任务 ID', async () => {
    httpMock.post.mockResolvedValue(envelope({ task_id: 't1' }))
    const res = await retryTask('t1')
    expect(res).toEqual({ task_id: 't1' })
    expect(httpMock.post).toHaveBeenCalledWith('/tasks/t1/retry')
  })

  it('retryTask 业务错误走 bizErrorHandler 并上抛', async () => {
    const err = new BizHttpError(409, '任务状态不允许重试', 'tr-4')
    httpMock.post.mockRejectedValue(err)
    await expect(retryTask('t1')).rejects.toBe(err)
    expect(bizErrorHandler).toHaveBeenCalledWith(err)
  })

  it('fetchKnowledgeContext 成功返回上下文', async () => {
    const ctx: KnowledgeContext = {
      doc_id: 'd1',
      doc_name: '手册.pdf',
      page: 3,
      self: { chunk_id: 'c1', content: 'x', page: 3, score: 0.9 },
      prev: [],
      next: [],
    }
    httpMock.get.mockResolvedValue(envelope(ctx))
    const res = await fetchKnowledgeContext('t1', 'c1', 3)
    expect(res).toEqual(ctx)
    expect(httpMock.get).toHaveBeenCalledWith('/tasks/t1/knowledge/c1/context', { params: { n: 3 } })
  })

  it('fetchKnowledgeContext 接口未实现（404）走 bizErrorHandler 并上抛', async () => {
    const err = new BizHttpError(404, '接口未实现', 'tr-4')
    httpMock.get.mockRejectedValue(err)
    await expect(fetchKnowledgeContext('t1', 'c1', 3)).rejects.toBe(err)
    expect(bizErrorHandler).toHaveBeenCalledWith(err)
  })

  it('fetchKnowledgeSource 接口未实现（404）走 bizErrorHandler 并上抛', async () => {
    const err = new BizHttpError(404, '接口未实现', 'tr-5')
    httpMock.get.mockRejectedValue(err)
    await expect(fetchKnowledgeSource('t1', 'c1')).rejects.toBe(err)
    expect(bizErrorHandler).toHaveBeenCalledWith(err)
  })
})
