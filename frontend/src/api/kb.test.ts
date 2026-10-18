import { describe, expect, it, vi, beforeEach, beforeAll } from 'vitest'

vi.mock('./http', async (importOriginal) => {
  const actual = await importOriginal<typeof import('./http')>()
  return {
    ...actual,
    default: { post: vi.fn(), get: vi.fn(), patch: vi.fn() },
  }
})

import http from './http'
import { searchKnowledge, fetchDocChunks, fetchChatDatasets, buildImageUrl, downloadDocFile, renameChatSession, streamChatCompletion } from './kb'

const mockPost = vi.mocked(http.post)
const mockGet = vi.mocked(http.get)
const mockPatch = vi.mocked(http.patch)

const ENVELOPE_OK = <T>(result: T) => ({ data: { code: 0, message: 'ok', trace_id: null, result } })

// jsdom 的 URL 上无 createObjectURL/revokeObjectURL 静态方法（vi.spyOn 需要属性存在），直接赋 stub；用例断言不依赖其返回值
beforeAll(() => {
  Object.assign(URL, { createObjectURL: vi.fn(() => 'blob:mock-url'), revokeObjectURL: vi.fn() })
})

describe('kb api', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('searchKnowledge 按三要素发请求并不带 silent，解包返回 hits', async () => {
    mockPost.mockResolvedValue(ENVELOPE_OK({ query: '孔探', mode: 'hybrid', elapsed_ms: 12, hits: [{ chunk_id: 'c1' }] }) as never)
    const hits = await searchKnowledge('孔探', 'hybrid')
    expect(mockPost).toHaveBeenCalledWith('/ragflow/search', { question: '孔探', mode: 'hybrid', top_k: 5 })
    expect(hits).toEqual([{ chunk_id: 'c1' }])
    const config = mockPost.mock.calls[0][2]
    expect(config).toBeUndefined() // 不传 silent —— 用户主动操作走正常错误提示
  })

  it('fetchDocChunks 解包文档与 chunks', async () => {
    mockGet.mockResolvedValue(ENVELOPE_OK({ document_id: 'd1', doc_name: '手册', chunks: [{ chunk_id: 'c1', content: 'x', image_id: null }] }) as never)
    const doc = await fetchDocChunks('ds1', 'd1')
    expect(mockGet).toHaveBeenCalledWith('/ragflow/datasets/ds1/documents/d1/chunks')
    expect(doc.doc_name).toBe('手册')
  })

  it('fetchChatDatasets 返回可读知识库摘要', async () => {
    mockGet.mockResolvedValue(ENVELOPE_OK([{ id: 'ds1', name: '航发判级标准', document_count: 12 }]) as never)
    const datasets = await fetchChatDatasets()
    expect(mockGet).toHaveBeenCalledWith('/ragflow/chat/datasets')
    expect(datasets[0].name).toBe('航发判级标准')
  })

  it('renameChatSession 通过 PATCH 持久化会话名称', async () => {
    mockPatch.mockResolvedValue(ENVELOPE_OK({ id: 's1', chat_id: 'chat1', name: 'GH738 疲劳研究', messages: [] }) as never)
    const session = await renameChatSession('s1', 'GH738 疲劳研究')
    expect(mockPatch).toHaveBeenCalledWith('/ragflow/chat/sessions/s1', { name: 'GH738 疲劳研究' })
    expect(session.name).toBe('GH738 疲劳研究')
  })

  it('buildImageUrl 拼代理路径', () => {
    expect(buildImageUrl('img-123')).toBe('/api/v1/ragflow/images/img-123')
  })

  it('downloadDocFile 触发浏览器下载并解析 Content-Disposition 中文文件名', async () => {
    const click = vi.fn()
    const create = vi.spyOn(document, 'createElement')
    const anchor = { click, remove: vi.fn() } as unknown as HTMLAnchorElement
    create.mockReturnValue(anchor)
    mockGet.mockResolvedValue({ data: new Blob(['pdf']), headers: { 'content-disposition': "attachment; filename*=UTF-8''%E5%AD%94%E6%8E%A2%E6%89%8B%E5%86%8C.pdf" } } as never)
    await downloadDocFile('ds1', 'd1', 'fallback.pdf')
    expect(anchor.download).toBe('孔探手册.pdf')
    expect(click).toHaveBeenCalledTimes(1)
  })

  it('downloadDocFile 无 Content-Disposition 时用 fallback 文件名', async () => {
    const click = vi.fn()
    const create = vi.spyOn(document, 'createElement').mockReturnValue({ click, remove: vi.fn() } as unknown as HTMLAnchorElement)
    mockGet.mockResolvedValue({ data: new Blob(['pdf']), headers: {} } as never)
    await downloadDocFile('ds1', 'd1', '手册.pdf')
    expect(click).toHaveBeenCalledTimes(1)
    expect((create.mock.results[0].value as HTMLAnchorElement).download).toBe('手册.pdf')
  })

  it('streamChatCompletion 能跨网络分片解析 SSE delta 和完整引用', async () => {
    const encoder = new TextEncoder()
    const payload = [
      'data:{"code":0,"data":{"answer":"前缘","reference":{"chunks":[]}}}\n\n',
      'data:{"code":0,"data":{"answer":"裂纹","reference":{"chunks":[{"id":"c1","document_name":"手册"}]}}}\n\n',
      'data:{"code":0,"data":true}\n\n',
    ].join('')
    const stream = new ReadableStream({
      start(controller) {
        controller.enqueue(encoder.encode(payload.slice(0, 37)))
        controller.enqueue(encoder.encode(payload.slice(37)))
        controller.close()
      },
    })
    vi.stubGlobal('fetch', vi.fn(async () => new Response(stream, { status: 200, headers: { 'Content-Type': 'text/event-stream' } })))
    const chunks: string[] = []
    await streamChatCompletion('s1', '问题', 'm1', (event) => chunks.push(event.answer ?? ''))
    expect(chunks).toEqual(['前缘', '裂纹'])
    expect(fetch).toHaveBeenCalledWith('/api/v1/ragflow/chat/sessions/s1/completions', expect.objectContaining({ method: 'POST' }))
  })
})
