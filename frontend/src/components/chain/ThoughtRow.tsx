/** Think 行 —— ReasoningRow 语义（共识轮）：
 *
 * - 永远默认收起（`useState(false)`）；
 * - 收起摘要：落定 = 思考首句（firstLine）；运行中 = 思考尾句（latestLine）
 *   且横向滚动跟随行尾；
 * - 运行信号 = 扫光（CSS data-state=running），不靠展开内容；
 * - 展开体 = 思考全文，内滚。
 */
import { useEffect, useRef, useState, type KeyboardEvent } from 'react'
import { firstLine, latestLine } from './chain'

export function ThoughtRow({ text, running = false }: { text: string; running?: boolean }) {
  const [expanded, setExpanded] = useState(false)
  const summaryRef = useRef<HTMLSpanElement>(null)
  const summary = running ? latestLine(text) : firstLine(text)

  // 运行中摘要跟随行尾（data-follow-end 语义；思考按轮到达，无节流需求）
  useEffect(() => {
    const el = summaryRef.current
    if (!el) return
    el.scrollLeft = running ? el.scrollWidth - el.clientWidth : 0
  }, [running, summary])

  const toggle = () => setExpanded((v) => !v)
  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    if (e.key !== 'Enter' && e.key !== ' ') return
    e.preventDefault()
    toggle()
  }

  return (
    <div
      className="cr-root"
      data-variant="think"
      data-state={running ? 'running' : 'ok'}
      data-open={expanded || undefined}
    >
      {running && <span className="cr-vh">思考中</span>}
      <div
        className="cr-head"
        role="button"
        tabIndex={0}
        aria-expanded={expanded}
        onClick={toggle}
        onKeyDown={onKeyDown}
      >
        <span className="cr-leading" aria-hidden>
          <span className="cr-dot" />
          <span className={`cr-chev${expanded ? ' open' : ''}`}>▸</span>
        </span>
        <span className="cr-title">思考</span>
        <span className="cr-sep" aria-hidden />
        <span ref={summaryRef} className="cr-summary">{summary}</span>
      </div>
      {expanded && <div className="cr-body cr-think-body">{text}</div>}
    </div>
  )
}
