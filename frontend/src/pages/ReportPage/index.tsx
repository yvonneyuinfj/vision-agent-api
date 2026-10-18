/** 质检报告页（优化轮 D6）：独立纸张文档风整页，强制浅色、独立于应用主题。
 *
 * - 数据骨架取 TaskDetail 结构化字段；AI 综合分析与指标解释两节从
 *   report.md 抽取渲染（保留引用锚点）；
 * - 打印级导出（优化轮 D8）：@media print 隐藏导航/工具条，纸张分页友好，
 *   浏览器原生「存为 PDF」即得正式报告。
 */
import { useEffect, useMemo, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { fetchTaskDetail } from '../../api/tasks'
import { fetchReport, type ReportMeta } from '../../api/reports'
import type { ClassDefItem, GradeLevel, KnowledgeItem, TaskDetail } from '../../api/types'
import { SCENE_MAP } from '../../constants/scene'
import { GRADE_META } from '../../constants/task'
import Md from '../../components/markdown/Md'
import { formatBeijing } from '../../utils/time'
import { pickArtifactImagePair, isImageArtifact } from '../../utils/artifacts'
import './index.css'

const SEV_LABEL: Record<string, string> = {
  high: '高严重度',
  medium: '中严重度',
  low: '低严重度',
}

/** 从 report.md 抽取指定 `## N. 标题` 小节正文（标题按关键词匹配，编号不定）。 */
function extractSection(md: string, keyword: string): string | null {
  const lines = md.split('\n')
  let start = -1
  for (let i = 0; i < lines.length; i += 1) {
    if (/^##\s/.test(lines[i]) && lines[i].includes(keyword)) { start = i; break }
  }
  if (start < 0) return null
  let end = lines.length
  for (let i = start + 1; i < lines.length; i += 1) {
    if (/^##\s/.test(lines[i])) { end = i; break }
  }
  const body = lines.slice(start + 1, end).join('\n').trim()
  return body || null
}

function sceneName(code: string | null): string {
  return code ? (SCENE_MAP[code as keyof typeof SCENE_MAP] ?? code) : '—'
}

/** 类别定义表 → 代码索引：缺陷卡按 defect_type 转译中文类别名与真缺陷/虚假显示语义。 */
function indexClassDefs(defs: ClassDefItem[] | undefined): Map<string, ClassDefItem> {
  const map = new Map<string, ClassDefItem>()
  for (const d of defs ?? []) if (d.class_name) map.set(d.class_name, d)
  return map
}

function durationOf(detail: TaskDetail): string {
  if (!detail.created_at || !detail.finished_at) return '—'
  const sec = Math.max(0, Math.round((new Date(detail.finished_at).getTime() - new Date(detail.created_at).getTime()) / 1000))
  if (sec < 60) return `${sec} 秒`
  const min = Math.floor(sec / 60)
  return `${min} 分 ${sec % 60} 秒`
}

/** 逐图配对（多图任务核心呈现，2026-09-16 需求：每张图都要检测与对比）：
 *  标注图按来源图名（fan-out 后缀 __<stem> / mock <stem>_annotated）与
 *  输入图配对；未带来源标记的标注图按顺序兜底。无标注图的输入图跳过
 *  （不臆造配对）；单图任务退化为单对（与既有行为兼容）。 */
function pickComparePairs(detail: TaskDetail): { image_name: string; original: string | null; annotated: string }[] {
  const arts = detail.report?.artifacts ?? []
  const images = arts.filter(isImageArtifact)
  const lower = (s: string) => s.toLowerCase()
  const pairs: { image_name: string; original: string | null; annotated: string }[] = []
  // 未带来源标记的标注图（顺序兜底池）：排除原图/非图片产物
  const rest = images.filter(
    (a) => !lower(a.name ?? '').includes('original') && !lower(a.name ?? '').includes('result_json')
  )
  // 标注图候选池（stem 匹配专用）：排除原图产物，防止 __<stem> 后缀误配到原图
  const annotatedPool = images.filter((a) => !lower(a.name ?? '').includes('original'))
  // 通用原图产物池（无 stem 标记时按序消费，已被占用的不再重复配对）
  const originalRest = images.filter((a) => lower(a.name ?? '').includes('original'))
  for (const uri of detail.input_uris) {
    const imageName = uri.split('/files/')[1]?.split('?')[0] ?? uri
    const stem = imageName.replace(/\.[^.]+$/, '')
    const annotated =
      annotatedPool.find((a) => lower(a.name ?? '').includes(`__${lower(stem)}`)) ??
      annotatedPool.find((a) => lower(a.name ?? '').startsWith(`${lower(stem)}_annotated`)) ??
      rest.find((a) => lower(a.name ?? '').includes('result_image')) ??
      rest.shift()
    if (!annotated) continue
    // 原图配对：stem 后缀精确匹配优先；无标记时按序消费通用原图产物池
    // （stem 命中也从池中移除，避免同一原图产物配到两张输入图）
    const originalIdx = originalRest.findIndex((a) => lower(a.name ?? '').includes(`__${lower(stem)}`))
    const originalUri = (originalIdx >= 0 ? originalRest.splice(originalIdx, 1)[0] : originalRest.shift())?.uri ?? null
    pairs.push({ image_name: imageName, original: originalUri ?? uri, annotated: annotated.uri })
  }
  // 输入图配不上任何标注图时的兜底：至少渲染产物中的第一对（存量单图行为）
  if (pairs.length === 0) {
    const { original, annotated } = pickArtifactImagePair(arts)
    if (annotated) {
      pairs.push({ image_name: '检测图', original: original?.uri ?? detail.input_uris[0] ?? null, annotated: annotated.uri })
    }
  }
  return pairs
}

export default function ReportPage() {
  const { taskId = '' } = useParams()
  const [detail, setDetail] = useState<TaskDetail | null>(null)
  const [loadError, setLoadError] = useState('')
  const [reportMd, setReportMd] = useState('')
  const [reportMeta, setReportMeta] = useState<ReportMeta | null>(null)

  useEffect(() => {
    let alive = true
    setLoadError('')
    fetchTaskDetail(taskId)
      .then((d) => { if (alive) setDetail(d) })
      .catch(() => { if (alive) setLoadError('任务加载失败') })
    return () => { alive = false }
  }, [taskId])

  // 报告正文走 `GET /reports/{task_id}`（唯一权威源）；未生成时后端 404，静默降级为结构化骨架
  useEffect(() => {
    if (!taskId) return
    let alive = true
    fetchReport(taskId, { silent: true })
      .then((doc) => {
        if (!alive) return
        setReportMd(doc.content)
        setReportMeta(doc.meta)
      })
      .catch(() => { if (alive) { setReportMd(''); setReportMeta(null) } })
    return () => { alive = false }
  }, [taskId])

  const citationsMap = useMemo(() => {
    const map: Record<string, KnowledgeItem> = {}
    for (const k of detail?.knowledge ?? []) if (k.chunk_id) map[k.chunk_id] = k
    return map
  }, [detail?.knowledge])

  const analysisMd = useMemo(() => extractSection(reportMd, 'AI 综合分析'), [reportMd])
  const metricsMd = useMemo(() => extractSection(reportMd, '指标解释'), [reportMd])

  if (loadError) {
    return (
      <div className="rp-toolbar"><Link className="btn btn-sm" to={`/tasks/${taskId}`}>← 返回详情</Link>
        <span className="rp-err">{loadError}</span></div>
    )
  }
  if (!detail) {
    return <div className="rp-loading">加载报告中…</div>
  }

  const grade = detail.grade
  const gradeMeta = grade ? GRADE_META[grade.level as GradeLevel] : null
  const defects = detail.standardized ?? []
  const classDefs = detail.class_definitions ?? []
  const classDefMap = indexClassDefs(classDefs)
  const comparePairs = pickComparePairs(detail)
  // 口径对照（react 轨裁决溯源）：模型原始检出数（grade.model_defect_count）
  // 与终裁列表数不一致时，图注讲清差异来源——标注图是模型推理时刻的
  // 原始快照（不随后续智能体裁决/人工修订重绘），列表是全链路终裁结论
  const modelCount = (grade?.model_defect_count as number | undefined) ?? null
  const agentVisualCount = defects.filter((d) => d.source === 'agent_visual').length
  const manualCount = defects.filter((d) => d.source === 'manual').length
  const annotatedCaption =
    modelCount != null && modelCount !== defects.length
      ? `模型标注 ${modelCount} 处 · 终裁结论 ${defects.length} 处（${
          [
            agentVisualCount ? `智能体视觉补充 ${agentVisualCount} 处` : '',
            manualCount ? `人工新增 ${manualCount} 处` : '',
          ]
            .filter(Boolean)
            .join('、') || '差异见列表条目标记'
        }）`
      : `模型标注${defects.length ? ` · 检出 ${defects.length} 处` : ''}`

  return (
    <div className="rp-shell">
      {/* 工具条（打印时隐藏） */}
      <div className="rp-toolbar">
        <Link className="btn btn-sm" to={`/tasks/${taskId}`}>← 返回任务详情</Link>
        <span className="rp-toolbar-id mono">{taskId}</span>
        {!reportMeta && (
          <span className="hint">报告正文未生成（<code className="mono">GET /reports/&#123;task_id&#125;</code> 无记录）：以下为任务结构化骨架</span>
        )}
        <span className="grow" />
        {detail.report && (
          <a className="btn btn-sm" href={detail.report.uri} download>下载 .md</a>
        )}
        <button className="btn btn-sm btn-primary" type="button" onClick={() => window.print()}>
          打印 / 存为 PDF
        </button>
      </div>

      {/* 纸张主体 */}
      <article className="report-paper">
        <header className="paper-head">
          <div className="paper-kicker">AeroQC · 无损检测智能质检</div>
          <h1 className="paper-title">智能质检报告</h1>
          <div className="paper-meta">
            <span>任务编号 <b className="mono">{detail.task_id}</b></span>
            <span>检测场景 <b>{sceneName(detail.scene)}</b></span>
            <span>执行模式 <b>{detail.mode === 'agent' ? 'ReAct 智能体' : detail.mode === 'joint' ? '联合质检' : '固定流水线'}</b></span>
            <span>视觉能力 <b>{detail.capability_used ?? '—'}</b></span>
            <span>提交时间 <b>{formatBeijing(detail.created_at)}</b></span>
            <span>用时 <b>{durationOf(detail)}</b></span>
            <span>报告生成 <b>{reportMeta?.generated_at ? formatBeijing(reportMeta.generated_at) : '—'}</b></span>
          </div>
          {grade && gradeMeta && (
            <div className="paper-stamp" data-level={grade.level}>
              <span className="stamp-text">{gradeMeta.label}</span>
              <span className="stamp-sub">综合判级 · 自动判级引擎</span>
            </div>
          )}
        </header>

        {/* 一 · 检测概述 */}
        <section className="paper-sec">
          <h2 className="paper-h">一 · 检测概述 <span className="en">OVERVIEW</span></h2>
          <p className="paper-p">
            本任务对「{sceneName(detail.scene)}」场景执行自动质检，共 {detail.input_uris.length} 幅图像参与推理，
            判级节点融合视觉检出与知识库依据给出最终结论。
          </p>
          {/* 逐图对比（多图任务核心呈现）：每张输入图一个「原始图像 vs
              检测结果图」对比块，标注图按来源图名配对；无标注图产物时
              回退输入图缩略（不臆造文件名） */}
          {comparePairs.length > 0 ? (
            <div className="paper-compare-list">
              {comparePairs.map((pair, i) => (
                <div className="paper-compare" key={pair.annotated}>
                  {comparePairs.length > 1 && (
                    <div className="pc-title">
                      图 {i + 1}/{comparePairs.length} <span className="mono">{pair.image_name}</span>
                    </div>
                  )}
                  {pair.original && (
                    <figure className="pc-fig">
                      <a href={pair.original} target="_blank" rel="noreferrer">
                        <img src={pair.original} alt="原始图像" />
                      </a>
                      <figcaption>原始图像（检测输入）<span className="pc-en mono">ORIGINAL</span></figcaption>
                    </figure>
                  )}
                  <figure className="pc-fig">
                    <a href={pair.annotated} target="_blank" rel="noreferrer">
                      <img src={pair.annotated} alt="检测结果图" />
                    </a>
                    <figcaption>
                      检测结果图（{comparePairs.length > 1 ? '本图标注' : annotatedCaption}）
                      <span className="pc-en mono">ANNOTATED</span>
                    </figcaption>
                  </figure>
                </div>
              ))}
            </div>
          ) : detail.input_uris.length > 0 ? (
            <div className="paper-imgs">
              {detail.input_uris.map((uri) => (
                <a key={uri} className="paper-img" href={uri} target="_blank" rel="noreferrer">
                  <img src={uri} alt="检测图像" />
                </a>
              ))}
            </div>
          ) : null}
        </section>

        {/* 二 · 检测结果 */}
        <section className="paper-sec">
          <h2 className="paper-h">二 · 检测结果 <span className="en">FINDINGS</span></h2>
          {defects.length === 0 ? (
            <p className="paper-p paper-nofind">未检出缺陷条目。</p>
          ) : (
            <div className="paper-defects">
              {defects.map((d, idx) => {
                const def = classDefMap.get(d.defect_type)
                const nameCn = d.defect_name_cn || def?.description || d.defect_type
                return (
                  <div className="pd-card" key={d.defect_id ?? idx}>
                    <div className="pd-top">
                      <span className="pd-idx mono">D{idx + 1}</span>
                      <b className="pd-name">{nameCn}</b>
                      <span className="pd-type mono">{d.defect_type}</span>
                      {/* 真缺陷/虚假显示语义取自工具类别定义表；无定义表时不下结论 */}
                      {def && (
                        <span className={def.is_true_defect ? 'pd-nature true' : 'pd-nature false'}>
                          {def.is_true_defect ? '真缺陷' : '虚假显示'}
                        </span>
                      )}
                      {d.severity && <span className={`pd-sev sev-${d.severity}`}>{SEV_LABEL[d.severity] ?? d.severity}</span>}
                      {d.confidence !== null && d.confidence !== undefined && (
                        <span className="pd-conf mono">置信 {(d.confidence * 100).toFixed(1)}%</span>
                      )}
                      {/* 裁决/修订溯源徽标：智能体视觉判断（无模型标注框）、
                          人工复核新增——报告口径对照的差异条目可辨识 */}
                      {d.source === 'agent_visual' && (
                        <span className="pd-src agent">智能体视觉判断</span>
                      )}
                      {d.source === 'manual' && <span className="pd-src manual">人工复核新增</span>}
                      {!d.location && (
                        <span className="pd-noloc">无标注框</span>
                      )}
                      {/* 多图 fan-out 溯源：条目来源图（与逐图对比块编号对应） */}
                      {d.image_name && <span className="pd-img mono">{d.image_name}</span>}
                    </div>
                    {d.description && <p className="pd-desc">{d.description}</p>}
                    {/* 无定位条目附注：说明为何标注图上没有对应框 */}
                    {!d.location && (
                      <p className="pd-desc pd-desc-note">
                        该条目无定位信息，检测结果图上无对应标注框。
                      </p>
                    )}
                  </div>
                )
              })}
            </div>
          )}
          {/* 类别定义表（工具 class_definitions）：代码 → 中文名 + 真缺陷/虚假显示，
              正文不再罗列 bbox 坐标，位置信息看「一 · 检测概述」的缺陷标注图 */}
          {classDefs.length > 0 && (
            <div className="paper-classdef">
              <div className="pcd-h">检测类别定义 <span className="en">CLASS DEFINITIONS</span></div>
              <table className="pcd-table">
                <thead>
                  <tr>
                    <th>代码</th>
                    <th>类别说明</th>
                    <th>性质</th>
                    <th>检出</th>
                  </tr>
                </thead>
                <tbody>
                  {classDefs.map((c) => (
                    <tr key={`${c.class_id}-${c.class_name}`}>
                      <td className="mono">{c.class_name || '—'}</td>
                      <td>{c.description || '—'}</td>
                      <td>
                        <span className={c.is_true_defect ? 'pcd-tag true' : 'pcd-tag false'}>
                          {c.is_true_defect ? '真缺陷' : '虚假显示 · 不计入'}
                        </span>
                      </td>
                      <td className="mono">{defects.filter((d) => d.defect_type === c.class_name).length} 处</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>

        {/* 三 · 知识库判级依据 */}
        <section className="paper-sec">
          <h2 className="paper-h">三 · 知识库判级依据 <span className="en">RAG GROUNDS</span></h2>
          {detail.knowledge.length === 0 ? (
            <p className="paper-p paper-nofind">本任务未命中知识库引用条目。</p>
          ) : (
            <ol className="paper-cites">
              {detail.knowledge.map((k, idx) => (
                <li key={k.chunk_id ?? idx}>
                  <b>引用 {idx + 1}</b> ← 《{k.doc_name}》
                  <span className="mono cite-score">（相似度 {k.score.toFixed(2)}）</span>
                  <div className="cite-body">{k.content}</div>
                </li>
              ))}
            </ol>
          )}
        </section>

        {/* 四 · 人工复核记录 */}
        {detail.review && (
          <section className="paper-sec">
            <h2 className="paper-h">四 · 人工复核记录 <span className="en">REVIEW</span></h2>
            <div className="paper-review">
              <div className="rv-row"><span className="rv-k">复核动作</span><b>{detail.review.action}</b></div>
              {detail.review.reviewer && <div className="rv-row"><span className="rv-k">复核人</span><b>{detail.review.reviewer}</b></div>}
              {detail.review.reviewed_at && <div className="rv-row"><span className="rv-k">复核时间</span><b>{formatBeijing(detail.review.reviewed_at)}</b></div>}
              {detail.review.opinion && <div className="rv-row"><span className="rv-k">复核意见</span><span>{detail.review.opinion}</span></div>}
            </div>
          </section>
        )}

        {/* 五 · 指标解释（如有） */}
        {metricsMd && (
          <section className="paper-sec">
            <h2 className="paper-h">指标解释 <span className="en">METRICS</span></h2>
            <div className="paper-md"><Md citations={citationsMap}>{metricsMd}</Md></div>
          </section>
        )}

        {/* 六 · AI 综合分析 */}
        <section className="paper-sec">
          <h2 className="paper-h">AI 综合分析 <span className="en">MODEL ANALYSIS</span></h2>
          {analysisMd ? (
            <div className="paper-md"><Md citations={citationsMap}>{analysisMd}</Md></div>
          ) : grade ? (
            <div className="paper-md"><Md citations={citationsMap}>{grade.summary}</Md></div>
          ) : (
            <p className="paper-p paper-nofind">暂无综合分析内容。</p>
          )}
        </section>

        <footer className="paper-foot">
          <div className="foot-line" />
          {/* 与结果页第 1 区同源声明：工具原始响应未落库（G1-02） */}
          <p className="paper-p paper-nofind">
            原始输出说明：本报告引用适配器归一后的结构化结果；工具原始响应尚未落库，
            暂不可作为原始证据随报告导出（后端 G1-02 项）。
          </p>
          <div className="foot-row">
            <span>本报告由智能质检 Agent 自动生成 · 判级依据 RAGFlow 知识库</span>
            <span>
              生成时间 {reportMeta?.generated_at ? formatBeijing(reportMeta.generated_at) : formatBeijing(detail.updated_at)}
            </span>
          </div>
        </footer>
      </article>
    </div>
  )
}
