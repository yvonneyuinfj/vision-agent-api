import { Alert, Collapse, Input, Modal, Slider, Spin, Switch } from 'antd'
import type { InputRef } from 'antd'
import { useEffect, useMemo, useRef, useState } from 'react'
import type { ChatConfig, ChatDataset } from '../../api/kb'

type AnswerStyle = 'precise' | 'balanced' | 'creative'

interface Props {
  open: boolean
  config: ChatConfig | null | undefined
  datasets: ChatDataset[]
  datasetsLoading: boolean
  datasetsError: string | null
  onRetryDatasets: () => void
  onCancel: () => void
  onSave: (config: ChatConfig) => Promise<void>
}

const STYLE_PRESETS: Array<{
  id: AnswerStyle
  name: string
  badge: string
  description: string
  temperature: number
  topP: number
  parameter: string
}> = [
  {
    id: 'precise',
    name: '严谨',
    badge: '低随机性',
    description: '优先按资料原意回答，适合判级标准、工艺规范和结论复核。',
    temperature: 0.2,
    topP: 0.75,
    parameter: 'Precise',
  },
  {
    id: 'balanced',
    name: '平衡',
    badge: '推荐',
    description: '兼顾事实准确与表达自然，适合大多数知识问答。',
    temperature: 0.5,
    topP: 0.85,
    parameter: 'Balanced',
  },
  {
    id: 'creative',
    name: '灵活',
    badge: '高发散',
    description: '更善于联想和组织内容，不建议用于直接判定质量结论。',
    temperature: 0.8,
    topP: 0.95,
    parameter: 'Creative',
  },
]

function copyConfig(config: ChatConfig): ChatConfig {
  return JSON.parse(JSON.stringify(config)) as ChatConfig
}

function readString(source: Record<string, unknown>, key: string, fallback = ''): string {
  return typeof source[key] === 'string' ? source[key] : fallback
}

function readBoolean(source: Record<string, unknown>, key: string, fallback = false): boolean {
  return typeof source[key] === 'boolean' ? source[key] : fallback
}

function readNumber(source: Record<string, unknown>, key: string, fallback: number): number {
  return typeof source[key] === 'number' ? source[key] : fallback
}

function currentStyle(setting: Record<string, unknown>): AnswerStyle {
  const parameter = readString(setting, 'parameter').toLowerCase()
  if (parameter.includes('precise')) return 'precise'
  if (parameter.includes('creative')) return 'creative'
  if (parameter.includes('balance')) return 'balanced'
  const temperature = readNumber(setting, 'temperature', 0.5)
  if (temperature <= 0.3) return 'precise'
  if (temperature >= 0.7) return 'creative'
  return 'balanced'
}

function modelLabel(id?: string): { name: string; provider: string } {
  if (!id) return { name: 'RAGFlow 默认模型', provider: '跟随服务端设置' }
  const [name, instance, provider] = id.split('@')
  return { name, provider: provider || instance || 'RAGFlow' }
}

function datasetMeta(dataset: ChatDataset): string {
  const parts: string[] = []
  if (typeof dataset.document_count === 'number') parts.push(`${dataset.document_count} 份文档`)
  if (typeof dataset.chunk_count === 'number') parts.push(`${dataset.chunk_count} 个知识片段`)
  return parts.join(' · ') || '可用知识库'
}

