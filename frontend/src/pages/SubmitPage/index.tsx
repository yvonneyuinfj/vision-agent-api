import { useEffect, useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useSystemStore } from '../../store/useSystemStore'
import { useCapabilities } from '../../hooks/useCapabilities'
import { useScenes } from '../../hooks/useScenes'
import { TASK_FLOW } from '../../constants/workbench'
import { changedParams, defaultParamValues, validateParams, type ParamValues } from '../../constants/paramContract'
import FileIntake from '../../components/intake/FileIntake'
import ParamIntake, { paramTierOf } from '../../components/intake/ParamIntake'
import { IntakeConfirm, SubmitDialog, useIntakeSubmit } from '../../components/intake/IntakeSubmit'
import { useIntakeFiles } from '../../components/intake/useIntakeFiles'
import { maxMBOf } from '../../components/intake/fileValidation'
import { Notice } from '../../components/common/ModuleShell'
import './index.css'

/** 采集注意事项：原「场景联动输入要求」表里未被工作台契约（`WorkbenchDef.input`）覆盖的部分，
 *  随场景卡片一并呈现——原型 /tasks/new 没有独立的要求表，物料要求由场景卡与联动上传块承担。
 *  目录可长（决策 52），新场景没有注意事项属正常：取不到就不显示这一行。 */
const SCENE_NOTES: Record<string, string> = {
  borescope: '录像支持整段孔探过程提交',
  infrared: 'CSV 需含像素级温度矩阵',
  dr: '标定文件由探伤设备导出',
  fpi: '暗场拍摄，勿做亮度修正',
  fracture: '建议附放大倍数元数据',
  metallography: '建议附放大倍数元数据',
}

/** 执行模式三档（原型 /tasks/new 的 chips）。
 *  后端 `POST /tasks` 的 `mode` 只接受 single / agent / joint（`app/api/v1/task.py:66`）：
 *  跨场景**没有独立的 parallel 模式**——`joint` 本身就是"父任务按场景并行派发子任务、落定后汇总"，
 *  故「多场景并行」与「联合研判」两档提交的都是 `mode=joint`，只是叙述口径不同；
 *  `mode=agent`（智能体自主判场景）恒由智能助手会话派发（`app/agents/session/tools.py:73`），本页不设入口。 */
type ModeKey = 'single' | 'parallel' | 'joint'

interface ModeChip {
  key: ModeKey
  label: string
  /** 真实下发给 `POST /tasks` 的 mode 值 */
  mode: 'single' | 'joint'
  /** 该档最少需要几个场景 */
  minScenes: number
  desc: string
}

const MODE_CHIPS: readonly ModeChip[] = [
  { key: 'single', label: '单场景', mode: 'single', minScenes: 1, desc: '1 个场景 · 固定流水线' },
  { key: 'parallel', label: '多场景并行', mode: 'joint', minScenes: 2, desc: '≥2 个场景 · 各专业子任务并行检测' },
  { key: 'joint', label: '联合研判（父任务 + 各专业子任务）', mode: 'joint', minScenes: 2, desc: '≥2 个场景 · 子任务落定后由父任务汇总' },
]

const modeChipOf = (key: ModeKey): ModeChip => MODE_CHIPS.find((m) => m.key === key) ?? MODE_CHIPS[0]

/** `?scene=` 只接受场景目录里的路由键；其余（含手改 URL）按未带入处理（决策 52） */
function sceneFromQuery(raw: string | null, catalog: readonly string[]): string | null {
  return raw && catalog.includes(raw) ? raw : null
}

