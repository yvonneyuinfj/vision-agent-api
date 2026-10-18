import { useEffect, useMemo, useState } from 'react'
import { Select } from 'antd'
import { Link } from 'react-router-dom'
import dayjs from 'dayjs'
import { useTaskStore } from '../../store/useTaskStore'
import { fetchTaskList, fetchTaskStats, retryTask } from '../../api/tasks'
import { SCENE_MAP } from '../../constants/scene'
import { STATUS_META } from '../../constants/task'
import StatusTag from '../../components/status/StatusTag'
import Pager from '../../components/common/Pager'
import { formatBeijing } from '../../utils/time'
import type { TaskStats, TaskStatus, TaskSummary } from '../../api/types'
import './index.css'

const POLL_INTERVAL_MS = 3000
const ACTIVE_STATUSES: ReadonlySet<TaskStatus> = new Set(['awaiting_review', 'pending', 'running'])
const DEFAULT_PAGE_SIZE = 20

type StatusFilter = TaskStatus | 'all'

const sceneLabel = (s: string | null | undefined) => (s ? (SCENE_MAP[s as keyof typeof SCENE_MAP] ?? s) : '-')

/** 任务号 / 场景 / 能力命中等关键字匹配（小写忽略大小写），只在已取的 100 条窗口内生效 */
export function matchesKeyword(t: TaskSummary, kw: string): boolean {
  const q = kw.trim().toLowerCase()
  if (!q) return true
  const fields = [t.task_id, t.capability_used ?? '', t.scene ?? '', sceneLabel(t.scene)]
  return fields.some((f) => f.toLowerCase().includes(q))
}

interface LifeNode {
  key: TaskStatus
  label: string
  en: string
  cls: string
}

const LIFE_NODES: LifeNode[] = [
  { key: 'pending', label: '排队中', en: 'PENDING · 等待推理', cls: 'c-pend' },
  { key: 'running', label: '推理中', en: 'RUNNING · GPU 执行', cls: 'c-run' },
  { key: 'awaiting_review', label: '待复核', en: 'REVIEWING · 高风险', cls: 'c-warn' },
  { key: 'completed', label: '已完成', en: 'SUCCESS · 报告就绪', cls: 'done' },
  { key: 'failed', label: '失败', en: 'FAILED · 可重试', cls: 'c-danger' },
]

const STATUS_META_TEXT: Record<TaskStatus, string> = {
  pending: '等待推理',
  running: '推理中',
  awaiting_review: '等待分配',
  completed: '无需复核',
  failed: '可重试',
  canceled: '已取消',
}

const PLUS = (
  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9">
    <path d="M12 5v14M5 12h14" />
  </svg>
)

