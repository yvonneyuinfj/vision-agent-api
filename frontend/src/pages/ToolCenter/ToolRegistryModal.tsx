import React, { useEffect, useMemo, useState } from 'react'
import { Modal, Spin, message } from 'antd'
import {
  ADAPTER_TYPE_TEXT,
  CAPABILITY_STATUS_TEXT,
  CAPABILITY_TYPE_TEXT,
  CHANGE_LOG_ACTION_TEXT,
  createCapability,
  dryRunCapability,
  fetchCapabilityDetail,
  patchCapability,
  type AdapterType,
  type CapabilityDetail,
  type CapabilityPatchPayload,
  type CapabilityType,
  type CapabilityWritable,
  type DryRunResult,
} from '../../api/capabilities'
import { useScenes } from '../../hooks/useScenes'
import { writeErrorMessage } from './registryUtils'

/* 工具登记 / 编辑共用的宽 Modal（决策 44）：三段——基本信息 / 连接与请求 / 响应与门禁，
   动作条＝完整试运行 · 测试连通性 · 保存。status 在表单里只读（决策 45：启停走行内按钮）。 */

interface HeaderRow {
  key: string
  /** 用户新填或重填的明文；空串＝沿用原值（掩码不回传明文，决策 43） */
  value: string
  masked: string
  existing: boolean
  removed: boolean
}

interface Draft {
  capabilityId: string
  toolName: string
  scene: string
  capabilityType: CapabilityType
  adapterType: AdapterType
  description: string
  toolVersion: string
  modelVersion: string
  apiVersion: string
  weightsSha: string
  timeoutS: string
  maxRetry: string
  endpoint: string
  healthEndpoint: string
  healthPath: string
  inferencePath: string
  imageField: string
  images: string
  defaultParams: string
  note: string
  headers: HeaderRow[]
  sample: string
  probeImages: string
  probeParams: string
  changeReason: string
  operator: string
}

/** 编辑态基线：详情下发的原值，用于「只提交改动过的键」（决策 39/40） */
interface Base {
  scene: string
  capabilityType: CapabilityType
  adapterType: AdapterType
  description: string
  toolVersion: string
  modelVersion: string
  apiVersion: string
  weightsSha: string
  timeoutS: number
  maxRetry: number
  endpoint: string
  healthEndpoint: string
  inputRequired: Record<string, unknown>
  defaultParams: string
  note: string
  sample: string
  headerKeys: string[]
}

const BLANK: Draft = {
  capabilityId: '',
  toolName: '',
  scene: 'fpi',
  capabilityType: 'defect_detection',
  adapterType: 'http',
  description: '',
  toolVersion: '1.0.0',
  modelVersion: '1.0.0',
  apiVersion: 'v1',
  weightsSha: '',
  timeoutS: '60',
  maxRetry: '2',
  endpoint: '',
  healthEndpoint: '',
  healthPath: '/health',
  inferencePath: '/api/v1/inference',
  imageField: 'image',
  images: '',
  defaultParams: '{}',
  note: '',
  headers: [],
  sample: '',
  probeImages: '',
  probeParams: '{"part_id":"dryrun","device_id":"dryrun"}',
  changeReason: '',
  operator: '',
}

function json(text: unknown): string {
  if (typeof text === 'string') return text
  return JSON.stringify(text ?? {}, null, 2)
}

function parseObject(text: string): Record<string, unknown> | null | string {
  if (!text.trim()) return null
  try {
    const value = JSON.parse(text)
    if (value && typeof value === 'object' && !Array.isArray(value)) return value as Record<string, unknown>
  } catch {
    /* 统一走下面的错误串 */
  }
  return 'JSON 解析失败：需为对象（{…}）'
}

function headersPayload(rows: HeaderRow[]): Record<string, string | null> {
  const out: Record<string, string | null> = {}
  for (const row of rows) {
    if (row.removed) {
      if (row.existing) out[row.key] = null
      continue
    }
    if (row.value.trim()) out[row.key] = row.value.trim()
  }
  return out
}

const sameText = (a: string, b: string | null | undefined) => a.trim() === (b ?? '').trim()