export function AssistantSettingsDialog({
  open,
  config,
  datasets,
  datasetsLoading,
  datasetsError,
  onRetryDatasets,
  onCancel,
  onSave,
}: Props) {
  const [draft, setDraft] = useState<ChatConfig | null>(null)
  const [initial, setInitial] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const nameRef = useRef<InputRef>(null)

  useEffect(() => {
    if (!open || !config) return
    const next = copyConfig(config)
    setDraft(next)
    setInitial(JSON.stringify(next))
    setError(null)
  }, [config, open])

  const dirty = draft ? JSON.stringify(draft) !== initial : false
  const model = modelLabel(draft?.llm_id)
  const selectedStyle = draft ? currentStyle(draft.llm_setting) : 'balanced'
  const prompt = draft?.prompt_config ?? {}

  const unknownDatasets = useMemo(() => {
    const known = new Set(datasets.map((item) => item.id))
    return (draft?.dataset_ids ?? []).filter((id) => !known.has(id))
  }, [datasets, draft?.dataset_ids])

  const updateDraft = (patch: Partial<ChatConfig>): void => {
    setDraft((current) => current ? { ...current, ...patch } : current)
    setError(null)
  }

  const updatePrompt = (patch: Record<string, unknown>): void => {
    if (!draft) return
    updateDraft({ prompt_config: { ...draft.prompt_config, ...patch } })
  }

  const chooseStyle = (style: AnswerStyle): void => {
    if (!draft) return
    const preset = STYLE_PRESETS.find((item) => item.id === style)
    if (!preset) return
    updateDraft({
      llm_setting: {
        ...draft.llm_setting,
        parameter: preset.parameter,
        temperature: preset.temperature,
        top_p: preset.topP,
      },
    })
  }

  const toggleDataset = (id: string): void => {
    if (!draft) return
    const selected = draft.dataset_ids.includes(id)
    updateDraft({
      dataset_ids: selected
        ? draft.dataset_ids.filter((item) => item !== id)
        : [...draft.dataset_ids, id],
    })
  }

  const submit = async (): Promise<void> => {
    if (!draft || saving) return
    if (!draft.name.trim()) {
      setError('请填写助手名称。')
      nameRef.current?.focus()
      return
    }
    if (draft.dataset_ids.length === 0) {
      setError('请至少选择一个知识库，助手才能根据资料回答。')
      document.getElementById('assistant-datasets')?.focus()
      return
    }
    setSaving(true)
    setError(null)
    try {
      await onSave({ ...draft, name: draft.name.trim(), description: draft.description?.trim() ?? '' })
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '配置保存失败，请稍后重试。')
    } finally {
      setSaving(false)
    }
  }

  return (
    <Modal
      open={open}
      width={980}
      footer={null}
      destroyOnHidden
      maskClosable={false}
      keyboard={!saving}
      closable={!saving}
      onCancel={onCancel}
      title={
        <div className="assistant-settings__title">
          <span className="assistant-settings__eyebrow">ASSISTANT PROFILE</span>
          <strong>配置知识助手</strong>
          <span>用业务语言决定它参考什么、怎样检索和如何回答</span>
        </div>
      }
      className="assistant-settings"
      aria-label="助手配置"
    >
      {!draft ? (
        <div className="assistant-settings__loading"><Spin /><span>正在读取当前配置…</span></div>
      ) : (
        <form noValidate onSubmit={(event) => { event.preventDefault(); void submit() }}>
          <div className="assistant-settings__summary" aria-label="当前配置摘要">
            <div><span>知识来源</span><strong>{draft.dataset_ids.length} 个知识库</strong></div>
            <div><span>回答方式</span><strong>{STYLE_PRESETS.find((item) => item.id === selectedStyle)?.name}</strong></div>
            <div><span>生成模型</span><strong title={draft.llm_id}>{model.name}</strong></div>
          </div>

          <div className="assistant-settings__body">
            <section className="settings-section" aria-labelledby="settings-basic-title">
              <div className="settings-section__intro">
                <span className="settings-section__index">01</span>
                <div><h3 id="settings-basic-title">助手信息</h3><p>这些内容会帮助使用者快速理解助手的用途。</p></div>
              </div>
              <div className="settings-fields settings-fields--two">
                <label className="settings-field">
                  <span>助手名称 <em>必填</em></span>
                  <Input
                    ref={nameRef}
                    value={draft.name}
                    maxLength={255}
                    status={error?.includes('名称') ? 'error' : undefined}
                    placeholder="例如：航空发动机判级助手"
                    onChange={(event) => updateDraft({ name: event.target.value })}
                  />
                </label>
                <label className="settings-field">
                  <span>用途说明 <small>可选</small></span>
                  <Input
                    value={draft.description ?? ''}
                    maxLength={500}
                    placeholder="例如：查询孔探与荧光检测判级标准"
                    onChange={(event) => updateDraft({ description: event.target.value })}
                  />
                </label>
              </div>
            </section>

            <section className="settings-section" aria-labelledby="settings-dataset-title">
              <div className="settings-section__intro">
                <span className="settings-section__index">02</span>
                <div><h3 id="settings-dataset-title">知识来源</h3><p>选择助手回答时可以查阅的资料库，可多选。</p></div>
                <span className="settings-section__count">已选 {draft.dataset_ids.length}</span>
              </div>
              <div id="assistant-datasets" className="dataset-picker" tabIndex={-1}>
                {datasetsLoading && <div className="dataset-picker__state"><Spin size="small" /><span>正在读取知识库…</span></div>}
                {!datasetsLoading && datasetsError && (
                  <div className="dataset-picker__state dataset-picker__state--error">
                    <span>知识库列表加载失败，已保留当前选择。</span>
                    <button type="button" onClick={onRetryDatasets}>重新加载</button>
                  </div>
                )}
                {!datasetsLoading && !datasetsError && datasets.length === 0 && (
                  <div className="dataset-picker__state">RAGFlow 中暂时没有可用知识库。</div>
                )}
                {datasets.map((dataset) => {
                  const checked = draft.dataset_ids.includes(dataset.id)
                  return (
                    <label key={dataset.id} className={`dataset-option${checked ? ' is-selected' : ''}`}>
                      <input type="checkbox" checked={checked} onChange={() => toggleDataset(dataset.id)} />
                      <span className="dataset-option__check" aria-hidden="true">{checked ? '✓' : ''}</span>
                      <span className="dataset-option__body">
                        <strong>{dataset.name}</strong>
                        <small>{datasetMeta(dataset)}</small>
                        {dataset.description && <span>{dataset.description}</span>}
                      </span>
                    </label>
                  )
                })}
                {unknownDatasets.map((id) => (
                  <label key={id} className="dataset-option dataset-option--unknown is-selected">
                    <input type="checkbox" checked onChange={() => toggleDataset(id)} />
                    <span className="dataset-option__check" aria-hidden="true">!</span>
                    <span className="dataset-option__body">
                      <strong>暂时无法识别的知识库</strong>
                      <small title={id}>内部标识：{id}</small>
                      <span>可能已被删除或当前无权访问；取消勾选后将从配置移除。</span>
                    </span>
                  </label>
                ))}
              </div>
            </section>

            <section className="settings-section" aria-labelledby="settings-style-title">
              <div className="settings-section__intro">
                <span className="settings-section__index">03</span>
                <div><h3 id="settings-style-title">回答方式</h3><p>不需要理解温度或 Top P，选择符合业务风险的方式即可。</p></div>
              </div>
              <div className="answer-style" role="radiogroup" aria-label="回答方式">
                {STYLE_PRESETS.map((preset) => (
                  <label
                    key={preset.id}
                    className={`answer-style__option${selectedStyle === preset.id ? ' is-selected' : ''}`}
                    onClick={() => chooseStyle(preset.id)}
                  >
                    <input type="radio" name="answer-style" value={preset.id} checked={selectedStyle === preset.id} onChange={() => chooseStyle(preset.id)} />
                    <span className="answer-style__visual" data-style={preset.id} aria-hidden="true"><i /><i /><i /><i /></span>
                    <span className="answer-style__heading"><strong>{preset.name}</strong><em>{preset.badge}</em></span>
                    <span>{preset.description}</span>
                  </label>
                ))}
              </div>
              {selectedStyle === 'creative' && (
                <Alert showIcon type="warning" message="灵活模式会增加表达多样性，也更容易偏离资料；质量判定场景建议使用严谨或平衡。" />
              )}
            </section>

            <section className="settings-section" aria-labelledby="settings-retrieval-title">
              <div className="settings-section__intro">
                <span className="settings-section__index">04</span>
                <div><h3 id="settings-retrieval-title">检索与引用</h3><p>控制资料筛选强度，以及回答中展示多少依据。</p></div>
              </div>
              <div className="retrieval-grid">
                <div className="slider-field">
                  <div><label htmlFor="similarity-threshold">匹配门槛</label><output>{Math.round(draft.similarity_threshold * 100)}%</output></div>
                  <Slider id="similarity-threshold" ariaLabelForHandle="匹配门槛" min={0.1} max={0.9} step={0.05} value={draft.similarity_threshold} onChange={(value) => updateDraft({ similarity_threshold: value })} />
                  <p><span>资料更多</span><span>只保留高相关资料</span></p>
                </div>
                <div className="slider-field">
                  <div><label htmlFor="vector-weight">检索偏好</label><output>{draft.vector_similarity_weight >= 0.65 ? '偏语义' : draft.vector_similarity_weight <= 0.35 ? '偏关键词' : '均衡'}</output></div>
                  <Slider id="vector-weight" ariaLabelForHandle="检索偏好" min={0.1} max={0.9} step={0.1} value={draft.vector_similarity_weight} onChange={(value) => updateDraft({ vector_similarity_weight: value })} />
                  <p><span>关键词精确命中</span><span>理解相近表达</span></p>
                </div>
                <div className="slider-field">
                  <div><label htmlFor="reference-count">最多引用</label><output>{draft.top_n} 条</output></div>
                  <Slider id="reference-count" ariaLabelForHandle="最多引用" min={1} max={12} step={1} value={Math.min(draft.top_n, 12)} onChange={(value) => updateDraft({ top_n: value })} />
                  <p><span>回答更精简</span><span>依据更充分</span></p>
                </div>
                <div className="switch-field">
                  <div><strong>展示原文引用</strong><span>回答后附带来源，便于复核和追溯。</span></div>
                  <Switch aria-label="展示原文引用" checked={readBoolean(prompt, 'quote', draft.do_refer === '1')} onChange={(checked) => { updatePrompt({ quote: checked }); updateDraft({ do_refer: checked ? '1' : '0' }) }} />
                </div>
                <div className="switch-field">
                  <div><strong>理解连续追问</strong><span>结合前文改写检索问题，适合多轮对话。</span></div>
                  <Switch aria-label="理解连续追问" checked={readBoolean(prompt, 'refine_multiturn')} onChange={(checked) => updatePrompt({ refine_multiturn: checked })} />
                </div>
              </div>
            </section>

            <section className="settings-section" aria-labelledby="settings-opening-title">
              <div className="settings-section__intro">
                <span className="settings-section__index">05</span>
                <div><h3 id="settings-opening-title">对话提示</h3><p>设置首次见面和查不到可靠资料时的说法。</p></div>
              </div>
              <div className="settings-fields settings-fields--two">
                <label className="settings-field">
                  <span>开场白</span>
                  <Input.TextArea autoSize={{ minRows: 3, maxRows: 5 }} value={readString(prompt, 'prologue')} placeholder="你好，我可以帮你查询判级标准与历史案例。" onChange={(event) => updatePrompt({ prologue: event.target.value })} />
                </label>
                <label className="settings-field">
                  <span>没有可靠资料时</span>
                  <Input.TextArea autoSize={{ minRows: 3, maxRows: 5 }} value={readString(prompt, 'empty_response')} placeholder="留空时模型会继续尝试回答，可能产生错误。" onChange={(event) => updatePrompt({ empty_response: event.target.value })} />
                  <small className="settings-field__help">质量场景建议填写明确的兜底说明，例如“知识库中未找到可靠依据，请人工确认”。</small>
                </label>
              </div>
            </section>

            <Collapse
              ghost
              className="settings-advanced"
              items={[{
                key: 'advanced',
                label: <span><strong>高级设置</strong><small>模型与系统指令，仅建议管理员修改</small></span>,
                children: (
                  <div className="settings-advanced__content">
                    <div className="model-readonly">
                      <span>当前生成模型</span>
                      <strong>{model.name}</strong>
                      <small>{model.provider} · 模型接入和凭据由 RAGFlow 统一管理</small>
                    </div>
                    <label className="settings-field">
                      <span>系统指令</span>
                      <Input.TextArea autoSize={{ minRows: 7, maxRows: 12 }} value={readString(prompt, 'system')} onChange={(event) => updatePrompt({ system: event.target.value })} />
                      <small className="settings-field__help">使用知识库时必须保留 <code>{'{knowledge}'}</code> 占位符，否则保存会失败。</small>
                    </label>
                    <label className="settings-field settings-field--compact">
                      <span>候选片段扫描上限</span>
                      <Input type="number" min={1} max={1024} value={draft.top_k} onChange={(event) => updateDraft({ top_k: Math.max(1, Math.min(1024, Number(event.target.value) || 1)) })} />
                      <small className="settings-field__help">数值越高，召回范围越大，也可能增加检索耗时。一般无需调整。</small>
                    </label>
                  </div>
                ),
              }]}
            />
          </div>

          <div className="assistant-settings__footer">
            <div className="assistant-settings__status" role="status">
              {error ? <span className="is-error">{error}</span> : dirty ? '有尚未保存的更改' : '当前配置未更改'}
            </div>
            <button type="button" className="btn" disabled={saving} onClick={onCancel}>取消</button>
            <button type="submit" className="btn btn-primary assistant-settings__save" disabled={saving || !dirty} aria-busy={saving}>
              {saving && <span className="spin" aria-hidden="true" />}
              <span>{saving ? '正在应用…' : '保存并应用'}</span>
            </button>
          </div>
        </form>
      )}
    </Modal>
  )
}
