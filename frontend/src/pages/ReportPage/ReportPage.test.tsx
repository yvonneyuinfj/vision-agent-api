import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, cleanup } from '@testing-library/react'
import ReportPage from './index'

const mocks = vi.hoisted(() => ({
  fetchTaskDetail: vi.fn(),
  fetchReport: vi.fn(),
}))

vi.mock('react-router-dom', () => ({
  useParams: () => ({ taskId: 't1' }),
  Link: ({ children, to }: { children: React.ReactNode; to: string }) => <a href={to}>{children}</a>,
}))

vi.mock('../../api/tasks', async (importOriginal) => {
  const mod = await importOriginal<typeof import('../../api/tasks')>()
  return { ...mod, fetchTaskDetail: mocks.fetchTaskDetail }
})

vi.mock('../../api/reports', () => ({ fetchReport: mocks.fetchReport }))

const ARTIFACTS = [
  { name: 'artifact_original_image', mime: 'image/jpeg', uri: '/api/v1/tasks/t1/files/original.jpg?kind=artifacts' },
  { name: 'artifact_result_image', mime: 'image/jpeg', uri: '/api/v1/tasks/t1/files/result-image.jpg?kind=artifacts' },
  { name: 'artifact_result_json', mime: 'application/json', uri: '/api/v1/tasks/t1/files/result-json.json?kind=artifacts' },
]

const DETAIL = {
  task_id: 't1',
  scene: 'fracture',
  mode: 'agent',
  status: 'completed',
  capability_used: 'fracture_v1',
  created_at: '2026-08-25T10:00:00+00:00',
  updated_at: '2026-08-25T10:05:00+00:00',
  finished_at: '2026-08-25T10:05:00+00:00',
  trace_id: null,
  input_uris: ['/api/v1/tasks/t1/files/a.jpg'],
  steps: [],
  standardized: [
    {
      defect_id: 'd1',
      defect_type: 'cleavage',
      defect_name_cn: '解理断裂',
      confidence: 0.87,
      severity: 'high',
      location: { type: 'bbox', bbox: [1, 2, 3, 4] },
      description: '河流花样明显',
    },
  ],
  grade: { level: 'review', summary: '存在高严重度缺陷，需人工复核' },
  knowledge: [{ content: '解理断裂判级依据', score: 0.91, doc_name: '断口手册.pdf', chunk_id: 'c1', image_uri: null }],
  review: null,
  report: {
    report_type: 'agent_report',
    name: 't1_report.md',
    uri: '/api/v1/tasks/t1/files/report.md?kind=reports',
    version: '1.0.0',
    artifacts: ARTIFACTS,
  },
  error: null,
}

const REPORT_MD = `# 智能质检报告 t1

## 1. 结果概览
判级 review。

## 5. AI 综合分析
综合分析正文：检出解理断裂，建议复核。
`

const REPORT_DOC = {
  meta: {
    task_id: 't1',
    report_uri: '/api/v1/tasks/t1/files/report.md?kind=reports',
    format: 'markdown',
    generated_at: '2026-08-25T10:05:00+00:00',
  },
  content: REPORT_MD,
}

/** GET /reports/{task_id} 未生成时的后端错误形状（report_service 404 TASK_NOT_FOUND） */
class ReportNotGenerated extends Error {
  status = 404
  constructor() {
    super('报告尚未生成')
  }
}

beforeEach(() => {
  vi.clearAllMocks()
  mocks.fetchTaskDetail.mockResolvedValue(DETAIL)
  mocks.fetchReport.mockResolvedValue(REPORT_DOC)
})

afterEach(() => {
  cleanup()
})

