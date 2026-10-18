import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { fetchTaskList } from '../api/tasks'
import type { SceneCode, TaskSummary } from '../api/types'
import { SCENE_MAP } from '../constants/scene'
import Pager from '../components/common/Pager'
import StatusTag from '../components/status/StatusTag'
import Tabs from '../components/ui/Tabs'
import { LoadError, Notice } from '../components/common/ModuleShell'
import { formatBeijing } from '../utils/time'
import './ReportsPage.css'

/* 报告与综合研判（决策 7）：报告在任务终局后自动撰写，正文按任务从
   GET /reports/{task_id} 直连读取，本页是报告入口清单 + 综合研判承接声明。

   取数边界（不做的事）：
   - 方案 §10.2 的跨任务综合研判依赖 business_ref，后端不存在该字段（缺 G1-02），不建页面；
   - 报告四态生命周期与模板版本化无接口支撑 → 状态列只呈现任务侧真实状态；
   - 列表接口不返回判级摘要，故原型「结论摘要」列在此不落地（不做恒为「—」的列）。
   列表接口只有 limit、无 offset，故窗口 = 最近 100 条；这一口径**不写在页面上**（决策 51-⑪），分页条只报「共 N 条」。 */

const WINDOW_SIZE = 100
const TRACKS = ['全部', '单场景报告', '联合综合研判', '智能体报告'] as const
const TRACK_MODE: Array<TaskSummary['mode'] | null> = [null, 'single', 'joint', 'agent']

/** 综合研判承接（原型批注：真实可用的聚合，不编审批流） */
const AGGREGATION_ROWS: Array<{ title: string; desc: string; state: '可用' | '待后端' }> = [
  {
    title: 'mode=joint 父任务 + 子任务',
    desc: '结果页保留各专业结论出处，不相加置信度、不合成综合分',
    state: '可用',
  },
  {
    title: '同一任务的多次复核',
    desc: '以 Revision 序列呈现，最新为报告取数版本',
    state: '可用',
  },
  {
    title: '同零件/批次跨任务聚合',
    desc: '需 business_ref 落库与查询接口',
    state: '待后端',
  },
]

/** 报告固定字段（方案 §7.4）：只读清单，不可点 */
const REPORT_FIELDS = [
  '业务对象',
  '任务与样本',
  '工具/模型版本',
  '参数',
  '原始结果',
  '人工结论',
  '引用依据',
  'AI 建议',
  '签发记录',
]