function Field({
  label,
  required,
  opt,
  hint,
  wide,
  children,
}: {
  label: string
  required?: boolean
  opt?: string
  hint?: string
  wide?: boolean
  children: React.ReactNode
}) {
  return (
    <label className={`field${wide ? ' reg-field--wide' : ''}`}>
      <span className="label">
        {label}
        {required && <span className="req">*</span>}
        {opt && <span className="opt">{opt}</span>}
      </span>
      {children}
      {hint && <span className="hint">{hint}</span>}
    </label>
  )
}

function Section({
  index,
  title,
  sub,
  children,
}: {
  index: string
  title: string
  sub: string
  children: React.ReactNode
}) {
  return (
    <section className="reg-section">
      <div className="reg-section__head">
        <span className="mono faint">{index}</span>
        <div>
          <strong>{title}</strong>
          <span className="hint"> {sub}</span>
        </div>
      </div>
      <div className="reg-grid">{children}</div>
    </section>
  )
}

function StepList({ dry }: { dry: DryRunResult }) {
  return (
    <div className="reg-steps">
      {dry.steps.map((s) => (
        <div key={s.name} className="reg-step">
          <span className={s.ok ? 'tag t-pass' : 'tag t-fail'}>{s.ok ? '通过' : '未过'}</span>
          <span className="mono">{s.name}</span>
          <span className="muted">{s.message}</span>
          {s.latency_ms != null && <span className="hint mono">{s.latency_ms}ms</span>}
        </div>
      ))}
      {dry.gate_errors.length > 0 && (
        <div className="hint err">
          样例门禁未过：{dry.gate_errors.map((e) => `<${e}>`).join(' ')}
        </div>
      )}
      {dry.snapshot && (
        <div className="hint">
          保存回执快照 <span className="mono">{dry.snapshot}</span>（端点/请求头再改动即失效，需重跑）
        </div>
      )}
    </div>
  )
}

