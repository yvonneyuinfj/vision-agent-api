/** 生命周期卡片（流式协议"卡片只推不改"）：创建卡 / 完成卡 / 报告就绪卡。
 *
 * 每张卡渲染**落卡时刻的快照**（payload 由后端在落卡时固化），任务后续
 * 状态翻转不改写既有卡片——创建、复核、完成、报告就绪各推独立新卡。
 * 报告卡「查看报告」沿用现有 React 报告展开逻辑：点击展开在对话内渲染
 * report.md 全文（tc-report-doc 样式与 Md 组件）。
 */
import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { SCENE_MAP } from '../../constants/scene'
import { GRADE_META } from '../../constants/task'
import Md from '../../components/markdown/Md'
import StatusTag from '../../components/status/StatusTag'
import type { GradeLevel, TaskStatus } from '../../api/types'

function sceneLabel(code: string | null | undefined): string {
  return code ? (SCENE_MAP[code as keyof typeof SCENE_MAP] ?? code) : '—'
}

function modeLabel(mode: string | null | undefined): string {
  return mode === 'agent' ? '智能体' : mode === 'joint' ? '联合' : '流水线'
}

/* ---------------- 创建卡（launch_inspection 派发即落，快照恒为 running） ---------------- */

export interface TaskCardPayload {
  task_id?: string
  status?: string
  scene?: string | null
  mode?: string | null
  note?: string
  image_count?: number
}

export function TaskCard({ taskId: frameTaskId, payload }: { taskId: string | null; payload: TaskCardPayload }) {
  const navigate = useNavigate()
  const taskId = payload.task_id ?? frameTaskId ?? ''
  const status = (payload.status ?? 'running') as TaskStatus
  return (
    <div className="msg msg-assistant">
      <div
        className="bubble assistant task-card"
        role="button"
        tabIndex={0}
        onClick={() => taskId && navigate(`/tasks/${taskId}`)}
        onKeyDown={(e) => { if ((e.key === 'Enter' || e.key === ' ') && taskId) navigate(`/tasks/${taskId}`) }}
      >
        <div className="tc-top">
          <span className="tc-kind">质检任务 · 创建</span>
          <span className="mono tc-id">{taskId}</span>
          <span className="grow" />
          <StatusTag status={status} />
        </div>
        <div className="tc-meta">
          <span>场景：{sceneLabel(payload.scene)}</span>
          <span>模式：{modeLabel(payload.mode)}</span>
          {typeof payload.image_count === 'number' && <span>图片：{payload.image_count} 张</span>}
        </div>
        {payload.note && <div className="tc-note">备注：{payload.note}</div>}
        <div className="tc-go">任务执行中，落定后将推送完成卡 · 查看详情 →</div>
      </div>
    </div>
  )
}

/* ---------------- 完成卡（任务落定：结论 + 报告状态快照） ---------------- */

export interface DoneCardPayload {
  task_id?: string
  status?: string
  scene?: string | null
  mode?: string | null
  grade_level?: string | null
  grade_summary?: string
  defect_count?: number
  error_message?: string | null
  report?: { status?: string; uri?: string | null }
}

export function DoneCard({ taskId: frameTaskId, payload }: { taskId: string | null; payload: DoneCardPayload }) {
  const navigate = useNavigate()
  const taskId = payload.task_id ?? frameTaskId ?? ''
  const status = (payload.status ?? 'completed') as TaskStatus
  const failed = status === 'failed'
  const gradeMeta = payload.grade_level ? GRADE_META[payload.grade_level as GradeLevel] : null
  const reportStatus = payload.report?.status ?? 'none'
  return (
    <div className="msg msg-assistant">
      <div
        className="bubble assistant task-card done-card"
        role="button"
        tabIndex={0}
        onClick={() => taskId && navigate(`/tasks/${taskId}`)}
        onKeyDown={(e) => { if ((e.key === 'Enter' || e.key === ' ') && taskId) navigate(`/tasks/${taskId}`) }}
      >
        <div className="tc-top">
          <span className={`tc-kind${failed ? ' danger' : ''}`}>{failed ? '质检任务 · 失败' : '质检任务 · 完成'}</span>
          <span className="mono tc-id">{taskId}</span>
          <span className="grow" />
          <StatusTag status={status} />
        </div>
        <div className="tc-meta">
          <span>场景：{sceneLabel(payload.scene)}</span>
          <span>模式：{modeLabel(payload.mode)}</span>
        </div>
        {failed ? (
          <div className="dc-error">⚠ {payload.error_message || '任务执行失败'}</div>
        ) : (
          <>
            <div className="tc-meta">
              {typeof payload.defect_count === 'number' && <span>检出 {payload.defect_count} 项</span>}
              {gradeMeta && <span className="tc-grade" data-level={payload.grade_level}>综合判级：{gradeMeta.label}</span>}
            </div>
            {payload.grade_summary && <div className="dc-summary">{payload.grade_summary}</div>}
            <div className="dc-report">
              <span className="dc-report-label">报告状态</span>
              {reportStatus === 'ready' && <span className="dc-report-ready">📄 已生成</span>}
              {reportStatus === 'generating' && <span className="dc-report-gen"><span className="spin sm" /> 生成中…</span>}
              {reportStatus === 'none' && <span className="dc-report-none">无报告</span>}
            </div>
          </>
        )}
        <div className="tc-go">查看详情 →</div>
      </div>
    </div>
  )
}

