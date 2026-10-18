import axios, { AxiosError } from 'axios'

declare module 'axios' {
  interface AxiosRequestConfig {
    silent?: boolean
  }
}

export class BizHttpError extends Error {
  constructor(public code: number, message: string, public traceId?: string) {
    super(message)
    this.name = 'BizHttpError'
  }
}

export function unwrapResponse<T>(response: { data: { code: number; message: string; trace_id?: string | null; result?: T | null } }): T {
  const res = response.data
  // 非信封响应＝这条路径没命中后端接口（SPA 兜底把 HTML 当 200 返回了）。
  // 不显式报出来的话，调用方只能显示"读取失败"，把"后端实例没有这个接口"伪装成"数据坏了"。
  if (!res || typeof res !== 'object' || typeof (res as { code?: unknown }).code !== 'number') {
    throw new BizHttpError(-1, '后端未返回标准信封（该接口在当前后端实例上不存在，通常是实例未重启到最新代码）')
  }
  if (res.code !== 0) {
    throw new BizHttpError(res.code, res.message, res.trace_id ?? undefined)
  }
  return res.result as T
}

export function bizErrorHandler(error: unknown): never {
  if (error instanceof BizHttpError) {
    // UI 层统一提示（避免 circular import，用动态导入 antd message）
    import('antd').then(({ message }) => {
      message.error(`${error.message}（Trace: ${error.traceId ?? '-'}）`)
    })
    throw error
  }
  throw error
}

const http = axios.create({
  baseURL: '/api/v1',
  timeout: 60_000,
  // 携带登录会话 httpOnly Cookie（同源部署，无需显式 origin）
  withCredentials: true,
})

// 拦截器：成功解包，业务错误提示并 reject；401 统一跳登录页
http.interceptors.response.use(
  (response) => response,
  (error: AxiosError) => {
    const status = error.response?.status
    const url = error.config?.url || ''
    const isAuthApi = url.startsWith('/auth/')

    // 401 + 非 auth 接口自身 → 会话失效/未登录，跳登录页
    if (status === 401 && !isAuthApi) {
      // 避免重复 push（已在登录页则不重复跳转）
      if (window.location.pathname !== '/login') {
        // 用 history replace 而非 location.assign，避免刷新丢失 SPA 状态
        window.location.replace('/login')
      }
      return Promise.reject(error)
    }

    // 网络级错误（断网/502 等）；silent 标记的请求（状态探测）静默降级不弹窗
    if (!error.config?.silent) {
      import('antd').then(({ message }) => {
        message.error('网络请求失败，请检查网络连接')
      })
    }
    return Promise.reject(error)
  },
)

export default http
