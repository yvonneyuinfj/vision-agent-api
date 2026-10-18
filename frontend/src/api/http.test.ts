import { describe, it, expect, vi } from 'vitest'

// 捕获响应拦截器 reject 回调，供拦截器测试直接调用（纯函数级驱动，无需真实网络）
const { capturedReject } = vi.hoisted(() => ({
  capturedReject: { fn: undefined as ((error: unknown) => unknown) | undefined },
}))

vi.mock('antd', () => ({ message: { error: vi.fn() } }))

vi.mock('axios', async (importOriginal) => {
  const actual = await importOriginal<typeof import('axios')>()
  const mockInstance = {
    get: vi.fn(), post: vi.fn(),
    interceptors: {
      request: { use: vi.fn() },
      response: {
        use: (_ok: unknown, err: (error: unknown) => unknown): void => {
          capturedReject.fn = err
        },
      },
    },
  }
  return { ...actual, default: { create: vi.fn(() => mockInstance) }, __mockInstance: mockInstance }
})

// 直接测拦截器函数（从 http.ts 导出，便于单测）
import { unwrapResponse } from './http'
import { BizHttpError } from './http'
import { message } from 'antd'

const mockMessageError = vi.mocked(message.error)

describe('unwrapResponse', () => {
  it('code===0 时返回 result', () => {
    const result = unwrapResponse({ data: { code: 0, message: 'success', result: { a: 1 } } })
    expect(result).toEqual({ a: 1 })
  })

  it('code!==0 时抛 BizHttpError 且携带 trace_id', () => {
    expect(() => unwrapResponse({
      data: { code: 4002, message: 'task not found', trace_id: 'tr-1', result: null },
    })).toThrowError(BizHttpError)
    try {
      unwrapResponse({ data: { code: 4002, message: 'x', trace_id: 'tr-1', result: null } })
    } catch (e) {
      expect(e).toBeInstanceOf(BizHttpError)
      expect((e as BizHttpError).code).toBe(4002)
      expect((e as BizHttpError).traceId).toBe('tr-1')
    }
  })

  it('响应不是信封（SPA 兜底返回 HTML）时报出"接口不存在"，不塌成空消息', () => {
    const html = '<!doctype html><html lang="zh-CN"></html>'
    try {
      unwrapResponse({ data: html as never })
      throw new Error('should have thrown')
    } catch (e) {
      expect(e).toBeInstanceOf(BizHttpError)
      expect((e as BizHttpError).code).toBe(-1)
      // 调用方（writeErrorMessage）取的就是这条 message：运维要能分辨"后端落后"与"数据坏了"
      expect((e as BizHttpError).message).toContain('该接口在当前后端实例上不存在')
    }
    expect(() => unwrapResponse({ data: null as never })).toThrowError(BizHttpError)
  })
})

describe('http 响应拦截器', () => {
  it('网络级错误且请求未标记 silent 时弹全局 toast 并 reject', async () => {
    const error = { config: { url: '/x' } } as never
    const result: unknown = capturedReject.fn!(error)
    await expect(result as PromiseLike<unknown>).rejects.toBe(error)
    await vi.waitFor(() => {
      expect(mockMessageError).toHaveBeenCalledWith('网络请求失败，请检查网络连接')
    })
  })

  it('网络级错误且请求标记 silent 时不弹 toast 并按原样 reject', async () => {
    mockMessageError.mockClear()
    const error = { config: { url: '/ragflow/status', silent: true } } as never
    const result: unknown = capturedReject.fn!(error)
    await expect(result as PromiseLike<unknown>).rejects.toBe(error)
    // 等动态 import('antd') 的微任务完成，确保不发生未断言的迟到弹窗
    await Promise.resolve()
    await Promise.resolve()
    expect(mockMessageError).not.toHaveBeenCalled()
  })
})