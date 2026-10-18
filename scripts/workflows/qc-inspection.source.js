async function run(wf, args) {
  const a = args || {}
  const TOOL = 'http://127.0.0.1:8002'
  const ALL_SCENES = ['borescope', 'infrared', 'dr', 'fpi', 'fracture', 'metallography']
  const SCENE_ZH = { borescope: '孔探检测', infrared: '红外热成像', dr: 'X射线DR检测', fpi: '荧光渗透检测', fracture: '断口分析', metallography: '金相分析' }
  const DEFAULTS = {
    requirement: '对涡轮叶片孔探图像进行缺陷检测，结果经规则判级与人工复核后生成综合检测报告',
    images: ['docs/image/class1_1.jpg'],
    mode: 'single',
    mockStatus: 'abnormal',
    mockSeverity: 'random',
    autoReview: false
  }
  const CONFIG_PATH = 'scripts/workflows/run-config.json'
  const TASKS_DIR = 'data/workflow-tasks'
  const REPORTS_DIR = 'data/workflow-reports'

  // ── Phase 1 intake：运行参数三级合并（显式 args > run-config.json > 内置默认）+ 需求→场景解析 ──
  const intentRes = await wf.phase('intake', async () => await wf.runAgent({
    name: 'intake-analyst',
    modelHint: 'fast',
    terseResult: true,
    prompt: '你是工业图像智能检测平台的意图解析智能体。相对路径均相对你的工作目录（项目根）。两步任务：\n' +
      '第一步 合并运行参数：先执行 cat ' + CONFIG_PATH + '（文件不存在或不是合法 JSON 时视为空配置 {}）。然后按优先级合并三份配置——显式 args（最高）> run-config.json > 内置默认（最低），逐字段合并（显式 args 中缺失或为空的字段才用下一优先级补齐）。显式 args：' + JSON.stringify(a) + ' 。内置默认：' + JSON.stringify(DEFAULTS) + ' 。合并结果字段：requirement(字符串)、images(字符串数组)、scenes(字符串数组，可为空)、mode(single|batch|joint)、mockStatus(normal|abnormal)、mockSeverity(random|low|medium|high；mock 联调参数，非 random 时强制全部缺陷严重度为该值，用于可复现地触发复核门；随机演示保持 random)、autoReview(布尔)。\n' +
      '第二步 场景解析：场景枚举与含义：borescope=孔探检测(叶片内腔缺陷)、infrared=红外热成像(热端部件温度异常)、dr=X射线DR(内部结构与关键点偏移测量)、fpi=荧光渗透(表面裂纹类缺陷)、fracture=断口分析(断口形貌与疲劳)、metallography=金相分析(显微组织定量)。若合并结果已含非空 scenes 则直接采用；否则根据合并后的 requirement 文本推断 scenes(按建议调用顺序)。missing 输出需求中缺少的关键信息(可为空数组)。\n' +
      '输出合并与解析后的完整配置。',
    outputSchema: {
      type: 'object',
      properties: {
        requirement: { type: 'string' },
        images: { type: 'array', items: { type: 'string' } },
        scenes: { type: 'array', items: { type: 'string', enum: ALL_SCENES } },
        mode: { type: 'string', enum: ['single', 'batch', 'joint'] },
        mockStatus: { type: 'string', enum: ['normal', 'abnormal'] },
        mockSeverity: { type: 'string', enum: ['random', 'low', 'medium', 'high'] },
        autoReview: { type: 'boolean' },
        missing: { type: 'array', items: { type: 'string' } }
      },
      required: ['requirement', 'images', 'scenes', 'mode'],
      additionalProperties: false
    }
  }))
  const parsed = (intentRes && intentRes.structured) ? intentRes.structured : null
  const cfg = parsed
    ? {
        requirement: parsed.requirement || DEFAULTS.requirement,
        images: (parsed.images && parsed.images.length > 0) ? parsed.images : DEFAULTS.images,
        scenes: (parsed.scenes && parsed.scenes.length > 0) ? parsed.scenes : null,
        mode: parsed.mode || DEFAULTS.mode,
        mockStatus: parsed.mockStatus || DEFAULTS.mockStatus,
        mockSeverity: parsed.mockSeverity || DEFAULTS.mockSeverity,
        autoReview: parsed.autoReview === true
      }
    : {
        requirement: a.requirement || DEFAULTS.requirement,
        images: (a.images && a.images.length > 0) ? a.images : DEFAULTS.images,
        scenes: (a.scenes && a.scenes.length > 0) ? a.scenes : null,
        mode: a.mode || DEFAULTS.mode,
        mockStatus: a.mockStatus || DEFAULTS.mockStatus,
        mockSeverity: a.mockSeverity || DEFAULTS.mockSeverity,
        autoReview: a.autoReview === true
      }
  const mode = cfg.mode
  const images = cfg.images
  const mockStatus = cfg.mockStatus
  const mockSeverity = cfg.mockSeverity
  const autoReview = cfg.autoReview
  const scenes = cfg.scenes || ['borescope']
  wf.log({ message: 'intake 完成', data: { mode: mode, images: images, scenes: scenes, autoReview: autoReview, degraded: parsed === null } })
  if (images.length === 0) {
    wf.log('未提供输入图像，直接终止')
    return { status: 'blocked', reason: 'images 为空' }
  }

  // ── Phase 2 dispatch：工具服务健康巡检 + 缺陷域覆盖判定（运行管理层语义） ──
  const dispatchRes = await wf.phase('dispatch', async () => await wf.runAgent({
    name: 'tool-dispatch',
    modelHint: 'fast',
    terseResult: true,
    prompt: '你是工具能力调度智能体。用 bash curl 完成巡检（不要修改任何文件）：\n' +
      '1) curl -s ' + TOOL + '/health ；服务不可达或 code 非 0 时：available=[]，notes 说明原因。\n' +
      '2) 校验 result.status=="healthy"，记录 tool_name、tool_version、model_version、defect_domains。\n' +
      '3) 对目标场景逐个判断：场景编码 ∈ defect_domains 才可用。目标场景：' + scenes.join(',') + '\n' +
      '输出 available(可执行场景数组)、unavailable(不可执行场景数组)、tool_name、tool_version、model_version、notes(一句话说明)。',
    outputSchema: {
      type: 'object',
      properties: {
        available: { type: 'array', items: { type: 'string' } },
        unavailable: { type: 'array', items: { type: 'string' } },
        tool_name: { type: 'string' },
        tool_version: { type: 'string' },
        model_version: { type: 'string' },
        notes: { type: 'string' }
      },
      required: ['available', 'unavailable'],
      additionalProperties: false
    }
  }))
  const dispatch = (dispatchRes && dispatchRes.structured) ? dispatchRes.structured : { available: scenes, unavailable: [], tool_name: 'unknown', tool_version: 'unknown', model_version: 'unknown', notes: '调度智能体失败，降级为目标场景全量执行' }
  const runnable = dispatch.available
  if (runnable.length === 0) {
    const blocked = await wf.artifact('qc-evidence', { stage: 'dispatch-blocked', requirement: cfg.requirement, intent: parsed, dispatch: dispatch })
    return { status: 'blocked', reason: '工具服务不可用或场景未覆盖', dispatch: dispatch, artifact: blocked.path }
  }

  // ── Phase 3 inspect：直调统一 API 推理（同步秒级返回）+ 任务留痕落盘 ──
  const inspectSchema = {
    type: 'object',
    properties: {
      task_id: { type: 'string' },
      scene: { type: 'string' },
      image: { type: 'string' },
      code: { type: 'integer' },
      status: { type: 'string', enum: ['normal', 'abnormal', 'error'] },
      summary: { type: 'string' },
      defects: {
        type: 'array',
        items: {
          type: 'object',
          properties: {
            defect_id: { type: 'string' },
            defect_type: { type: 'string' },
            defect_name_cn: { type: 'string' },
            confidence: { type: 'number' },
            severity: { type: 'string', enum: ['low', 'medium', 'high'] },
            bbox: { oneOf: [{ type: 'array', items: { type: 'number' } }, { type: 'null' }] }
          },
          required: ['defect_type', 'confidence', 'severity'],
          additionalProperties: false
        }
      },
      tool_version: { type: 'string' },
      model_version: { type: 'string' },
      api_version: { type: 'string' },
      trace_id: { type: 'string' },
      processing_time_ms: { type: 'number' },
      artifacts: {
        type: 'array',
        items: {
          type: 'object',
          properties: { name: { type: 'string' }, uri: { type: 'string' } },
          required: ['name'],
          additionalProperties: false
        }
      },
      error: { type: 'string' },
      ledger_path: { type: 'string' }
    },
    required: ['task_id', 'scene', 'image', 'code', 'status'],
    additionalProperties: false
  }
  const inspectPrompt = function (scene, image, taskId, traceId) {
    return '你是检测执行智能体（集成层：按动力所统一API接入规范直调专业工具）。相对路径均相对你的工作目录（项目根）。步骤：\n' +
      '1) mkdir -p ' + TASKS_DIR + '\n' +
      '2) 同步推理（秒级返回，无需轮询）：curl -s -X POST ' + TOOL + '/api/v1/inference -F "image=@' + image + '" -F "task_id=' + taskId + '" -F "scene=' + scene + '" -F "trace_id=' + traceId + '" -F \'parameters={"confidence_threshold":0.5,"max_defects":5,"return_annotated":true,"mock_status":"' + mockStatus + '","mock_severity":' + (mockSeverity === 'random' ? 'null' : '"' + mockSeverity + '"') + '}\' 。若图像文件不存在，先 ls docs/image/ 定位实际文件名再重试一次。\n' +
      '3) 任务留痕：将完整响应 JSON 写入 ' + TASKS_DIR + '/' + taskId + '.json，并在 JSON 顶层追加 _meta 字段 {"scene":"' + scene + '","image":"' + image + '","run_id":"' + wf.runId + '","saved_at":"<date -u +%FT%TZ 的输出>"}（可用 python3 处理；不得改写响应原有字段）。\n' +
      '4) 输出结构化结果：task_id=' + taskId + '、scene=' + scene + '、image=' + image + '、code(外壳 code)、status(result.result.status 的 normal|abnormal；code 非 0 或请求失败时为 error)、summary(result.result.summary；error 时填错误消息)、defects(result.result.defects 数组，每项含 defect_id/defect_type/defect_name_cn/confidence/severity/bbox；bbox 为 [cx,cy,w,h] 归一化数字数组或 null，照实转写)、tool_version/model_version/api_version(result.tool 三元组)、trace_id(外壳 trace_id)、processing_time_ms(result.performance.processing_time_ms)、artifacts(result.artifacts 数组，每项 name/uri)、error(code 非 0 时填)、ledger_path(留痕文件相对路径)。\n' +
      '不要做判级、不要做复核——由后续阶段负责。'
  }
  const callPlan = []
  if (mode === 'batch') {
    for (let i = 0; i < images.length; i++) {
      callPlan.push({ scene: runnable[i % runnable.length], image: images[i], n: i + 1 })
    }
  } else if (mode === 'joint') {
    for (let i = 0; i < runnable.length; i++) {
      callPlan.push({ scene: runnable[i], image: images[0], n: i + 1 })
    }
  } else {
    callPlan.push({ scene: runnable[0], image: images[0], n: 1 })
  }
  const inspectRaw = await wf.phase('inspect', async () => await wf.parallel(callPlan.map((c) => async () => {
    const taskId = 'QC-' + wf.runId + '-' + c.n
    const traceId = 'trace-' + wf.runId + '-' + c.n
    return await wf.runAgent({ name: 'inspect-' + c.n, modelHint: 'balanced', prompt: inspectPrompt(c.scene, c.image, taskId, traceId), outputSchema: inspectSchema })
  }), { concurrency: 3 }))
  const tasks = []
  for (const r of inspectRaw) {
    if (r && r.structured && r.structured.task_id) { tasks.push(r.structured) }
  }
  wf.log({ message: '推理调用完成', data: { ok: tasks.length, expected: callPlan.length } })
  if (tasks.length === 0) {
    const failedEv = await wf.artifact('qc-evidence', { stage: 'inspect-failed', requirement: cfg.requirement, dispatch: dispatch })
    return { status: 'failed', reason: '无成功完成的推理调用', artifact: failedEv.path }
  }

  // ── Phase 4 grade：源码内确定性标准化 + 规则判级（移植 config/rules/grade_rules.yaml，不经 LLM） ──
  const SEVERITY_NUM = { high: 3, medium: 2, low: 1 }
  const gradeOne = function (r) {
    if (r.code !== 0 || r.status === 'error') {
      return { level: 'fail', summary: '工具调用失败：' + (r.error || 'code=' + r.code), triggered_rules: ['code != 0'], max_severity: null, min_confidence: null, family: 'defect' }
    }
    const defects = r.defects || []
    if (defects.length === 0) {
      return { level: 'pass', summary: '未检出缺陷（status=' + r.status + '）', triggered_rules: [], max_severity: 0, min_confidence: null, family: 'defect' }
    }
    let maxSev = 0
    let minConf = 1
    let maxConf = 0
    for (const d of defects) {
      const s = SEVERITY_NUM[d.severity] || 0
      if (s > maxSev) { maxSev = s }
      const c = (typeof d.confidence === 'number') ? d.confidence : 1
      if (c < minConf) { minConf = c }
      if (c > maxConf) { maxConf = c }
    }
    const rules = []
    if (maxSev >= 3) { rules.push('max_severity >= 3') }
    // 需求：仅当检测结果低于设定阈值才转人工。用 maxConf（最高置信度）判「结果是否可信」，
    // 不用 minConf——模型几乎总伴随弱检出，单个低分噪声框会把每张有检出的图都拖进人工复核。
    if (maxConf < 0.5) { rules.push('max_confidence < 0.5') }
    const sevName = maxSev >= 3 ? 'high' : (maxSev === 2 ? 'medium' : 'low')
    if (rules.length > 0) {
      return { level: 'review', summary: '规则命中需要人工复核：' + rules.join(' & '), triggered_rules: rules, max_severity: sevName, min_confidence: minConf, max_confidence: maxConf, family: 'defect' }
    }
    const lv = r.status === 'abnormal' ? 'defect' : 'pass'
    return { level: lv, summary: lv === 'defect' ? ('存在缺陷（最高严重度 ' + sevName + '），规则可采纳') : '未检出缺陷', triggered_rules: [], max_severity: sevName, min_confidence: minConf, max_confidence: maxConf, family: 'defect' }
  }
  const graded = await wf.phase('grade', async () => {
    const out = []
    for (const r of tasks) {
      const g = gradeOne(r)
      out.push({
        task_id: r.task_id, scene: r.scene, image: r.image, code: r.code, status: r.status,
        summary: r.summary || '', defects: r.defects || [], grade: g,
        tool_version: r.tool_version || dispatch.tool_version, model_version: r.model_version || dispatch.model_version,
        api_version: r.api_version || 'v1', trace_id: r.trace_id || '', processing_time_ms: r.processing_time_ms || null,
        artifacts: r.artifacts || [], ledger_path: r.ledger_path || (TASKS_DIR + '/' + r.task_id + '.json'), error: r.error || null
      })
    }
    const counts = { pass: 0, defect: 0, review: 0, fail: 0 }
    for (const t of out) { counts[t.grade.level] = (counts[t.grade.level] || 0) + 1 }
    wf.log({ message: '规则判级完成（确定性，移植 grade_rules.yaml）', data: counts })
    return out
  })

  // ── Phase 5 review：HITL 复核门（level=review 触发；autoReview=true 无人值守自动放行） ──
  const reviewSchema = {
    type: 'object',
    properties: {
      task_id: { type: 'string' },
      action: { type: 'string', enum: ['confirm', 'revise', 'requeue', 'reject'] },
      final_level: { type: 'string', enum: ['defect', 'pass', 'requeue_pending'] },
      opinion: { type: 'string' },
      need_human: { type: 'boolean' },
      note: { type: 'string' }
    },
    required: ['task_id'],
    additionalProperties: false
  }
  const reviewPrompt = function (t) {
    const defectDigest = (t.defects || []).map((d) => (d.defect_name_cn || d.defect_type) + '(严重度' + d.severity + '/置信度' + d.confidence + ')').join('、')
    const head = '你是人工复核门智能体。检测任务 ' + t.task_id + '（场景 ' + (SCENE_ZH[t.scene] || t.scene) + '，图像 ' + t.image + '）规则判级为 review，需要人工复核。判级依据：' + t.grade.summary + '（命中规则：' + (t.grade.triggered_rules || []).join(' & ') + '）。检出缺陷：' + (defectDigest || '（无）') + '。工具结论：' + t.summary + '。'
    const imgBase = String(t.image).split('/').pop()
    const ann = (t.artifacts && t.artifacts.length > 0) ? t.artifacts[0] : null
    const pendingRecord = {
      task_id: t.task_id, run_id: wf.runId, scene: t.scene, scene_zh: SCENE_ZH[t.scene] || t.scene,
      image: t.image, image_url: '/images/original/' + imgBase,
      annotated_url: ann ? '/images/annotated/' + t.task_id + '-' + ann.name : null,
      artifact_uri: ann ? ann.uri : null,
      defects: t.defects || [], grade: t.grade, tool_summary: t.summary,
      tool: { tool_version: t.tool_version, model_version: t.model_version, api_version: t.api_version },
      trace_id: t.trace_id,
      question: head + '请在前端复核页作出终判。',
      options: [
        { action: 'confirm', label: '接受自动判定', final_level: 'defect' },
        { action: 'revise', label: '接受并补充修正意见', final_level: 'defect' },
        { action: 'reject', label: '判定误报', final_level: 'pass' },
        { action: 'requeue', label: '退回重检', final_level: 'requeue_pending' }
      ]
    }
    const pendingPath = 'data/review-queue/pending/' + t.task_id + '.json'
    const decisionPath = 'data/review-queue/decisions/' + t.task_id + '.json'
    const pendingJson = JSON.stringify(pendingRecord, null, 2)
    const annStep = ann
      ? '2) 下载标注图供复核页展示（404 或失败则跳过，不阻塞）：mkdir -p data/workflow-reports/artifacts && curl -s -f -o data/workflow-reports/artifacts/' + t.task_id + '-' + ann.name + ' ' + TOOL + ann.uri + ' || true。'
      : '2) 本任务无标注图 artifact，跳过该步。'
    const ask = autoReview
      ? '无人值守模式（autoReview）：直接采用 confirm（接受自动判定），无需询问用户，也无需写复核队列。'
      : '前端复核页模式：真实用户将在 Web 复核工作台（review_bridge.py 桥接，http://127.0.0.1:8010/）上作出决定，你负责登记与等待，严格按序执行：' +
        '1) 登记待复核记录：mkdir -p data/review-queue/pending data/review-queue/decisions，然后把 -----JSON-START----- 与 -----JSON-END----- 之间的 JSON 原样写入 ' + pendingPath + '（用 heredoc：cat > ' + pendingPath + " <<'PENDING_EOF' 然后 JSON 然后 PENDING_EOF）：\n-----JSON-START-----\n" + pendingJson + '\n-----JSON-END-----\n' +
        annStep +
        '3) 轮询等待前端决定文件 ' + decisionPath + '：最多 30 轮，每轮执行一次 bash（约 50 秒）：for i in 1 2 3 4 5 6 7 8 9 10; do if [ -f ' + decisionPath + ' ]; then cat ' + decisionPath + '; exit 0; fi; sleep 5; done; exit 42 ——退出码 0 且有 JSON 输出即拿到决定（字段 action/opinion/reviewer），停止轮询；退出码 42 表示本轮未等到，进入下一轮。' +
        '4) 拿到决定：action=决定动作；final_level 映射 confirm/revise→defect、reject→pass、requeue→requeue_pending；opinion=决定中的 opinion（空则填「前端复核页决定，无附加意见」）。' +
        '5) 若 30 轮全部超时（约 30 分钟）仍无决定：不写复核记录文件，输出 need_human=true（由顶级 Agent 转呈用户兜底）。禁止代替用户决定。'
    const tail = '决定得出后（autoReview 或前端决定均适用）：1) 写复核记录文件 ' + TASKS_DIR + '/' + t.task_id + '.review.json，内容 {"task_id":"' + t.task_id + '","action":"<决定>","final_level":"<终判，映射：confirm/revise→defect，reject→pass，requeue→requeue_pending>","opinion":"<复核意见>","reviewed_by":"<前端决定用 reviewer 字段值加 -via-review-bridge 后缀；autoReview 填 workflow-hitl>","decided_at":"<date -u +%FT%TZ 输出>"}。2) 输出 task_id、action、final_level、opinion、note(一句话说明决定来源：前端复核页/autoReview/超时兜底)；超时未获人工处理时输出 need_human=true。'
    return head + ask + tail
  }
  let reviews = []
  const reviewTargets = graded.filter((t) => t.grade.level === 'review')
  if (reviewTargets.length === 0) {
    await wf.phase('review', async () => {
      wf.log('无 review 级任务，跳过复核门')
      return null
    })
  } else {
    const reviewRaw = await wf.phase('review', async () => await wf.parallel(reviewTargets.map((t, i) => async () => {
      return await wf.runAgent({ name: 'reviewer-' + (i + 1), modelHint: 'balanced', prompt: reviewPrompt(t), outputSchema: reviewSchema })
    }), { concurrency: 2 }))
    for (const r of reviewRaw) {
      if (r && r.structured && r.structured.task_id) { reviews.push(r.structured) }
    }
  }
  const finalTasks = graded.map((t) => {
    const rv = reviews.find((x) => x.task_id === t.task_id)
    const out = {
      task_id: t.task_id, scene: t.scene, image: t.image, code: t.code, status: t.status, summary: t.summary,
      defects: t.defects, grade: t.grade, tool_version: t.tool_version, model_version: t.model_version,
      api_version: t.api_version, trace_id: t.trace_id, processing_time_ms: t.processing_time_ms,
      artifacts: t.artifacts, ledger_path: t.ledger_path, error: t.error,
      review_action: null, final_level: t.grade.level, review_opinion: null
    }
    if (rv) {
      out.review_action = rv.need_human === true ? 'pending_human' : (rv.action || null)
      out.final_level = rv.need_human === true ? 'review' : (rv.final_level || t.grade.level)
      out.review_opinion = rv.opinion || null
    }
    return out
  })

  // ── Phase 6 knowledge：本地知识增强（工作区文档检索，生产版对应 RAGFlow 私有知识库） ──
  const digest = finalTasks.map((t) => (SCENE_ZH[t.scene] || t.scene) + '任务' + t.task_id + '判级' + t.grade.level + (t.defects && t.defects.length ? '，缺陷' + t.defects.map((d) => d.defect_name_cn || d.defect_type).join('/') : '')).join('；')
  const knowledgeRes = await wf.phase('knowledge', async () => await wf.runAgent({
    name: 'knowledge-retrieval',
    modelHint: 'fast',
    terseResult: true,
    prompt: '你是知识增强智能体。生产版本平台使用 RAGFlow 私有知识库（FR-KB-005 待确认），本工作流以工作区本地文档为知识源。检测结果摘要：「' + digest + '」。步骤：\n' +
      '1) 构造一个中文检索问句（关于上述缺陷类型的判定依据、严重度标准或检测规范）。\n' +
      '2) 在工作区 markdown 中检索：grep -rn --include="*.md" 关键词（如缺陷类型中文名/英文名、判级、严重度、复核）于项目根与 docs/ 目录；对命中文件读取相关段落（限制在前几处命中，不要全量读取大文件）。\n' +
      '3) 可选加分项：若 command -v pdftotext 可用，对 docs/article/ 中最相关的一篇论文提取与缺陷/疲劳/组织相关的 1-2 段（失败立即放弃，不阻塞）。\n' +
      '4) 输出：available(命中任一本地来源即 true)、question(检索问句)、sources(来源文件相对路径数组)、hits(每条不超过100字的依据摘要，注明来源)、reason(available=false 时说明)。\n' +
      '只读取，不修改任何文件；知识引用不改变检测结果本身（FR-KB-002 语义）。',
    outputSchema: {
      type: 'object',
      properties: {
        available: { type: 'boolean' },
        question: { type: 'string' },
        sources: { type: 'array', items: { type: 'string' } },
        hits: { type: 'array', items: { type: 'string' } },
        reason: { type: 'string' }
      },
      required: ['available'],
      additionalProperties: false
    }
  }))
  const knowledge = (knowledgeRes && knowledgeRes.structured) ? knowledgeRes.structured : { available: false, reason: '知识智能体调用失败' }

  // ── Phase 7 synthesize：综合研判（三层结果区分：工具原始/系统汇总/人工复核） ──
  const compactTasks = finalTasks.map((t) => ({
    task_id: t.task_id, scene: t.scene, image: t.image, tool_status: t.status,
    grade_level: t.grade.level, grade_summary: t.grade.summary, final_level: t.final_level,
    review_action: t.review_action, defect_count: (t.defects || []).length,
    max_severity: t.grade.max_severity, min_confidence: t.grade.min_confidence
  }))
  const synthesis = await wf.phase('synthesize', async () => await wf.synthesize({
    inputs: { intent: parsed, dispatch: dispatch, tasks: compactTasks, reviews: reviews, knowledge: knowledge },
    rubric: '生成任务级综合研判摘要（中文，不超过300字）：1) 严格区分三层——工具原始结果(status/缺陷/置信度)、系统汇总结论(跨任务统计与异常，规则判级为确定性引擎产出)、人工复核结论(复核动作与终判)；2) 对 fail、pending_human、requeue_pending 的任务明确标记为待处理项；3) 提及知识检索支撑情况；4) 不得虚构输入中不存在的事实。'
  }))
  const summaryText = (synthesis && synthesis.text) ? synthesis.text : null

  // ── Phase 8 report：报告汇编 + 标注图证据归档（FR-RPT 语义） ──
  const reportable = finalTasks.filter((t) => t.code === 0 && t.status !== 'error')
  let reportOut = null
  if (reportable.length === 0) {
    await wf.phase('report', async () => {
      wf.log('无可用检测结果，跳过报告生成')
      return null
    })
  } else {
    const reportRes = await wf.phase('report', async () => await wf.runAgent({
      name: 'report-writer',
      modelHint: 'balanced',
      prompt: '你是报告归档智能体。相对路径均相对你的工作目录（项目根）。运行编号 ' + wf.runId + '，检测需求「' + cfg.requirement + '」，执行模式 ' + mode + '，工具 ' + (dispatch.tool_name || '未知') + '（tool_version=' + (dispatch.tool_version || '?') + ' / model_version=' + (dispatch.model_version || '?') + '）。\n' +
        '任务汇总 JSON：' + JSON.stringify(finalTasks) + '\n综合研判摘要：' + (summaryText || '（综合不可用）') + '\n知识支撑：' + JSON.stringify(knowledge) + '\n步骤：\n' +
        '1) mkdir -p ' + REPORTS_DIR + '/artifacts ；对每个任务：cat ' + TASKS_DIR + '/<task_id>.json 与 <task_id>.review.json(若存在) 核对留痕。\n' +
        '2) 下载标注图证据：对每个任务 artifacts 数组的每项执行 curl -s ' + TOOL + '<uri> -o ' + REPORTS_DIR + '/artifacts/<task_id>-<name>（404 说明 mock 已重启丢失内存产物，报告注明跳过即可）。\n' +
        '3) 汇编综合检测报告（Markdown），结构：标题「工业图像智能检测综合报告（Harness Workflow 版）」→ 元信息（运行编号、生成时间 date 命令、检测需求、工具三版本、模式、判级规则说明：max_severity>=3 或 min_confidence<=0.5 转人工复核）→ 任务清单表（任务号/场景/图像/工具状态/规则判级/触发规则/复核动作/最终结论）→ 每任务缺陷明细表（defect_id/类型/中文名/置信度/严重度/bbox）→ 复核记录 → 知识支撑（来源+摘要）→ 综合研判摘要 → 附录（任务留痕文件与标注图落盘路径清单、trace_id 对照表）。\n' +
        '4) 写入 ' + REPORTS_DIR + '/qc-report-' + wf.runId + '.md 。\n' +
        '5) 输出 report_path(相对项目根)、task_count(整数)、headline(一句话结论)、annotated_saved(整数，成功下载的标注图数)。',
      outputSchema: {
        type: 'object',
        properties: {
          report_path: { type: 'string' },
          task_count: { type: 'integer' },
          headline: { type: 'string' },
          annotated_saved: { type: 'integer' }
        },
        required: ['report_path'],
        additionalProperties: false
      }
    }))
    reportOut = (reportRes && reportRes.structured) ? reportRes.structured : null
  }

  // ── 证据归档：run 目录 artifacts/（全链路可追溯） ──
  const evidence = await wf.artifact('qc-evidence', {
    requirement: cfg.requirement,
    mode: mode,
    run_id: wf.runId,
    intent: parsed,
    dispatch: dispatch,
    tasks: finalTasks,
    reviews: reviews,
    knowledge: knowledge,
    summary: summaryText,
    report: reportOut
  })
  const pendingHuman = finalTasks.filter((t) => t.review_action === 'pending_human' || t.final_level === 'requeue_pending').length
  return {
    status: 'done',
    mode: mode,
    scenes: scenes,
    tasks: compactTasks.map((t) => ({ task_id: t.task_id, scene: t.scene, grade_level: t.grade_level, final_level: t.final_level, review_action: t.review_action, defect_count: t.defect_count })),
    pending_human_review: pendingHuman,
    knowledge_available: knowledge.available === true,
    summary: summaryText,
    report: reportOut,
    artifact: evidence.path
  }
}