export default function SubmitPage() {
  const [params, setParams] = useSearchParams()
  /* 场景项与工作台/注册表单同源：GET /scenes，读取失败回退内置六条（决策 52） */
  const { scenes: workbenches } = useScenes()
  const sceneParam = sceneFromQuery(
    params.get('scene'),
    workbenches.map((w) => w.scene),
  )
  /* 工作台带入：预选并锁定该场景，可点「更换场景」回到多选 */
  const [locked, setLocked] = useState(Boolean(sceneParam))
  const [scenes, setScenes] = useState<string[]>(sceneParam ? [sceneParam] : [])
  const [modeKey, setModeKey] = useState<ModeKey>('single')
  const [paramValues, setParamValues] = useState<ParamValues>(defaultParamValues)

  const intake = useIntakeFiles(scenes)
  const { submitting, submitted, submit, clearSubmitted } = useIntakeSubmit()

  // 联合轨（ReAct）需要 VLM 读图：不可用时多场景档给出可读拦截（决策 16）
  const vlmAvailable = useSystemStore((s) => s.vlmAvailable)
  const probe = useSystemStore((s) => s.probe)
  useEffect(() => { void probe() }, [probe])
  const jointNeedsVlm = vlmAvailable === false

  // 能力注册表（GET /capabilities）：确认卡「能力」行的真实来源
  const { caps, loading: capsLoading, error: capsError } = useCapabilities()

  useEffect(() => {
    if (!sceneParam) return
    setLocked(true)
    setScenes([sceneParam])
    setModeKey('single')
  }, [sceneParam])

  const chip = modeChipOf(modeKey)
  const mode = chip.mode
  const requiredScenes: string[] = mode === 'single' ? scenes.slice(0, 1) : scenes
  const multiScene = chip.minScenes > 1

  const paramTier = paramTierOf(scenes, workbenches)

  const validation = useMemo(() => {
    const errors = [...intake.fileErrors]
    if (intake.files.length === 0) errors.unshift('请上传检测图像或视频')
    if (scenes.length < chip.minScenes) {
      errors.unshift(multiScene ? `多场景模式请至少选择 ${chip.minScenes} 个检测场景` : '请选择检测场景')
    } else if (mode === 'single' && scenes.length > 1) {
      errors.unshift('单场景模式只取第 1 个场景，如需一次检测多个场景请切到多场景档')
    }
    if (multiScene && jointNeedsVlm) errors.unshift('多场景联合质检需要 VLM 多模态模型，当前不可用')
    return errors
  }, [intake.fileErrors, intake.files.length, scenes, chip.minScenes, mode, multiScene, jointNeedsVlm])

  const paramErrs = paramTier === 'frozen' ? validateParams(paramValues) : []
  const blockReasons = [...validation, ...intake.linkageMiss, ...paramErrs]

  const toggleScene = (code: string) => {
    if (locked) return
    const next = scenes.includes(code) ? scenes.filter((c) => c !== code) : [...scenes, code]
    setScenes(next)
    // 选到第 2 个场景即进入跨场景档（后端只有 joint 一档跨场景模式）
    if (next.length > 1 && modeKey === 'single') setModeKey('joint')
  }

  const unlockScene = () => {
    setLocked(false)
    setScenes([])
    params.delete('scene')
    setParams(params, { replace: true })
  }

  const sceneTextOf = (code: string) =>
    workbenches.find((w) => w.scene === code)?.label ?? code

  const parameters = paramTier === 'frozen' ? changedParams(paramValues) : null

  async function handleSubmit() {
    const ok = await submit(
      {
        scene: mode === 'single' ? (requiredScenes[0] ?? null) : null,
        mode,
        scenes: requiredScenes,
        files: intake.files.map((f) => f.file),
        parameters,
      },
      blockReasons,
    )
    if (ok) intake.resetFiles()
  }

  const summaryScenes = scenes.length
    ? scenes.map(sceneTextOf).join(' + ') + (scenes.length > 1 ? `（${scenes.length} 项）` : '')
    : '未选择'

  const capsForScenes = scenes.length ? caps.filter((c) => scenes.includes(c.scene)) : caps
  const summaryCaps = capsError
    ? '能力注册表读取失败'
    : capsLoading && !caps.length
      ? '读取能力注册表…'
      : !scenes.length
        ? `注册表共 ${caps.length} 个 · 选场景后按场景匹配`
        : capsForScenes.length
          ? `${capsForScenes.length} 个已登记`
          : '所选场景在注册表内暂无工具'

  /** 请求体预览：就是本次 `POST /tasks` 真要发的字段（multipart 的 files 以数量占位） */
  const requestPreview = JSON.stringify(
    {
      scene: mode === 'single' ? (requiredScenes[0] ?? null) : null,
      scenes: mode === 'joint' ? requiredScenes : [],
      mode,
      parameters: parameters && Object.keys(parameters).length ? JSON.stringify(parameters) : null,
      files: `<multipart ×${intake.files.length}>`,
    },
    null,
    2,
  )

  const lockedCard = sceneParam ? workbenches.find((w) => w.scene === sceneParam) : null

  return (
    <>
      <div className="page-head">
        <div>
          <div className="page-title">新建任务</div>
          <div className="page-sub">通用发起入口：跨场景与联合研判从这里进 · {workbenches.length} 个工作台复用同一提交组件，只把场景锁定</div>
        </div>
        <span className="hint mono">POST /tasks · scene / scenes / mode / parameters / files</span>
      </div>

      <div className="grid-23">
        <div className="stack">
          <section className="card">
            <div className="card-h">
              <div className="card-title"><span className="step-num">1</span>场景与模式<span className="en">SCOPE</span></div>
            </div>
            <div className="card-b stack">
              <div className="field">
                <label className="label">场景类型<span className="req">*</span><span className="opt">支持多选 · 同一部件可一次发起多场景检测</span></label>
                {locked && lockedCard ? (
                  <div className="locked-scene mt-8">
                    <div className="sc-t">{lockedCard.label}<span>{lockedCard.en}</span></div>
                    <div className="sc-req">{lockedCard.input}</div>
                    <span className="tag t-accent">由工作台带入 · 已锁定</span>
                    <button type="button" className="btn btn-sm" onClick={unlockScene}>更换场景</button>
                  </div>
                ) : (
                  <div className="scene-grid mt-8">
                    {workbenches.map((card) => {
                      const on = scenes.includes(card.scene)
                      return (
                        <div key={card.scene} role="button" tabIndex={0} data-code={card.scene}
                          className={on ? 'scene-card selected' : 'scene-card'}
                          onClick={() => toggleScene(card.scene)}
                          onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') toggleScene(card.scene) }}
                        >
                          <span className="chk" />
                          <div className="sc-t">{card.label}<span>{card.en}</span></div>
                          <div className="sc-req">{card.input}</div>
                          <div className="sc-note">
                            {card.autoRecognize
                              ? SCENE_NOTES[card.scene]
                              : '自动识别不覆盖此场景 · 只能显式选择提交（决策 52）'}
                          </div>
                        </div>
                      )
                    })}
                  </div>
                )}
                <span className="hint mt-8">
                  {locked
                    ? '需同一次提交多个场景时，点「更换场景」回到多选（多场景档需 ≥2 个场景）。'
                    : '物料要求按所选场景取并集，提交前统一校验；配套文件缺失会在送检区就近提示。'}
                </span>
              </div>

              <div className="field">
                <label className="label">执行模式<span className="req">*</span></label>
                <div className="chip-row mt-8" role="group" aria-label="执行模式">
                  {MODE_CHIPS.map((m) => (
                    <button key={m.key} type="button" data-mode={m.key}
                      className={m.key === modeKey ? 'chip active' : 'chip'}
                      aria-pressed={m.key === modeKey}
                      title={`${m.desc} · 提交 mode=${m.mode}`}
                      onClick={() => setModeKey(m.key)}
                    >
                      {m.label}
                    </button>
                  ))}
                </div>
                <span className="hint mt-8">{chip.desc} · 提交 <code className="mono">mode={mode}</code>。</span>
                <span className="hint">
                  跨场景在后端只有 <code className="mono">mode=joint</code> 一种形态：父任务按场景<b>并行</b>派发子任务、
                  子任务落定后由父任务<b>汇总</b>，不存在独立的 parallel 模式；
                  智能体自主判场景的 <code className="mono">mode=agent</code> 经智能助手会话发起，不在本页提交。
                </span>
                {multiScene && jointNeedsVlm && (
                  <span className="hint" style={{ color: 'var(--warn)' }}>
                    未检测到 VLM 多模态模型：联合轨无法读图（配置 VLM_MODEL 后自动解锁）。
                  </span>
                )}
              </div>

              <div className="cond-note">
                <b>联动规则 — </b>
                {intake.linkageMiss.length
                  ? `缺失：${intake.linkageMiss.join('；')}，提交将被前置校验拦截。`
                  : scenes.length ? '已选场景的配套文件均齐备，可提交。' : '尚未选择场景，暂无联动校验项。'}
              </div>

              <div className="flowline">{TASK_FLOW}</div>
            </div>
          </section>

          <section className="card">
            <div className="card-h">
              <div className="card-title"><span className="step-num">2</span>上传样本<span className="en">ASSETS</span></div>
              <span className="hint">图像 ≤ {maxMBOf('image')}MB · 视频 ≤ {maxMBOf('video') / 1024}GB</span>
            </div>
            <div className="card-b">
              <FileIntake scenes={scenes} intake={intake} />
              <span className="card-note">
                视频（MP4 / MOV / AVI / WEBM · ≤ {maxMBOf('video') / 1024}GB）适用于孔探全程录像、装配过程录像等场景，
                图像与视频可混合提交；帧率建议 ≥ 24fps。
              </span>
            </div>
          </section>

          <section className="card">
            <div className="card-h">
              <div className="card-title"><span className="step-num">3</span>参数<span className="en">PARAMETERS</span></div>
              <span className="hint">按场景契约冻结状态分档 · 改动项透传到工具</span>
            </div>
            <div className="card-b">
              {/* 参数区随场景契约状态出现：frozen 由 ParamIntake 给参数表，
                  draft/mixed 由同一组件声明「专业参数契约未冻结」，此处只补"未选场景"这一档。 */}
              {scenes.length ? (
                <ParamIntake scenes={scenes} values={paramValues} onChange={setParamValues} catalog={workbenches} />
              ) : (
                <Notice tone="gray">
                  <span>
                    <b>专业参数契约未冻结</b>：参数区随所选场景动态出现——契约已冻结的场景给参数表，
                    未冻结的场景只声明契约状态，提交时沿用注册中心 <code className="mono">default_params</code>；
                    与专业工作台同一组件、同一逻辑。
                  </span>
                </Notice>
              )}
            </div>
          </section>
        </div>

        <div className="card submit-confirm">
          <div className="card-h">
            <div className="card-title">提交前确认<span className="en">REVIEW</span></div>
          </div>
          <div className="card-b">
            <div className="sum-row"><span className="muted">场景</span><b>{summaryScenes}</b></div>
            <div className="sum-row"><span className="muted">模式</span><b>{chip.label}</b></div>
            <div className="sum-row"><span className="muted">样本</span><b>{intake.summary}</b></div>
            <div className="sum-row"><span className="muted">能力</span><b>{summaryCaps}</b></div>
            <div className="sum-row">
              <span className="muted">参数</span>
              <b>{paramTier === 'frozen'
                ? Object.keys(parameters ?? {}).length ? `${Object.keys(parameters ?? {}).length} 项覆盖` : '全部沿用工具默认'
                : '契约未冻结 · 沿用注册中心默认'}</b>
            </div>
            <IntakeConfirm
              submitting={submitting}
              onSubmit={handleSubmit}
              hint="提交后可在任务详情跟踪受理 → 校验 → 推理 → 判级 → 待复核事件流"
            />
            <details className="trace req-preview">
              <summary>请求体预览</summary>
              <div className="req-preview-b">
                <pre className="req-json">{requestPreview}</pre>
                <span className="card-note">
                  预览即实际下发字段（<code className="mono">files</code> 为 multipart 文件流，此处按数量占位）。
                </span>
              </div>
            </details>
          </div>
        </div>
      </div>

      {submitted && <SubmitDialog taskId={submitted.taskId} onClose={clearSubmitted} />}
    </>
  )
}