describe('ReportPage（优化轮 D6 纸张文档风报告页）', () => {
  it('渲染纸张骨架：标题、印章判级、缺陷卡与知识依据', async () => {
    render(<ReportPage />)
    expect(await screen.findByText('智能质检报告')).toBeInTheDocument()
    // 印章式判级（review → 需人工复核）
    expect(screen.getByText('需人工复核')).toBeInTheDocument()
    // 缺陷条目
    expect(screen.getByText('解理断裂')).toBeInTheDocument()
    expect(screen.getByText(/置信 87\.0%/)).toBeInTheDocument()
    // 知识库依据
    expect(screen.getByText(/断口手册\.pdf/)).toBeInTheDocument()
  })

  it('报告正文走 GET /reports/{task_id}，抽取 AI 综合分析小节渲染', async () => {
    render(<ReportPage />)
    expect(await screen.findByText(/综合分析正文：检出解理断裂/)).toBeInTheDocument()
    expect(mocks.fetchReport).toHaveBeenCalledWith('t1', { silent: true })
    // meta.generated_at 进入报告头时间行
    expect(screen.getByText(/报告生成/)).toBeInTheDocument()
  })

  it('/reports 404（报告未生成）时静默降级为结构化骨架并提示', async () => {
    mocks.fetchReport.mockRejectedValue(new ReportNotGenerated())
    render(<ReportPage />)
    await screen.findByText('智能质检报告')
    expect(screen.getByText(/报告正文未生成/)).toBeInTheDocument()
    // 综合分析回退判级摘要
    expect(screen.getByText(/存在高严重度缺陷，需人工复核/)).toBeInTheDocument()
  })

  it('无 report 时以判级摘要兜底综合分析', async () => {
    mocks.fetchTaskDetail.mockResolvedValue({ ...DETAIL, report: null })
    mocks.fetchReport.mockRejectedValue(new ReportNotGenerated())
    render(<ReportPage />)
    expect(await screen.findByText(/存在高严重度缺陷，需人工复核/)).toBeInTheDocument()
  })

  it('检测概述节渲染原始图像 vs 检测结果图同行对比（report.artifacts 图像对）', async () => {
    render(<ReportPage />)
    await screen.findByText('智能质检报告')
    const orig = screen.getByAltText('原始图像')
    const annotated = screen.getByAltText('检测结果图')
    expect(orig).toHaveAttribute('src', '/api/v1/tasks/t1/files/original.jpg?kind=artifacts')
    expect(annotated).toHaveAttribute('src', '/api/v1/tasks/t1/files/result-image.jpg?kind=artifacts')
    // 口径一致时（DETAIL 无 model_defect_count）保持原简洁图注
    expect(screen.getByText(/检测结果图（模型标注 · 检出 1 处）/)).toBeInTheDocument()
    // 对比块位于「一 · 检测概述」节内（需求：概述含两图对比，不再用小缩略图）
    const section = orig.closest('section.paper-sec')
    expect(section?.querySelector('.paper-h')?.textContent).toContain('一 · 检测概述')
    expect(screen.queryByAltText('检测图像')).not.toBeInTheDocument()
  })

  it('无原图产物时对比区回退 input_uris 作原图', async () => {
    mocks.fetchTaskDetail.mockResolvedValue({
      ...DETAIL,
      report: { ...DETAIL.report, artifacts: ARTIFACTS.slice(1) },
    })
    render(<ReportPage />)
    await screen.findByText('智能质检报告')
    expect(screen.getByAltText('原始图像')).toHaveAttribute('src', '/api/v1/tasks/t1/files/a.jpg')
  })

  it('无标注图产物时不渲染对比区块，回退输入图缩略', async () => {
    mocks.fetchTaskDetail.mockResolvedValue({
      ...DETAIL,
      report: { ...DETAIL.report, artifacts: [ARTIFACTS[0], ARTIFACTS[2]] },
    })
    render(<ReportPage />)
    await screen.findByText('智能质检报告')
    expect(screen.queryByAltText('检测结果图')).not.toBeInTheDocument()
    // 概述节回退为输入图缩略
    expect(screen.getByAltText('检测图像')).toHaveAttribute('src', '/api/v1/tasks/t1/files/a.jpg')
  })

  it('加载失败展示错误态', async () => {
    mocks.fetchTaskDetail.mockRejectedValue(new Error('boom'))
    render(<ReportPage />)
    expect(await screen.findByText('任务加载失败')).toBeInTheDocument()
  })

  it('二 · 检测结果：缺陷卡不罗列 bbox 坐标（位置信息由标注图承载）', async () => {
    render(<ReportPage />)
    await screen.findByText('智能质检报告')
    expect(screen.queryByText(/bbox \[/)).not.toBeInTheDocument()
    // 缺陷语义仍完整：中文名 + 代码 + 置信度 + 描述
    expect(screen.getByText('解理断裂')).toBeInTheDocument()
    expect(screen.getByText('cleavage')).toBeInTheDocument()
    expect(screen.getByText(/置信 87\.0%/)).toBeInTheDocument()
    expect(screen.getByText('河流花样明显')).toBeInTheDocument()
  })

  it('二 · 检测结果：class_definitions 转译类别语义并渲染类别定义表', async () => {
    mocks.fetchTaskDetail.mockResolvedValue({
      ...DETAIL,
      standardized: [
        { ...DETAIL.standardized[0], defect_type: 'BP', defect_name_cn: '', description: '荧光渗透显示 BP（模糊的真缺陷，置信度 0.83）' },
      ],
      class_definitions: [
        { class_id: 0, class_name: 'BP', description: '模糊的真缺陷', is_true_defect: true },
        { class_id: 1, class_name: 'SP', description: '清晰真缺陷', is_true_defect: true },
        { class_id: 2, class_name: 'FI', description: '虚假荧光显示', is_true_defect: false },
      ],
    })
    render(<ReportPage />)
    await screen.findByText('智能质检报告')

    // 缺陷卡按定义表转译中文名（defect_name_cn 为空时取 description）+ 真缺陷性质徽标；
    // 中文名在缺陷卡与类别定义表各一处；「真缺陷」= 缺陷卡徽标 1 + 定义表 BP/SP 两行
    expect(screen.getAllByText('模糊的真缺陷').length).toBe(2)
    expect(screen.getAllByText('真缺陷').length).toBe(3)

    // 类别定义表：三类齐备，虚假显示标注不计入，并按 defect_type 统计检出数
    expect(screen.getByText('检测类别定义')).toBeInTheDocument()
    expect(screen.getByText('清晰真缺陷')).toBeInTheDocument()
    expect(screen.getByText('虚假显示 · 不计入')).toBeInTheDocument()
    expect(screen.getByText('1 处')).toBeInTheDocument()
    expect(screen.getAllByText('0 处').length).toBe(2) // SP / FI 未检出
  })

  it('无 class_definitions（存量任务/mock 工具）时不渲染类别定义表，也不臆造性质结论', async () => {
    render(<ReportPage />)
    await screen.findByText('智能质检报告')
    expect(screen.queryByText('检测类别定义')).not.toBeInTheDocument()
    expect(screen.queryByText('真缺陷')).not.toBeInTheDocument()
  })
})

  // ---------------------------------------------------------------------
  // 口径对照（2026-09-16 事故）：标注图框数 vs 终裁列表数差异透明化
  // ---------------------------------------------------------------------
  it('图注口径对照：模型标注数 ≠ 终裁数时点明差异来源（智能体视觉补充）', async () => {
    // 复刻事故现场 T48086…：模型检出 3 处（BP×2、SP×1，全带框），
    // 智能体裁决补充 1 条手写 crack 条目（无定位），人工 confirm
    mocks.fetchTaskDetail.mockResolvedValue({
      ...DETAIL,
      standardized: [
        { defect_id: 'd1', defect_type: 'BP', confidence: 0.76, severity: 'low',
          location: { type: 'bbox', bbox: [292, 107, 324, 136] } },
        { defect_id: 'd2', defect_type: 'SP', confidence: 0.52, severity: 'low',
          location: { type: 'bbox', bbox: [291, 108, 325, 136] } },
        { defect_id: 'd3', defect_type: 'BP', confidence: 0.38, severity: 'low',
          location: { type: 'bbox', bbox: [400, 300, 450, 360] } },
        { defect_id: 'd4', defect_type: 'crack', confidence: 0.9, severity: 'medium',
          location: null, source: 'agent_visual', description: '视觉判断：线状荧光迹痕' },
      ],
      grade: { level: 'review', summary: '智能体异议，请求人工复核', model_defect_count: 3 },
    })
    render(<ReportPage />)
    await screen.findByText('智能质检报告')
    expect(
      screen.getByText(/检测结果图（模型标注 3 处 · 终裁结论 4 处（智能体视觉补充 1 处））/)
    ).toBeInTheDocument()
    // 手写条目徽标 + 无标注框提示 + 附注说明
    expect(screen.getByText('智能体视觉判断')).toBeInTheDocument()
    expect(screen.getByText('无标注框')).toBeInTheDocument()
    expect(screen.getByText(/该条目无定位信息，检测结果图上无对应标注框。/)).toBeInTheDocument()
    // 模型条目不带徽标
    expect(screen.getAllByText('智能体视觉判断').length).toBe(1)
  })

  it('口径一致时不显示差异对照图注（避免噪声）', async () => {
    mocks.fetchTaskDetail.mockResolvedValue({
      ...DETAIL,
      grade: { level: 'defect', summary: '存在低严重度缺陷', model_defect_count: 1 },
    })
    render(<ReportPage />)
    await screen.findByText('智能质检报告')
    expect(screen.getByText(/检测结果图（模型标注 · 检出 1 处）/)).toBeInTheDocument()
    expect(screen.queryByText(/终裁结论/)).not.toBeInTheDocument()
  })

  it('人工修订条目带「人工复核新增」徽标并计入图注差异来源', async () => {
    mocks.fetchTaskDetail.mockResolvedValue({
      ...DETAIL,
      standardized: [
        DETAIL.standardized[0],
        { defect_id: 'M001', defect_type: 'pore', confidence: 0.8, severity: 'low',
          location: null, source: 'manual' },
      ],
      grade: { level: 'review', summary: '人工修订', model_defect_count: 1 },
    })
    render(<ReportPage />)
    await screen.findByText('智能质检报告')
    expect(screen.getByText('人工复核新增')).toBeInTheDocument()
    expect(
      screen.getByText(/模型标注 1 处 · 终裁结论 2 处（人工新增 1 处）/)
    ).toBeInTheDocument()
  })

  // ---------------------------------------------------------------------
  // 多图任务：每张图都要检测与对比（2026-09-16 生产事故回归）
  // ---------------------------------------------------------------------
  it('多图任务逐图对比：每张输入图一个对比块，标注图按来源图名配对', async () => {
    mocks.fetchTaskDetail.mockResolvedValue({
      ...DETAIL,
      input_uris: [
        '/api/v1/tasks/t1/files/a.jpg?kind=inputs',
        '/api/v1/tasks/t1/files/b.jpg?kind=inputs',
      ],
      standardized: [
        { defect_id: 'd1', defect_type: 'BP', confidence: 0.8, severity: 'low',
          location: { type: 'bbox', bbox: [1, 2, 3, 4] }, image_name: 'a.jpg' },
        { defect_id: 'd2', defect_type: 'SP', confidence: 0.7, severity: 'low',
          location: { type: 'bbox', bbox: [5, 6, 7, 8] }, image_name: 'b.jpg' },
      ],
      grade: { level: 'defect', summary: '检出 2 处' },
      report: {
        ...DETAIL.report,
        artifacts: [
          { name: 'artifact_original_image__a', mime: 'image/jpeg', uri: '/api/a-orig.jpg' },
          { name: 'artifact_result_image__a', mime: 'image/jpeg', uri: '/api/a-annot.jpg' },
          { name: 'artifact_result_image__b', mime: 'image/jpeg', uri: '/api/b-annot.jpg' },
        ],
      },
    })
    render(<ReportPage />)
    await screen.findByText('智能质检报告')

    // 两个对比块（每图一对），图名标题可见（标题内带图名）
    expect(screen.getByText('图 1/2')).toBeInTheDocument()
    expect(screen.getByText('图 2/2')).toBeInTheDocument()
    expect(screen.getByText('a.jpg', { selector: '.pc-title .mono' })).toBeInTheDocument()
    expect(screen.getByText('b.jpg', { selector: '.pc-title .mono' })).toBeInTheDocument()
    // 每块的原图/标注图配对正确（a 有原图产物，b 回退 input_uris）
    const imgs = screen.getAllByAltText('检测结果图')
    expect(imgs[0]).toHaveAttribute('src', '/api/a-annot.jpg')
    expect(imgs[1]).toHaveAttribute('src', '/api/b-annot.jpg')
    const origs = screen.getAllByAltText('原始图像')
    expect(origs[0]).toHaveAttribute('src', '/api/a-orig.jpg')
    expect(origs[1]).toHaveAttribute('src', '/api/v1/tasks/t1/files/b.jpg?kind=inputs')
    // 缺陷卡来源图徽标
    expect(screen.getByText('a.jpg', { selector: '.pd-img' })).toBeInTheDocument()
    expect(screen.getByText('b.jpg', { selector: '.pd-img' })).toBeInTheDocument()
  })

  it('多图任务无标注产物时回退输入图缩略（不臆造对比）', async () => {
    mocks.fetchTaskDetail.mockResolvedValue({
      ...DETAIL,
      input_uris: [
        '/api/v1/tasks/t1/files/a.jpg?kind=inputs',
        '/api/v1/tasks/t1/files/b.jpg?kind=inputs',
      ],
      report: { ...DETAIL.report, artifacts: [] },
    })
    render(<ReportPage />)
    await screen.findByText('智能质检报告')
    expect(screen.queryByAltText('检测结果图')).not.toBeInTheDocument()
    expect(screen.getAllByAltText('检测图像').length).toBe(2)
  })
