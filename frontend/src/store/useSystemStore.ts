import { create } from 'zustand'
import { fetchCapabilityHealth, fetchRagflowStatus, fetchServerDate } from '../api/system'
import type { CapabilityHealth, RagflowStatus } from '../api/system'

interface SystemState {
  caps: CapabilityHealth[] | null
  ragflow: RagflowStatus | null
  /** VLM（多模态）可用性：agent 轨入口置灰依据；null=未知 */
  vlmAvailable: boolean | null
  probe: () => Promise<void>
}

// 系统状态探测：挂载/路由切换时各触发一次（网页级共享，不做定时轮询）。
// in-flight 去重：同一次导航中布局与页面组件同时触发只发一个真实请求。
let inflight: Promise<void> | null = null

export const useSystemStore = create<SystemState>((set) => ({
  caps: null,
  ragflow: null,
  vlmAvailable: null,
  probe: async () => {
    if (inflight) return inflight
    inflight = (async () => {
      try {
        const [c, r, h] = await Promise.all([fetchCapabilityHealth(), fetchRagflowStatus(), fetchServerDate()])
        set({ caps: c, ragflow: r, vlmAvailable: h?.vlm_available ?? null })
      } catch {
        // 探测失败静默降级为 null（"未知"），不弹错误
        set({ caps: null, ragflow: null, vlmAvailable: null })
      } finally {
        inflight = null
      }
    })()
    return inflight
  },
}))