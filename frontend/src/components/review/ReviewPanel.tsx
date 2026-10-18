import { useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import { message } from 'antd'
import {
  CheckCircleOutlined,
  EditOutlined,
  RollbackOutlined,
  StopOutlined,
} from '@ant-design/icons'
import { useTaskStore } from '../../store/useTaskStore'
import { hasUntypedManual, useDefectEditStore } from '../../store/useDefectEditStore'
import { defectDiff, diffSummaryText, hasRevision } from '../../utils/defectDiff'
import type { DefectItem, GradeLevel, ReviewAction } from '../../api/types'

/** 复核动作卡（文案与 review-workbench.html 一致；与后端 ReviewAction 枚举一一对应） */
const ACTIONS: Array<{ action: ReviewAction; label: string; desc: string; danger?: boolean }> = [
  { action: 'confirm', label: '同意 AI 判级', desc: '确认 A1 / A2 判定无误，流转至报告生成节点。' },
  { action: 'revise', label: '改判 / 修正', desc: '修改缺陷类型与严重度；在图像上补框漏检、删除误检。' },
  { action: 'requeue', label: '退回重检', desc: '图像质量不合格，退回任务提交节点重新采集。' },
  { action: 'reject', label: '驳回', desc: '任务数据不符合规范，直接终止本次处理流程。', danger: true },
]

/** 动作图标：与 data-action 语义色配合（base.css .act-ico） */
const ACTION_ICONS: Record<ReviewAction, ReactNode> = {
  confirm: <CheckCircleOutlined />,
  revise: <EditOutlined />,
  requeue: <RollbackOutlined />,
  reject: <StopOutlined />,
}

/** 退回重检原因（对应设计稿「退回原因」选项，提交时并入复核意见） */
const REQUEUE_REASONS = ['图像模糊 / 对焦失败', '辐射值 CSV 与图像不匹配', '采集区域错误']

const SEVERITIES: Array<{ value: NonNullable<DefectItem['severity']>; label: string }> = [
  { value: 'low', label: 'Low' },
  { value: 'medium', label: 'Medium' },
  { value: 'high', label: 'High' },
]

/** 严重度 → 判级 level（设计稿 hint 原话即言明：改判为 High 后自动附加工卡建议并通知工艺负责人） */
function sevToLevel(sev: string): GradeLevel {
  if (sev === 'high') return 'fail'
  if (sev === 'low') return 'pass'
  return 'review'
}

interface Props {
  taskId: string
  /** AI 检出缺陷：提供「缺陷类型」下拉的候选与初始值（可空，空时仅剩「其他…」） */
  defects?: DefectItem[]
  onSubmitted: () => void
  /** 嵌入三分区结果页第 ③ 区：外层卡框由分区持有，本组件只渲染内容 */
  bare?: boolean
}

/** 改判目标：默认 AI 检出第一项，未检出则落在「其他…」 */
function defectOptions(defects: DefectItem[] | undefined): string[] {
  const names = (defects ?? []).map((d) => d.defect_name_cn || d.defect_type).filter(Boolean)
  return names.length ? [...new Set(names), '其他…'] : ['其他…']
}

/**
 * 人工复核面板：四张动作卡单选 + 条件表单（改判=缺陷类型/严重度，退回=退回原因）+ 底部双按钮。
 * 外层透明卡由本组件渲染（.card），结构对齐设计稿 review-workbench.html。
 */
export default function ReviewPanel({ taskId, defects, onSubmitted, bare }: Props) {
  const submitReview = useTaskStore((s) => s.submitReview)
  const options = useMemo(() => defectOptions(defects), [defects])
  const [action, setAction] = useState<ReviewAction | null>(null)
  const [opinion, setOpinion] = useState('')
  const [defectType, setDefectType] = useState<string>(() => options[0])
  const [severity, setSeverity] = useState<string>(() => defects?.[0]?.severity ?? 'medium')
  const [requeueReason, setRequeueReason] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [err, setErr] = useState('')

  /* 缺陷修订会话（决策 Q4：全量列表契约——revise 提交时 draft 整表上送，
     后端替换 standardized 并生成 diff 留痕；其他动作忽略修订） */
  const editDraft = useDefectEditStore((s) => (s.open && s.taskId === taskId ? s.draft : null))
  const editBaseline = useDefectEditStore((s) => (s.open && s.taskId === taskId ? s.baseline : null))
  const closeEditor = useDefectEditStore((s) => s.closeEditor)
  const revDiff = useMemo(
    () => (editDraft && editBaseline ? defectDiff(editBaseline, editDraft) : null),
    [editBaseline, editDraft],
  )
  const revised = Boolean(revDiff && hasRevision(revDiff))

  function select(a: ReviewAction) {
    if (submitting) return
    setAction(a)
    setErr('')
  }

  async function handleSubmit(finalAction: ReviewAction) {
    // 退回重检的「退回原因」是结构化分类，后端无独立字段，并入意见保留操作记录
    const trimmed = opinion.trim()
    let merged = finalAction === 'requeue' && requeueReason ? `${requeueReason}；${trimmed}` : trimmed
    if (!merged) {
      setErr('请填写复核意见（必填）')
      return
    }
    // 缺陷修订仅在「改判 / 修正」下生效：数据有效性拦截（人工新增须先选类型）
    // + 一致性强提示留痕（决策 Q3：不拦截，摘要并入复核意见）
    const submitDefects = finalAction === 'revise' && revised ? editDraft : null
    if (submitDefects && hasUntypedManual({ draft: submitDefects })) {
      setErr('存在未选择类型的人工新增条目，请先在标注编辑器中指定缺陷类型')
      return
    }
    if (submitDefects && revDiff) {
      merged = `${merged}；[缺陷修订] ${diffSummaryText(revDiff)}`
    }
    setSubmitting(true)
    try {
      await submitReview(taskId, {
        action: finalAction,
        opinion: merged,
        // 改判：level 由严重度推导（保证 LangGraph 流转语义），缺陷类型/严重度原文一并记录
        override_grade:
          finalAction === 'revise'
            ? { level: sevToLevel(severity), summary: merged, severity, defect_type: defectType }
            : null,
        ...(submitDefects ? { defects: submitDefects } : {}),
      })
      message.success('复核已提交')
      // 编辑会话随提交清场：草稿已上送，避免残留到下一任务
      if (editDraft) closeEditor()
      onSubmitted()
    } catch {
      // API 层已弹错误提示，此处保持为空避免重复 toast
    } finally {
      setSubmitting(false)
    }
  }

  const inner = (
    <>
      {ACTIONS.map(({ action: a, label, desc, danger }) => (
          <div
            key={a}
            className={`action-card${action === a ? ' selected' : ''}`}
            data-action={a}
            role="radio"
            aria-checked={action === a}
            tabIndex={0}
            onClick={() => select(a)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' || e.key === ' ') select(a)
            }}
          >
            <span className="radio-dot" />
            <div className="action-t">
              <b style={danger ? { color: 'var(--danger)' } : undefined}>
                <span className="act-ico">{ACTION_ICONS[a]}</span>
                {label}
              </b>
              <p>{desc}</p>
            </div>
          </div>
        ))}

        {action === 'revise' && (
          <div className="mt-16">
            <div className="field">
              <label className="label">
                缺陷类型{defects && defects.length > 0 ? '（A1）' : ''}
                <span className="req">*</span>
              </label>
              <select
                className="select"
                aria-label="缺陷类型"
                value={defectType}
                onChange={(e) => setDefectType(e.target.value)}
              >
                {options.map((o) => (
                  <option key={o}>{o}</option>
                ))}
              </select>
            </div>
            <div className="field mt-12">
              <label className="label">
                严重度<span className="req">*</span>
              </label>
              <select
                className="select"
                aria-label="严重度"
                value={severity}
                onChange={(e) => setSeverity(e.target.value)}
              >
                {SEVERITIES.map((s) => (
                  <option key={s.value} value={s.value}>
                    {s.label}
                  </option>
                ))}
              </select>
              <span className="hint">改判为 High 后，将自动附加工卡建议并通知工艺负责人。</span>
            </div>
          </div>
        )}

        {action === 'requeue' && (
          <div className="mt-16">
            <div className="field">
              <label className="label">
                退回原因<span className="req">*</span>
              </label>
              <select
                className="select"
                aria-label="退回原因"
                value={requeueReason}
                onChange={(e) => setRequeueReason(e.target.value)}
              >
                <option value="">请选择退回原因…</option>
                {REQUEUE_REASONS.map((r) => (
                  <option key={r}>{r}</option>
                ))}
              </select>
            </div>
          </div>
        )}

        {action === 'reject' && (
          <span className="hint mt-16">驳回后任务将终止流转；如需重新采集，请选择「退回重检」。</span>
        )}

        {/* 缺陷修订状态（编辑会话由「人工标注」入口开启，Modal/工作台共用 store） */}
        {editDraft && (
          <div className="rev-draft">
            <div className="rev-draft-h">
              <b>缺陷修订</b>
              <span className={`rev-draft-sum${revised ? '' : ' none'}`}>
                {revised && revDiff ? diffSummaryText(revDiff) : '暂无修订'}
              </span>
              {revised && (
                <button type="button" className="rev-draft-clear" onClick={closeEditor}>
                  清除修订
                </button>
              )}
            </div>
            {revised && action === 'revise' && (
              <span className="hint warn">
                判级一致性提示：缺陷修订不会自动重算判级。缺陷构成已变化时，请同步调整上方改判严重度（不强制拦截，修订摘要将留痕进复核意见）。
              </span>
            )}
            {revised && action !== null && action !== 'revise' && (
              <span className="hint warn">
                当前动作为「{ACTIONS.find((a) => a.action === action)?.label}」：缺陷修订仅随「改判 / 修正」提交生效，如需修订落地请切换动作。
              </span>
            )}
          </div>
        )}

        <div className="field mt-16">
          <label className="label">
            复核意见<span className="req">*</span>
            <span className="opt">必填 · 记录人工干预理由</span>
          </label>
          <textarea
            className="textarea"
            rows={3}
            placeholder="复核意见（必填）"
            value={opinion}
            onChange={(e) => {
              setOpinion(e.target.value)
              setErr('')
            }}
          />
          {err && <span className="hint err">{err}</span>}
        </div>

        <div className="rev-foot">
          <button className="btn btn-danger-ghost" disabled={submitting} onClick={() => void handleSubmit('requeue')}>
            退回重检
          </button>
          <button className="btn btn-primary" disabled={submitting} onClick={() => action && void handleSubmit(action)}>
            {submitting ? '受理中…' : '提交复核 · 继续流水线'}
          </button>
        </div>
        {submitting && (
          <div className="rev-submiting">
            <span className="spin sm" /> 正在提交复核决定，任务即将恢复执行…
          </div>
        )}
    </>
  )

  if (bare) return <div className="review-panel">{inner}</div>

  return (
    <section className="card">
      <div className="card-h">
        <div className="card-title">复核动作</div>
      </div>
      <div className="card-b" style={{ paddingTop: 12 }}>
        {inner}
      </div>
    </section>
  )
}