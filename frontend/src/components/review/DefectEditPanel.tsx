/* 缺陷修订面板（决策 Q2/Q5）：草稿条目列表（改类/改严重度/排除/删除）+
   新增漏检框开关 + diff 摘要。Modal 与复核工作台共享；数据源 useDefectEditStore。
   操作语义：
   - 排除 = 人工判定误检，条目保留（human_rejected 标记，报告并入虚假显示口径）；
   - 模型条目不可硬删（审计留痕），人工新增条目可删；
   - 改类/改严重度自动落 revised 原值快照（后端 diff 与报告修订叙述用）。 */
import { useMemo } from 'react'
import {
  NEW_DEFECT_TYPE,
  hasUntypedManual,
  selectDiff,
  useDefectEditStore,
} from '../../store/useDefectEditStore'
import { diffSummaryText, hasRevision } from '../../utils/defectDiff'
import type { ClassDefItem } from '../../api/types'
import './DefectEditPanel.css'

interface Props {
  /** 工具类别定义表：改类下拉的候选与中文名转译（无定义表时退化为存量类型集合） */
  classDefs?: ClassDefItem[]
}

const SEV_OPTIONS: Array<{ value: string; label: string }> = [
  { value: '', label: '未分级' },
  { value: 'low', label: 'Low' },
  { value: 'medium', label: 'Medium' },
  { value: 'high', label: 'High' },
]

export default function DefectEditPanel({ classDefs }: Props) {
  const draft = useDefectEditStore((s) => s.draft)
  const baseline = useDefectEditStore((s) => s.baseline)
  const tool = useDefectEditStore((s) => s.tool)
  const selectedId = useDefectEditStore((s) => s.selectedId)
  const setTool = useDefectEditStore((s) => s.setTool)
  const setSelectedId = useDefectEditStore((s) => s.setSelectedId)
  const patchDefect = useDefectEditStore((s) => s.patchDefect)
  const toggleReject = useDefectEditStore((s) => s.toggleReject)
  const removeDefect = useDefectEditStore((s) => s.removeDefect)
  const resetDraft = useDefectEditStore((s) => s.resetDraft)

  const diff = useMemo(() => selectDiff({ baseline, draft }), [baseline, draft])
  const revised = hasRevision(diff)
  const untyped = hasUntypedManual({ draft })

  /** 改类候选：类别定义表 ∪ 基线/草稿存量类型（保持出现序） */
  const typeOptions = useMemo(() => {
    const names: string[] = []
    for (const c of classDefs ?? []) if (c.class_name && !names.includes(c.class_name)) names.push(c.class_name)
    for (const d of [...baseline, ...draft]) {
      if (d.defect_type && d.defect_type !== NEW_DEFECT_TYPE && !names.includes(d.defect_type)) names.push(d.defect_type)
    }
    return names
  }, [classDefs, baseline, draft])

  function typeLabel(t: string): string {
    const def = (classDefs ?? []).find((c) => c.class_name === t)
    return def?.description ? `${t}（${def.description}）` : t
  }

  return (
    <div className="dep">
      <div className="dep-head">
        <b>缺陷修订</b>
        <span className={`dep-diff${revised ? '' : ' none'}`}>
          {revised ? diffSummaryText(diff) : '暂无修订'}
        </span>
      </div>

      <div className="dep-tools">
        <button
          type="button"
          className={`btn btn-sm${tool === 'add' ? ' btn-primary' : ''}`}
          onClick={() => setTool(tool === 'add' ? 'select' : 'add')}
        >
          {tool === 'add' ? '画框中：图上拖拽…' : '＋ 新增漏检框'}
        </button>
        <button type="button" className="btn btn-sm dep-reset" disabled={!revised} onClick={resetDraft}>
          重置修订
        </button>
      </div>

      {untyped && (
        <div className="dep-guard">存在未选择类型的人工新增条目（A 号见列表），提交前请先指定缺陷类型。</div>
      )}

      <div className="dep-list">
        {draft.map((d, i) => {
          const id = d.defect_id ?? `__idx_${i}`
          const rejected = Boolean(d.human_rejected)
          const manual = d.source === 'manual'
          return (
            <div
              key={id}
              className={`dep-row${selectedId === id ? ' selected' : ''}${rejected ? ' rejected' : ''}`}
              onClick={() => setSelectedId(id)}
            >
              <span className="dep-id">A{i + 1}</span>
              <div className="dep-fields">
                <select
                  className="select dep-sel"
                  aria-label={`缺陷类型 A${i + 1}`}
                  disabled={rejected}
                  value={d.defect_type}
                  onChange={(e) => {
                    const t = e.target.value
                    const def = (classDefs ?? []).find((c) => c.class_name === t)
                    patchDefect(id, {
                      defect_type: t,
                      defect_name_cn: def?.description ?? (manual ? '人工补框' : d.defect_name_cn),
                    })
                  }}
                >
                  {d.defect_type === NEW_DEFECT_TYPE && <option value={NEW_DEFECT_TYPE}>未分类…</option>}
                  {!d.defect_type && <option value="">未分类…</option>}
                  {typeOptions.map((t) => (
                    <option key={t} value={t}>{typeLabel(t)}</option>
                  ))}
                </select>
                <select
                  className="select dep-sel"
                  aria-label={`严重度 A${i + 1}`}
                  disabled={rejected}
                  value={d.severity ?? ''}
                  onChange={(e) =>
                    patchDefect(id, { severity: (e.target.value || null) as 'low' | 'medium' | 'high' | null })
                  }
                >
                  {SEV_OPTIONS.map((s) => (
                    <option key={s.value} value={s.value}>{s.label}</option>
                  ))}
                </select>
              </div>
              <div className="dep-acts">
                {manual && <span className="dep-tag">人工</span>}
                <button
                  type="button"
                  className="dep-btn"
                  onClick={(e) => {
                    e.stopPropagation()
                    toggleReject(id)
                  }}
                >
                  {rejected ? '恢复' : '排除'}
                </button>
                {manual && (
                  <button
                    type="button"
                    className="dep-btn danger"
                    onClick={(e) => {
                      e.stopPropagation()
                      removeDefect(id)
                    }}
                  >
                    删除
                  </button>
                )}
              </div>
            </div>
          )
        })}
        {draft.length === 0 && <div className="dep-empty">无检出条目；可点「新增漏检框」在图上补框</div>}
      </div>

      <p className="dep-hint">
        排除 = 判定误检（条目保留供审计，报告并入虚假显示口径）；模型条目不可删除，仅可排除。
        修订需选择「改判 / 修正」并填写复核意见后提交生效。
      </p>
    </div>
  )
}
