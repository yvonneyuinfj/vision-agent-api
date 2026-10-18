/** 专业工作台（/detect/:scene，方案 §5.2）。
 *  版式对齐已评审原型 docs/design/frontend-v05-ia-prototype.html 的 P['/detect/:scene']：
 *    0. 场景头卡 .scenebar：场景名 + EN + 能力就绪/降级 + 一行能力说明 + 执行能力/模型 +
 *       三个 chip（参数契约冻结档 / 能力类型 / 专业依据与判定标准）+「提交后执行链」，右侧场景叶切换（决策 51-⑪ 已移除）
 *    1 · 样本上传 INPUT ASSETS   （左）
 *    2 · 专业参数 PARAMETERS     （左，含「本次将提交的 parameters」真实载荷预览）
 *    3 · 提交 SUBMIT             （右，摘要行 + 跳转说明 + 幂等口径）
 *    本场景最近任务 RECENT       （右，窗口内真分页）
 *
 * 口径与约束：
 *   - 决策 22：工作台 = scene 路由键，不是工具（条数取自场景目录 GET /scenes，决策 52）；同场景可挂多工具
 *     （候选歧义走场景确认门），注册工具不新增工作台。页内那行场景切换 chip 已移除（决策 51-⑪）：
 *     侧栏「智能检测」下已常驻工作台，
 *     同一页再放一份只是重复导航，且 chip 用 EN 码反而比侧栏中文全称更难读。
 *   - 决策 3：/capabilities 不返回 input_schema，参数表只能前端自带；契约冻结的四个场景给真表单，
 *     热部图像与 X射线只声明「契约未冻结」并给出禁用理由，不预先编造字段名。
 *   - 决策 11：上传 / 校验 / 参数 / 提交与 /tasks/new 用同一套 components/intake 组件，本页只是「场景锁定」形态。
 *   - 决策 4：结果只有共享的 /tasks/:id 一套，本页不建第二套结果视图；提交后真的跳任务详情
 *     （详情页按 GET /tasks/{id} 轮询事件流），使「提交后自动进入任务详情」这句话成立。
 *   - 决策 23：本场景任务用 GET /tasks?limit=100 的窗口 + 窗口内客户端分页 + 紧凑三列表（耗时右对齐防溢出）。
 *   - 数据全部来自现有接口与本地上传状态，无任何 mock；接口没有的项（报告模板、任务备注、
 *     Idempotency-Key、图像分辨率、服务端哈希）不呈现或按原文声明为待补项。
 */
