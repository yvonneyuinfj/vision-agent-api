import type { DefectItem } from '../api/types'

/** 人工修订 diff（与后端 human_review._defect_revision_diff 同构）：
 * 按 defect_id 对齐新旧表（id 缺失时索引兜底），一条只计一次，排除优先。 */
export interface DefectDiff {
  added: number
  excluded: number
  reclassified: number
  severity_changed: number
  moved: number
  removed: number
}

function keyOf(d: DefectItem, i: number): string {
  return d.defect_id || `__idx_${i}`
}

/** bbox 比较与后端一致：只看契约键 location.bbox（存量 bbox_xyxy 不参与） */
function bboxOf(d: DefectItem): number[] | null {
  return d.location?.bbox ?? null
}

function sameBBox(a: number[] | null, b: number[] | null): boolean {
  if (a === b) return true
  if (!a || !b || a.length !== b.length) return false
  return a.every((v, i) => v === b[i])
}

export function defectDiff(baseline: DefectItem[], draft: DefectItem[]): DefectDiff {
  const origByKey = new Map<string, DefectItem>()
  baseline.forEach((d, i) => origByKey.set(keyOf(d, i), d))
  const diff: DefectDiff = {
    added: 0, excluded: 0, reclassified: 0,
    severity_changed: 0, moved: 0, removed: 0,
  }
  const seen = new Set<string>()
  draft.forEach((d, i) => {
    const key = keyOf(d, i)
    seen.add(key)
    const old = origByKey.get(key)
    if (!old || d.source === 'manual') {
      diff.added += 1
      return
    }
    if (d.human_rejected && !old.human_rejected) {
      diff.excluded += 1
      return
    }
    if (d.defect_type !== old.defect_type) diff.reclassified += 1
    if (d.severity !== old.severity) diff.severity_changed += 1
    if (!sameBBox(bboxOf(d), bboxOf(old))) diff.moved += 1
  })
  origByKey.forEach((_, key) => {
    if (!seen.has(key)) diff.removed += 1
  })
  return diff
}

export function hasRevision(diff: DefectDiff): boolean {
  return Object.values(diff).some((v) => v > 0)
}

/** 一行中文摘要（复核面板 chip 与复核意见留痕共用） */
export function diffSummaryText(diff: DefectDiff): string {
  const parts: string[] = []
  if (diff.added) parts.push(`新增 ${diff.added}`)
  if (diff.excluded) parts.push(`排除 ${diff.excluded}`)
  if (diff.reclassified) parts.push(`改类 ${diff.reclassified}`)
  if (diff.severity_changed) parts.push(`严重度调整 ${diff.severity_changed}`)
  if (diff.moved) parts.push(`移框 ${diff.moved}`)
  if (diff.removed) parts.push(`移除 ${diff.removed}`)
  return parts.join('、')
}
