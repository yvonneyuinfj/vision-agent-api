import type { CapabilityHealth, RagflowStatus } from '../api/system'

/** 系统状态文案（AppLayout 环境徽标与 SettingsPage 详情共用，保证口径一致） */

export type StatusTone = 'ok' | 'error' | 'muted'

export function clusterText(caps: CapabilityHealth[] | null): { label: string; tone: StatusTone } {
  if (caps === null) return { label: '推理集群 · 未知', tone: 'muted' }
  if (caps.length === 0) return { label: '推理集群 · 未知', tone: 'muted' }
  return caps.every((c) => c.healthy)
    ? { label: '推理集群 · 正常', tone: 'ok' }
    : { label: '推理集群 · 异常', tone: 'error' }
}

export function ragflowText(status: RagflowStatus | null): { label: string; tone: StatusTone } {
  if (status === null) return { label: 'RAGFlow · 未知', tone: 'muted' }
  if (!status.configured) return { label: 'RAGFlow · 未配置', tone: 'muted' }
  return status.reachable ? { label: 'RAGFlow · 已连接', tone: 'ok' } : { label: 'RAGFlow · 断开', tone: 'error' }
}
