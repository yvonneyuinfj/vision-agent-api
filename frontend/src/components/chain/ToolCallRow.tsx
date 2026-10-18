/** 工具行 —— ToolRow 语义（共识轮）：
 *
 * - 调用即出现（running：扫光）；结果到达后**原地更新同一行**（done）；
 * - 收起 = 工具标题 + 参数一行摘要；错误行摘要 = 失败首行（红）；
 * - 展开 = IN（完整参数）/ OUT（完整结果）分节，内滚；
 * - 前导位：正常 = 图标；错误 = 红点；中断（任务落定仍未完成）= 琥珀点。
 */
import { useState, type KeyboardEvent } from 'react'
import type { ToolCallPayload } from './chain'
import { argsSummary } from './chain'
import { TOOL_LABELS } from '../../utils/activity'

export type ToolRowState = 'running' | 'ok' | 'error' | 'stopped'

export function ToolCallRow({ data, state }: { data: ToolCallPayload; state: ToolRowState }) {
  const [expanded, setExpanded] = useState(false)
  const title = TOOL_LABELS[data.name] ?? data.name
  const isError = state === 'error'
  // 错误行收起摘要 = 失败首行（errorSummary 语义）
  const failFirstLine = isError && data.content
    ? data.content.split('\n')[0]
    : ''
  const summary = failFirstLine || argsSummary(data.args)
  const running = state === 'running'
  const expandable = Object.keys(data.args ?? {}).length > 0 || Boolean(data.content)

  const toggle = () => { if (expandable) setExpanded((v) => !v) }
  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    if (!expandable || (e.key !== 'Enter' && e.key !== ' ')) return
    e.preventDefault()
    toggle()
  }

  const open = expanded && expandable
  return (
    <div
      className="cr-root"
      data-variant="tool"
      data-state={state}
      data-open={open || undefined}
    >
      {running && <span className="cr-vh">执行中</span>}
      <div
        className="cr-head"
        role={expandable ? 'button' : undefined}
        tabIndex={expandable ? 0 : undefined}
        aria-expanded={expandable ? open : undefined}
        onClick={expandable ? toggle : undefined}
        onKeyDown={expandable ? onKeyDown : undefined}
      >
        <span className="cr-leading" aria-hidden>
          {isError || state === 'stopped'
            ? <span className={`cr-state-dot ${state}`} />
            : <span className="cr-dot" />}
          {expandable && <span className={`cr-chev${open ? ' open' : ''}`}>▸</span>}
        </span>
        <span className="cr-title">{title}</span>
        {summary && (
          <>
            <span className="cr-sep" aria-hidden />
            <span className="cr-summary">{summary}</span>
          </>
        )}
      </div>
      {open && (
        <div className="cr-body">
          {Object.keys(data.args ?? {}).length > 0 && (
            <>
              <div className="cr-gutter">IN</div>
              <div className="cr-pre">{JSON.stringify(data.args, null, 2)}</div>
            </>
          )}
          {data.content !== undefined && (
            <>
              <div className="cr-gutter">OUT</div>
              <div className="cr-pre">{data.content}</div>
            </>
          )}
        </div>
      )}
    </div>
  )
}