export default function ReportsPage() {
  const [rows, setRows] = useState<TaskSummary[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [track, setTrack] = useState(0)
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(8)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      setRows(await fetchTaskList(WINDOW_SIZE))
    } catch (e) {
      setError(e instanceof Error ? e.message : '加载失败')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  // 报告只在任务终局后生成：已完成任务即报告清单
  const ready = useMemo(() => rows.filter((t) => t.status === 'completed'), [rows])
  const filtered = useMemo(() => {
    const mode = TRACK_MODE[track]
    return mode ? ready.filter((t) => t.mode === mode) : ready
  }, [ready, track])
  const pageCount = Math.max(1, Math.ceil(filtered.length / pageSize))
  const safePage = Math.min(page, pageCount)
  const paged = filtered.slice((safePage - 1) * pageSize, safePage * pageSize)

  const jointCount = ready.filter((t) => t.mode === 'joint').length
  const agentCount = ready.filter((t) => t.mode === 'agent').length
  const pendingCount = rows.filter((t) => t.status === 'awaiting_review').length

  return (
    <>
      <div className="page-head">
        <div>
          <div className="page-title">报告与综合研判</div>
          <div className="page-sub">
            按任务出报告 · 正文按 <span className="mono">GET /reports/&#123;task_id&#125;</span> 直连读取 ·
            清单来源 <span className="mono">GET /tasks</span>
          </div>
        </div>
        <Link className="btn btn-sm" to="/tasks">
          返回任务列表
        </Link>
      </div>

      {error && (
        <div style={{ marginBottom: 16 }}>
          <LoadError message={error} onRetry={() => void load()} />
        </div>
      )}

      <div className="kpi-grid">
        <div className="card kpi">
          <div className="kpi-label">窗口内报告</div>
          <div className="kpi-value num">{ready.length}</div>
          <div className="kpi-delta">最近 {WINDOW_SIZE} 条任务中已完成</div>
        </div>
        <div className="card kpi">
          <div className="kpi-label">联合综合研判</div>
          <div className="kpi-value num">{jointCount}</div>
          <div className="kpi-delta">多场景证据汇总成报</div>
        </div>
        <div className="card kpi">
          <div className="kpi-label">智能体报告</div>
          <div className="kpi-value num">{agentCount}</div>
          <div className="kpi-delta">自主路由 + 检查点轨迹</div>
        </div>
        <div className="card kpi">
          <div className="kpi-label">尚未出报告</div>
          <div className="kpi-value num">{pendingCount}</div>
          <div className="kpi-delta hot">待人工复核通过后生成</div>
        </div>
      </div>

      <div className="grid-23 mt-16">
        <section className="card report-list">
          <div className="card-h">
            <div className="card-title">
              报告清单 <span className="en">REPORTS</span>
            </div>
            <Tabs
              items={[...TRACKS]}
              value={track}
              onChange={(i) => {
                setTrack(i)
                setPage(1)
              }}
            />
          </div>
          <div style={{ padding: '10px 18px 0' }}>
            <span className="hint">
              来源：已完成任务（最近 {WINDOW_SIZE} 条窗口）· 状态 = 任务真实状态（报告四态无接口，不做）
            </span>
          </div>
          {loading ? (
            <div className="empty">报告清单读取中…</div>
          ) : paged.length ? (
            <div style={{ padding: '0 4px 4px' }}>
              <table className="tbl">
                <thead>
                  <tr>
                    <th>任务 ID</th>
                    <th>场景</th>
                    <th>状态</th>
                    <th>生成时间</th>
                    <th className="th-action">查看</th>
                  </tr>
                </thead>
                <tbody>
                  {paged.map((t) => (
                    <tr key={t.task_id}>
                      <td className="task-id">
                        <Link to={`/tasks/${t.task_id}/report`}>{t.task_id}</Link>
                        {t.parent_task_id && (
                          <span className="tag t-child" title={`联合子任务 · 父任务 ${t.parent_task_id}`}>
                            子
                          </span>
                        )}
                      </td>
                      <td>{(t.scene && (SCENE_MAP as Record<string, string>)[t.scene as SceneCode]) ?? t.scene ?? '多场景'}</td>
                      <td>
                        <StatusTag status={t.status} />
                      </td>
                      <td className="mono muted">{formatBeijing(t.finished_at ?? t.updated_at, 'MM-DD HH:mm')}</td>
                      <td className="td-action">
                        <Link className="btn btn-sm" to={`/tasks/${t.task_id}/report`}>
                          查看
                        </Link>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <Pager
                total={filtered.length}
                page={safePage}
                pageSize={pageSize}
                onPage={setPage}
                onPageSize={(s) => {
                  setPageSize(s)
                  setPage(1)
                }}
                pageSizeOptions={[8, 20, 50]}
              />
            </div>
          ) : (
            <div className="empty">窗口内暂无已出报告的任务</div>
          )}
        </section>

        <div className="stack">
          <section className="card">
            <div className="card-h">
              <div className="card-title">
                综合研判承接 <span className="en">AGGREGATION</span>
              </div>
            </div>
            <div className="card-b stack" style={{ gap: 11 }}>
              <Notice tone="warn">
                <b>方案 §10.2 的跨任务综合研判本轮不建页面。</b>它要求按{' '}
                <code className="mono">business_ref</code>{' '}
                聚合同一零件/批次的多个独立任务，而后端不存在该字段（缺 G1-02 / §10）。
              </Notice>
              <div className="label">当前可用的真实聚合</div>
              {AGGREGATION_ROWS.map((r) => (
                <div className="dcard" key={r.title}>
                  <div className="row">
                    <span style={{ fontSize: 13, fontWeight: 600 }}>{r.title}</span>
                    <span className="grow" />
                    <span className={`tag ${r.state === '待后端' ? 't-pending' : 't-accent'}`}>
                      {r.state}
                    </span>
                  </div>
                  <div className="dcard-d">{r.desc}</div>
                </div>
              ))}
            </div>
          </section>

          <section className="card report-fields">
            <div className="card-h">
              <div className="card-title">报告固定字段（方案 §7.4）</div>
            </div>
            <div className="card-b">
              <div className="row" style={{ gap: 6, flexWrap: 'wrap' }}>
                {REPORT_FIELDS.map((f) => (
                  <span className="chip" key={f} title="只读字段清单">
                    {f}
                  </span>
                ))}
              </div>
              <div className="hint mt-12">
                字段清单为方案条款，非可点入口。其中「原始结果」当前只能取结构化结果（原始响应未落库）；
                「模板版本」待模板库接口就绪后由后端提供。
              </div>
            </div>
          </section>
        </div>
      </div>

      <div className="mt-16">
        <Notice tone="gray">
          报告正文与元信息按任务读取（<code className="mono">GET /reports/&#123;task_id&#125;</code>）；
          超出窗口的历史报告可用任务 ID 从结果与复核页直达。
        </Notice>
      </div>
    </>
  )
}
