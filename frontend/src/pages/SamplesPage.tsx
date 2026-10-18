import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  fetchReviewMatrix,
  ACTION_TEXT,
  DEFECT_DIFF_TEXT,
  type ReviewMatrix,
} from '../api/reviewMatrix'
import { useTaskStore } from '../store/useTaskStore'
import Pager from '../components/common/Pager'
import TaskTable from '../components/common/TaskTable'
import MeterRows from '../components/common/MeterRows'
import { LoadError, Notice } from '../components/common/ModuleShell'
import { SCENE_MAP } from '../constants/scene'
import './SamplesPage.css'

/* 反馈样本管理（建设方案 §6 数据闭环 · 决策 7）：聚合矩阵来自 GET /tasks/review-matrix
   （后端已实现，本轮唯一消费方），争议样本从 /tasks 的 100 条窗口客户端过滤。

   取数边界（不做的事）：
   - 矩阵只有 confirmed / revised / rejected 三个动作桶（requeue 不入矩阵），
     故动作分布是 3 行而非原型示意里的 4 行；
   - 无任何接口按缺陷类别（裂纹/烧蚀/…）计数，故右栏出「缺陷修订差异」（total.defects 六桶），
     不出原型示意的「缺陷类别分布」；
   - 原型 KPI 的 326/41/12.7% 与「环比 +1.4」是示意值，此处全部取矩阵真实字段。 */

const MATRIX_LIMIT = 500
const WINDOW_SIZE = 100
const PAGE_SIZE = 8

const pct = (n: number, d: number) => (d ? `${((n / d) * 100).toFixed(1)}%` : '—')

/** 动作条配色沿用状态色板：确认=success / 改判=accent / 驳回=danger */
const ACTION_TONE: Record<keyof typeof ACTION_TEXT, string> = {
  confirmed: 'var(--success)',
  revised: 'var(--accent)',
  rejected: 'var(--danger)',
}

