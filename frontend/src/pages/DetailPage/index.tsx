import { useCallback, useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { Spin, Result } from 'antd'
import { ReloadOutlined, EditOutlined } from '@ant-design/icons'
import dayjs from 'dayjs'
import { fetchTaskDetail } from '../../api/tasks'
import Viewer from '../../viewer/Viewer'
import { buildTaskImages, defectsForImage } from '../../viewer/taskImages'
import ReviewPanel from '../../components/review/ReviewPanel'
import DefectEditModal from '../../components/review/DefectEditModal'
import StatusTag from '../../components/status/StatusTag'
import Md from '../../components/markdown/Md'
import ThoughtChainPanel from '../../components/steps/ThoughtChainPanel'
import CitationModal from './CitationModal'
import { formatBeijing } from '../../utils/time'
import { pickArtifactImagePair } from '../../utils/artifacts'
import { useViewerStore } from '../../store/useViewerStore'
import type { TaskDetail, TaskStatus, KnowledgeItem, GradeLevel, DefectItem, StepTraceItem } from '../../api/types'
import { SCENE_MAP } from '../../constants/scene'
import './index.css'

/** SSE 优先 + 3s 轮询降级（后端暂无 stream 端点，onerror 后自动走轮询） */
function useTaskPolling(taskId: string, status: string | undefined, refresh: () => void) {
  useEffect(() => {
    if (!status || !['pending', 'running'].includes(status)) return
    let source: EventSource | null = null
    let timer: number | null = null

    try {
      source = new EventSource(`/api/v1/tasks/stream?task_id=${taskId}`)
      source.onmessage = () => refresh()
      source.onerror = () => {
        source?.close()
        source = null
        timer = window.setInterval(refresh, 3000)
      }
    } catch {
      // 环境无 EventSource（如 jsdom）→ 直接轮询
      timer = window.setInterval(refresh, 3000)
    }

    const onVisibility = () => {
      if (document.visibilityState === 'visible') refresh()
    }
    document.addEventListener('visibilitychange', onVisibility)
    return () => {
      source?.close()
      if (timer !== null) window.clearInterval(timer)
      document.removeEventListener('visibilitychange', onVisibility)
    }
  }, [taskId, status, refresh])
}

/** 判级级别文案（verdict 卡 tag 与三分区第 ② 区共用，勿与 banner 分隔） */
const GRADE_TEXT: Record<GradeLevel, string> = {
  pass: '合格',
  review: '待复核',
  fail: '异常 · 需关注',
  defect: '有缺陷 · 建议回修',
}

/** 判级 Tag（原型「C · 异常」风格统一定义，勿与 banner 的 label 重复） */
function verdictTag(level: GradeLevel | undefined) {
  const cls: Record<GradeLevel, string> = {
    pass: 't-success',
    review: 't-warn',
    fail: 't-failed',
    defect: 't-danger',
  }
  const m = level ? { cls: cls[level], text: GRADE_TEXT[level] } : null
  return m ? <span className={`tag ${m.cls}`}>{m.text}</span> : <span className="tag">未判级</span>
}

const SEV_META: Record<string, { tagCls: string; tagLabel: string; label: string; cls: string }> = {
  high: { tagCls: 'sev-high', tagLabel: 'High', label: '高严重度', cls: 'sev-h' },
  medium: { tagCls: 'sev-med', tagLabel: 'Medium', label: '中危险度', cls: 'sev-m' },
  low: { tagCls: 'sev-low', tagLabel: 'Low', label: '低风险', cls: 'sev-l' },
  null: { tagCls: 'sev-low', tagLabel: 'Low', label: '未分级', cls: 'sev-l' },
}

const VERDICT_ICO = (
  <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
    <path d="M12 4 2.8 20h18.4L12 4Z" />
    <path d="M12 10v4.5m0 2.6v.2" />
  </svg>
)

const WARN_ICO = (
  <span className="n-ico">
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9">
      <path d="M12 4 2.8 20h18.4L12 4Z" />
      <path d="M12 10v4.5m0 2.6v.2" />
    </svg>
  </span>
)

const INFO_ICO = (
  <span className="n-ico">
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9">
      <circle cx="12" cy="12" r="9" />
      <path d="M12 11v5m0-8.5v.2" />
    </svg>
  </span>
)

function sceneName(code: string | null | undefined): string {
  return code ? (SCENE_MAP[code as keyof typeof SCENE_MAP] ?? code) : '—'
}

// 详情页时间统一按北京时间展示（后端 UTC 时刻 → UTC+8 墙钟），不随浏览器时区漂移
const fmtTs = (ts?: string | null): string => formatBeijing(ts)

function ellipsis(s: string, n: number): string {
  return s.length > n ? `${s.slice(0, n)}…` : s
}

/** 输入文件名（URI 形如 /api/v1/tasks/{id}/files/{name}?kind=inputs） */
function imageName(uri: string): string {
  return uri.split('/files/')[1]?.split('?')[0] ?? uri
}

/** 判级卡标题：按检出缺陷数与高严重度计数动态生成（原型「检出 3 处缺陷，其中 1 处高严重度」） */
function verdictHeadline(defects: DefectItem[]): string {
  const n = defects.length
  if (n === 0) return '未检出缺陷'
  const high = defects.filter((d) => d.severity === 'high').length
  return high > 0 ? `检出 ${n} 处缺陷，其中 ${high} 处高严重度` : `检出 ${n} 处缺陷`
}

/** 列表/卡片位展示文档名时去掉扩展名（完整文件名只保留在知识卡标题，供用户定位检索来源） */
function displayDocName(name: string): string {
  return name.replace(/\.(md|pdf|docx?|txt)$/i, '')
}

function bboxText(d: DefectItem): string | null {
  // 契约 bbox 优先；bbox_xyxy 兼容存量 FPI 数据（旧任务无 type 字段）
  const bbox = d.location?.bbox ?? d.location?.bbox_xyxy
  return Array.isArray(bbox) && bbox.length > 0 ? bbox.map((v) => v.toFixed(1)).join(', ') : null
}

/** 三分分区头（决策 5）：序号 + 区名 + 状态 tag + 数据源标注，物理隔离、各自标源 */
function Zone({
  n,
  title,
  tag,
  tagCls,
  source,
  children,
}: {
  n: 1 | 2 | 3
  title: string
  tag: string
  tagCls: string
  source: string
  children: ReactNode
}) {
  return (
    <section className="zone">
      <div className="zone-h">
        <span className={`zn z${n}`}>{n}</span>
        <b>{title}</b>
        <span className={`tag ${tagCls}`}>{tag}</span>
        <span className="zsrc mono">{source}</span>
      </div>
      <div className="zone-b">{children}</div>
    </section>
  )
}

/** 任务详情页（决策 5 三分区）：图层对照 + ① 模型输出 ② AI 解释与依据 ③ 人工复核结论 */
export default function DetailPage() {
  const { taskId = '' } = useParams()
  const navigate = useNavigate()
  const [detail, setDetail] = useState<TaskDetail | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [layerIsArtifact, setLayerIsArtifact] = useState(false)
  const [openCitation, setOpenCitation] = useState<KnowledgeItem | null>(null)
  /* 人工标注（缺陷修订）入口：复核台已并入本页第③区，编辑弹窗随结果页常驻 */
  const [annotateOpen, setAnnotateOpen] = useState(false)
  const setActiveDefectId = useViewerStore((s) => s.setActiveDefectId)
  const activeDefectId = useViewerStore((s) => s.activeDefectId)
  const setActiveImageKey = useViewerStore((s) => s.setActiveImageKey)

  const refresh = useCallback(() => {
    fetchTaskDetail(taskId)
      .then((d) => {
        setError(null)
        setDetail(d)
      })
      .catch((e) => setError(e instanceof Error ? e.message : '加载失败'))
  }, [taskId])

  useEffect(() => {
    refresh()
  }, [refresh])
  useTaskPolling(taskId, detail?.status, refresh)

  const citationsMap = useMemo(() => {
    const map: Record<string, KnowledgeItem> = {}
    for (const k of detail?.knowledge ?? []) if (k.chunk_id) map[k.chunk_id] = k
    return map
  }, [detail])

  const images = useMemo(() => buildTaskImages(detail), [detail])

  /** 检测模型渲染的标注图产物：缺陷条目无可绘制 bbox 时（agent 轨常见）
   * 作为「算法标注图」层图像源（决策在 viewer/annotatedLayer） */
  const annotatedSrc = useMemo(
    () => pickArtifactImagePair(detail?.report?.artifacts).annotated?.uri ?? null,
    [detail],
  )

  const durationText = useMemo(() => {
    if (!detail?.created_at || !detail.finished_at) return '—'
    const sec = dayjs(detail.finished_at).diff(dayjs(detail.created_at), 'second')
    return sec >= 0 ? `${sec}s` : '—'
  }, [detail])

  const maxConf = useMemo(() => {
    let max = 0
    for (const d of detail?.standardized ?? []) max = Math.max(max, d.confidence ?? 0)
    return max > 0 ? max : null
  }, [detail])

  if (error) {
    return (
      <Result
        status="error"
        title="任务加载失败"
        subTitle={error}
        extra={
          <button className="btn btn-primary" type="button" onClick={refresh}>
            <ReloadOutlined /> 重试
          </button>
        }
      />
    )
  }

  if (!detail) {
    return (
      <div style={{ display: 'flex', justifyContent: 'center', paddingTop: 120 }}>
        <Spin tip="任务详情加载中…" />
      </div>
    )
  }

  const awaitingReview = detail.status === 'awaiting_review'
  const modeText = detail.mode === 'joint' ? '多场景联合' : detail.mode === 'agent' ? 'ReAct 智能体' : '单场景'
  const scene = sceneName(detail.scene)
  const childList = detail.children ?? []
  const defects = detail.standardized ?? []
  const knowledge = detail.knowledge ?? []
  // 人工结论：awaiting_review 时 review_json 是复核门中断载荷（question/decision_options），
  // 只有带 action + opinion 才是已提交决定；auto_confirm 是规则直通（后端不经人工写入），
  // 不能当作人工结论展示
  const reviewAction = (detail.review?.action ?? '') as string
  const isHumanDecision = Boolean(reviewAction) && reviewAction !== 'auto_confirm' && Boolean(detail.review?.opinion)
  const submittedReview = !awaitingReview && isHumanDecision ? detail.review : null
  const autoPassed = !awaitingReview && reviewAction === 'auto_confirm' ? detail.review : null
  const steps = detail.steps ?? []
  const hasImageTag = defects.some((d) => Boolean(d.image_name))

  return (
    <>
      <div className="page-head">
        <div>
          <div className="page-title mono" style={{ fontSize: 19 }}>{taskId}</div>
          <div className="page-sub">
            {scene} · {modeText} · 提交 {fmtTs(detail.created_at)} · 耗时 {durationText} · <StatusTag status={detail.status} />
          </div>
        </div>
        <div className="row">
          <button className="btn btn-primary btn-sm" type="button" onClick={() => navigate(`/tasks/${taskId}/report`)} disabled={!detail.report}>
            查看完整报告 →
          </button>
          {detail.report && (
            <a className="btn btn-sm" href={detail.report.uri} download>导出报告</a>
          )}
          <a className="btn btn-sm" href={`/tasks/review?task=${taskId}`}>送人工复核</a>
          <a className="btn btn-sm" href="/tasks">返回列表</a>
        </div>
      </div>

      <div className="notice gray">
        {INFO_ICO}
        <span>
          <b>溯源：</b>
          视觉能力 <code className="mono">{detail.capability_used ?? '—'}</code> ·
          模式 <code className="mono">{detail.mode}</code> ·
          trace <code className="mono">{detail.trace_id ?? '—'}</code> ·
          结果归一于 <code className="mono">GET /tasks/&#123;id&#125; → standardized</code>。
          版本随任务记录冻结；重试为原地重置，不生成关联任务。
        </span>
      </div>

      {/* 优化轮 E2：联合质检父子链可视化 */}
      {detail.parent_task_id && (
        <a className="parent-link mt-12" href={`/tasks/${detail.parent_task_id}`}>
          ← 本任务为联合质检子任务 · 返回父任务 <b className="mono">{detail.parent_task_id}</b>
        </a>
      )}

      {/* 优化轮 E4：失败横幅；4004（场景无可用能力）给质检助手跳转善后 */}
      {detail.status === 'failed' && detail.error && (
        <div className={`err-banner mt-12${detail.error.code === 4004 ? ' err-reject' : ''}`}>
          <div className="err-main">
            <b>任务失败</b>
            <span className="mono"> [{detail.error.code}] </span>
            {detail.error.message}
            {detail.error.code === 4004 && (
              <div className="err-hint">
                该图像对应场景暂无可用检测能力。可到质检助手重新上传识别，或向助手索取图像采集指引。
              </div>
            )}
          </div>
          {detail.error.code === 4004 && (
            <button className="btn btn-sm btn-primary" type="button" onClick={() => navigate('/detect/assistant')}>
              去质检助手 →
            </button>
          )}
        </div>
      )}
      {detail.error && detail.status !== 'failed' && (
        <p className="err-line">节点 {detail.error.node} 报错: [{detail.error.code}] {detail.error.message}</p>
      )}

      <div className="grid-23 mt-12">
        {/* 左：图层对照 + 三分区（决策 5） */}
        <div className="stack">
          <div className="card">
            {/* 图层 tab：算法标注图（缺陷框开；条目无 bbox 时用模型标注产物图）/ 原图（像素原样）；与 Viewer 内切换按钮、Space 快捷键同源 */}
            <div className="viewer-tabs">
              <button
                type="button"
                className={layerIsArtifact ? 'tab' : 'tab active'}
                onClick={() => setLayerIsArtifact(false)}
              >
                算法标注图
              </button>
              <button
                type="button"
                className={layerIsArtifact ? 'tab active' : 'tab'}
                onClick={() => setLayerIsArtifact(true)}
              >
                原图
              </button>
              <span className="grow" />
              <span className="hint">{defects.length} 处检出 · 点缺陷卡联动定位</span>
            </div>
            <div className="viewer-box">
              <Viewer
                taskId={taskId}
                images={images}
                defects={defects}
                showDefects={!layerIsArtifact}
                annotatedSrc={annotatedSrc}
                artifacts={detail.report?.artifacts ?? null}
                onSelectDefect={(id) => {
                  setActiveDefectId(id)
                  document.getElementById(`defect-card-${id}`)?.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
                }}
                onLayerSwitch={() => setLayerIsArtifact((v) => !v)}
                layerLabel={layerIsArtifact ? '原图' : '标注图'}
                layerIsArtifact={layerIsArtifact}
              />
            </div>
            <div className="row" style={{ flexWrap: 'wrap', gap: 10, padding: '11px 14px', borderTop: '1px solid var(--border)' }}>
              <span className="hint">图例：</span>
              <span className="legend-sw" style={{ color: 'var(--danger)' }}><i />严重</span>
              <span className="legend-sw" style={{ color: 'var(--warn)' }}><i />一般</span>
              <span className="legend-sw" style={{ color: 'var(--info)' }}><i />提示</span>
              <span className="legend-sw dashed" style={{ color: 'var(--success)' }}><i />人工新增</span>
              <span className="grow" />
              <span className="hint">图层对照仅切换显示，不改动第 ① 区数据</span>
              <button type="button" className="btn btn-sm" onClick={() => setAnnotateOpen(true)}>
                <EditOutlined /> 人工标注
              </button>
            </div>
            <DefectEditModal
              open={annotateOpen}
              taskId={taskId}
              inputUris={detail.input_uris ?? []}
              defects={detail.standardized ?? []}
              artifacts={detail.review?.artifacts ?? detail.report?.artifacts ?? null}
              classDefs={detail.class_definitions}
              onClose={() => {
                setAnnotateOpen(false)
                refresh()
              }}
            />
          </div>

          {/* ① 模型输出（结构化结果）：只读 standardized + 版本/溯源 */}
          <Zone n={1} title="模型输出（结构化结果）" tag="只读" tagCls="t-pending" source="GET /tasks/{id} → standardized">
            <div className="notice">
              {WARN_ICO}
              <span>
                <b>工具原始响应尚未落库</b>（后端 G1-02 项：<code className="mono">raw_results_json</code> 恒为 NULL
                且全仓库无读取处）。本区展示适配器归一后的结构化结果，<b>不等同于算法原始输出</b>，
                不可作为原始证据引用。
              </span>
            </div>
            {defects.length > 0 ? (
              <div className="defect-grid">
                {defects.map((d, i) => {
                  const id = d.defect_id ?? `defect-${i}`
                  const sev = d.severity ? SEV_META[d.severity] : SEV_META.null
                  const bbox = bboxText(d)
                  return (
                    <div
                      key={id}
                      id={`defect-card-${id}`}
                      className={`defect-card${activeDefectId === id ? ' hot' : ''} ${sev.cls}`}
                      style={{ cursor: 'pointer' }}
                      onClick={() => {
                        setActiveDefectId(id)
                        document.querySelector('.konvajs-content')?.scrollIntoView({ behavior: 'smooth' })
                      }}
                    >
                      <div className="defect-top">
                        <span className="defect-id mono">D{i + 1}</span>
                        {d.image_name && <span className="mono faint">{d.image_name}</span>}
                        <span className="grow" />
                        <span className={`tag ${sev.tagCls}`}>{sev.tagLabel}</span>
                      </div>
                      <div className="defect-name">
                        <span className="dn-cn">{d.defect_name_cn || d.defect_type}</span>
                        {d.defect_type && <span className="dn-en">{d.defect_type}</span>}
                      </div>
                      {d.confidence !== null && d.confidence !== undefined && (
                        <div className="conf-row">
                          <div className="meter">
                            <div className="meter-fill" style={{ width: `${Math.round(d.confidence * 100)}%` }} />
                          </div>
                          <span className="conf-val">{d.confidence.toFixed(2)}</span>
                        </div>
                      )}
                      <div className="defect-meta">
                        <span>{bbox ? `bbox [${bbox}]` : 'bbox [—]'}</span>
                        <span>{d.description ?? '无补充描述'}</span>
                      </div>
                    </div>
                  )
                })}
              </div>
            ) : (
              <div className="list-empty">本任务未检出缺陷条目</div>
            )}
            <div className="zone-foot">
              <span>模型 / 能力：<b className="mono">{detail.capability_used ?? '—'}</b></span>
              <span>推理耗时：<b className="mono">{durationText}</b></span>
              <span>Trace：<b className="mono">{detail.trace_id ?? '—'}</b></span>
              <span className="grow" />
              <a className="btn btn-sm" href={images[0]?.src} download>下载原图</a>
            </div>
          </Zone>

          {/* ② AI 解释与依据：判级说明 + RAG 引用（数字与等级以上区字段为准） */}
          <Zone n={2} title="AI 解释与依据" tag="大模型生成" tagCls="t-accent" source="LangGraph 判级节点 + RAGFlow 检索">
            <div className="notice info">
              {INFO_ICO}
              <span>
                实施原则：<b>确定性程序掌管事实与数值，大模型只生成解释性文字</b>。
                下列置信度、类别与定位取自第 ① 区字段，不由模型复述。
              </span>
            </div>

            {detail.grade ? (
              <div className="verdict">
                <span className="verdict-ico">{VERDICT_ICO}</span>
                <div className="grow">
                  <b>{verdictHeadline(defects)}</b>
                  <Md citations={citationsMap} onCitationClick={(id) => setOpenCitation(citationsMap[id] ?? null)}>
                    {detail.grade.summary}
                  </Md>
                </div>
                {verdictTag(detail.grade.level)}
              </div>
            ) : (
              <p className="faint">本任务尚未生成判级结论</p>
            )}

            <div className="verdict-line">
              <span>判定路径：LangGraph 判级节点 → RAG 引用 {knowledge.length} 条</span>
              <span className="grow" />
              <span className="mono">综合置信 {maxConf?.toFixed(2) ?? '—'}</span>
            </div>

            {knowledge.length > 0 ? (
              <div className="rag-grid mt-12">
                {knowledge.map((k, idx) => (
                  <div
                    className="cite-card hover"
                    key={k.chunk_id ?? idx}
                    onClick={() => setOpenCitation(k)}
                    style={{ cursor: 'pointer' }}
                  >
                    <div className="cite-src">
                      <svg className="m-ico" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
                        <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8l-6-6Z" />
                        <path d="M14 2v6h6M8 13h8M8 17h8" />
                      </svg>
                      <div>
                        <div className="cite-doc">{displayDocName(k.doc_name)}</div>
                        <div className="cite-loc">{ellipsis(k.content, 48)}</div>
                      </div>
                    </div>
                    <div className="sim-row">
                      <div className="meter">
                        <div className="meter-fill" style={{ width: `${Math.round(k.score * 100)}%` }} />
                      </div>
                      <span className="sim-val">相似度 {k.score.toFixed(2)}</span>
                    </div>
                    <div className="row mt-12">
                      <button
                        type="button"
                        className="btn btn-sm"
                        onClick={(e) => {
                          e.stopPropagation()
                          setOpenCitation(k)
                        }}
                      >
                        预览原文
                      </button>
                      <span className="hint">引用段落已高亮</span>
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <div className="list-empty mt-12">本任务暂无知识库引用</div>
            )}

            <p className="zone-note">
              局限性声明：综合置信取各检出项置信度最大值{maxConf !== null ? <>（<b>{(maxConf * 100).toFixed(1)}%</b>）</> : null}，
              置信度接近复核阈值或缺陷形态复杂时系统自动转人工复核。完整叙述版报告（含逐项分析与指标解释）见
              <b>「查看完整报告」</b>，正文由 <code className="mono">GET /reports/&#123;task_id&#125;</code> 提供。
            </p>
          </Zone>

          {/* ③ 人工复核结论：待复核填单 / 已提交结论只读 */}
          <Zone
            n={3}
            title="人工复核结论"
            tag={awaitingReview ? '待填写' : submittedReview ? '已提交' : '未进入复核'}
            tagCls={awaitingReview ? 't-success' : submittedReview ? 't-success' : 't-pending'}
            source="POST /tasks/{id}/review"
          >
            {awaitingReview ? (
              <ReviewPanel bare taskId={taskId} defects={defects} onSubmitted={refresh} />
            ) : submittedReview ? (
              <div className="review-done">
                <div className="rd-row">
                  <b>复核动作</b>
                  <span>{submittedReview.action}</span>
                </div>
                <div className="rd-row">
                  <b>复核人 / 时间</b>
                  <span className="mono">{submittedReview.reviewer ?? '—'} · {fmtTs(submittedReview.reviewed_at)}</span>
                </div>
                <div className="rd-opinion">{submittedReview.opinion}</div>
                {submittedReview.defects_diff && Object.keys(submittedReview.defects_diff).length > 0 && (
                  <div className="rd-diff mono">
                    缺陷修订：{Object.entries(submittedReview.defects_diff).map(([k, v]) => `${k} ${v}`).join(' · ')}
                  </div>
                )}
                <span className="hint">原始结果只读；人工修订以新增记录留痕，不覆盖模型输出。</span>
              </div>
            ) : (
              <>
                {autoPassed && <p className="faint">规则自动通过（未经人工）：{autoPassed.opinion}</p>}
                <p className="faint">
                  本任务未停在复核门{detail.grade ? `（当前判级：${GRADE_TEXT[detail.grade.level]}）` : ''}。
                  如有异议可点右上角「送人工复核」进入复核工作台。
                </p>
              </>
            )}
            <div className="notice gray">
              {INFO_ICO}
              <span>
                复核动作聚合见<a href="/tasks/samples"> 反馈样本管理</a>；本轮<b>无取消接口</b>，运行中任务不可中断。
              </span>
            </div>
          </Zone>
        </div>

        {/* 右：执行链 / 思考链 / 父子链 / 样本清单 */}
        <div className="stack">
          <section className="card">
            <div className="card-h">
              <div className="card-title">任务事件 <span className="en">EVENTS · {steps.length}</span></div>
              <span className="hint">按节点执行链逐步留痕</span>
            </div>
            <div className="card-b ev-body">
              {steps.length > 0 ? (
                steps.map((s: StepTraceItem, idx) => (
                  <div className="ev-row" key={`${s.node}-${s.ts ?? idx}`}>
                    <span className="mono faint ev-ts">{fmtTs(s.ts).slice(11, 16) || '—'}</span>
                    <span className="grow ev-clamp" title={s.message ?? s.node}>{s.message ?? s.node}</span>
                    <span className="mono ev-node">{s.node}</span>
                  </div>
                ))
              ) : (
                <div className="list-empty">无步骤记录</div>
              )}
            </div>
          </section>

          {/* ReAct 思考链：智能体轨任务（单场景 agent / 联合 joint）有完整轨迹（决策 18 一期） */}
          {(detail.mode === 'agent' || detail.mode === 'joint') && (
            <ThoughtChainPanel taskId={taskId} running={detail.status === 'running' || detail.status === 'pending'} />
          )}

          {detail.mode === 'joint' && childList.length > 0 && (
            <section className="card">
              <div className="card-h">
                <div className="card-title">联合研判子任务 <span className="en">CHILDREN · {childList.length}</span></div>
              </div>
              <div className="card-b stack" style={{ gap: 10 }}>
                <div className="hint">
                  跨任务综合研判依赖的 <code className="mono">business_ref</code> 无后端字段，本页只呈现{' '}
                  <code className="mono">mode=joint</code> 的真实父子关系。
                </div>
                {childList.map((c) => (
                  <a key={c.task_id} className="child-card" href={`/tasks/${c.task_id}`}>
                    <div className="cc-top">
                      <span className="tag t-child">child</span>
                      <span className="grow" style={{ fontSize: 13, fontWeight: 600 }}>{sceneName(c.scene)}</span>
                      <StatusTag status={c.status as TaskStatus} />
                    </div>
                    <div className="cc-meta">
                      <span className="mono cc-id">{c.task_id}</span>
                      <span className="grow" />
                      {verdictTag((c.grade_level ?? undefined) as GradeLevel | undefined)}
                    </div>
                  </a>
                ))}
                <div className="notice">
                  {WARN_ICO}
                  <span><b>各专业结论保持独立出处</b>，不做置信度相加、不生成综合质量分。</span>
                </div>
              </div>
            </section>
          )}

          <section className="card">
            <div className="card-h">
              <div className="card-title">样本清单 <span className="en">ITEMS · {images.length}</span></div>
              <span className="hint">详情接口无分页参数，按全量渲染</span>
            </div>
            <div className="card-b stack" style={{ gap: 7 }}>
              {images.length > 0 ? (
                <>
                  {images.map((img) => {
                    // 存量/单图数据的缺陷条目不带 image_name，此时按图计数会重复计数，如实标注
                    const n = hasImageTag ? defectsForImage(defects, img.src, images[0]?.src).length : null
                    return (
                      <button
                        type="button"
                        className="file-row"
                        key={img.key}
                        onClick={() => setActiveImageKey(img.key)}
                      >
                        <span className="fname mono">{imageName(img.src)}</span>
                        <span className="fmeta">{n === null ? '未按图归属' : `${n} 处检出`}</span>
                      </button>
                    )
                  })}
                  {defects.length > 0 && !hasImageTag && (
                    <span className="hint">条目未带 image_name（存量或单图任务），第 ① 区计数为任务全量，不按图拆分</span>
                  )}
                </>
              ) : (
                <div className="list-empty">本任务无输入图像记录</div>
              )}
            </div>
          </section>
        </div>
      </div>

      <CitationModal taskId={taskId} citation={openCitation} onClose={() => setOpenCitation(null)} />
    </>
  )
}
