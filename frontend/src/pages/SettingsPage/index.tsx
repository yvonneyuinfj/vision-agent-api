/** 运行监控（后台管理挂载 / 决策 11）：六张子系统状态卡 + 场景能力状态卡 + 建设中壳说明。
 *  数据源仅用已有接口：/health、/capabilities/health、/ragflow/status、/tasks/stats、/capabilities。
 *  无接口的子系统一律显示「—」并标注「无接口，不估算」，不填 mock 数字。
 *  原「能力明细」表已换成与首页同源的整组场景卡（决策 51-⑩）：本页只回答「场景目录里的场景现在能不能用」
 *  （条数＝GET /scenes，决策 52），
 *  工具/模型版本与探测异常原因等台账字段一律去 /tool/registry 看，不在这里重复一份低配台账。
 *  外观（主题）在顶栏用户区、关于信息在页脚，本页不再重复。 */
import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { useSystemStore } from '../../store/useSystemStore'
import { useCapabilities } from '../../hooks/useCapabilities'
import { useScenes } from '../../hooks/useScenes'
import { fetchServerDate } from '../../api/system'
import { fetchTaskStats } from '../../api/tasks'
import type { TaskStats } from '../../api/types'
import { clusterText, ragflowText, type StatusTone } from '../../utils/systemStatus'
import SceneStatusCards from '../../components/common/SceneStatusCards'
import { LoadError, ModuleShell } from '../../components/common/ModuleShell'
import './index.css'

const EMPTY = '—'

/** /health 实际下发的字段宽于 api/system 的声明类型，此处仅按可选字段读取 */
interface HealthPayload {
  server_date?: string
  vlm_available?: boolean
  status?: string
  database?: string
}

type CardTone = 't-success' | 't-failed' | 't-review' | 't-accent' | 't-pending'

const DOT_COLOR: Record<CardTone, string> = {
  't-success': 'var(--success)',
  't-failed': 'var(--danger)',
  't-review': 'var(--warn)',
  't-accent': 'var(--accent)',
  't-pending': 'var(--faint)',
}

const toneOfStatus = (t: StatusTone): CardTone =>
  t === 'ok' ? 't-success' : t === 'error' ? 't-failed' : 't-pending'

/** 状态卡（原型 grid-12 单元）：名称 + 等宽端点行 + 状态标签；无数据即「—」 */
function StatusCard({
  name,
  endpoint,
  tone,
  tag,
  note,
}: {
  name: string
  endpoint: string
  tone: CardTone
  tag: string
  note?: string
}) {
  return (
    <div className="card">
      <div className="card-b mon-row">
        <span className={`env-dot${tone === 't-success' || tone === 't-failed' ? ' pulse' : ''}`} style={{ background: DOT_COLOR[tone] }} />
        <div className="grow">
          <div className="mon-name">{name}</div>
          <div className="mono mon-ep">{endpoint}</div>
          {note && <div className="mon-note">{note}</div>}
        </div>
        <span className={`tag ${tone}`}>{tag}</span>
      </div>
    </div>
  )
}

/** 运行状态与告警壳说明（决策 9：只给条款、依赖接口与工作包号） */
const MONITOR_SHELL = {
  title: '运行状态与告警',
  sub: '队列、存储、失败任务处置与告警阈值，本轮不实现',
  goal: '运行状态、队列、存储、工具健康与失败任务处置的统一监控；配置错误须触发告警而不是静默失败。',
  clause: '建设方案 §8.2 管理后台 · §11 部署与运维',
  api: '队列深度与并发指标、存储用量、告警规则与阈值配置、`/metrics`',
  pkg: 'G4-02 运维监控与告警',
  next: '队列/存储面板、失败任务批量处置、告警阈值配置（凭证只写不读）',
}

