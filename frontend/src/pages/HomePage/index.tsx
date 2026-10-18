/** 首页（《建设方案 v0.5》锁定稿 · 决策 11：HomePage ∪ DashboardPage 合并为本页）。
 *
 * 结构（对齐 docs/design/frontend-v05-ia-prototype.html 路由 `/`）：
 *   1. hero：双业务入口（新建检测任务 / 智能助手）
 *   2. KPI 横幅：待复核 · 执行中 · 累计完成 · 可用能力 x/N（N＝场景目录条数）
 *   3. 专业工作台：场景卡（SceneStatusCards，与运行监控共用；条数取自后端场景目录，决策 52）
 *   4. grid-23：待复核队列（紧凑任务表 ≤5 行）｜场景分布（窗口聚合）+ 平台态势（真实探测）
 *   （原第 5 段「本轮不做的能力」公告已移出页面，差距清单只在 docs 维护——决策 51-⑨）
 *
 * 数据来源全部为现有接口，无任何 mock：
 *   - GET /tasks/stats            → KPI 计数（后端权威全量，列表受 limit 截断不可用于计数）
 *   - GET /tasks?limit=100        → 待复核队列 + 场景分布聚合（决策 20：无分场景统计，前端窗口聚合）
 *   - GET /capabilities           → 各场景能力 ID 与就绪/降级
 *   - GET /scenes                 → 场景目录（工作台条数与参数冻结态，决策 52；读失败回退内置六条）
 *   - useSystemStore.probe()      → /capabilities/health + /ragflow/status + /health（平台态势）
 * 旧 /dashboard 已重定向到本页（决策 19），DashboardPage 随之下线。
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { fetchTaskStats } from '../../api/tasks'
import { fetchCapabilities, type CapabilitySummary } from '../../api/capabilities'
import { useTaskStore } from '../../store/useTaskStore'
import { useSystemStore } from '../../store/useSystemStore'
import type { SceneCode, TaskStats } from '../../api/types'
import { SCENE_MAP } from '../../constants/scene'
import TaskTable from '../../components/common/TaskTable'
import MeterRows from '../../components/common/MeterRows'
import SceneStatusCards, { buildSceneCards } from '../../components/common/SceneStatusCards'
import { useScenes } from '../../hooks/useScenes'
import { Notice } from '../../components/common/ModuleShell'
import { Icon } from '../../components/ui/Icon'
import './index.css'

const POLL_INTERVAL_MS = 5000
/** 待复核队列卡只展示窗口内前 N 条（全量在 /tasks/review） */
const QUEUE_ROWS = 5
/** /tasks 单次可取上限（接口无 offset/page，决策 23） */
const WINDOW_NOTE = '最近 100 条'

const sceneText = (scene: string | null): string =>
  scene ? SCENE_MAP[scene as SceneCode] ?? scene : '未定场景'

interface Kpi {
  label: string
  value: string | number
  unit?: string
  delta: string
  tone?: string
}

