import type { HitGroup } from './format'
import { highlightSentence, truncateSnippet } from './format'

interface ResultListProps {
  groups: HitGroup[]
  activeKey: string | null
  query: string
  onPreview: (group: HitGroup) => void
}

export function ResultList({ groups, activeKey, query, onPreview }: ResultListProps) {
  if (groups.length === 0) return null
  return (
    <div className="kb-results">
      {groups.map((group) => {
        const snippet = truncateSnippet(group.hits[0].content)
        const active = group.key === activeKey
        return (
          // button 内嵌 p 非法，改用 div + role="button"（brief Step 3 修正选项一）
          <div
            key={group.key}
            role="button"
            tabIndex={0}
            className={`result-card${active ? ' is-active' : ''}`}
            onClick={() => onPreview(group)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault()
                onPreview(group)
              }
            }}
          >
            <div className="result-card__head">
              <h3 className="result-card__title">{group.docName}</h3>
              <span className="result-card__meta">
                <span className="result-card__tag">命中 {group.hits.length} 段</span>
                <span className="result-card__score">{group.topScore.toFixed(2)}</span>
              </span>
            </div>
            <p className="result-card__snippet">{highlightSentence(snippet, query)}</p>
          </div>
        )
      })}
    </div>
  )
}