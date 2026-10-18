import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, fireEvent, cleanup, act, within } from '@testing-library/react'
import DetailPage from './index'

// vi.mock 工厂被提升到文件顶部，跨作用域引用需 vi.hoisted
const mocks = vi.hoisted(() => ({
  fetchTaskDetail: vi.fn(),
  fetchKnowledgeContext: vi.fn(),
  fetch: vi.fn(),
  navigate: vi.fn(),
}))

vi.mock('react-router-dom', () => ({
  useParams: () => ({ taskId: 't1' }),
  useNavigate: () => mocks.navigate,
}))

vi.mock('../../api/tasks', async (importOriginal) => {
  const mod = await importOriginal<typeof import('../../api/tasks')>()
  return {
    ...mod,
    fetchTaskDetail: mocks.fetchTaskDetail,
    fetchKnowledgeContext: mocks.fetchKnowledgeContext,
  }
})

// react-konva Stage 在 jsdom 无法挂载（无 canvas 2d context，Task 6 minor①），
// 详情页测试聚焦组装逻辑，用轻量 stub 代替，渲染 images 键以验证拼装结果
// data-defects 透出 showDefects 传递值，供「图层 tab 控制缺陷框」契约断言；
// data-annotated-src 透出模型标注产物图源，供「条目无 bbox 时标注层兜底」契约断言
vi.mock('../../viewer/Viewer', () => ({
  default: ({ images, showDefects, annotatedSrc }: { images: Array<{ key: string }>; showDefects?: boolean; annotatedSrc?: string | null }) => (
    <div data-testid="mock-viewer" data-defects={showDefects ? 'on' : 'off'} data-annotated-src={annotatedSrc ?? ''}>
      {images.map((i) => <span key={i.key}>{i.key}</span>)}
    </div>
  ),
}))

const DETAIL = {
  task_id: 't1',
  scene: 'borescope',
  mode: 'single',
  status: 'completed',
  capability_used: 'detector-v2',
  created_at: '2026-08-18T10:00:00Z',
  updated_at: '2026-08-18T10:05:00Z',
  finished_at: '2026-08-18T10:05:00Z',
  trace_id: 'trace-1',
  input_uris: ['/api/v1/tasks/_upload/files/class1_1.jpg?kind=inputs'],
  steps: [{ node: 'intent', message: '识别意图', ts: '2026-08-18T10:00:01Z' }],
  standardized: [
    { defect_id: 'd1', defect_type: 'crack', defect_name_cn: '裂纹', confidence: 0.87, severity: 'high', location: null, description: '表面裂纹' },
  ],
  grade: { level: 'fail', summary: '判级结论：不合格。依据 [标准 A](ref://c1)。' },
  knowledge: [{ content: '片段', score: 0.9, doc_name: '标准手册.pdf', chunk_id: 'c1', image_uri: null }],
  review: null,
  report: { report_type: 'md', name: 'report.md', uri: '/api/v1/tasks/t1/files/report.md?kind=reports', version: '1' },
  error: null,
}

beforeEach(() => {
  mocks.fetchTaskDetail.mockResolvedValue(DETAIL)
  mocks.fetchKnowledgeContext.mockResolvedValue({
    doc_id: 'd1',
    doc_name: '标准手册.pdf',
    page: 3,
    self: { chunk_id: 'c1', content: '判级依据内容', page: 3, score: 0.9 },
    prev: [],
    next: [],
  })
  mocks.fetch.mockResolvedValue({ text: () => Promise.resolve('# 质检报告内容') })
  vi.stubGlobal('fetch', mocks.fetch)
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
  vi.unstubAllGlobals()
  vi.useRealTimers()
})