export function ToolRegistryModal({
  open,
  capabilityId,
  enabledScenes,
  onClose,
  onSaved,
}: {
  open: boolean
  /** null＝登记新工具；非空＝编辑该行 */
  capabilityId: string | null
  /** 当前 enabled 行占用的 scene→capability_id，用于同场景多行提示（决策 49-①） */
  enabledScenes: Array<{ scene: string; capability_id: string }>
  onClose: () => void
  onSaved: () => Promise<void> | void
}) {
  const mode: 'create' | 'edit' = capabilityId ? 'edit' : 'create'
  /* 场景下拉与工作台/提交页同源（决策 52）：目录读取失败回退内置六条 */
  const { scenes: workbenches } = useScenes()
  const [draft, setDraft] = useState<Draft>(BLANK)
  const sceneAutoRecognize =
    workbenches.find((w) => w.scene === draft.scene)?.autoRecognize ?? true
  const [base, setBase] = useState<Base | null>(null)
  const [detail, setDetail] = useState<CapabilityDetail | null>(null)
  const [loading, setLoading] = useState(false)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [dry, setDry] = useState<DryRunResult | null>(null)
  const [receipt, setReceipt] = useState<{ key: string; snapshot: string } | null>(null)
  const [running, setRunning] = useState<'full' | 'connect' | null>(null)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!open) return
    setDry(null)
    setReceipt(null)
    setError(null)
    setLoadError(null)
    if (!capabilityId) {
      setDetail(null)
      setBase(null)
      setDraft({ ...BLANK })
      return
    }
    setLoading(true)
    void fetchCapabilityDetail(capabilityId)
      .then((d) => {
        const meta = d.meta ?? {}
        const inputRequired = (meta.input_required ?? {}) as Record<string, unknown>
        const nextBase: Base = {
          scene: d.scene,
          capabilityType: d.capability_type,
          adapterType: d.adapter_type,
          description: d.description ?? '',
          toolVersion: d.tool_version,
          modelVersion: d.model_version,
          apiVersion: d.api_version,
          weightsSha: d.model_weights_sha256 ?? '',
          timeoutS: d.timeout_s,
          maxRetry: d.max_retry,
          endpoint: d.endpoint,
          healthEndpoint: d.health_endpoint ?? '',
          inputRequired,
          defaultParams: json(meta.default_params ?? {}),
          note: typeof meta.note === 'string' ? meta.note : '',
          sample: d.output_schema ? json(d.output_schema) : '',
          headerKeys: Object.keys(d.headers_masked ?? {}),
        }
        setBase(nextBase)
        setDetail(d)
        setDraft({
          ...BLANK,
          capabilityId: d.capability_id,
          toolName: d.tool_name,
          scene: d.scene,
          capabilityType: d.capability_type,
          adapterType: d.adapter_type,
          description: d.description ?? '',
          toolVersion: d.tool_version,
          modelVersion: d.model_version,
          apiVersion: d.api_version,
          weightsSha: d.model_weights_sha256 ?? '',
          timeoutS: String(d.timeout_s),
          maxRetry: String(d.max_retry),
          endpoint: d.endpoint,
          healthEndpoint: d.health_endpoint ?? '',
          imageField: typeof inputRequired.image_field === 'string' ? inputRequired.image_field : 'image',
          images: typeof inputRequired.images === 'number' ? String(inputRequired.images) : '',
          defaultParams: nextBase.defaultParams,
          note: nextBase.note,
          sample: nextBase.sample,
          headers: Object.entries(d.headers_masked ?? {}).map(([key, masked]) => ({
            key,
            value: '',
            masked: `${key}: ${masked}`,
            existing: true,
            removed: false,
          })),
        })
      })
      .catch((e) => setLoadError(writeErrorMessage(e) ?? '详情读取失败'))
      .finally(() => setLoading(false))
  }, [capabilityId, open])

  const patch = (next: Partial<Draft>): void => {
    setDraft((current) => ({ ...current, ...next }))
    setError(null)
  }

  /** 连接快照键：与后端 snapshot_of(endpoint, headers) 同范围（端点 + 将要提交的请求头明文） */
  const connKey = useMemo(
    () =>
      `${draft.endpoint.trim()}|${JSON.stringify(
        Object.entries(headersPayload(draft.headers)).sort(([a], [b]) => a.localeCompare(b)),
      )}`,
    [draft.endpoint, draft.headers],
  )

  const headersTouched = useMemo(() => {
    const payload = headersPayload(draft.headers)
    return Object.keys(payload).length > 0
  }, [draft.headers])

  const unretypedKeys = draft.headers
    .filter((row) => row.existing && !row.removed && !row.value.trim())
    .map((row) => row.key)

  const connChanged =
    mode === 'edit' && base !== null && (!sameText(draft.endpoint, base.endpoint) || headersTouched)
  const needsReceipt = connChanged && detail?.status === 'enabled'
  const receiptValid = receipt !== null && receipt.key === connKey && unretypedKeys.length === 0

  const dirty = useMemo(() => {
    if (!base) return true
    if (mode === 'create') return true
    const fieldsChanged: boolean =
      !sameText(draft.scene, base.scene) ||
      draft.capabilityType !== base.capabilityType ||
      draft.adapterType !== base.adapterType ||
      !sameText(draft.description, base.description) ||
      !sameText(draft.toolVersion, base.toolVersion) ||
      !sameText(draft.modelVersion, base.modelVersion) ||
      !sameText(draft.apiVersion, base.apiVersion) ||
      !sameText(draft.weightsSha, base.weightsSha) ||
      Number(draft.timeoutS) !== base.timeoutS ||
      Number(draft.maxRetry) !== base.maxRetry ||
      !sameText(draft.endpoint, base.endpoint) ||
      !sameText(draft.healthEndpoint, base.healthEndpoint) ||
      !sameText(draft.note, base.note) ||
      draft.defaultParams.trim() !== base.defaultParams.trim() ||
      draft.sample.trim() !== base.sample.trim() ||
      (typeof base.inputRequired.image_field === 'string'
        ? draft.imageField !== base.inputRequired.image_field
        : !!draft.imageField) ||
      (typeof base.inputRequired.images === 'number'
        ? draft.images !== String(base.inputRequired.images)
        : draft.images !== '')
    return fieldsChanged || headersTouched
  }, [base, draft, headersTouched, mode])

  const blocking = useMemo(() => {
    const list: string[] = []
    if (mode === 'create') {
      if (!draft.capabilityId.trim()) list.push('能力 ID 必填（创建后不可改）')
      if (!draft.toolName.trim()) list.push('工具名必填（创建后不可改）')
    }
    if (!draft.endpoint.trim()) list.push('端点必填')
    if (!draft.toolVersion.trim() || !draft.modelVersion.trim()) list.push('工具版本与模型版本必填')
    if (!Number(draft.timeoutS) || Number(draft.timeoutS) < 1) list.push('超时需为 ≥1 的秒数')
    if (Number(draft.maxRetry) < 0 || Number(draft.maxRetry) > 5) list.push('重试次数需为 0–5')
    if (parseObject(draft.defaultParams) === null || typeof parseObject(draft.defaultParams) === 'string')
      list.push('默认参数需为 JSON 对象')
    if (draft.sample.trim() && typeof parseObject(draft.sample) === 'string')
      list.push('期望响应样例需为 JSON 对象')
    const reason = draft.changeReason.trim()
    if (!reason) list.push('变更原因必填（≤100 字，落 meta_json.change_log）')
    if (reason.length > 100) list.push('变更原因不得超过 100 字')
    if (!dirty) list.push('没有需要保存的改动')
    if (needsReceipt && !receiptValid) {
      list.push(
        unretypedKeys.length > 0
          ? `端点/请求头已变更：请重填这些请求头明文后跑完整试运行（${unretypedKeys.join('、')}）`
          : '端点/请求头已变更：需先「完整试运行」并通过后才能保存',
      )
    }
    return list
  }, [dirty, draft, mode, needsReceipt, receiptValid, unretypedKeys])

  const sceneClash = enabledScenes.filter(
    (row) => row.scene === draft.scene && row.capability_id !== capabilityId,
  )

  async function run(mode2: 'full' | 'connect') {
    if (!draft.endpoint.trim()) {
      setError('端点必填后才能测试')
      return
    }
    const parsedParams = parseObject(draft.probeParams)
    if (typeof parsedParams === 'string') {
      setError(`干跑参数：${parsedParams}`)
      return
    }
    if (mode2 === 'full' && !draft.probeImages.trim()) {
      setError('完整试运行需填样例物料路径（仓库内相对路径，可逗号/换行分隔多帧，如 X光需 2 个 .opi）')
      return
    }
    setRunning(mode2)
    setError(null)
    setDry(null)
    try {
      const result = await dryRunCapability({
        endpoint: draft.endpoint.trim(),
        health_path: draft.healthPath.trim() || '/health',
        inference_path: draft.inferencePath.trim() || '/api/v1/inference',
        headers: Object.fromEntries(
          Object.entries(headersPayload(draft.headers)).filter(
            (entry): entry is [string, string] => typeof entry[1] === 'string',
          ),
        ),
        parameters: (parsedParams ?? {}) as Record<string, unknown>,
        image_field: draft.imageField.trim() || 'image',
        image_path: mode2 === 'full' ? draft.probeImages.trim() : '',
        capability_type: draft.capabilityType,
        timeout_s: Number(draft.timeoutS) || 60,
      })
      setDry(result)
      if (mode2 === 'full' && result.passed && result.snapshot) {
        setReceipt({ key: connKey, snapshot: result.snapshot })
        message.success('完整试运行通过，已发放保存回执')
      } else if (mode2 === 'connect' && result.passed) {
        message.success('连通性通过（保存仍须完整试运行）')
      } else {
        message.warning(result.passed ? '试运行未完全通过' : '试运行未通过：见三段结果')
      }
    } catch (e) {
      setError(writeErrorMessage(e) ?? '试运行请求失败')
    } finally {
      setRunning(null)
    }
  }

  async function save() {
    if (blocking.length > 0) {
      setError(blocking[0])
      return
    }
    const defaultParams = parseObject(draft.defaultParams) as Record<string, unknown>
    const sample = draft.sample.trim() ? (parseObject(draft.sample) as Record<string, unknown>) : null
    const inputRequired = Object.fromEntries(
      Object.entries({ ...(base?.inputRequired ?? {}), image_field: draft.imageField.trim() || undefined, images: draft.images ? Number(draft.images) : undefined })
        .filter(([, v]) => v !== undefined && v !== null),
    )
    const headerPatch = headersPayload(draft.headers)
    const reason = draft.changeReason.trim()
    const operator = draft.operator.trim() || null

    setSaving(true)
    setError(null)
    try {
      if (mode === 'create') {
        const meta: Record<string, unknown> = {
          default_params: defaultParams,
          input_required: inputRequired,
          note: draft.note.trim(),
        }
        if (Object.keys(headerPatch).length > 0) meta.headers = headerPatch
        const ack = await createCapability({
          capability_id: draft.capabilityId.trim(),
          tool_name: draft.toolName.trim(),
          scene: draft.scene,
          capability_type: draft.capabilityType,
          adapter_type: draft.adapterType,
          endpoint: draft.endpoint.trim(),
          health_endpoint: draft.healthEndpoint.trim() || null,
          tool_version: draft.toolVersion.trim(),
          model_version: draft.modelVersion.trim(),
          api_version: draft.apiVersion.trim() || 'v1',
          output_schema: sample,
          timeout_s: Number(draft.timeoutS),
          max_retry: Number(draft.maxRetry),
          description: draft.description.trim() || null,
          model_weights_sha256: draft.weightsSha.trim() || null,
          meta,
          status: 'disabled',
          change_reason: reason,
          operator,
          expected_snapshot: receipt?.snapshot ?? null,
        })
        message.success(`已登记 ${ack.capability_id}（默认「已停用」，行内启用前会跑健康探测）`)
      } else {
        if (!detail || !base) return
        const writable: CapabilityWritable = {}
        if (!sameText(draft.scene, base.scene)) writable.scene = draft.scene
        if (draft.capabilityType !== base.capabilityType) writable.capability_type = draft.capabilityType
        if (draft.adapterType !== base.adapterType) writable.adapter_type = draft.adapterType
        if (!sameText(draft.endpoint, base.endpoint)) writable.endpoint = draft.endpoint.trim()
        if (!sameText(draft.healthEndpoint, base.healthEndpoint)) {
          writable.health_endpoint = draft.healthEndpoint.trim() || null
        }
        if (!sameText(draft.toolVersion, base.toolVersion)) writable.tool_version = draft.toolVersion.trim()
        if (!sameText(draft.modelVersion, base.modelVersion)) writable.model_version = draft.modelVersion.trim()
        if (!sameText(draft.apiVersion, base.apiVersion)) writable.api_version = draft.apiVersion.trim()
        if (!sameText(draft.weightsSha, base.weightsSha)) {
          writable.model_weights_sha256 = draft.weightsSha.trim() || null
        }
        if (Number(draft.timeoutS) !== base.timeoutS) writable.timeout_s = Number(draft.timeoutS)
        if (Number(draft.maxRetry) !== base.maxRetry) writable.max_retry = Number(draft.maxRetry)
        if (!sameText(draft.description, base.description)) {
          writable.description = draft.description.trim() || null
        }
        if (draft.sample.trim() !== base.sample.trim()) writable.output_schema = sample

        const meta: Record<string, unknown> = {}
        if (draft.defaultParams.trim() !== base.defaultParams.trim()) meta.default_params = defaultParams
        const inputChanged =
          JSON.stringify(inputRequired) !== JSON.stringify(base.inputRequired ?? {})
        if (inputChanged) meta.input_required = inputRequired
        if (!sameText(draft.note, base.note)) meta.note = draft.note.trim()
        if (headersTouched) meta.headers = headerPatch

        const payload: CapabilityPatchPayload = {
          ...writable,
          ...(Object.keys(meta).length > 0 ? { meta } : {}),
          expected_version: detail.version,
          change_reason: reason,
          operator,
          ...(connChanged && receipt && receipt.key === connKey ? { expected_snapshot: receipt.snapshot } : {}),
        }
        const ack = await patchCapability(detail.capability_id, payload)
        message.success(`已更新 ${(ack.fields ?? []).length} 个字段（version ${detail.version} → ${detail.version + 1}）`)
      }
      await onSaved()
      onClose()
    } catch (e) {
      const message2 = writeErrorMessage(e)
      setError(message2 ?? '保存失败，请稍后重试')
      if (message2?.includes('version')) {
        message.warning('该行已被改动，请关闭后重新打开表单')
      }
    } finally {
      setSaving(false)
    }
  }

  return (
    <Modal
      open={open}
      width={960}
      footer={null}
      destroyOnHidden
      maskClosable={false}
      keyboard={!saving}
      closable={!saving}
      onCancel={onClose}
      className="tool-registry-modal"
      title={
        <div>
          <div className="mono faint">CAPABILITY REGISTRY WRITE</div>
          <strong>{mode === 'create' ? '登记新工具能力' : `编辑能力 · ${capabilityId}`}</strong>
        </div>
      }
    >
      {loading ? (
        <div className="row" style={{ padding: '40px 0', gap: 12 }}>
          <Spin />
          <span className="muted">正在读取详情与掩码请求头…</span>
        </div>
      ) : loadError ? (
        <div className="hint err" style={{ padding: 20 }}>
          {loadError}
        </div>
      ) : (
        <div className="reg-body">
          <Section index="01" title="基本信息" sub="身份列创建后全锁（决策 36）：换身份＝新建一行 + 停用旧行">
            <Field label="能力 ID" required={mode === 'create'} hint={mode === 'edit' ? '创建后不可改' : '如 borescope_defect_detection_v1'}>
              <input
                className="input mono"
                value={draft.capabilityId}
                disabled={mode === 'edit'}
                onChange={(e) => patch({ capabilityId: e.target.value })}
              />
            </Field>
            <Field label="工具名（detect/&lt;tool_name&gt;）" required={mode === 'create'} hint="不得与其他「已启用」行同名（决策 35）">
              <input
                className="input mono"
                value={draft.toolName}
                disabled={mode === 'edit'}
                onChange={(e) => patch({ toolName: e.target.value })}
              />
            </Field>
            <Field
              label="专业场景（路由键）"
              required
              hint={
                sceneClash.length > 0
                  ? undefined
                  : sceneAutoRecognize
                    ? '白名单＝后端场景目录（决策 37 / 52），名单外即死能力'
                    : '该场景不在自动识别关键词表内：登记后只能由人工在工作台/提交页显式选场景，任务链不会自动路由到它（决策 52）'
              }
            >
              <select className="select" value={draft.scene} onChange={(e) => patch({ scene: e.target.value })}>
                {workbenches.map((w) => (
                  <option key={w.scene} value={w.scene}>
                    {w.label} · {w.scene}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="能力类型（决定样例门禁分支）" required hint="运行时归一仍以 tool_profiles 的 transform 为准（决策 49-②）">
              <select
                className="select"
                value={draft.capabilityType}
                onChange={(e) => patch({ capabilityType: e.target.value as CapabilityType })}
              >
                {(Object.keys(CAPABILITY_TYPE_TEXT) as CapabilityType[]).map((v) => (
                  <option key={v} value={v}>
                    {CAPABILITY_TYPE_TEXT[v]} · {v}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="适配器">
              <select
                className="select"
                value={draft.adapterType}
                onChange={(e) => patch({ adapterType: e.target.value as AdapterType })}
              >
                {(Object.keys(ADAPTER_TYPE_TEXT) as AdapterType[]).map((v) => (
                  <option key={v} value={v}>
                    {ADAPTER_TYPE_TEXT[v]} · {v}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="注册状态（只读）" hint={mode === 'create' ? '新行一律以「已停用」落库，行内启用时才探健康（决策 27/45）' : '启停请用行内按钮'}>
              <input
                className="input"
                value={mode === 'create' ? '已停用（登记后需行内启用）' : CAPABILITY_STATUS_TEXT[detail?.status ?? 'disabled']}
                readOnly
              />
            </Field>
            <Field label="工具简介（工作台/首页首行）" wide hint="只写「看什么图、判什么」；端口、耗时、参数契约、归一入口写进下面的技术备注，别塞进简介">
              <input className="input" value={draft.description} onChange={(e) => patch({ description: e.target.value })} />
            </Field>
            <Field label="技术备注（meta.note，仅本页「说明」列）" wide hint="台账事实：工具代号与端口、必填参数与错误码、响应形态与归一函数名">
              <textarea className="textarea" rows={2} value={draft.note} onChange={(e) => patch({ note: e.target.value })} />
            </Field>
            <Field label="工具版本" required>
              <input className="input mono" value={draft.toolVersion} onChange={(e) => patch({ toolVersion: e.target.value })} />
            </Field>
            <Field label="模型版本" required>
              <input className="input mono" value={draft.modelVersion} onChange={(e) => patch({ modelVersion: e.target.value })} />
            </Field>
            <Field label="接口版本">
              <input className="input mono" value={draft.apiVersion} onChange={(e) => patch({ apiVersion: e.target.value })} />
            </Field>
            <Field label="权重 SHA256" opt="≤64 位">
              <input className="input mono" value={draft.weightsSha} onChange={(e) => patch({ weightsSha: e.target.value })} />
            </Field>
            <Field label="超时（秒）" required>
              <input className="input mono" type="number" min={1} max={600} value={draft.timeoutS} onChange={(e) => patch({ timeoutS: e.target.value })} />
            </Field>
            <Field label="重试次数" hint="0–5">
              <input className="input mono" type="number" min={0} max={5} value={draft.maxRetry} onChange={(e) => patch({ maxRetry: e.target.value })} />
            </Field>
          </Section>

          <Section index="02" title="连接与请求" sub="端点与请求头参与保存回执；路径两项仅用于试运行，不落库（决策 33）">
            <Field label="推理端点" required wide>
              <input className="input mono" value={draft.endpoint} onChange={(e) => patch({ endpoint: e.target.value })} placeholder="http://127.0.0.1:18016" />
            </Field>
            <Field label="健康端点覆盖" opt="留空＝端点 + /health">
              <input className="input mono" value={draft.healthEndpoint} onChange={(e) => patch({ healthEndpoint: e.target.value })} />
            </Field>
            <Field label="请求头（明文只写入，不回显）" wide hint={unretypedKeys.length > 0 ? `掩码值不回传：${unretypedKeys.join('、')} 需改动端点时重新填写明文` : undefined}>
              <div className="reg-headers">
                {draft.headers.map((row, i) => (
                  <div key={`${row.key}-${i}`} className="reg-header">
                    <input
                      className="input mono"
                      value={row.key}
                      readOnly={row.existing}
                      placeholder="X-API-Key"
                      onChange={(e) =>
                        patch({
                          headers: draft.headers.map((h, j) => (j === i ? { ...h, key: e.target.value } : h)),
                        })
                      }
                    />
                    <input
                      className="input mono"
                      value={row.value}
                      placeholder={row.masked || '值'}
                      onChange={(e) =>
                        patch({
                          headers: draft.headers.map((h, j) => (j === i ? { ...h, value: e.target.value } : h)),
                        })
                      }
                    />
                    <button
                      type="button"
                      className="lnk"
                      onClick={() =>
                        patch({
                          headers: row.existing
                            ? draft.headers.map((h, j) => (j === i ? { ...h, removed: !h.removed } : h))
                            : draft.headers.filter((_, j) => j !== i),
                        })
                      }
                    >
                      {row.existing ? (row.removed ? '撤销删除' : '删除') : '移除'}
                    </button>
                  </div>
                ))}
                <button
                  type="button"
                  className="btn btn-sm"
                  onClick={() => patch({ headers: [...draft.headers, { key: '', value: '', masked: '', existing: false, removed: false }] })}
                >
                  添加请求头
                </button>
              </div>
            </Field>
            <Field label="图像字段名" hint="X光/DR 为 images，其余多为 image">
              <input className="input mono" value={draft.imageField} onChange={(e) => patch({ imageField: e.target.value })} />
            </Field>
            <Field label="声明图像帧数" opt="可选，写入 input_required.images">
              <input className="input mono" type="number" min={1} max={12} value={draft.images} onChange={(e) => patch({ images: e.target.value })} />
            </Field>
            <Field label="默认参数（meta.default_params，JSON）" wide hint="任务参数在调用时覆盖同名默认值">
              <textarea className="textarea mono" rows={4} value={draft.defaultParams} onChange={(e) => patch({ defaultParams: e.target.value })} />
            </Field>
            <Field label="健康路径（仅试运行）">
              <input className="input mono" value={draft.healthPath} onChange={(e) => patch({ healthPath: e.target.value })} />
            </Field>
            <Field label="推理路径（仅试运行）">
              <input className="input mono" value={draft.inferencePath} onChange={(e) => patch({ inferencePath: e.target.value })} />
            </Field>
            <Field label="样例物料路径（仅试运行）" wide hint="限 local-tools/ 与 data/ 之下，多帧用逗号或换行分隔">
              <textarea
                className="textarea mono"
                rows={2}
                value={draft.probeImages}
                onChange={(e) => patch({ probeImages: e.target.value })}
                placeholder="local-tools/fluorescence/server-bundle/test01.jpg"
              />
            </Field>
            <Field label="干跑 parameters（仅试运行）" wide>
              <textarea className="textarea mono" rows={2} value={draft.probeParams} onChange={(e) => patch({ probeParams: e.target.value })} />
            </Field>
          </Section>

          <Section index="03" title="响应与门禁" sub="期望响应样例落 output_schema 列；门禁为硬校验（决策 28/29）">
            <Field label="期望响应样例（JSON）" wide hint="留空＝清空；须含 code=0 与 result，缺陷类还须 result.defects 数组">
              <textarea className="textarea mono" rows={8} value={draft.sample} onChange={(e) => patch({ sample: e.target.value })} />
            </Field>
            <div className="reg-side">
              {dry?.sample && (
                <button
                  type="button"
                  className="btn btn-sm"
                  disabled={saving}
                  onClick={() => patch({ sample: json(dry.sample) })}
                >
                  用本次试运行归一样例填入
                </button>
              )}
              {dry && <StepList dry={dry} />}
              {sceneClash.length > 0 && (
                <div className="hint err">
                  场景 {draft.scene} 已有 {sceneClash.length} 条「已启用」能力（
                  <span className="mono">{sceneClash.map((r) => r.capability_id).join(' / ')}</span>
                  ）；同场景多行按登记序取首条命中，歧义时由任务链 scene_confirm 人工确认（决策 49-①）。
                </div>
              )}
              {detail && detail.change_log.length > 0 && (
                <div className="reg-log">
                  <span className="label">变更留痕（最近 {Math.min(detail.change_log.length, 3)} 条）</span>
                  {detail.change_log.slice(0, 3).map((entry, i) => (
                    <div key={i} className="hint mono">
                      {entry.at?.slice(0, 19).replace('T', ' ')} · {CHANGE_LOG_ACTION_TEXT[entry.action ?? ''] ?? entry.action} ·{' '}
                      {entry.operator || '未填'} · {entry.fields?.join(',') ?? '-'} · {entry.reason}
                    </div>
                  ))}
                </div>
              )}
            </div>
          </Section>

          <div className="reg-footer">
            <div className="reg-reason">
              <Field label="变更原因" required hint="落 meta_json.change_log；自报优先、留空取登录会话用户名，无角色授权与前后值审计（决策 46）">
                <input
                  className="input"
                  value={draft.changeReason}
                  maxLength={100}
                  placeholder="例如：厂商交付新模型版本，端点由 8002 迁至 18016"
                  onChange={(e) => patch({ changeReason: e.target.value })}
                />
              </Field>
              <Field label="操作人" opt="留空＝取登录会话用户名">
                <input className="input mono" value={draft.operator} maxLength={64} onChange={(e) => patch({ operator: e.target.value })} />
              </Field>
            </div>
            <div className="reg-actions">
              <button type="button" className="btn" disabled={!!running || saving} onClick={() => void run('full')}>
                {running === 'full' && <span className="spin" />}
                完整试运行
              </button>
              <button type="button" className="btn" disabled={!!running || saving} onClick={() => void run('connect')}>
                {running === 'connect' && <span className="spin" />}
                测试连通性
              </button>
              <button type="button" className="btn" disabled={saving} onClick={onClose}>
                取消
              </button>
              <button type="button" className="btn btn-primary" disabled={blocking.length > 0 || saving} onClick={() => void save()}>
                {saving && <span className="spin" />}
                {mode === 'create' ? '登记' : '保存'}
              </button>
            </div>
            <div className="reg-status" role="status">
              {error ? <span className="hint err">{error}</span> : blocking.length > 0 ? <span className="hint">{blocking[0]}</span> : <span className="hint">可保存</span>}
            </div>
          </div>
        </div>
      )}
    </Modal>
  )
}
