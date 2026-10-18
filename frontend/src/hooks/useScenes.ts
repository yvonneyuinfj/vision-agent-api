import { useEffect, useState } from 'react'
import { fetchScenes, scenesToWorkbenches } from '../api/scenes'
import { WORKBENCHES, type WorkbenchDef } from '../constants/workbench'

export interface ScenesView {
  /** 已取到的目录；null = 尚未返回或读取失败（调用方回退到内置六条） */
  fetched: WorkbenchDef[] | null
  /** 呈现用列表：取到用取到的，取不到用兜底表——导航与页面不能因为一次读取失败就空掉 */
  scenes: WorkbenchDef[]
  error: string | null
}

/** 目录是静态常量级数据（只有后端代码变更才会变），一次取数全局复用：
 *  首页、运行监控、提交页、工作台与注册表单会各自挂载这个 hook。 */
let cached: Promise<WorkbenchDef[]> | null = null

function loadScenes(): Promise<WorkbenchDef[]> {
  if (!cached) {
    cached = fetchScenes().then(scenesToWorkbenches)
    cached.catch(() => {
      cached = null // 失败不缓存，下次挂载可重试
    })
  }
  return cached
}

/** 场景目录取数（决策 52）：工作台/提交页/注册表单的场景项同源。
 *  读取失败不阻塞页面——内置六条与后端目录初始值同序同值，回退不改变可见内容。 */
export function useScenes(): ScenesView {
  const [fetched, setFetched] = useState<WorkbenchDef[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let alive = true
    loadScenes()
      .then((list) => {
        if (!alive) return
        setFetched(list.length ? list : null)
      })
      .catch((e: unknown) => {
        if (!alive) return
        setError(e instanceof Error ? e.message : '场景目录读取失败')
      })
    return () => {
      alive = false
    }
  }, [])

  return { fetched, scenes: fetched ?? WORKBENCHES, error }
}