export default function ListPage() {
  const { tasks, loading, fetchList } = useTaskStore()
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('all')
  const [sceneFilter, setSceneFilter] = useState<string>('all')
  const [keyword, setKeyword] = useState<string>('')
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(DEFAULT_PAGE_SIZE)
  const [retryingId, setRetryingId] = useState<string | null>(null)

  useEffect(() => {
    void fetchList()
  }, [fetchList])

  // 统计横幅数据源：后端全量权威聚合（列表接口受 limit=100 截断，不可用于计数）
  const [stats, setStats] = useState<TaskStats | null>(null)
  useEffect(() => {
    fetchTaskStats()
      .then(setStats)
      .catch(() => {}) // 失败已由 api 层 bizErrorHandler 提示，仅避免未处理 rejection
  }, [])

  const hasActiveTask = useMemo(
    () => tasks.some((t) => ACTIVE_STATUSES.has(t.status)),
    [tasks],
  )

  useEffect(() => {
    if (!hasActiveTask) return
    const id = window.setInterval(() => {
      // 轮询失败已由 api 层 bizErrorHandler 提示，此处仅避免未处理 rejection
      fetchTaskList(100)
        .then((items) => useTaskStore.setState({ tasks: items }))
        .catch(() => {})
      fetchTaskStats()
        .then(setStats)
        .catch(() => {})
    }, POLL_INTERVAL_MS)
    return () => window.clearInterval(id)
  }, [hasActiveTask])

  const filtered = useMemo(
    () =>
      tasks.filter(
        (t) =>
          (statusFilter === 'all' || t.status === statusFilter) &&
          (sceneFilter === 'all' || t.scene === sceneFilter) &&
          matchesKeyword(t, keyword),
      ),
    [tasks, statusFilter, sceneFilter, keyword],
  )

  // 筛选条件变化时回到第一页
  useEffect(() => {
    setPage(1)
  }, [statusFilter, sceneFilter, keyword])

  const pageRows = useMemo(
    () => filtered.slice((page - 1) * pageSize, page * pageSize),
    [filtered, page, pageSize],
  )

  /** 改每页条数时回到首行，避免停在越界页 */
  function handlePageSizeChange(size: number) {
    setPageSize(size)
    setPage(1)
  }

  const modeLabel = (m: string) => (m === 'joint' ? '联合' : m === 'agent' ? '智能体' : '单场景')

  async function handleRetry(t: TaskSummary) {
    setRetryingId(t.task_id)
    try {
      await retryTask(t.task_id)
      await fetchList()
    } catch {
      // 业务错误已由 api 层 bizErrorHandler 提示，此处仅避免未处理 rejection
    } finally {
      setRetryingId(null)
    }
  }

  function duration(t: TaskSummary): string {
    if (t.finished_at && t.created_at) {
      const seconds = dayjs(t.finished_at).diff(dayjs(t.created_at), 'second')
      if (seconds > 0) return `${seconds}s`
    }
    return '—'
  }

  function rowAction(t: TaskSummary) {
    if (t.status === 'completed') {
      return <Link className="btn btn-sm btn-primary" to={`/tasks/${t.task_id}`}>查看报告</Link>
    }
    if (t.status === 'awaiting_review') {
      return <Link className="btn btn-sm" to={`/tasks/${t.task_id}`}>去复核</Link>
    }
    if (t.status === 'failed') {
      const isRetrying = retryingId === t.task_id
      return (
        <button
          className="btn btn-sm"
          type="button"
          disabled={isRetrying}
          onClick={() => void handleRetry(t)}
        >
          {isRetrying ? '重试中…' : '重试'}
        </button>
      )
    }
    return (
      <button className="btn btn-sm" type="button" disabled style={{ opacity: 0.45, cursor: 'default' }}>
        等待
      </button>
    )
  }

  const hasFilter = statusFilter !== 'all' || sceneFilter !== 'all' || keyword.trim() !== ''

  return (
    <>
      <div className="page-head">
        <div>
          <div className="page-title">任务列表</div>
        </div>
        <div className="row">
          <div className="stat-mini">
            {/* stats 加载前占位 —，加载后展示后端全量权威计数 */}
            <span>今日 <b>{stats ? stats.today : '—'}</b></span>
            <span>进行中 <b>{stats ? stats.pending + stats.running : '—'}</b></span>
            <span>待复核 <b style={{ color: 'var(--warn)' }}>{stats ? stats.awaiting_review : '—'}</b></span>
            <span>失败 <b style={{ color: 'var(--danger)' }}>{stats ? stats.failed : '—'}</b></span>
          </div>
        </div>
      </div>

      <section className="card life">
        <div className="life-head">
          <div className="life-title">任务生命周期 · 实时流转</div>
          <div className="life-live"><span className="dot pulse" style={{ background: 'var(--accent)' }} />{hasActiveTask ? '有进行中任务 · 3s 轮询刷新' : '无进行中任务 · 计数为窗口内（最近 100 条）口径'}</div>
        </div>
        <div className="tlx">
          {LIFE_NODES.map((node, i) => {
            const n = tasks.filter((t) => t.status === node.key).length
            return (
              <div className={`tlx-node ${n > 0 ? 'active' : 'idle'}`} key={node.key}>
                <span className="tlx-line" />
                <span className={`tlx-dot ${node.cls}`}>{i + 1}</span>
                <div className="tlx-txt"><b>{node.label}</b><span>{node.en}</span></div>
              </div>
            )
          })}
        </div>
      </section>

      <section className="card">
        <div className="filter-bar">
          <div className="field">
            <label className="label" htmlFor="lp-scene">场景</label>
            <Select
              id="lp-scene"
              value={sceneFilter}
              onChange={(v) => setSceneFilter(v as string)}
              options={[
                { value: 'all', label: '全部场景' },
                ...Object.entries(SCENE_MAP).map(([v, label]) => ({ value: v, label })),
              ]}
            />
          </div>
          <div className="field">
            <label className="label" htmlFor="lp-status">状态</label>
            <Select
              id="lp-status"
              value={statusFilter}
              onChange={(v) => setStatusFilter(v as StatusFilter)}
              options={[
                { value: 'all', label: '全部状态' },
                ...(Object.keys(STATUS_META) as TaskStatus[]).map((k) => ({ value: k, label: STATUS_META[k].label })),
              ]}
            />
          </div>
          <div className="field">
            <label className="label" htmlFor="lp-kw">关键字</label>
            <input
              id="lp-kw"
              className="input w-kw"
              value={keyword}
              onChange={(e) => setKeyword(e.target.value)}
              placeholder="任务 ID / 场景 / 能力（仅本窗口）"
            />
          </div>
          {hasFilter && (
            <button className="btn" type="button" onClick={() => { setStatusFilter('all'); setSceneFilter('all'); setKeyword('') }}>
              重置
            </button>
          )}
          <span className="grow" />
          <Link className="btn btn-primary" to="/tasks/new">{PLUS}新建任务</Link>
        </div>

        {loading ? (
          <div className="list-loading">加载中…</div>
        ) : filtered.length === 0 ? (
          <div className="list-empty">未匹配到符合条件的任务（当前 0 条）</div>
        ) : (
          <table className="tbl">
            <thead>
              <tr>
                <th>任务 ID</th>
                <th>场景</th>
                <th>状态</th>
                <th>模式与能力</th>
                <th>提交时间</th>
                <th className="num">耗时</th>
                <th style={{ width: 96 }}>操作</th>
              </tr>
            </thead>
            <tbody>
              {pageRows.map((t) => (
                <tr key={t.task_id}>
                  <td className="task-id">
                    <Link to={`/tasks/${t.task_id}`}>{t.task_id}</Link>
                    {t.parent_task_id && (
                      <span className="tag t-child" title={`联合子任务 · 父任务 ${t.parent_task_id}`}>子</span>
                    )}
                  </td>
                  <td>{sceneLabel(t.scene)}</td>
                  <td>
                    <span className="st-tag">
                      <StatusTag status={t.status} />
                      <span className="st-meta">{STATUS_META_TEXT[t.status]}</span>
                    </span>
                  </td>
                  <td className="muted">
                    {modeLabel(t.mode)}
                    <br />
                    <span className="mono sub">{t.capability_used ?? '—'}</span>
                  </td>
                  <td className="mono muted">{formatBeijing(t.created_at, 'MM-DD HH:mm')}</td>
                  <td className="num muted cost">{duration(t)}</td>
                  <td>{rowAction(t)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}

        <div className="card-b row" style={{ borderTop: '1px solid var(--border)' }}>
          <span className="hint">运行中任务的操作：</span>
          <button className="btn btn-sm" type="button" disabled style={{ marginLeft: 4 }}>取消</button>
          <span className="hint">该工具未提供中止能力，无法中断（不伪造 CANCELLED 状态）</span>
          <span className="grow" />
          <span className="hint">重试 = 原地重置该任务，接口不生成 retry_of 关联任务</span>
        </div>

        <div className="card-b row">
          <span className="hint">
            场景 / 状态 / 关键字均在「最近 100 条窗口」内匹配 · 顶部统计取后端全量口径
            · 待复核不是卡住，是在等人（判级 review/fail 会停在复核门）
          </span>
          <Pager
            total={filtered.length}
            page={page}
            pageSize={pageSize}
            onPage={setPage}
            onPageSize={handlePageSizeChange}
            pageSizeOptions={[10, 20, 50]}
          />
        </div>
      </section>
    </>
  )
}