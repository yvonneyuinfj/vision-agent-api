import { useState } from 'react'
import { Modal, message } from 'antd'
import {
  CAPABILITY_STATUS_TEXT,
  setCapabilityStatus,
  type CapabilityStatus,
} from '../../api/capabilities'
import { DISABLE_WARNING, ENABLE_WARNING, writeErrorMessage } from './registryUtils'

/* 行内启停的二次确认（决策 45）：独立端点 POST /capabilities/{id}/status，
   status 不在编辑表单里改；启用后端会先跑 health 探测。 */

export function CapabilityStatusModal({
  target,
  capability,
  onClose,
  onDone,
}: {
  target: CapabilityStatus | null
  capability: { capability_id: string; tool_name: string; status: CapabilityStatus } | null
  onClose: () => void
  onDone: () => Promise<void> | void
}) {
  const [reason, setReason] = useState('')
  const [operator, setOperator] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  if (!target || !capability) return null
  const enabling = target === 'enabled'

  async function submit() {
    const trimmed = reason.trim()
    if (!trimmed) {
      setError('变更原因必填（≤100 字）')
      return
    }
    if (trimmed.length > 100) {
      setError('变更原因不得超过 100 字')
      return
    }
    setSaving(true)
    setError(null)
    try {
      const ack = await setCapabilityStatus(capability!.capability_id, {
        status: target!,
        change_reason: trimmed,
        operator: operator.trim() || null,
      })
      message.success(
        `${capability!.capability_id} → ${CAPABILITY_STATUS_TEXT[target!]}（注册表 ${ack.capabilities} 条）`,
      )
      setReason('')
      await onDone()
      onClose()
    } catch (e) {
      setError(writeErrorMessage(e) ?? '启停失败')
    } finally {
      setSaving(false)
    }
  }

  return (
    <Modal
      open
      width={560}
      footer={null}
      destroyOnHidden
      maskClosable={false}
      keyboard={!saving}
      closable={!saving}
      onCancel={onClose}
      title={
        <div>
          <div className="mono faint">CAPABILITY STATUS</div>
          <strong>
            {enabling ? '启用' : '停用'}能力 · {capability.capability_id}
          </strong>
        </div>
      }
    >
      <div className="stack" style={{ gap: 12 }}>
        <div className="hint">
          <span className="mono">{capability.tool_name}</span>（{capability.capability_id}）·{' '}
          当前 <b>{CAPABILITY_STATUS_TEXT[capability.status]}</b> → 目标{' '}
          <b>{CAPABILITY_STATUS_TEXT[target]}</b>
        </div>
        <div className={`hint${enabling ? '' : ' err'}`} style={{ lineHeight: 1.7 }}>
          {enabling ? ENABLE_WARNING : DISABLE_WARNING}
        </div>
        <label className="field">
          <span className="label">
            变更原因<span className="req">*</span>
            <span className="opt">≤100 字，落 meta_json.change_log</span>
          </span>
          <input
            className="input"
            value={reason}
            maxLength={100}
            autoFocus
            placeholder="例如：现场检修需暂时下线该工具"
            onChange={(e) => setReason(e.target.value)}
          />
        </label>
        <label className="field">
          <span className="label">
            操作人<span className="opt">留空＝取登录会话用户名</span>
          </span>
          <input
            className="input mono"
            value={operator}
            maxLength={64}
            onChange={(e) => setOperator(e.target.value)}
          />
        </label>
        <div className="row" style={{ justifyContent: 'space-between' }}>
          <span className="hint err" role="status">
            {error}
          </span>
          <div className="row">
            <button type="button" className="btn btn-sm" disabled={saving} onClick={onClose}>
              取消
            </button>
            <button
              type="button"
              className={`btn btn-sm ${enabling ? 'btn-primary' : 'btn-danger'}`}
              disabled={saving || !reason.trim()}
              onClick={() => void submit()}
            >
              {saving && <span className="spin" />}
              确认{enabling ? '启用' : '停用'}
            </button>
          </div>
        </div>
      </div>
    </Modal>
  )
}
