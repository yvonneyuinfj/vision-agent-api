import type { ReactNode } from 'react'
import { GRADE_META } from '../../constants/task'
import type { GradeInfo } from '../../api/types'

/* 线性 SVG 判级图标（替代 emoji，保持全线性风格） */
const ICONS: Record<string, ReactNode> = {
  'check-circle': (
    <svg width={18} height={18} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8}>
      <circle cx="12" cy="12" r="9" />
      <path d="m8.5 12.2 2.4 2.4 4.6-4.9" />
    </svg>
  ),
  warning: (
    <svg width={18} height={18} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8}>
      <path d="M12 3.5 21.5 20h-19L12 3.5Z" />
      <path d="M12 10v4.5M12 17.6v.2" />
    </svg>
  ),
  'close-circle': (
    <svg width={18} height={18} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8}>
      <circle cx="12" cy="12" r="9" />
      <path d="m9 9 6 6M15 9l-6 6" />
    </svg>
  ),
  'exclamation-circle': (
    <svg width={18} height={18} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8}>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 7.5V13M12 16.5v.2" />
    </svg>
  ),
}

const TINT: Record<string, string> = {
  pass: 't-success',
  review: 't-review',
  fail: 't-failed',
  defect: 't-review',
}

/** 判级结论横幅：tinted 胶囊 + 线性图标 + 结论摘要（无左侧色条） */
export default function GradeBanner({ grade }: { grade: GradeInfo | null }) {
  if (!grade) return null
  const meta = GRADE_META[grade.level]
  return (
    <div
      className={`tag ${TINT[grade.level] ?? 't-accent'}`}
      style={{ display: 'flex', gap: 10, padding: '10px 16px', borderRadius: 10, fontSize: 13 }}
    >
      <span style={{ display: 'inline-flex', marginTop: 1 }}>{ICONS[meta.icon] ?? ICONS.warning}</span>
      <span>
        <b style={{ display: 'inline-block', marginRight: 8, fontSize: 13.5 }}>{meta.label}</b>
        <span style={{ fontWeight: 440 }}>{grade.summary}</span>
      </span>
    </div>
  )
}