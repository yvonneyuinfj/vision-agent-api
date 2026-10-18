/* 横向条形统计（决策 23 的列对齐规格）：标签 / 条形 / 数量 / 占比四列定宽，
   数字用等宽字形对齐，避免"数量与百分比同时显示时错位"。 */
import type { ReactNode } from 'react'

export interface MeterRow {
  label: ReactNode
  value: number
  /** 传入则显示占比列（如 '12.0%'） */
  pct?: string
  tone?: string
}

export default function MeterRows({
  rows,
  max,
  head = '动作',
  showPct = true,
}: {
  rows: MeterRow[]
  max?: number
  head?: ReactNode
  showPct?: boolean
}) {
  const peak = max ?? Math.max(1, ...rows.map((r) => r.value))
  return (
    <div className="meters">
      <div className="brow bhead">
        <span className="blab">{head}</span>
        <span className="meter" />
        <span className="bnum">数量</span>
        {showPct && <span className="bpct">占比</span>}
      </div>
      {rows.map((r, i) => (
        <div className="brow" key={i}>
          <span className="blab">{r.label}</span>
          <span className="meter">
            <span
              className="meter-fill"
              style={{ width: `${Math.round((r.value / peak) * 100)}%`, background: r.tone }}
            />
          </span>
          <span className="bnum">{r.value}</span>
          {showPct && <span className="bpct">{r.pct ?? '—'}</span>}
        </div>
      ))}
    </div>
  )
}
