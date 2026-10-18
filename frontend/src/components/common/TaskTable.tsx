import { Link } from 'react-router-dom'
import type { TaskSummary } from '../../api/types'
import StatusTag from '../status/StatusTag'
import { formatBeijing } from '../../utils/time'
import { SCENE_MAP } from '../../constants/scene'
import type { SceneCode } from '../../api/types'

/* 紧凑任务表（决策 23）：用于窄容器（工作台本场景最近任务、复核队列、样本窗口）。
   三列固定：任务（ID + 时间堆叠）｜状态｜耗时（右对齐等宽，杜绝溢出）。 */

/* 列表接口的 finished_at 对历史任务可能为空；终局态回退到 updated_at（报告撰写即最后一次更新），
   避免整列耗时恒为"—"。 */
const TERMINAL: ReadonlySet<TaskSummary['status']> = new Set(['completed', 'failed', 'canceled'])

export function taskDuration(t: TaskSummary): string {
  if (!t.created_at) return '—'
  const end = t.finished_at ?? (TERMINAL.has(t.status) ? t.updated_at : null)
  if (!end) return t.status === 'running' ? '进行中' : '—'
  const seconds = Math.round((new Date(`${end}Z`).getTime() - new Date(`${t.created_at}Z`).getTime()) / 1000)
  if (!Number.isFinite(seconds) || seconds <= 0) return '—'
  return seconds >= 60 ? `${Math.floor(seconds / 60)}m ${String(seconds % 60).padStart(2, '0')}s` : `${seconds}s`
}

const modeText = (mode: TaskSummary['mode']) => (mode === 'joint' ? '联合研判' : mode === 'agent' ? '智能体' : '单场景')

export default function TaskTable({
  rows,
  withScene = false,
  emptyText = '窗口内暂无任务',
}: {
  rows: TaskSummary[]
  withScene?: boolean
  emptyText?: string
}) {
  if (!rows.length) return <div className="empty">{emptyText}</div>
  return (
    <table className="tbl compact">
      <thead>
        <tr>
          <th>任务</th>
          {withScene && <th>场景</th>}
          <th>状态</th>
          <th className="num">耗时</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((t) => (
          <tr key={t.task_id}>
            <td>
              <Link className="task-id" to={`/tasks/${t.task_id}`}>
                {t.task_id}
              </Link>
              {t.parent_task_id && (
                <span className="tag t-child" title={`联合子任务 · 父任务 ${t.parent_task_id}`}>
                  子
                </span>
              )}
              <div className="sub">
                {formatBeijing(t.created_at, 'MM-DD HH:mm')} · {modeText(t.mode)}
              </div>
            </td>
            {withScene && <td>{(t.scene && SCENE_MAP[t.scene as SceneCode]) ?? t.scene ?? '—'}</td>}
            <td>
              <StatusTag status={t.status} />
            </td>
            <td className="num cost">{taskDuration(t)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}
