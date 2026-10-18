import { useState, type ReactNode } from 'react'
import './intake.css'
import { useNavigate } from 'react-router-dom'
import { message } from 'antd'
import { submitTask, type SubmitTaskForm } from '../../api/tasks'

/** 提交动作 + 结果弹窗：新建任务页与各专业工作台共用同一套反馈与出口。 */
export function useIntakeSubmit() {
  const [submitting, setSubmitting] = useState(false)
  const [submitted, setSubmitted] = useState<{ taskId: string } | null>(null)

  const submit = async (form: SubmitTaskForm, blockReasons: string[]) => {
    if (blockReasons.length) {
      message.warning(blockReasons.join('；'))
      return false
    }
    setSubmitting(true)
    try {
      const res = await submitTask(form)
      setSubmitted({ taskId: res.task_id })
      return true
    } catch {
      // 错误提示已在拦截器/解包层弹出
      return false
    } finally {
      setSubmitting(false)
    }
  }

  return { submitting, submitted, clearSubmitted: () => setSubmitted(null), submit }
}

/** 保密确认 + 提交按钮（决策：外发操作前须确认）。
 *  校验原因不在这里重复渲染——文件行、联动提示条与提交时的 toast 已各自就近说明。 */
export function IntakeConfirm({
  submitting,
  disabled,
  onSubmit,
  hint = '提交后进入推理队列，可在任务列表实时跟踪状态',
  submitText = '提交检测任务',
}: {
  submitting: boolean
  disabled?: boolean
  onSubmit: () => void
  hint?: string
  submitText?: string
}) {
  const [agreed, setAgreed] = useState(true)
  return (
    <>
      <div className="checked-row">
        <input
          type="checkbox"
          id="intake-agree"
          checked={agreed}
          onChange={(e) => setAgreed(e.target.checked)}
        />
        <label htmlFor="intake-agree">我确认所提交图像与数据符合保密规范，且采集流程已通过现场工艺确认。</label>
      </div>
      <button
        type="button"
        className="btn btn-primary btn-lg"
        style={{ width: '100%' }}
        disabled={submitting || disabled || !agreed}
        onClick={() => onSubmit()}
      >
        {submitting ? '提交中…' : submitText}
      </button>
      {!agreed && <div className="hint err mt-8">请先勾选保密与工艺确认。</div>}
      <div className="hint mt-8" style={{ textAlign: 'center' }}>{hint}</div>
    </>
  )
}

export function SubmitDialog({
  taskId,
  listHref = '/tasks',
  onClose,
  children,
}: {
  taskId: string
  listHref?: string
  onClose: () => void
  children?: ReactNode
}) {
  const navigate = useNavigate()
  return (
    <div className="modal-mask open" role="dialog" aria-modal="true" aria-label="任务已提交">
      <div className="card modal">
        <div className="m-ico">
          <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
            <circle cx="12" cy="12" r="9" />
            <path d="M12 7v5l3.2 2" />
          </svg>
        </div>
        <h3>任务已提交，正在排队推理</h3>
        <p>
          任务号 <b className="mono" style={{ color: 'var(--fg)' }}>{taskId}</b> 已进入推理队列。
          {children ??
            '受负载影响，模型推理可能耗时数秒至数十秒；完成后任务列表将自动翻转状态（SSE），无需手动刷新。'}
        </p>
        <div className="m-actions">
          <button type="button" className="btn" onClick={onClose}>留在本页</button>
          <button type="button" className="btn btn-primary" onClick={() => navigate(listHref)}>前往任务列表</button>
        </div>
      </div>
    </div>
  )
}
