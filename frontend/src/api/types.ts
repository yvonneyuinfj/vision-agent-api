// 契约唯一权威，逐字段对齐 app/api/schemas/task.py
export type TaskStatus = 'pending' | 'running' | 'awaiting_review' | 'completed' | 'failed' | 'canceled'
export type SceneCode = 'borescope' | 'infrared' | 'dr' | 'fpi' | 'fracture' | 'metallography'
export type ReviewAction = 'confirm' | 'revise' | 'requeue' | 'reject'
export type GradeLevel = 'pass' | 'review' | 'fail' | 'defect'

export interface StandardEnvelope<T> {
  code: number
  message: string
  task_id: string | null
  trace_id: string | null
  tool: { tool_name: string; tool_version: string; api_version: string }
  result: T | null
}

export interface PageMeta { total: number; page: number; page_size: number }
export interface PageResult<T> { items: T[]; meta: PageMeta }

/** 任务统计（GET /tasks/stats）：后端全量权威计数，非列表快照 */
export interface TaskStats {
  total: number
  today: number // 北京时间今日创建数
  pending: number
  running: number
  awaiting_review: number
  completed: number
  failed: number
  canceled: number
}

export interface TaskSummary {
  task_id: string
  scene: string | null
  mode: 'single' | 'joint' | 'agent'
  status: TaskStatus
  capability_used: string | null
  created_at: string
  updated_at: string
  finished_at: string | null
  /** 联合子任务回指父任务（优化轮 E2：列表「子」标签） */
  parent_task_id?: string | null
}

/** 联合父任务的子任务摘要（优化轮 E2） */
export interface TaskChildSummary {
  task_id: string
  scene: string | null
  mode: string
  status: string
  grade_level: string | null
  created_at: string | null
}

export interface StepTraceItem { node: string; message: string; ts: string }

export interface DefectItem {
  defect_id: string | null
  defect_type: string
  defect_name_cn: string
  confidence: number | null
  severity: 'low' | 'medium' | 'high' | null
  /** 平台契约 {type:'bbox', bbox:[x1,y1,x2,y2]}；bbox_xyxy 为旧 FPI 数据的兼容键 */
  location: {
    type?: string
    bbox?: [number, number, number, number]
    bbox_xyxy?: [number, number, number, number]
  } | null
  description?: string
  /** 修订溯源标记（revise 全量修订列表契约 + react 轨裁决溯源）：
   *  source=manual 人工补检漏检（defect_id 形如 M001）；
   *  source=agent_visual 智能体视觉判断手写条目（模型标注图上无对应框）；
   *  human_rejected=true 人工排除误检（条目保留供审计，不入缺陷表）；
   *  revised 为改类/改严重度/移框前的原值快照 */
  source?: 'model' | 'manual' | 'agent_visual'
  human_rejected?: boolean
  revised?: Record<string, unknown>
  /** 多图 fan-out 溯源：条目来源图名（单图工具单次调用时无此字段） */
  image_name?: string
}

export interface GradeInfo { level: GradeLevel; summary: string; [key: string]: unknown }

/** 工具类别定义（class_definitions 透传）：类别代码 → 中文名 + 真缺陷/虚假显示语义。
 *  报告「二 · 检测结果」类别图例与缺陷卡语义转译数据源。 */
export interface ClassDefItem {
  class_id: number | string | null
  class_name: string
  description: string
  is_true_defect: boolean
}

export interface KnowledgeItem {
  content: string
  score: number
  doc_name: string
  chunk_id: string | null
  image_uri: string | null
}

export interface ReviewDecision {
  action: ReviewAction
  opinion: string
  reviewer?: string
  reviewed_at?: string | null
  grading_result?: string
  /** awaiting_review 期间 review_json = 中断载荷：携带工具渲染标注图等产物引用
   *  （复核期三方对照证据，无需等终局 report）与缺陷修订 diff 摘要 */
  artifacts?: ArtifactRef[]
  defects_diff?: Record<string, number>
  question?: string
  decision_options?: string[]
}

