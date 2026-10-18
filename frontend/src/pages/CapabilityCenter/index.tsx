import { Link } from 'react-router-dom'
import { useCapabilities } from '../../hooks/useCapabilities'
import { LoadError, Notice } from '../../components/common/ModuleShell'
import { Icon } from '../../components/ui/Icon'
import {
  ADAPTER_TYPE_TEXT,
  CAPABILITY_STATUS_TEXT,
  CAPABILITY_TYPE_TEXT,
  type CapabilitySummary,
} from '../../api/capabilities'
import type { CapabilityHealth } from '../../api/system'
import { useSystemStore } from '../../store/useSystemStore'
import { PARAM_FIELDS } from '../../constants/paramContract'
import { WORKBENCH_MAP, type WorkbenchDef } from '../../constants/workbench'

/* 能力中心三页（方案 §5.1）：结构对齐已评审原型 frontend-v05-ia-prototype.html。
 * 实时数据只来自 GET /capabilities 与 /capabilities/health、系统状态探测，零 mock；
 * 专业说明文案为内置基线（方案 §7.2），以标准原文与工具侧 Schema 为准。 */

function healthOf(health: CapabilityHealth[] | null, toolName: string) {
  return health?.find((h) => h.tool_name === toolName) ?? null
}

function healthText(h: CapabilityHealth | null) {
  if (!h) return '未探测'
  return h.healthy ? `正常（${h.latency_ms ?? '—'} ms）` : `异常：${h.message}`
}

/* ---- 专业能力说明：内置基线文案（方案 §7.2，逐场景），非接口数据 ---- */

const PRO_BASIS: Record<string, string> = {
  fpi: '渗透显像后的表面开口缺陷：裂纹/气孔/疏松显示。按 EN 5152 与内部工艺规程判废，对显示长度与分布做定量测量。',
  borescope: '发动机内部目视：裂纹、烧蚀、掉块、凹坑、异物附着。按叶片展段与严重度分级，给出位置与量度。',
  infrared: '热像温度场反演：超温区域定位与峰值测温。测温精度依赖发射率设定，未按材质设发射率时结论仅供参考。',
  dr: '射线底片/数字像：气孔、夹渣、未焊透、未熔合。按等级表评定，厚度与透照几何影响可检出下限。',
  fracture: '断口形貌：疲劳条带、韧窝、解理等起源判据；需结合载荷历史与材质信息，孤立图像结论有限。',
  metallography: '金相组织：晶粒度、夹杂物级别、组织评定。评级表依据 GB/T 标准图谱，制样质量为前置条件。',
}

const PRO_INPUT: Record<string, string> = {
  fpi: '可见光渗透显像图',
  borescope: '孔探视频帧/静帧',
  infrared: '红外热像（含辐射定标信息）',
  dr: 'DR 数字射线图或 TIFF',
  fracture: '宏/微观断口照片',
  metallography: '金相明场图像',
}

const PRO_FALLBACK_BASIS =
  '该场景未纳入内置基线：判定依据以标准原文与工具侧 Schema 为准，此处只登记实时状态与版本。'

/** 单个能力卡：实时字段（状态/版本/工具）来自 /capabilities，说明文字为内置基线 */
function ProCapCard({ cap, health }: { cap: CapabilitySummary; health: CapabilityHealth[] | null }) {
  const scene = cap.scene
  const wb = (WORKBENCH_MAP as Record<string, WorkbenchDef>)[scene]
  const ready = cap.status === 'enabled'
  const h = healthOf(health, cap.tool_name)

  return (
    <div className="card" key={cap.capability_id}>
      <div className="card-h">
        <div className="card-title">
          <Icon name="flask" size={15} /> {cap.display_name}{' '}
          <span className="en mono">
            {cap.capability_id}@{cap.tool_version}
          </span>
        </div>
        <div className="row" style={{ gap: 8 }}>
          <span className="tag t-accent">{CAPABILITY_TYPE_TEXT[cap.capability_type]}</span>
          <span className={`tag ${ready ? 't-success' : 't-review'}`}>{ready ? '就绪' : '降级'}</span>
          <Link to={`/detect/${scene}`} className="btn btn-sm">
            进入工作台
          </Link>
        </div>
      </div>
      <div className="card-b grid-23" style={{ gap: 20 }}>
        <div>
          <div className="label" style={{ marginBottom: 7 }}>
            检测对象与判定依据
          </div>
          <p className="muted" style={{ fontSize: '12.5px', lineHeight: 1.8 }}>
            {PRO_BASIS[scene] ?? PRO_FALLBACK_BASIS}
          </p>
          <div className="label mt-16" style={{ marginBottom: 7 }}>
            可确认参数
          </div>
          {wb?.params === 'frozen' ? (
            /* 参数名不臆造：取自与工作台表单同源的前端契约（决策 3） */
            <div className="row" style={{ gap: 6, flexWrap: 'wrap' }}>
              {PARAM_FIELDS.map((f) => (
                <span className="chip" key={f.key} style={{ cursor: 'default' }}>
                  {f.label}
                </span>
              ))}
            </div>
          ) : (
            <div className="notice" style={{ fontSize: 12 }}>
              <span className="n-ico">
                <Icon name="warn" size={14} />
              </span>
              <span>专业参数契约未冻结，工作台不显示表单，按工具默认参数执行。</span>
            </div>
          )}
        </div>
        <div>
          <div className="label" style={{ marginBottom: 7 }}>
            输入要求与局限
          </div>
          <ul className="muted" style={{ fontSize: '12.5px', lineHeight: 1.9, paddingLeft: 18 }}>
            <li>输入：{PRO_INPUT[scene] ?? (wb ? wb.input : '见对应工作台送检要求')}</li>
            <li>输出：{CAPABILITY_TYPE_TEXT[cap.capability_type]}结果 + 量度字段 + 检出图层 + 置信度</li>
            <li>局限：低置信度检出不给肯定结论；跨任务综合评分不在本能力范围</li>
          </ul>
          <div className="dcard-f" style={{ marginTop: 14 }}>
            <span>
              tool <i className="mono">{cap.tool_name}</i>
            </span>
            <span>
              model <i className="mono">{cap.model_version}</i>
            </span>
            <span>
              adapter <i>{ADAPTER_TYPE_TEXT[cap.adapter_type]}</i>
            </span>
            <span>
              状态 <i>{CAPABILITY_STATUS_TEXT[cap.status]}</i>
            </span>
            <span>
              健康 <i>{healthText(h)}</i>
            </span>
          </div>
        </div>
      </div>
    </div>
  )
}