/* ---------------- 报告就绪卡（点击查看：展开报告全文，沿用现有 React 展开逻辑） ---------------- */

export interface ReportCardPayload {
  task_id?: string
  scene?: string | null
  grade_level?: string | null
  defect_count?: number
  report?: { status?: string; uri?: string | null; analysis_lead?: string; created_at?: string }
}

export function ReportCard({ taskId: frameTaskId, payload }: { taskId: string | null; payload: ReportCardPayload }) {
  const navigate = useNavigate()
  const taskId = payload.task_id ?? frameTaskId ?? ''
  const reportUri = payload.report?.uri ?? null
  const gradeMeta = payload.grade_level ? GRADE_META[payload.grade_level as GradeLevel] : null
  const lead = payload.report?.analysis_lead ?? ''

  // 报告全文（会话内展示，与现有 React 报告展开逻辑一致）：
  // 有 uri 后拉取一次并缓存；点击展开查看报告内容（默认展开）
  const [reportMd, setReportMd] = useState<string | null>(null)
  const [reportOpen, setReportOpen] = useState(true)
  useEffect(() => {
    if (!reportUri || reportMd !== null) return
    let alive = true
    fetch(reportUri)
      .then((r) => (r.ok ? r.text() : ''))
      .then((t) => { if (alive) setReportMd(t || '') })
      .catch(() => { if (alive) setReportMd('') })
    return () => { alive = false }
  }, [reportUri, reportMd])

  return (
    <div className="msg msg-assistant">
      <div className="bubble assistant task-card report-card">
        <div className="tc-top">
          <span className="tc-kind success">智能质检报告</span>
          <span className="mono tc-id">{taskId}</span>
          <span className="grow" />
          {gradeMeta && <span className="tc-grade" data-level={payload.grade_level}>判级：{gradeMeta.label}</span>}
        </div>
        <div className="tc-meta">
          <span>场景：{sceneLabel(payload.scene)}</span>
          {typeof payload.defect_count === 'number' && <span>检出 {payload.defect_count} 项</span>}
          <span className="dc-report-ready">📄 报告已就绪</span>
        </div>
        {lead && <div className="tc-report-lead">{lead}</div>}

        {reportUri && (
          <div className="tc-report-doc">
            <button type="button" className="tc-report-doc-h" onClick={() => setReportOpen((o) => !o)}>
              <span className={`tc-doc-chev${reportOpen ? ' open' : ''}`}>▸</span>
              {reportOpen ? '收起报告内容' : '查看报告'}
              <span className="mono faint">report.md</span>
            </button>
            {reportOpen && (
              <div className="tc-report-doc-body">
                {reportMd === null && <div className="rc-loading">报告内容加载中…</div>}
                {reportMd === '' && <div className="rc-loading">报告内容为空或加载失败。</div>}
                {reportMd !== null && reportMd !== '' && <Md>{reportMd}</Md>}
              </div>
            )}
          </div>
        )}
        <button
          type="button"
          className="tc-report-link"
          onClick={() => taskId && navigate(`/tasks/${taskId}/report`)}
        >
          打开独立报告页 →
        </button>
      </div>
    </div>
  )
}
