type PanelSide = 'left' | 'right'

export function PanelToggleButton({
  side,
  label,
  collapsed,
  controls,
  onToggle,
}: {
  side: PanelSide
  label: string
  collapsed: boolean
  controls: string
  onToggle: () => void
}) {
  const actionLabel = `${collapsed ? '展开' : '收起'}${label}`
  const pointsLeft = side === 'left' ? !collapsed : collapsed

  return (
    <button
      type="button"
      className="chat-panel-toggle"
      aria-label={actionLabel}
      aria-controls={controls}
      aria-expanded={!collapsed}
      title={actionLabel}
      onClick={onToggle}
    >
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true">
        <path d={pointsLeft ? 'm14.5 5-7 7 7 7' : 'm9.5 5 7 7-7 7'} />
      </svg>
    </button>
  )
}
