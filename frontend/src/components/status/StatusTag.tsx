import { STATUS_META } from '../../constants/task'
import type { TaskStatus } from '../../api/types'

export default function StatusTag({ status }: { status: TaskStatus }) {
  const meta = STATUS_META[status]
  return (
    <span className={`tag ${meta.tint}`}>
      {status === 'running' && <span className="spin" />}
      {meta.label}
    </span>
  )
}