export default function HomePage() {
  const tasks = useTaskStore((s) => s.tasks)
  const fetchList = useTaskStore((s) => s.fetchList)
  const listLoading = useTaskStore((s) => s.loading)
  const healthCaps = useSystemStore((s) => s.caps)
  const ragflow = useSystemStore((s) => s.ragflow)
  const vlmAvailable = useSystemStore((s) => s.vlmAvailable)
  const probe = useSystemStore((s) => s.probe)

  /** null = 尚未取到（渲染为 —，不填占位数字） */
  const [stats, setStats] = useState<TaskStats | null>(null)
  const [regCaps, setRegCaps] = useState<CapabilitySummary[] | null>(null)

  useEffect(() => {
    void probe()
  }, [probe])

  const loadStats = useCallback(() => {
    fetchTaskStats()
      .then(setStats)
      .catch(() => {
        /* 静默：统计卡降级为 —（api 层已提示） */
      })
  }, [])

  useEffect(() => {
    void fetchList()
    loadStats()
  }, [fetchList, loadStats])

  useEffect(() => {
    fetchCapabilities()
      .then(setRegCaps)
      .catch(() => setRegCaps(null))
  }, [])

  /* 有活跃任务时轮询列表与统计（同任务中心约定） */
  const hasActive = useMemo(
    () => tasks.some((t) => t.status === 'running' || t.status === 'pending'),
    [tasks],
  )
  useEffect(() => {
    if (!hasActive) return
    const id = window.setInterval(() => {
      void fetchList()
      loadStats()
    }, POLL_INTERVAL_MS)
    return () => window.clearInterval(id)
  }, [hasActive, fetchList, loadStats])

  /* ---------------- 专业工作台场景卡（场景 = scene 路由键，条目取自 /scenes，决策 52） ---------------- */

  const { scenes: workbenches } = useScenes()
  const sceneCards = useMemo(() => buildSceneCards(regCaps, workbenches), [regCaps, workbenches])
  const readyScenes = useMemo(() => sceneCards.filter((c) => c.cap?.status === 'enabled'), [sceneCards])
  const notReadyLabels = useMemo(
    () => sceneCards.filter((c) => c.cap?.status !== 'enabled').map((c) => c.wb.label),
    [sceneCards],
  )

  /* ---------------- KPI 横幅（GET /tasks/stats 全量口径） ---------------- */

  const windowReviewCount = useMemo(
    () => tasks.filter((t) => t.status === 'awaiting_review').length,
    [tasks],
  )
  const kpis: Kpi[] = useMemo(
    () => [
      {
        label: '待复核',
        value: stats ? stats.awaiting_review : '—',
        unit: '项',
        delta: stats
          ? `等待人工提交复核 · ${WINDOW_NOTE}窗口内 ${windowReviewCount} 条`
          : '统计读取中…',
        tone: 'hot',
      },
      {
        label: '执行中',
        value: stats ? stats.running : '—',
        unit: '项',
        delta: stats ? `排队 ${stats.pending} 项 · 失败 ${stats.failed} 项` : '统计读取中…',
      },
      {
        label: '累计完成',
        value: stats ? stats.completed : '—',
        unit: '项',
        // 接口无时间参数，取不出「近 7 日」，改用 stats 今日（北京时区）新增这一真实口径
        delta: stats ? `今日新增 ${stats.today} 项 · 累计 ${stats.total} 项` : '统计读取中…',
      },
      {
        label: '可用能力',
        value: regCaps === null ? '—' : readyScenes.length,
        unit: `/ ${workbenches.length}`,
        delta:
          regCaps === null
            ? '能力注册表读取中…'
            : notReadyLabels.length === 0
              ? `${workbenches.length} 个场景能力均已就绪`
              : `${notReadyLabels.join(' · ')} 未就绪`,
        tone: regCaps !== null && notReadyLabels.length > 0 ? 'down' : undefined,
      },
    ],
    [stats, regCaps, readyScenes.length, notReadyLabels, windowReviewCount, workbenches.length],
  )

  /* ---------------- 待复核队列 / 场景分布（100 条窗口内聚合，决策 20） ---------------- */

  const queueRows = useMemo(
    () => tasks.filter((t) => t.status === 'awaiting_review').slice(0, QUEUE_ROWS),
    [tasks],
  )

  const sceneDist = useMemo(() => {
    const counts = new Map<string, number>()
    for (const t of tasks) counts.set(t.scene ?? '', (counts.get(t.scene ?? '') ?? 0) + 1)
    return [...counts.entries()]
      .map(([scene, n]) => ({
        label: scene ? sceneText(scene) : '未定场景',
        value: n,
        pct: tasks.length ? `${((n / tasks.length) * 100).toFixed(1)}%` : '—',
      }))
      .sort((a, b) => b.value - a.value)
  }, [tasks])

  /* ---------------- 平台态势（全部为现有探测结果，无接口项不呈现） ---------------- */

  const healthyCount = healthCaps?.filter((c) => c.healthy).length ?? 0
  const runtimeRows: Array<{ name: string; tone: string; text: string }> = [
    {
      name: '后端与任务队列',
      tone: stats ? 'var(--success)' : 'var(--faint)',
      text: stats ? '正常 · 统计可达' : '未知（统计接口未返回）',
    },
    {
      name: '能力服务健康',
      tone:
        healthCaps === null
          ? 'var(--faint)'
          : healthyCount === healthCaps.length
            ? 'var(--success)'
            : 'var(--warn)',
      text:
        healthCaps === null
          ? '探测中…'
          : `${healthyCount} / ${healthCaps.length} 项健康${
              healthyCount < healthCaps.length
                ? ` · ${healthCaps.filter((c) => !c.healthy).map((c) => c.tool_name).join('、')}`
                : ''
            }`,
    },
    {
      name: '知识检索 RAGFlow',
      tone: ragflow?.reachable ? 'var(--success)' : ragflow ? 'var(--danger)' : 'var(--faint)',
      text: ragflow
        ? ragflow.configured
          ? ragflow.reachable
            ? `可达 · 知识库 ${ragflow.doc_count} 篇文档`
            : '已配置但不可达'
          : '未配置'
        : '探测中…',
    },
    {
      name: '大模型（智能助手）',
      tone: vlmAvailable === null ? 'var(--faint)' : vlmAvailable ? 'var(--success)' : 'var(--danger)',
      text:
        vlmAvailable === null ? '未知（/health 未返回）' : vlmAvailable ? '可用' : '不可用 · 助手输入置灰',
    },
  ]

  return (
    <div className="home">
      {/* 1 · hero：两条业务入口 */}
      <section className="card home-hero">
        <div className="home-hero-in">
          <div className="row" style={{ gap: 8 }}>
            <span className="tag t-accent">两条业务入口 · 一个任务事实源</span>
            <span className="tag t-pending">G1-01 骨架</span>
          </div>
          <h1 className="home-hero-title">工业图像智能检测智能体</h1>
          <p className="muted home-hero-desc">
            用自然语言向智能助手描述需求，或直接进入专业工作台按契约参数发起检测；结果、复核与报告统一在任务中心沉淀。
          </p>
          <div className="row mt-16" style={{ gap: 10, flexWrap: 'wrap' }}>
            <Link className="btn btn-primary btn-lg" to="/tasks/new">
              <Icon name="tasks" size={15} /> 新建检测任务
            </Link>
            <Link className="btn btn-lg" to="/detect/assistant">
              <Icon name="chat" size={15} /> 智能助手
            </Link>
          </div>
        </div>
        <svg
          className="home-hero-art"
          viewBox="0 0 220 160"
          fill="none"
          stroke="var(--accent)"
          strokeWidth="1.1"
          aria-hidden="true"
        >
          <rect x="18" y="26" width="86" height="66" rx="6" />
          <rect x="30" y="38" width="30" height="22" rx="3" />
          <rect x="66" y="44" width="26" height="34" rx="3" strokeDasharray="4 3" />
          <path d="M18 108h86M118 26h72v66h-72z" />
          <path d="M130 56c14-16 30-16 44 0M104 59h18M130 92h52" />
          <circle cx="152" cy="72" r="9" />
        </svg>
      </section>

      {/* 2 · KPI 横幅 */}
      <section className="kpi-grid">
        {kpis.map((k) => (
          <div className="card kpi" key={k.label}>
            <div className="kpi-label">{k.label}</div>
            <div className="kpi-value num">
              {k.value}
              {k.unit && <span className="unit">{k.unit}</span>}
            </div>
            <div className={`kpi-delta${k.tone ? ` ${k.tone}` : ''}`}>{k.delta}</div>
          </div>
        ))}
      </section>

      {/* 3 · 专业工作台六卡 */}
      <div className="page-head mt-24">
        <div>
          <div className="page-title" style={{ fontSize: 16 }}>
            专业工作台
          </div>
          <div className="page-sub">
            {workbenches.length} 个检测场景 · 参数契约按冻结状态分档 · 场景条目取自 /scenes
          </div>
        </div>
        <Link className="btn btn-sm" to="/capability/pro">
          专业能力说明
        </Link>
      </div>
      <SceneStatusCards caps={regCaps} workbenches={workbenches} />

      {/* 4 · 队列 / 场景分布 / 平台态势 */}
      <div className="grid-23 mt-16">
        <section className="card">
          <div className="card-h">
            <div className="card-title">
              待复核队列 <span className="en">AWAITING REVIEW</span>
            </div>
            <div className="row" style={{ gap: 10 }}>
              <span className="hint">
                {`${WINDOW_NOTE}内 ${windowReviewCount} 条${
                  windowReviewCount > QUEUE_ROWS ? ` · 仅列前 ${QUEUE_ROWS} 条` : ''
                }`}
              </span>
              <Link className="btn btn-sm" to="/tasks/review">
                进入复核台
              </Link>
            </div>
          </div>
          <div className="home-queue">
            <TaskTable rows={queueRows} emptyText={`${WINDOW_NOTE}窗口内暂无待复核任务`} />
          </div>
        </section>

        <div className="stack">
          <section className="card">
            <div className="card-h">
              <div className="card-title">
                场景分布 <span className="en">BY SCENE</span>
              </div>
              <span className="hint mono">{WINDOW_NOTE}</span>
            </div>
            <div className="card-b">
              <Notice tone="gray">
                <span>
                  仅统计<b>{WINDOW_NOTE}任务</b>窗口，非全量（<code className="mono">GET /tasks</code> 无时间参数）。
                </span>
              </Notice>
              <div className="mt-12">
                {sceneDist.length ? (
                  <MeterRows head="场景" rows={sceneDist} />
                ) : (
                  <div className="empty">{listLoading ? '任务窗口读取中…' : `${WINDOW_NOTE}内暂无任务`}</div>
                )}
              </div>
            </div>
          </section>

          <section className="card">
            <div className="card-h">
              <div className="card-title">
                平台态势 <span className="en">RUNTIME</span>
              </div>
              <Link className="btn btn-sm" to="/admin/monitor">
                运行监控
              </Link>
            </div>
            <div className="card-b stack" style={{ gap: 9 }}>
              {runtimeRows.map((r) => (
                <div className="row home-runtime-row" key={r.name}>
                  <span className="env-dot" style={{ background: r.tone }} />
                  <span className="muted">{r.name}</span>
                  <span className="grow" />
                  <span>{r.text}</span>
                </div>
              ))}
            </div>
          </section>
        </div>
      </div>
    </div>
  )
}
