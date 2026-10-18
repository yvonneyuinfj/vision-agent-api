import { useCallback, useRef, useState } from 'react'
import { searchKnowledge, type SearchHit, type SearchMode } from '../../api/kb'
import { bizErrorHandler } from '../../api/http'

export type KbSearchState = 'idle' | 'loading' | 'success' | 'error'

function messageOf(error: unknown): string {
  // 网络级失败（axios 断网/超时等）不把英文原文塞进页面内联
  if ((error as { isAxiosError?: boolean } | null)?.isAxiosError === true) return '网络错误，请稍后重试'
  // 业务错误（BizHttpError 等）用服务端 message
  if (error instanceof Error && error.message) return error.message
  return '网络错误，请稍后重试'
}

export function useKbSearch() {
  const [input, setInput] = useState('')
  const [mode, setModeState] = useState<SearchMode>('hybrid')
  const [state, setState] = useState<KbSearchState>('idle')
  const [errorMsg, setErrorMsg] = useState<string | null>(null)
  const [hits, setHits] = useState<SearchHit[]>([])
  const inputRef = useRef('')
  const seqRef = useRef(0)

  // 核心执行：mode 显式入参，供 setMode 切换后立即重查（避免闭包读旧 mode）
  const run = useCallback(async (query: string, m: SearchMode) => {
    const q = query.trim()
    if (!q) return
    const seq = ++seqRef.current
    setState('loading')
    setErrorMsg(null)
    try {
      const result = await searchKnowledge(q, m)
      if (seq !== seqRef.current) return // 旧响应，丢弃（已有更新请求）
      setHits(result)
      setState('success')
    } catch (error: unknown) {
      if (seq !== seqRef.current) return // 旧响应失败，丢弃
      setErrorMsg(messageOf(error))
      setState('error')
      try {
        bizErrorHandler(error) // 弹全局 toast（网络级在拦截器已弹）
      } catch {
        /* 页面内联错误已展示，这里只负责 toast 副作用，不必再抛 */
      }
    }
  }, [])

  const search = useCallback(async (query?: string) => {
    await run(query ?? inputRef.current, mode)
  }, [mode, run])

  const setInputSafe = useCallback((value: string) => {
    inputRef.current = value
    setInput(value)
  }, [])

  const setMode = useCallback((next: SearchMode) => {
    setModeState(next)
    if (inputRef.current.trim()) {
      void run(inputRef.current, next) // 已有查询：用新 mode 立即自动重查
    }
  }, [run])

  return { input, setInput: setInputSafe, mode, setMode, state, errorMsg, hits, search }
}