/** 专业能力说明：每能力一张卡（内置基线文案 + /capabilities 实时覆盖状态/版本） */
export function CapabilityProPage() {
  const { caps, health, loading, error, refresh } = useCapabilities()

  const ready = caps.filter((c) => c.status === 'enabled').length

  return (
    <>
      <div className="page-head">
        <div>
          <div className="page-title">专业能力说明</div>
          <div className="page-sub">
            各专业场景的判定依据、参数口径与局限 · 实时状态来自 <span className="mono">/capabilities</span>
          </div>
        </div>
        <span className="hint">
          共 {caps.length} 项 · 就绪 {ready} · 降级 {caps.length - ready}
        </span>
      </div>
      <div className="stack">
        {error && <LoadError message={error} onRetry={() => void refresh()} />}
        {loading && <div className="hint">正在读取能力注册表（GET /capabilities）…</div>}
        {!loading && caps.length === 0 && !error && (
          <Notice tone="gray">注册表为空：后端尚无能力记录（需先执行 scripts/sync_capabilities.py）。</Notice>
        )}
        {caps.map((cap) => (
          <ProCapCard key={cap.capability_id} cap={cap} health={health} />
        ))}
        <div className="notice gray mt-16">
          <span className="n-ico">
            <Icon name="info" size={14} />
          </span>
          <span>
            专业说明文案为<b>内置基线</b>（取自方案 §7.2），以标准原文与工具侧 Schema 为准；
            <code className="mono">/capabilities</code> 不提供判据文字，只提供 id / 名称 / 版本 / 状态。
            一个专业场景可挂多个工具（同场景歧义由任务链的 <b>scene_confirm</b> 人工确认门处理），
            注册新工具不会新增工作台。
          </span>
        </div>
      </div>
    </>
  )
}

/* ---- 通用能力说明：平台级能力（与专业工具解耦），不绑定具体检测专业 ---- */

interface GeneralCap {
  t: string
  d: string
  /** 端点证据行：均为 frontend/src/api 下已封装的现网端点 */
  ep: string
  /** 静态定性（核心/可用/受约束）；有实时探测证据的条目在渲染时覆盖 */
  tag: 'core' | 'available' | 'limited'
}

const GENERAL_CAPS: GeneralCap[] = [
  {
    t: '智能助手对话',
    d: '把自然语言需求路由到场景与能力，支持文件追问。局限：不承载专业参数，需转工作台补齐。',
    ep: 'POST /sessions/{id}/messages',
    tag: 'core',
  },
  {
    t: 'AI 解释与依据',
    d: '对结构化结果生成解释性文字并附知识引用。原则：数值与等级由确定性程序掌管，模型不复述。',
    ep: 'LangGraph + /ragflow/search',
    tag: 'core',
  },
  {
    t: '知识检索与引用',
    d: '检索并返回来源（标准号/版本/页码）。无依据时不生成肯定结论。',
    ep: 'POST /ragflow/search',
    tag: 'available',
  },
  {
    t: '报告生成',
    d: '按版本化模板 + 结构化数据生成草稿，固定字段（见报告中心与综合研判）。',
    ep: 'GET /reports/{task_id}',
    tag: 'limited',
  },
  {
    t: '任务编排',
    d: '单场景 / 多场景并行 / 联合研判（父子任务）。',
    ep: 'POST /tasks',
    tag: 'available',
  },
  {
    t: '大模型降级',
    d: 'VLM 不可用时输入框置灰并提示走专业工作台，不静默失败。',
    ep: 'GET /health → llm',
    tag: 'limited',
  },
]