import { useEffect, useMemo, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import './WorkbenchPage.css'
import { CAPABILITY_STATUS_TEXT, CAPABILITY_TYPE_TEXT, type CapabilitySummary } from '../api/capabilities'
import { TASK_FLOW } from '../constants/workbench'
import { changedParams, defaultParamValues, validateParams, type ParamValues } from '../constants/paramContract'
import { useTaskStore } from '../store/useTaskStore'
import { useCapabilities } from '../hooks/useCapabilities'
import { useScenes } from '../hooks/useScenes'
import Pager from '../components/common/Pager'
import TaskTable from '../components/common/TaskTable'
import FileIntake from '../components/intake/FileIntake'
import ParamIntake from '../components/intake/ParamIntake'
import { SCENE_LINKAGE } from '../components/intake/sceneInput'
import { IntakeConfirm, useIntakeSubmit } from '../components/intake/IntakeSubmit'
import { useIntakeFiles } from '../components/intake/useIntakeFiles'
import { maxMBOf } from '../components/intake/fileValidation'
import { Notice } from '../components/common/ModuleShell'
import { Icon } from '../components/ui/Icon'
import { NotFound } from './ShellPage'

/** 任务窗口上限：GET /tasks 单次最多 100 条（接口无 offset / 时间筛选参数） */
/** 原型口径：窄容器每页 5 行 */
const DEFAULT_PAGE_SIZE = 5

interface CapState {
  text: string
  cls: string
}

/** 能力就绪态：读取中 / 读取失败 / 未注册都要与「降级」区分，避免把没取到说成服务异常 */
function capState(
  cap: CapabilitySummary | null,
  flags: { loading: boolean; error: string | null },
): CapState {
  if (flags.error) return { text: '能力状态未知', cls: 't-pending' }
  if (!cap) {
    return flags.loading ? { text: '能力读取中', cls: 't-pending' } : { text: '未注册', cls: 't-pending' }
  }
  return cap.status === 'enabled'
    ? { text: '能力就绪', cls: 't-success' }
    : { text: `能力降级 · ${CAPABILITY_STATUS_TEXT[cap.status]}`, cls: 't-review' }
}

export default function WorkbenchPage() {
  const { scene = '' } = useParams()
  /* 工作台条目＝场景目录（决策 52）：目录里没有这个 scene 才是真的不存在 */
  const { scenes: workbenches } = useScenes()
  const wb = workbenches.find((w) => w.scene === scene)
  const navigate = useNavigate()

  const tasks = useTaskStore((s) => s.tasks)
  const tasksLoading = useTaskStore((s) => s.loading)
  const fetchList = useTaskStore((s) => s.fetchList)
  const { caps, loading: capsLoading, error: capsError } = useCapabilities()

  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(DEFAULT_PAGE_SIZE)
  const [paramValues, setParamValues] = useState<ParamValues>(defaultParamValues)

  const scenes = useMemo<readonly string[]>(() => (wb ? [wb.scene] : []), [wb])
  const intake = useIntakeFiles(scenes)
  const { submitting, submitted, submit, clearSubmitted } = useIntakeSubmit()

  useEffect(() => {
    if (!tasks.length) void fetchList()
  }, [tasks.length, fetchList])

  // 换工作台等于换一个窗口视图：分页回到首页
  useEffect(() => setPage(1), [scene])

  // 原型「提交后自动进入任务详情」：真的导航过去，由共享详情页按 GET /tasks/{id} 轮询事件流
  useEffect(() => {
    if (!submitted) return
    const { taskId } = submitted
    clearSubmitted()
    navigate(`/tasks/${taskId}`)
  }, [submitted, navigate, clearSubmitted])

  const sceneCaps = useMemo(() => caps.filter((c) => c.scene === scene), [caps, scene])
  const cap = sceneCaps[0] ?? null
  const frozen = wb?.params === 'frozen'

  const sceneTasks = useMemo(() => tasks.filter((t) => t.scene === scene), [tasks, scene])
  const pageCount = Math.max(1, Math.ceil(sceneTasks.length / pageSize))
  const safePage = Math.min(page, pageCount)
  const rows = sceneTasks.slice((safePage - 1) * pageSize, safePage * pageSize)

  if (!wb) return <NotFound path={`/detect/${scene}`} />

  const sceneCode = wb.scene
  const state = capState(cap, { loading: capsLoading, error: capsError ?? null })
  const capabilityLine = capsError
    ? '能力注册表读取失败'
    : cap
      ? `${cap.capability_id}@${cap.tool_version}`
      : capsLoading
        ? '能力注册表读取中…'
        : '注册表内该场景暂无已登记工具'
  const typeText = cap ? CAPABILITY_TYPE_TEXT[cap.capability_type] : '—'

  /** 参数分区：只有冻结契约的场景有表单，且只下发改动过的项（与 SubmitPage 同一套规则） */
  const parameters = frozen ? changedParams(paramValues) : null
  const changedCount = Object.keys(parameters ?? {}).length
  const parametersJson = JSON.stringify(parameters ?? {}, null, 2)
  const paramErrs = frozen ? validateParams(paramValues) : []
  const blockReasons = [
    ...intake.fileErrors,
    ...(intake.files.length ? [] : ['请上传检测图像或视频']),
    ...intake.linkageMiss,
    ...paramErrs,
  ]

  async function handleSubmit() {
    await submit(
      {
        scene: sceneCode,
        mode: 'single',
        scenes: [sceneCode],
        files: intake.files.map((f) => f.file),
        parameters,
      },
      blockReasons,
    )
  }

  return (
    <div className="workbench-page">
      <section className="card wb-head-card">
        <div className="scenebar">
          <span className="scene-ico">
            <Icon name="flask" size={20} />
          </span>

          <div className="scene-meta grow">
            <b>
              {wb.label}
              <span className="tag t-accent">{wb.en}</span>
              <span className={`tag ${state.cls}`}>{state.text}</span>
            </b>
            <p className="scene-intro">{cap ? `${cap.display_name}。` : wb.input}</p>
            <p className="tech-meta">
              执行能力 <code className="mono">{capabilityLine}</code> · 模型{' '}
              <code className="mono">{cap ? cap.model_version : '—'}</code>；提交后结果与复核统一由任务中心承载。
            </p>
            <div className="scene-chips">
              <span className={frozen ? 'chip active' : 'chip'}>
                {frozen ? '参数契约已冻结' : '参数契约未冻结'}
              </span>
              <span className="chip">能力类型 {typeText}</span>
              <Link className="chip" to="/capability/pro">
                专业依据与判定标准
              </Link>
            </div>
            <div className="flowline">
              <b>提交后执行链</b> <code>{TASK_FLOW}</code>
            </div>
            <span className="card-note">
              不是「调工具就直接出报告」：判级为 review / fail 的任务会<b>停在待复核</b>等复核人提交结论，之后才生成报告；
              pass / defect 才自动出报告。结果与复核只在共享的任务详情页呈现（本工作台不设第二套结果视图）。
            </span>
            {sceneCaps.length > 1 && (
              <span className="card-note">
                本场景在注册表内挂了 <b>{sceneCaps.length}</b> 个工具：工作台按<b>场景</b>存在、工具按<b>注册</b>存在，
                候选歧义由执行链的场景确认门处理，注册新工具不会新增工作台（台账见 <Link to="/tool/registry">工具注册</Link>）。
              </span>
            )}
            {!capsLoading && !capsError && sceneCaps.length === 0 && (
              <span className="card-note">
                注册表中该场景暂无已登记工具：本页不编造能力 ID 与版本，提交会停在能力选择门。
                登记与热刷新在 <Link to="/tool/registry">工具注册</Link>。
              </span>
            )}
            {capsError && (
              <span className="card-note">能力注册表读取失败：{capsError}（不影响参数口径，恢复后可重试）。</span>
            )}
          </div>
        </div>
      </section>

      <div className="grid-23">
        <div className="stack">
          <section className="card">
            <div className="card-h">
              <div className="card-title">
                <span className="step-num">1</span>样本上传<span className="en">INPUT ASSETS</span>
              </div>
              <span className="hint">
                图像 ≤ {maxMBOf('image')}MB · 视频 ≤ {maxMBOf('video') / 1024}GB · {wb.input}
              </span>
            </div>
            <div className="card-b">
              <FileIntake scenes={scenes} intake={intake} compact />
              <div className="cond-note">
                <b>联动规则 — </b>
                {intake.linkageMiss.length
                  ? `缺失：${intake.linkageMiss.join('；')}，提交将被前置校验拦截。`
                  : SCENE_LINKAGE[sceneCode]
                    ? '本场景配套文件已齐备。'
                    : '本场景无配套文件要求，仅需检测图像 / 视频。'}
              </div>
              <span className="card-note">
                校验在浏览器本地按扩展名与体积实时判定（图像 ≤ {maxMBOf('image')}MB、视频 ≤{' '}
                {maxMBOf('video') / 1024}GB），服务端格式校验发生在执行链的「输入校验」门。
              </span>
            </div>
          </section>

          <section className="card">
            <div className="card-h">
              <div className="card-title">
                <span className="step-num">2</span>专业参数<span className="en">PARAMETERS</span>
              </div>
              <span className="hint mono">→ parameters</span>
            </div>
            <div className="card-b">
              <ParamIntake scenes={scenes} values={paramValues} onChange={setParamValues} catalog={workbenches} />

              {frozen ? (
                <details className="trace mt-16">
                  <summary>本次将提交的 parameters</summary>
                  <div className="trace-b-json">
                    <pre className="json">{parametersJson}</pre>
                    <span className="card-note">
                      {changedCount
                        ? `以上 ${changedCount} 项为改动项，序列化后随 POST /tasks 的 parameters 透传给工具；未改动的字段不下发，沿用注册中心 default_params。`
                        : '未改动任何参数：提交时不发送 parameters 字段，全部沿用注册中心 default_params。'}
                    </span>
                  </div>
                </details>
              ) : (
                <>
                  <span className="card-note">
                    该工具的输入 / 结果 Schema 仍在与工具方对齐（方案 §9.1：字段删除、单位或枚举语义变化须升主版本；
                    未登记 Schema 版本的工具拒绝联调）。工作台仍可<b>按默认参数直接提交</b>完成检测；
                    本表单将在契约冻结后按工具侧 Schema 上线，前端不预先编造字段。
                  </span>
                  <div className="grid-12 mt-16">
                    <div className="field">
                      <label className="label">
                        专业参数表单<span className="opt">契约冻结后启用</span>
                      </label>
                      <input className="input" placeholder="按工具侧 Schema 生成字段" disabled />
                      <span className="hint">禁用原因：该参数未在已冻结 Schema 中登记</span>
                    </div>
                    <div className="field">
                      <label className="label">
                        提交时的 parameters<span className="opt">本场景恒为空</span>
                      </label>
                      <input className="input" value="不发送 parameters 字段" disabled />
                      <span className="hint">禁用原因：同上；服务端沿用注册中心 default_params</span>
                    </div>
                  </div>
                </>
              )}
            </div>
          </section>
        </div>

        <div className="stack">
          <section className="card">
            <div className="card-h">
              <div className="card-title">
                <span className="step-num">3</span>提交<span className="en">SUBMIT</span>
              </div>
              <span className="hint mono">POST /tasks</span>
            </div>
            <div className="card-b">
              <div className="submit-rows">
                <div className="sum-row">
                  <span className="label">执行能力</span>
                  <b className="mono">{capabilityLine}</b>
                </div>
                <div className="sum-row">
                  <span className="label">样本数</span>
                  <b className="num">{intake.files.length}</b>
                </div>
                <div className="sum-row">
                  <span className="label">模式</span>
                  <b>
                    <span className="tag t-accent">单场景</span>
                  </b>
                </div>
              </div>

              <Notice tone="info">
                <span>
                  提交后自动进入任务详情，事件流按 <code className="mono">{'GET /tasks/{id}'}</code> 轮询。
                </span>
              </Notice>

              <IntakeConfirm
                submitting={submitting}
                onSubmit={handleSubmit}
                submitText={`发起${wb.label}任务`}
                hint="场景与模式已锁定为本工作台（mode=single）· 校验未通过时逐条弹出拦截原因"
              />
              <div className="hint mt-8">
                幂等：本轮后端未提供 <code className="mono">Idempotency-Key</code> 通道，重复点击会产生新任务
                （方案 §9.3 待补项）。
              </div>
            </div>
          </section>

          <section className="card">
            <div className="card-h">
              <div className="card-title">
                本场景最近任务<span className="en">RECENT</span>
              </div>
              <Link className="btn btn-sm" to="/tasks">
                全部
              </Link>
            </div>
            <TaskTable
              rows={rows}
              emptyText={tasksLoading ? '读取任务窗口…' : `窗口内暂无${wb.label}任务`}
            />
            <Pager
              total={sceneTasks.length}
              page={safePage}
              pageSize={pageSize}
              onPage={setPage}
              onPageSize={(s) => {
                setPageSize(s)
                setPage(1)
              }}
            />
          </section>
        </div>
      </div>
    </div>
  )
}