export default function SettingsPage() {
  const caps = useSystemStore((s) => s.caps)
  const ragflow = useSystemStore((s) => s.ragflow)
  const vlmAvailable = useSystemStore((s) => s.vlmAvailable)
  const probe = useSystemStore((s) => s.probe)
  const { caps: registry, loading, error: capsError, refresh } = useCapabilities()
  const { scenes: workbenches } = useScenes()

  const [health, setHealth] = useState<HealthPayload | null>(null)
  const [stats, setStats] = useState<TaskStats | null>(null)
  const [refreshing, setRefreshing] = useState(false)

  const load = useCallback(async () => {
    const [h, st] = await Promise.allSettled([fetchServerDate() as Promise<HealthPayload>, fetchTaskStats()])
    setHealth(h.status === 'fulfilled' ? h.value : null)
    setStats(st.status === 'fulfilled' ? st.value : null)
    await probe()
  }, [probe])

  useEffect(() => {
    void load()
  }, [load])

  async function handleRefresh() {
    setRefreshing(true)
    try {
      await Promise.all([load(), refresh()])
    } finally {
      setRefreshing(false)
    }
  }

  const cluster = clusterText(caps)
  const rag = ragflowText(ragflow)
  const healthy = caps?.filter((c) => c.healthy).length ?? null
  const probed = caps?.length ?? 0

  return (
    <div className="settings">
      <div className="page-head">
        <div>
          <div className="page-title">运行监控</div>
          <div className="page-sub">
            子系统健康 · 能力就绪 · 任务量 · 路由切换时探测
            {health?.server_date ? ` · 服务器日期 ${health.server_date}` : ''}
          </div>
        </div>
        <button type="button" className="btn btn-sm" disabled={refreshing} onClick={() => void handleRefresh()}>
          {refreshing ? <span className="spin" /> : null} 立即重新探测
        </button>
      </div>

      {capsError && <LoadError message={capsError} onRetry={() => void refresh()} />}

      <div className="grid-12">
        <StatusCard
          name="后端服务"
          endpoint="/health"
          tone={health ? (health.status === 'healthy' ? 't-success' : 't-review') : 't-pending'}
          tag={health ? (health.status === 'degraded' ? '降级' : health.status === 'healthy' ? '正常' : '已响应') : '未知'}
          note={health ? `数据库 ${health.database ?? EMPTY}` : '探测未返回：不估算'}
        />
        <StatusCard
          name="推理集群（VLM 多模态）"
          endpoint="/health → vlm_available"
          tone={vlmAvailable === null ? 't-pending' : vlmAvailable ? 't-success' : 't-failed'}
          tag={vlmAvailable === null ? '未知' : vlmAvailable ? '可用' : '不可用 · 助手已降级'}
          note={vlmAvailable === null ? '未探测：不估算' : undefined}
        />
        <StatusCard
          name="对象存储"
          endpoint={EMPTY}
          tone="t-pending"
          tag={EMPTY}
          note="无接口，不估算"
        />
        <StatusCard
          name="知识检索 RAGFlow"
          endpoint="/ragflow/status"
          tone={toneOfStatus(rag.tone)}
          tag={rag.label.split(' · ')[1] ?? rag.label}
          note={
            ragflow?.configured
              ? `索引文档 ${ragflow.doc_count} 篇${ragflow.latest_doc_time ? ` · 最近入库 ${ragflow.latest_doc_time}` : ''}`
              : '未配置 / 未探测：不估算文档量'
          }
        />
        <StatusCard
          name="能力就绪"
          endpoint="/capabilities/health"
          tone={toneOfStatus(cluster.tone)}
          tag={healthy === null ? '未知' : `${healthy} 就绪 / ${probed - healthy} 降级`}
          note={healthy === null ? '探测未返回：不估算' : `逐个工具探测 ${probed} 项`}
        />
        <StatusCard
          name="任务量（stats）"
          endpoint="/tasks/stats"
          tone={stats ? 't-accent' : 't-pending'}
          tag={stats ? `总 ${stats.total}` : EMPTY}
          note={
            stats
              ? `今日 ${stats.today} · 待复核 ${stats.awaiting_review} · 执行中 ${stats.running} · 失败 ${stats.failed}`
              : '统计接口未返回：不估算'
          }
        />
      </div>

      <div className="page-head mt-16">
        <div>
          <div className="page-title" style={{ fontSize: 16 }}>
            场景能力状态
          </div>
          <div className="page-sub">
            {workbenches.length} 个检测场景 · 卡片状态取自 /capabilities，点击进对应工作台
          </div>
        </div>
        <Link to="/tool/registry" className="btn btn-sm">
          工具注册
        </Link>
      </div>
      {/* 传 null＝注册表读取中或读取失败，卡片显示「未知」；取到数组后某场景匹配不到才是真的「未注册」 */}
      <SceneStatusCards caps={loading || capsError ? null : registry} workbenches={workbenches} />

      <div className="mt-16">
        <ModuleShell {...MONITOR_SHELL} />
      </div>
    </div>
  )
}
