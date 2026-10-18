import { useCallback, useEffect, useState } from 'react'
import { fetchCapabilities, reloadCapabilities, type CapabilitySummary } from '../api/capabilities'
import { fetchCapabilityHealth, type CapabilityHealth } from '../api/system'

export interface CapabilitiesView {
  caps: CapabilitySummary[]
  /** 工具健康探测结果（/capabilities/health 逐个探，可能较慢；null = 尚未返回） */
  health: CapabilityHealth[] | null
  loading: boolean
  error: string | null
  refreshing: boolean
  /** 热刷新注册表：DB → 内存，返回条数；成功后重拉列表 */
  reload: () => Promise<{ ok: boolean; count?: number; message?: string }>
  refresh: () => Promise<void>
}

/** 能力注册中心取数（能力中心与工具服务中心各页共用） */
export function useCapabilities(): CapabilitiesView {
  const [caps, setCaps] = useState<CapabilitySummary[]>([])
  const [health, setHealth] = useState<CapabilityHealth[] | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [refreshing, setRefreshing] = useState(false)

  const refresh = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const [list, h] = await Promise.all([fetchCapabilities(), fetchCapabilityHealth().catch(() => null)])
      setCaps(list)
      setHealth(h)
    } catch (e) {
      setError(e instanceof Error ? e.message : '能力注册表读取失败')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void refresh()
  }, [refresh])

  const reload = useCallback(async () => {
    setRefreshing(true)
    try {
      const count = await reloadCapabilities()
      await refresh()
      return { ok: true, count }
    } catch (e) {
      return { ok: false, message: e instanceof Error ? e.message : '刷新失败' }
    } finally {
      setRefreshing(false)
    }
  }, [refresh])

  return { caps, health, loading, error, refreshing, reload, refresh }
}

/** 按 scene 归组：一个场景可挂多个工具（决策 22） */
export function groupByScene(caps: CapabilitySummary[]): Map<string, CapabilitySummary[]> {
  const m = new Map<string, CapabilitySummary[]>()
  for (const c of caps) {
    const arr = m.get(c.scene) ?? []
    arr.push(c)
    m.set(c.scene, arr)
  }
  return m
}
