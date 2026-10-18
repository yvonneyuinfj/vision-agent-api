import { useEffect, useMemo, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import dayjs from 'dayjs'
import { fetchTaskDetail } from '../../api/tasks'
import { useTaskStore } from '../../store/useTaskStore'
import StatusTag from '../../components/status/StatusTag'
import Pager from '../../components/common/Pager'
import { SCENE_MAP } from '../../constants/scene'
import type { TaskDetail, TaskSummary } from '../../api/types'
import './index.css'

/* 结果显示与复核（决策 4）：本叶 = 待复核队列 + 快捷跳转。
   全平台只有一套结果视图，复核与人工标注都在 /tasks/{id} 完成，此处不复制第二套。 */

const DEFAULT_PAGE_SIZE = 8

const CHECKLIST = [
  '原始模型输出只读，人工结论以新增记录保存，不覆盖模型输出',
  '低置信带（默认 < 0.60）不给肯定结论，须人工判定',
  '改判与驳回必须填写复核意见，意见随本次复核结论一并留痕',
  '待复核不是任务卡住：判级 review/fail 会停在复核门，等待人工处置',
]

const sceneName = (code: string | null | undefined) =>
  code ? (SCENE_MAP[code as keyof typeof SCENE_MAP] ?? code) : '—'

const modeText = (m: string) => (m === 'joint' ? '联合研判' : m === 'agent' ? '智能体' : '单场景')

export default function ReviewPage() {
  const [searchParams, setSearchParams] = useSearchParams()
  const navigate = useNavigate()
  const tasks = useTaskStore((s) => s.tasks)
  const loading = useTaskStore((s) => s.loading)
  const fetchList = useTaskStore((s) => s.fetchList)
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(DEFAULT_PAGE_SIZE)
  const [detail, setDetail] = useState<TaskDetail | null>(null)

  useEffect(() => {
    void fetchList()
  }, [fetchList])

  const queue = useMemo(
    () =>
      tasks
        .filter((t) => t.status === 'awaiting_review')
        .sort((a, b) => b.created_at.localeCompare(a.created_at)),
    [tasks],
  )

  const rows = useMemo(
    () => queue.slice((page - 1) * pageSize, page * pageSize),
    [queue, page, pageSize],
  )

  /* 选中项：URL 的 ?task= 优先，其次队列首项——翻页不换选中任务，J/K 只改 query */
  const selected = searchParams.get('task') ?? queue[0]?.task_id ?? null
  const select = (id: string) => setSearchParams({ task: id }, { replace: true })

  useEffect(() => {
    if (!selected) {
      setDetail(null)
      return
    }
    let alive = true
    fetchTaskDetail(selected)
      .then((d) => {
        if (alive) setDetail(d)
      })
      .catch(() => {
        if (alive) setDetail(null)
      })
    return () => {
      alive = false
    }
  }, [selected])

  /* 逐条处理的键盘语义：J/K 换选中，Enter 进入结果页 */
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'j' && e.key !== 'k' && e.key !== 'Enter') return
      const el = e.target as HTMLElement | null
      if (el && /^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName)) return
      const idx = queue.findIndex((t) => t.task_id === selected)
      if (idx < 0) return
      if (e.key === 'Enter') {
        navigate(`/tasks/${queue[idx].task_id}`)
        return
      }
      const next = queue[e.key === 'j' ? Math.min(idx + 1, queue.length - 1) : Math.max(idx - 1, 0)]
      if (next) {
        e.preventDefault()
        select(next.task_id)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [queue, selected, navigate])

  const lowConfCount = (detail?.standardized ?? []).filter((d) => (d.confidence ?? 1) < 0.6).length

  return (
    <>
      <div className="page-head">
        <div>
          <div className="page-title">待复核队列</div>
          <div className="page-sub">
            按提交时间倒序 · {queue.length} 项待办 · 数据源{' '}
            <span className="mono">GET /tasks?status=awaiting_review</span>
          </div>
        </div>
        <div className="row">
          <span className="hint">
            <kbd>J</kbd>/<kbd>K</kbd> 切换 · <kbd>Enter</kbd> 进入
          </span>
          <Link className="btn btn-sm" to="/tasks/samples">
            反馈样本管理
          </Link>
        </div>
      </div>

      <div className="grid-23">
        <section className="card">
          <div className="card-h">
            <div className="card-title">
              队列 <span className="en">QUEUE</span>
            </div>
            <span className="tag t-review">{queue.length}</span>
          </div>
          {rows.length === 0 ? (
            <div className="list-empty">{loading ? '队列加载中…' : '暂无待复核任务 · 队列已清空'}</div>
          ) : (
            <div className="queue-body stack">
              {rows.map((t: TaskSummary) => (
                <button
                  type="button"
                  key={t.task_id}
                  className={`dcard queue-row${t.task_id === selected ? ' on' : ''}`}
                  onClick={() => select(t.task_id)}
                >
                  <div className="row">
                    <span className="mono qid">{t.task_id}</span>
                    <span className="tag t-child">{sceneName(t.scene)}</span>
                    <span className="grow" />
                    <StatusTag status={t.status} />
                  </div>
                  <div className="dcard-d">
                    {modeText(t.mode)} · {t.capability_used ?? '—'} · 提交{' '}
                    {dayjs(t.created_at).format('MM-DD HH:mm')}
                  </div>
                </button>
              ))}
            </div>
          )}
          <Pager
            total={queue.length}
            page={page}
            pageSize={pageSize}
            onPage={setPage}
            onPageSize={(s) => {
              setPageSize(s)
              setPage(1)
            }}
            pageSizeOptions={[8, 20]}
          />
        </section>

        <div className="stack">
          <section className="card">
            <div className="card-h">
              <div className="card-title">
                复核要点 <span className="en">CHECKLIST</span>
              </div>
            </div>
            <div className="card-b stack">
              {CHECKLIST.map((x) => (
                <div className="cl-row" key={x}>
                  <span className="cl-dot" aria-hidden="true" />
                  <span>{x}</span>
                </div>
              ))}
            </div>
          </section>

          <section className="card">
            <div className="card-h">
              <div className="card-title">当前选中预览 <span className="en">PREVIEW</span></div>
              {selected && <span className="tag t-review mono">{selected}</span>}
            </div>
            <div className="card-b stack">
              {!selected ? (
                <span className="hint">队列为空，无需复核。</span>
              ) : !detail ? (
                <span className="hint">任务明细加载中…</span>
              ) : (
                <>
                  <div className="pv-row">
                    <span className="muted">场景</span>
                    <span className="grow" />
                    <span>{sceneName(detail.scene)}</span>
                  </div>
                  <div className="pv-row">
                    <span className="muted">能力</span>
                    <span className="grow" />
                    <span className="mono">{detail.capability_used ?? '—'}</span>
                  </div>
                  <div className="pv-row">
                    <span className="muted">检出</span>
                    <span className="grow" />
                    <span className="num">{detail.standardized?.length ?? 0} 项</span>
                  </div>
                  <div className="pv-row">
                    <span className="muted">低于阈值</span>
                    <span className="grow" />
                    <span className="num">{lowConfCount} 项</span>
                  </div>
                  <Link className="btn btn-primary" style={{ width: '100%' }} to={`/tasks/${selected}`}>
                    进入三分区结果页
                  </Link>
                  <span className="hint">
                    图像查看、图层叠加、人工标注与复核提交都在结果页完成。
                  </span>
                </>
              )}
            </div>
          </section>
        </div>
      </div>
    </>
  )
}