/** 推理产物引用（本地化 URI，可直接作 img src / 下载；对齐 app/api/schemas/task.py ArtifactRef） */
export interface ArtifactRef {
  /** 形如 artifact_original_image / artifact_result_image */
  name: string
  mime: string | null
  uri: string
}

export interface ReportRef {
  report_type: string
  name: string
  uri: string
  version: string
  /** AI 分析首段摘录（优化轮 D7：会话报告摘要卡免拉全文即可展示） */
  analysis_lead?: string
  /** 推理产物（原图/缺陷标注图等）：报告页「原图 vs 标注图」对比数据源 */
  artifacts?: ArtifactRef[]
}

export interface TaskDetail extends TaskSummary {
  trace_id: string | null
  /** 原图业务 URI（本身即下载端点，前端直接用作 img src，不得臆造文件名） */
  input_uris: string[]
  steps: StepTraceItem[]
  standardized: DefectItem[]
  /** 类别定义表（旧任务/无定义表工具为空数组） */
  class_definitions?: ClassDefItem[]
  grade: GradeInfo | null
  knowledge: KnowledgeItem[]
  review: ReviewDecision | null
  report: ReportRef | null
  error: { node: string; code: number; message: string } | null
  /** 联合父任务的子任务摘要（优化轮 E2 父子可视化） */
  children?: TaskChildSummary[]
}

export interface ReviewRequest {
  action: ReviewAction
  opinion: string
  override_grade: Partial<GradeInfo> | null
  /** revise 时提交的全量缺陷修订列表（条目带 source/human_rejected/revised 溯源标记）；
   *  后端整表替换 standardized 并自动生成 diff 摘要留痕。其他动作忽略。 */
  defects?: DefectItem[] | null
}

// ---------- ReAct 思考链（GET /tasks/{id}/trace，仅 mode=agent 有内容） ----------
export interface TraceEntryBriefing { type: 'briefing'; text: string }
export interface TraceEntryThought { type: 'thought'; turn: number; text: string }
export interface TraceEntryToolCall { type: 'tool_call'; turn: number; name: string; args: Record<string, string> }
export interface TraceEntryToolResult { type: 'tool_result'; turn: number; name: string | null; content: string }
export type TraceEntry = TraceEntryBriefing | TraceEntryThought | TraceEntryToolCall | TraceEntryToolResult

export interface TaskTrace {
  task_id: string
  mode: string
  status: string
  loop_count: number | null
  capability_calls: number | null
  submitted: boolean | null
  entries: TraceEntry[]
}

// ---------- 质检助手会话（会话轨） ----------
export type SessionRole = 'user' | 'assistant' | 'event'
export type SessionKind = 'text' | 'image' | 'task_card' | 'guidance' | 'event' | 'activity' | 'chain' | 'routing' | 'scene_card' | 'review_card'

export interface SessionImageRef { uri: string; name: string; media_type?: string }

/** 场景确认卡片候选（优化轮 D4）：candidates 模式带置信度，fingerprint 模式 confidence 为 null */
export interface SceneCardCandidate { scene: string; confidence: number | null }

export interface SceneCardPayload {
  mode: 'candidates' | 'fingerprint'
  candidates: SceneCardCandidate[]
  reasoning: string
  image: SessionImageRef | null
}

export interface SessionTaskSnapshot {
  task_id: string
  status: string
  scene: string | null
  mode: string
  grade_level: string | null
  grade_summary: string | null
  created_at: string | null
  /** 报告摘要卡（优化轮 D7） */
  defect_count?: number
  report_uri?: string | null
  analysis_lead?: string | null
}

export interface SessionMessage {
  message_id: string
  session_id: string
  role: SessionRole
  kind: SessionKind
  content: string
  images: SessionImageRef[]
  task_id: string | null
  task: SessionTaskSnapshot | null
  created_at: string | null
}

export interface SessionSummary {
  session_id: string
  title: string | null
  status: string
  created_at: string | null
  updated_at: string | null
  last_message: string | null
}

export interface SessionDetail extends SessionSummary {
  messages: SessionMessage[]
}
