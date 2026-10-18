import type { ReactNode } from 'react'

/* 分页器（决策 23）：只做"窗口内真分页"。
   全站分页条一律只报「共 N 条」：窗口口径（最近 100 条、接口无 offset）不再上页，见边界文档 A5 节 ④ 段（决策 51-⑪）。
   note 仍保留给"每页大小"这类当页操作口径。 */

export function pageWindow(page: number, pageCount: number): (number | 'gap')[] {
  if (pageCount <= 7) return Array.from({ length: pageCount }, (_, i) => i + 1)
  const nums = [1, pageCount]
  for (let p = page - 1; p <= page + 1; p++) if (p > 1 && p < pageCount) nums.push(p)
  const uniq = [...new Set(nums)].sort((a, b) => a - b)
  const out: (number | 'gap')[] = []
  let prev = 0
  for (const n of uniq) {
    if (prev && n - prev > 1) out.push('gap')
    out.push(n)
    prev = n
  }
  return out
}

export default function Pager({
  total,
  page,
  pageSize,
  onPage,
  onPageSize,
  pageSizeOptions = [5, 10, 20, 50],
  note,
}: {
  total: number
  page: number
  pageSize: number
  onPage: (p: number) => void
  onPageSize?: (size: number) => void
  pageSizeOptions?: number[]
  note?: ReactNode
}) {
  const pageCount = Math.max(1, Math.ceil(total / pageSize))
  return (
    <div className="pager">
      <span className="pinfo">
        共 <b>{total}</b> 条{note ? ` · ${note}` : ''}
      </span>
      {onPageSize && (
        <label className="psize">
          每页
          <select
            value={pageSize}
            onChange={(e) => onPageSize(Number(e.target.value))}
            aria-label="每页条数"
          >
            {pageSizeOptions.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
          条
        </label>
      )}
      <span className="pgrp">
        <button
          type="button"
          className="pbtn"
          disabled={page <= 1}
          onClick={() => onPage(page - 1)}
          aria-label="上一页"
        >
          ‹
        </button>
        {pageWindow(page, pageCount).map((p, i) =>
          p === 'gap' ? (
            <span className="pel" key={`gap-${i}`} aria-hidden="true">
              …
            </span>
          ) : (
            <button
              type="button"
              key={p}
              className={`pbtn${p === page ? ' on' : ''}`}
              aria-current={p === page ? 'page' : undefined}
              onClick={() => onPage(p)}
            >
              {p}
            </button>
          ),
        )}
        <button
          type="button"
          className="pbtn"
          disabled={page >= pageCount}
          onClick={() => onPage(page + 1)}
          aria-label="下一页"
        >
          ›
        </button>
      </span>
    </div>
  )
}