describe('DetailPage', () => {
  it('渲染任务详情：状态、场景、缺陷卡片、判级结论与标注图', async () => {
    render(<DetailPage />)
    expect(await screen.findByText('t1')).toBeInTheDocument()
    expect(screen.getByText('已完成')).toBeInTheDocument()
    // 场景名在页头副标题内（与模式/时间同行）
    expect(screen.getByText(/孔探/)).toBeInTheDocument()
    expect(screen.getAllByText('裂纹').length).toBeGreaterThan(0)
    // 判级 tag（页头溯源条与第 ② 区各一处）+ 动态标题（按标准化缺陷数/严重度生成）
    expect(screen.getAllByText('异常 · 需关注').length).toBeGreaterThan(0)
    expect(screen.getByText('检出 1 处缺陷，其中 1 处高严重度')).toBeInTheDocument()
    // 原图直接采用后端 input_uris 契约（URI 本身即下载端点），不再臆造 artifact 文件名
    expect(screen.getByText('input-0')).toBeInTheDocument()
    expect(screen.queryByText('artifact')).not.toBeInTheDocument()
  })

  it('三分区（决策 5）：三区齐备、各自标源，第 ① 区如实声明原始响应未落库', async () => {
    render(<DetailPage />)
    await screen.findByText('t1')
    const zones = document.querySelectorAll('.zone')
    expect(zones.length).toBe(3)
    expect(zones[0].querySelector('.zone-h')?.textContent).toContain('模型输出（结构化结果）')
    expect(zones[1].querySelector('.zone-h')?.textContent).toContain('AI 解释与依据')
    expect(zones[2].querySelector('.zone-h')?.textContent).toContain('人工复核结论')
    // 区头标源：数据来路写清楚，不让人误把归一结果当原始输出
    expect(zones[0].querySelector('.zsrc')?.textContent).toContain('GET /tasks/{id} → standardized')
    expect(zones[0].textContent).toContain('工具原始响应尚未落库')
    expect(zones[0].textContent).toContain('不可作为原始证据引用')
    // 第 ① 区只读：不出现复核提交控件；人工动作只在第 ③ 区
    expect(zones[0].querySelector('.action-card')).toBeNull()
  })

  it('第 ① 区缺陷卡：类别代码、置信度与 bbox 原样透出（结构化字段只读展示）', async () => {
    mocks.fetchTaskDetail.mockResolvedValue({
      ...DETAIL,
      standardized: [
        { ...DETAIL.standardized[0], location: { type: 'bbox', bbox: [10, 20, 300, 400] } },
      ],
    })
    render(<DetailPage />)
    await screen.findByText('t1')
    const card = document.querySelector('.zone .defect-card')
    expect(card?.textContent).toContain('crack')
    expect(card?.textContent).toContain('0.87')
    expect(card?.textContent).toContain('10.0, 20.0, 300.0, 400.0')
  })

  it('图层切换用自定义 tab：默认算法标注图（缺陷框开），切原图关闭缺陷框', async () => {
    render(<DetailPage />)
    await screen.findByText('t1')
    // 待 tab 渲染完成再做断言（避免与详情渲染提交产生时序竞态）
    await screen.findByText('算法标注图')
    const viewer = () => screen.getByTestId('mock-viewer')
    // 初始：算法标注图 tab 激活，showDefects=true
    expect(viewer()).toHaveAttribute('data-defects', 'on')
    expect(screen.getByText('算法标注图')).toHaveClass('active')
    // 切原图：缺陷框隐藏
    fireEvent.click(screen.getByText('原图'))
    await waitFor(() => expect(viewer()).toHaveAttribute('data-defects', 'off'))
    expect(screen.getByText('原图')).toHaveClass('active')
    // 切回算法标注图：缺陷框恢复
    fireEvent.click(screen.getByText('算法标注图'))
    await waitFor(() => expect(viewer()).toHaveAttribute('data-defects', 'on'))
  })

  it('报告产物含标注图时向 Viewer 传递 annotatedSrc（条目无 bbox 时的标注层兜底图源）', async () => {
    mocks.fetchTaskDetail.mockResolvedValue({
      ...DETAIL,
      report: {
        ...DETAIL.report,
        artifacts: [
          { name: 'artifact_original_image', mime: 'image/jpeg', uri: '/api/v1/tasks/t1/files/artifact_original_image?kind=artifacts' },
          { name: 'artifact_result_image', mime: 'image/jpeg', uri: '/api/v1/tasks/t1/files/artifact_result_image?kind=artifacts' },
          { name: 'artifact_report_pdf', mime: 'application/pdf', uri: '/api/v1/tasks/t1/files/artifact_report_pdf?kind=artifacts' },
        ],
      },
    })
    render(<DetailPage />)
    await screen.findByText('t1')
    expect(screen.getByTestId('mock-viewer')).toHaveAttribute(
      'data-annotated-src',
      '/api/v1/tasks/t1/files/artifact_result_image?kind=artifacts',
    )
  })

  it('无图像产物时 annotatedSrc 为空（标注层维持原图 + Konva 框）', async () => {
    render(<DetailPage />)
    await screen.findByText('t1')
    expect(screen.getByTestId('mock-viewer')).toHaveAttribute('data-annotated-src', '')
  })

  it('加载失败展示错误态，重试可恢复', async () => {
    mocks.fetchTaskDetail.mockRejectedValueOnce(new Error('网络错误'))
    render(<DetailPage />)
    expect(await screen.findByText('任务加载失败')).toBeInTheDocument()
    expect(screen.getByText('网络错误')).toBeInTheDocument()
    fireEvent.click(screen.getByText('重试'))
    await waitFor(() => expect(mocks.fetchTaskDetail).toHaveBeenCalledTimes(2))
    expect(await screen.findByText('t1')).toBeInTheDocument()
  })

  it('待复核任务展示复核面板', async () => {
    mocks.fetchTaskDetail.mockResolvedValue({ ...DETAIL, status: 'awaiting_review', grade: { level: 'review', summary: '需复核' } })
    render(<DetailPage />)
    expect(await screen.findByText('同意 AI 判级')).toBeInTheDocument()
  })

  it('判级结论中的引用链接可点击弹出引用弹窗', async () => {
    render(<DetailPage />)
    await screen.findByText('t1')
    // 「标准 A」渲染为 <a> 链接（判级结论卡与报告「五 · 综合判级」各一处），定位任一锚点
    const anchor = screen
      .getAllByText(/标准 A/)
      .map((el) => el.closest('a'))
      .find((a): a is HTMLAnchorElement => a !== null)
    expect(anchor).toBeTruthy()
    fireEvent.click(anchor!)
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByText(/标准手册\.pdf/)).toBeInTheDocument()
    expect(mocks.fetchKnowledgeContext).toHaveBeenCalledWith('t1', 'c1', 2)
  })

  it('点击「预览原文」弹出弹窗渲染引用内容，不再内嵌于右栏', async () => {
    render(<DetailPage />)
    await screen.findByText('t1')
    // 打开前无任何弹窗：引用内容不再内嵌展示在性能与追溯信息上方
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    fireEvent.click(screen.getAllByText('预览原文')[0])
    const dialog = await screen.findByRole('dialog')
    // 弹窗标题卡展示文档名与相似度；原文经 markdown 渲染为完整命中片段
    expect(within(dialog).getByText(/标准手册\.pdf/)).toBeInTheDocument()
    expect(within(dialog).getByText('片段')).toBeInTheDocument()
    expect(mocks.fetchKnowledgeContext).toHaveBeenCalledWith('t1', 'c1', 2)
  })

  it('点击「查看完整报告」跳转独立报告页（优化轮 D6：报告页取代弹窗）', async () => {
    render(<DetailPage />)
    await screen.findByText('t1')
    fireEvent.click(screen.getByText('查看完整报告 →'))
    expect(mocks.navigate).toHaveBeenCalledWith('/tasks/t1/report')
  })

  it('拒检任务（4004）展示失败横幅并可跳转质检助手（优化轮 E4）', async () => {
    mocks.fetchTaskDetail.mockResolvedValue({
      ...DETAIL,
      status: 'failed',
      grade: null,
      report: null,
      error: { node: 'routing', code: 4004, message: '场景无可用能力' },
    })
    render(<DetailPage />)
    expect(await screen.findByText('任务失败')).toBeInTheDocument()
    expect(screen.getByText('场景无可用能力')).toBeInTheDocument()
    expect(screen.getByText(/该图像对应场景暂无可用检测能力/)).toBeInTheDocument()
    fireEvent.click(screen.getByText('去质检助手 →'))
    expect(mocks.navigate).toHaveBeenCalledWith('/detect/assistant')
  })

  it('非拒检失败只展示横幅，不给跳转按钮（优化轮 E4）', async () => {
    mocks.fetchTaskDetail.mockResolvedValue({
      ...DETAIL,
      status: 'failed',
      grade: null,
      report: null,
      error: { node: 'detect', code: 5001, message: '检测能力内部错误' },
    })
    render(<DetailPage />)
    expect(await screen.findByText('任务失败')).toBeInTheDocument()
    expect(screen.queryByText('去质检助手 →')).not.toBeInTheDocument()
  })

  it('进行中任务通过 3s 轮询降级刷新（jsdom 无 EventSource）', async () => {
    vi.useFakeTimers()
    mocks.fetchTaskDetail.mockResolvedValue({ ...DETAIL, status: 'pending' })
    render(<DetailPage />)
    // 先 flush 首次加载的微任务链（fetch resolve → setDetail → 轮询 effect 注册 interval）
    await act(async () => {
      await Promise.resolve()
      await Promise.resolve()
    })
    expect(mocks.fetchTaskDetail).toHaveBeenCalledTimes(1)
    // 推进 3s 触发轮询第 2 次
    await act(async () => {
      vi.advanceTimersByTime(3000)
      await Promise.resolve()
    })
    expect(mocks.fetchTaskDetail).toHaveBeenCalledTimes(2)
  })
})