const GENERAL_TAG: Record<GeneralCap['tag'], { cls: string; text: string }> = {
  core: { cls: 't-accent', text: '核心' },
  available: { cls: 't-success', text: '可用' },
  limited: { cls: 't-review', text: '受约束' },
}

/** 通用能力说明：六条平台级能力，逐条给出端点与实时可用性证据 */
export function CapabilityGeneralPage() {
  const ragflow = useSystemStore((s) => s.ragflow)
  const vlmAvailable = useSystemStore((s) => s.vlmAvailable)

  return (
    <>
      <div className="page-head">
        <div>
          <div className="page-title">通用能力说明</div>
          <div className="page-sub">平台级能力与其可用性判据 · 不绑定具体检测专业</div>
        </div>
        <span className="hint mono">/health · /ragflow/status</span>
      </div>
      <div className="grid-12">
        {GENERAL_CAPS.map((it) => {
          const probe =
            it.t === '知识检索与引用'
              ? ragflow === null
                ? { tag: GENERAL_TAG.available, ev: '状态探测中' }
                : ragflow.configured
                  ? { tag: GENERAL_TAG.available, ev: `已配置 · 索引 ${ragflow.doc_count} 篇` }
                  : { tag: GENERAL_TAG.limited, ev: 'RAGFlow 未配置：检索降级为空结果' }
              : it.t === '大模型降级'
                ? {
                    tag: GENERAL_TAG.limited,
                    ev:
                      vlmAvailable === null
                        ? 'VLM 可用性未探测'
                        : vlmAvailable
                          ? 'VLM 可用：降级路径待命'
                          : 'VLM 不可用：助手输入已置灰',
                  }
                : null
          const tag = probe?.tag ?? GENERAL_TAG[it.tag]
          return (
            <div className="dcard" key={it.t}>
              <div className="row">
                <div className="dcard-t grow">{it.t}</div>
                <span className={`tag ${tag.cls}`}>{tag.text}</span>
              </div>
              <div className="dcard-d">{it.d}</div>
              <div className="dcard-f">
                <span className="mono">{it.ep}</span>
                {probe && <span>{probe.ev}</span>}
              </div>
            </div>
          )
        })}
      </div>
    </>
  )
}

/* ---- 样例与指南：内置静态指引（方案 §5 业务流程 + §7.2 判定口径），无样例接口不放假数据 ---- */

const GUIDE_GROUPS: Array<{ t: string; items: string[] }> = [
  {
    t: '标准检测流程（五步）',
    items: [
      '① 选择入口：智能助手（描述需求）或专业工作台（按参数发起）',
      '② 上传样本，服务端做哈希与格式校验',
      '③ 确认专业参数（契约未冻结的场景按默认参数执行）',
      '④ 等待执行：受理 → 校验 → 推理 → AI 解释 → 待复核',
      '⑤ 人工复核：同意 / 改判 / 退回重检 / 驳回，然后出报告',
    ],
  },
  {
    t: '判定与置信度口径',
    items: [
      '低于阈值的检出列入「低置信带」，不给出肯定结论',
      '人工新增的漏检框以虚线标记并计入修订，不覆盖模型输出',
      '改判与驳回必须填写复核意见，意见随本次复核结论一并留痕',
      '联合研判不相加各专业置信度、不合成综合质量分',
    ],
  },
  {
    t: '结果与报告约束',
    items: [
      '工具原始响应未落库，报告引用的是归一结构化结果',
      '报告批准后为不可变快照，重生成不覆盖历史',
      '引用依据须带标准号/版本/页码，无依据不得写肯定结论',
    ],
  },
]

/** 样例与指南：给现场检测人员的一页式操作指引 */
export function CapabilityGuidePage() {
  const ragflow = useSystemStore((s) => s.ragflow)
  return (
    <>
      <div className="page-head">
        <div>
          <div className="page-title">样例与指南</div>
          <div className="page-sub">
            操作流程与判定口径 · 内置内容 · 规程原文检索请去{' '}
            <Link to="/knowledge/search">知识中心 · 检索与引用</Link>
          </div>
        </div>
      </div>
      <div className="stack">
        {GUIDE_GROUPS.map((g) => (
          <section className="card" key={g.t}>
            <div className="card-h">
              <div className="card-title">{g.t}</div>
            </div>
            <div className="card-b stack" style={{ gap: 9, fontSize: '12.5px', color: 'var(--muted)' }}>
              {g.items.map((x) => (
                <div className="row" key={x} style={{ alignItems: 'flex-start', gap: 8 }}>
                  <span style={{ color: 'var(--accent)', flex: 'none' }}>
                    <Icon name="info" size={13} />
                  </span>
                  <span>{x}</span>
                </div>
              ))}
            </div>
          </section>
        ))}
      </div>
      <div className="mt-16">
        <Notice tone="info">
          规程与判据样例（含缺陷图谱）存放在知识库中，本次不提供独立样例库：
          {ragflow?.configured
            ? `当前索引 ${ragflow.doc_count} 篇文档，可直接按关键词与专业筛选。`
            : 'RAGFlow 未配置，知识检索暂不可用。'}
        </Notice>
      </div>
    </>
  )
}
