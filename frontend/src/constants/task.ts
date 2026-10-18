import type { TaskStatus, GradeLevel } from '../api/types'

/**
 * 状态/判级元数据。颜色对齐 新ui.html / AntD 浅色主题（base.css light token）：
 * 蓝 #1677ff 执行中、绿 #52c41a 完成、橙 #fa8c16 待复核、红 #f5222d 失败。
 */
export const STATUS_META: Record<TaskStatus, { label: string; color: string; tint: string }> = {
  pending: { label: '排队中', color: '#8c8c8c', tint: 't-pending' },
  running: { label: '推理中', color: '#1677ff', tint: 't-running' },
  awaiting_review: { label: '待复核', color: '#fa8c16', tint: 't-review' },
  completed: { label: '已完成', color: '#52c41a', tint: 't-success' },
  failed: { label: '失败', color: '#f5222d', tint: 't-failed' },
  canceled: { label: '已取消', color: '#8c8c8c', tint: 't-pending' },
}

export const GRADE_META: Record<GradeLevel, { label: string; color: string; icon: string }> = {
  pass: { label: '通过', color: '#52c41a', icon: 'check-circle' },
  review: { label: '需人工复核', color: '#fa8c16', icon: 'warning' },
  fail: { label: '不合格', color: '#f5222d', icon: 'close-circle' },
  defect: { label: '有缺陷 · 建议回修', color: '#faad14', icon: 'exclamation-circle' },
}