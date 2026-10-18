/** 终裁复核交互卡片（流式协议"卡片只推不改"）：任务暂停进入人工复核门
 *  （awaiting_review）时，由后端落定回注在对话内推送一张新卡。
 *
 *  - 卡片渲染**落卡时刻的 AI 预判快照**（payload：判级/摘要/检出明细行），
 *    与复核页（ReviewPage + ReviewPanel）的提交表单完全对齐：
 *    四动作单选（同意/改判/退回重检/驳回）+ 条件字段 + 必填复核意见；
 *  - 激活/落定态不依赖卡片快照翻转，而是挂载时拉一次任务详情取实时状态
 *    （刷新安全；他处复核后回到对话也能正确落定）；
 *  - 就地提交复用 ReviewPanel，改判缺陷类型候选取任务详情检出明细。
 */
import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { AuditOutlined, CheckCircleOutlined } from '@ant-design/icons'
import { fetchTaskDetail } from '../../api/tasks'
import ReviewPanel from '../../components/review/ReviewPanel'
import ReviewImageView from '../../components/review/ReviewImageView'
import DefectEditModal from '../../components/review/DefectEditModal'
import StatusTag from '../../components/status/StatusTag'
import { useDefectEditStore } from '../../store/useDefectEditStore'
import { GRADE_META } from '../../constants/task'
import type { DefectItem, GradeLevel, TaskDetail, TaskStatus } from '../../api/types'

export interface ReviewCardPayload {
  task_id?: string
  status?: string
  scene?: string | null
  grade_level?: string | null
  grade_summary?: string
  defect_count?: number
  defects?: Array<{ name?: string; severity?: string | null; confidence?: number | null }>
}

const SEV_LABEL: Record<string, string> = { high: 'High', medium: 'Medium', low: 'Low' }

interface Props {
  taskId: string
  payload: ReviewCardPayload
  /** 就地提交成功后回调（父层可重载流） */
  onSubmitted: () => void
}

export default function ReviewCard({ taskId, payload, onSubmitted }: Props) {
  const navigate = useNavigate()
  // 就地提交 → 立即落定；激活与否最终以服务端任务实时状态推导（刷新安全）
  const [submitted, setSubmitted] = useState(false)
  // 实时详情：状态（激活/落定）+ 检出明细（改判候选/缺陷框）+ 输入图/标注图产物
  const [detail, setDetail] = useState<TaskDetail | null>(null)
  const [fetchFailed, setFetchFailed] = useState(false)
  // 人工标注编辑 Modal（决策 Q5）：卡片就地打开完整 Konva 编辑器
  const [editOpen, setEditOpen] = useState(false)
  // 修订草稿：编辑会话存续期间，卡片图像区实时预览修订结果（排除灰框/人工绿框）
  const editDraft = useDefectEditStore((s) => (s.open && s.taskId === taskId ? s.draft : null))

  // 挂载即拉任务详情（Q7：review.artifacts 为复核期算法标注图数据源）
  useEffect(() => {
    if (!taskId) return
    let alive = true
    fetchTaskDetail(taskId)
      .then((d) => {
        if (!alive) return
        setDetail(d)
        setFetchFailed(false)
      })
      .catch(() => {
        if (!alive) return
        setDetail(null)
        setFetchFailed(true)
      })
    return () => { alive = false }
  }, [taskId, submitted])

  const status: TaskStatus = (detail?.status as TaskStatus) ?? ((payload.status as TaskStatus) ?? 'awaiting_review')
  const active = status === 'awaiting_review' && !submitted
  const defects: DefectItem[] | null = detail ? detail.standardized ?? [] : fetchFailed ? [] : null

  const gradeLevel = (payload.grade_level ?? null) as GradeLevel | null
  const gradeMeta = gradeLevel ? GRADE_META[gradeLevel] : null
  const summary = payload.grade_summary || ''
  const defectCount = payload.defect_count ?? payload.defects?.length ?? 0

  return (
    <div className="msg msg-assistant">
      <div className="bubble assistant review-card">
        <div className="rc-top">
          <span className="rc-ico">
            <AuditOutlined />
          </span>
          <span className="rc-badge">终裁复核</span>
          <span className="mono rc-id">{taskId}</span>
          <span className="grow" />
          <StatusTag status={status} />
        </div>

        <div className="rc-body">
          {active ? (
            <>
              <div className="rc-why">
                任务已暂停并进入人工复核门{gradeMeta ? <>：AI 预判「{gradeMeta.label}」</> : null}
                {defectCount > 0 ? <>，检出 {defectCount} 项缺陷</> : null}
                {summary ? <>。{summary}</> : null}
              </div>

              {/* 检出明细行（落卡快照，对齐复核页 AI 预判列表） */}
              {Array.isArray(payload.defects) && payload.defects.length > 0 && (
                <div className="rc-defects">
                  {payload.defects.map((d, i) => (
                    <div className="rc-drow" key={i}>
                      <span className="rc-did">A{i + 1}</span>
                      <b>{d.name ?? '未命名缺陷'}</b>
                      <span className="grow" />
                      {d.severity && (
                        <span className="rc-dsev" data-sev={d.severity}>
                          {SEV_LABEL[d.severity] ?? d.severity}
                        </span>
                      )}
                      {typeof d.confidence === 'number' && (
                        <span className="mono rc-dconf">{d.confidence.toFixed(2)}</span>
                      )}
                    </div>
                  ))}
                </div>
              )}

              {/* 内嵌图像区（决策 Q6/Q7）：原图/缺陷框叠加/算法标注图三档，
                  「人工标注」打开结构化编辑 Modal；编辑会话存续期间预览草稿 */}
              {detail && (detail.input_uris?.length || detail.review?.artifacts?.length) ? (
                <ReviewImageView
                  inputUris={detail.input_uris ?? []}
                  defects={editDraft ?? detail.standardized ?? []}
                  artifacts={detail.review?.artifacts ?? detail.report?.artifacts ?? null}
                  onAnnotate={() => setEditOpen(true)}
                />
              ) : null}

              {defects === null ? (
                <div className="rc-loading">检出明细加载中…</div>
              ) : (
                <ReviewPanel
                  key={taskId}
                  taskId={taskId}
                  defects={defects}
                  onSubmitted={() => {
                    setSubmitted(true)
                    onSubmitted()
                  }}
                />
              )}
              <div className="rc-foot">
                <span>需要核对标注图像再定夺？</span>
                <button
                  type="button"
                  className="rc-link"
                  onClick={() => navigate(`/tasks/review?task=${taskId}`)}
                >
                  打开复核工作台 →
                </button>
              </div>
              {detail && (
                <DefectEditModal
                  open={editOpen}
                  taskId={taskId}
                  inputUris={detail.input_uris ?? []}
                  defects={detail.standardized ?? []}
                  artifacts={detail.review?.artifacts ?? detail.report?.artifacts ?? null}
                  classDefs={detail.class_definitions}
                  onClose={() => setEditOpen(false)}
                />
              )}
            </>
          ) : (
            <div className="rc-done">
              <CheckCircleOutlined className="rc-done-ico" />
              <span>
                {submitted
                  ? '已提交终裁复核，流水线继续生成报告。'
                  : '该任务已完成终裁复核，无需人工处置。'}
              </span>
              {gradeMeta && (
                <span className="rc-grade" data-level={gradeLevel}>
                  判级：{gradeMeta.label}
                </span>
              )}
              <button type="button" className="rc-link" onClick={() => navigate(`/tasks/${taskId}`)}>
                查看任务详情 →
              </button>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