export default function SamplesPage() {
  const [matrix, setMatrix] = useState<ReviewMatrix | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(PAGE_SIZE)

  const tasks = useTaskStore((s) => s.tasks)
  const fetchList = useTaskStore((s) => s.fetchList)

  useEffect(() => {
    if (!tasks.length) void fetchList()
  }, [tasks.length, fetchList])

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      setMatrix(await fetchReviewMatrix(MATRIX_LIMIT))
    } catch (e) {
      setError(e instanceof Error ? e.message : '复核矩阵读取失败')
    } finally {
      setLoading(false)
    }
  }, [])
  useEffect(() => {
    void load()
  }, [load])

  // 争议样本：窗口内需要人工裁决的任务。列表接口不返回复核动作，改判/驳回明细拿不到。
  const disputed = useMemo(() => tasks.filter((t) => t.status === 'awaiting_review'), [tasks])
  const pageCount = Math.max(1, Math.ceil(disputed.length / pageSize))
  const safePage = Math.min(page, pageCount)
  const pageRows = disputed.slice((safePage - 1) * pageSize, safePage * pageSize)

  const total = matrix?.total
  const decided = total ? total.actions.confirmed + total.actions.revised + total.actions.rejected : 0
  const band = matrix?.low_confidence_band

  const sceneRows = useMemo(
    () =>
      Object.entries(matrix?.by_scene ?? {})
        .map(([scene, bucket]) => ({
          label: (SCENE_MAP as Record<string, string>)[scene] ?? scene,
          value: bucket.tasks,
        }))
        .sort((a, b) => b.value - a.value),
    [matrix],
  )

  const dash = loading && !matrix ? '—' : '0'

  return (
    <>
      <div className="page-head">
        <div>
          <div className="page-title">反馈样本管理</div>
          <div className="page-sub">
            复核动作与误检/漏检分布 · 为模型迭代提供候选样本依据 · 数据源{' '}
            <span className="mono">GET /tasks/review-matrix</span>
          </div>
        </div>
        <Link className="btn btn-sm" to="/tasks/review">
          进入复核台
        </Link>
      </div>

      {error && (
        <div style={{ marginBottom: 16 }}>
          <LoadError message={error} onRetry={() => void load()} />
        </div>
      )}

      <div className="kpi-grid">
        <div className="card kpi">
          <div className="kpi-label">复核样本总数</div>
          <div className="kpi-value num">
            {matrix ? matrix.sample_count : dash}
            <span className="unit">项</span>
          </div>
          <div className="kpi-delta">最多聚合 {MATRIX_LIMIT} 条已复核留痕</div>
        </div>
        <div className="card kpi">
          <div className="kpi-label">置信阈值</div>
          <div className="kpi-value num">
            {matrix ? matrix.confidence_threshold.toFixed(2) : dash}
          </div>
          <div className="kpi-delta">低于该置信度转人工（取自任务参数）</div>
        </div>
        <div className="card kpi">
          <div className="kpi-label">低置信带</div>
          <div className="kpi-value num hot">
            {band ? band.tasks : dash}
            <span className="unit">项</span>
          </div>
          <div className="kpi-delta">
            {band ? `确认 ${band.actions.confirmed} · 改判 ${band.actions.revised} · 驳回 ${band.actions.rejected}` : '置信低于阈值转人工的人群'}
          </div>
        </div>
        <div className="card kpi">
          <div className="kpi-label">人工改判率</div>
          <div className="kpi-value num">
            {total?.revised_rate === null || total?.revised_rate === undefined
              ? '—'
              : (total.revised_rate * 100).toFixed(1)}
            <span className="unit">%</span>
          </div>
          <div className="kpi-delta down">
            改判 {total ? total.actions.revised : '—'} / 已决 {decided}（接口无同周期对比字段）
          </div>
        </div>
      </div>

      <div className="grid-23 mt-16">
        <div className="stack">
          <section className="card">
            <div className="card-h">
              <div className="card-title">
                复核动作分布 <span className="en">ACTIONS</span>
              </div>
              <span className="hint mono">合计 {matrix ? matrix.sample_count : '—'} 项</span>
            </div>
            <div className="card-b">
              <MeterRows
                head="动作"
                rows={(Object.keys(ACTION_TEXT) as Array<keyof typeof ACTION_TEXT>).map((k) => ({
                  label: ACTION_TEXT[k],
                  value: total?.actions[k] ?? 0,
                  pct: pct(total?.actions[k] ?? 0, decided),
                  tone: ACTION_TONE[k],
                }))}
              />
              <p className="hint mt-12">
                只统计<b>已提交复核决定</b>的任务留痕（确认 / 改判 / 驳回）；复核台的「退回重检」在后端矩阵里
                不成桶（<span className="mono">grading_result</span> 仅三值），故此处三行、不补第四行。
              </p>
            </div>
          </section>

          <section className="card samples-disputed">
            <div className="card-h">
              <div className="card-title">
                争议样本（本窗口客户端过滤）<span className="en">DISPUTED</span>
              </div>
              <span className="hint">窗口 {WINDOW_SIZE} 条 · 接口无复核动作字段，状态近似</span>
            </div>
            <div style={{ padding: '0 4px 4px' }}>
              <TaskTable rows={pageRows} withScene emptyText="窗口内暂无待人工裁决的争议样本" />
              <Pager
                total={disputed.length}
                page={safePage}
                pageSize={pageSize}
                onPage={setPage}
                onPageSize={(s) => {
                  setPageSize(s)
                  setPage(1)
                }}
                pageSizeOptions={[8, 20]}
              />
            </div>
            <div style={{ padding: '0 18px 14px' }}>
              <Notice tone="gray">
                未经专业确认的反馈<b>不计入候选池统计</b>（方案 §6）。进入候选池依赖{' '}
                <code className="mono">CandidateSample</code> 表与按图像哈希 / 任务关联的去重规则，属{' '}
                <b>G3-01</b>。
              </Notice>
            </div>
          </section>
        </div>

        <div className="stack">
          <section className="card">
            <div className="card-h">
              <div className="card-title">
                分场景复核量 <span className="en">BY SCENE</span>
              </div>
            </div>
            <div className="card-b">
              {sceneRows.length ? (
                <MeterRows head="场景" rows={sceneRows} showPct={false} />
              ) : (
                <div className="empty">{loading ? '矩阵读取中…' : '暂无已复核任务留痕'}</div>
              )}
            </div>
          </section>

          <section className="card">
            <div className="card-h">
              <div className="card-title">
                缺陷修订差异 <span className="en">DEFECT DIFF</span>
              </div>
            </div>
            <div className="card-b">
              <MeterRows
                head="修订类型"
                rows={(Object.keys(DEFECT_DIFF_TEXT) as Array<keyof typeof DEFECT_DIFF_TEXT>).map(
                  (k) => ({
                    label: DEFECT_DIFF_TEXT[k],
                    value: total?.defects[k] ?? 0,
                    tone:
                      k === 'added' ? 'var(--warn)' : k === 'excluded' ? 'var(--danger)' : undefined,
                  }),
                )}
                showPct={false}
              />
              <p className="hint mt-12">
                补检 = 能力漏检、排除 = 能力误报，是能力质量评估的核心口径。矩阵只返回缺陷级修订
                diff 计数，<b>没有按缺陷类别（裂纹 / 烧蚀…）的统计接口</b>，故本页不出类别分布。
              </p>
            </div>
          </section>
        </div>
      </div>
    </>
  )
}
