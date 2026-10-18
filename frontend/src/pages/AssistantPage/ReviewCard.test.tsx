import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, waitFor, cleanup } from '@testing-library/react'
import ReviewCard, { type ReviewCardPayload } from './ReviewCard'
import type { DefectItem } from '../../api/types'

// vi.mock 工厂被提升到文件顶部，跨作用域引用需 vi.hoisted
const mocks = vi.hoisted(() => ({
  submitReview: vi.fn(),
  fetchTaskDetail: vi.fn(),
  navigate: vi.fn(),
}))

vi.mock('react-router-dom', () => ({
  useNavigate: () => mocks.navigate,
}))

vi.mock('../../store/useTaskStore', () => ({
  // ReviewPanel 以 selector 形式消费 store：useTaskStore((s) => s.submitReview)
  useTaskStore: vi.fn((selector: (state: unknown) => unknown) =>
    selector({ submitReview: mocks.submitReview }),
  ),
}))

vi.mock('../../api/tasks', () => ({
  fetchTaskDetail: mocks.fetchTaskDetail,
}))

vi.mock('../../components/review/DefectEditModal', () => ({
  default: (props: { open: boolean; taskId: string }) =>
    props.open ? <div data-testid="defect-edit-modal">{props.taskId}</div> : null,
}))

/** AI 检出明细（任务详情返回的 standardized，供「改判」缺陷类型候选） */
const DEFECTS: DefectItem[] = [
  { defect_id: 'd1', defect_name_cn: '疑似过热', defect_type: 'overheat', severity: 'high', confidence: 0.62, location: null },
]

function makeDetail(status: string) {
  return { status, standardized: DEFECTS }
}

/** 落卡时刻的 AI 预判快照（后端 _emit_review_card 的 content） */
const PAYLOAD: ReviewCardPayload = {
  task_id: 'T888',
  status: 'awaiting_review',
  scene: 'fracture',
  grade_level: 'review',
  grade_summary: '置信度不足',
  defect_count: 1,
  defects: [{ name: '疑似过热', severity: 'high', confidence: 0.62 }],
}

describe('ReviewCard（对话内终裁复核卡片）', () => {
  beforeEach(() => {
    mocks.submitReview.mockResolvedValue({ task_id: 'T888' })
    mocks.fetchTaskDetail.mockResolvedValue(makeDetail('awaiting_review'))
  })

  afterEach(() => {
    cleanup()
    vi.clearAllMocks()
  })

  it('待复核任务 → 激活态：弹出复核头 + 检出明细行 + 内嵌复核动作卡（四动作）+ 工作台入口', async () => {
    render(<ReviewCard taskId="T888" payload={PAYLOAD} onSubmitted={vi.fn()} />)
    // 复核头：终裁徽标 + 任务号 + 暂停原因
    expect(screen.getByText('终裁复核')).toBeInTheDocument()
    expect(screen.getByText('T888')).toBeInTheDocument()
    expect(screen.getByText(/任务已暂停并进入人工复核门/)).toBeInTheDocument()
    // 检出明细行（落卡快照）
    expect(screen.getByText('A1')).toBeInTheDocument()
    expect(screen.getByText('疑似过热')).toBeInTheDocument()
    // 检出明细加载完成后内嵌复核动作卡（复用 ReviewPanel 四动作）
    expect(await screen.findByText('同意 AI 判级')).toBeInTheDocument()
    expect(screen.getByText('改判 / 修正')).toBeInTheDocument()
    expect(screen.getByText('驳回')).toBeInTheDocument()
    expect(screen.getByText('提交复核 · 继续流水线')).toBeInTheDocument()
    // 工作台入口（核对标注图）
    expect(screen.getByText('打开复核工作台 →')).toBeInTheDocument()
  })

  it('激活态拉取任务详情以填充缺陷候选（改判展开可见候选缺陷名）', async () => {
    render(<ReviewCard taskId="T888" payload={PAYLOAD} onSubmitted={vi.fn()} />)
    fireEvent.click(await screen.findByText('改判 / 修正'))
    // 缺陷类型下拉含 AI 检出缺陷名（来自 fetchTaskDetail 的 standardized）
    await waitFor(() => expect(mocks.fetchTaskDetail).toHaveBeenCalledWith('T888'))
    const select = await screen.findByLabelText('缺陷类型')
    expect(select).toBeInTheDocument()
    expect((select as HTMLSelectElement).value).toBe('疑似过热')
  })

  it('就地提交：选中同意 + 填意见 → 提交并回调，卡片落定为「已提交」', async () => {
    const onSubmitted = vi.fn()
    render(<ReviewCard taskId="T888" payload={PAYLOAD} onSubmitted={onSubmitted} />)
    fireEvent.click(await screen.findByText('同意 AI 判级'))
    fireEvent.change(screen.getByPlaceholderText(/复核意见/), { target: { value: '确认无误' } })
    fireEvent.click(screen.getByText('提交复核 · 继续流水线'))
    await waitFor(() =>
      expect(mocks.submitReview).toHaveBeenCalledWith('T888', expect.objectContaining({ action: 'confirm' })),
    )
    await waitFor(() => expect(onSubmitted).toHaveBeenCalled())
    // 本地即时落定
    expect(await screen.findByText('已提交终裁复核，流水线继续生成报告。')).toBeInTheDocument()
  })

  it('服务端实时状态已翻转（非待复核）→ 落定态：不渲染动作卡，展示「已完成终裁复核」+ 详情入口', async () => {
    // 激活与否由挂载时拉取的任务实时状态推导（刷新安全），不再依赖卡片快照翻转
    mocks.fetchTaskDetail.mockResolvedValue(makeDetail('completed'))
    render(<ReviewCard taskId="T888" payload={PAYLOAD} onSubmitted={vi.fn()} />)
    expect(await screen.findByText('该任务已完成终裁复核，无需人工处置。')).toBeInTheDocument()
    expect(screen.queryByText('同意 AI 判级')).toBeNull()
    expect(screen.getByText('查看任务详情 →')).toBeInTheDocument()
    expect(mocks.fetchTaskDetail).toHaveBeenCalledWith('T888')
  })
})

describe('ReviewCard 内嵌图像区（Q6/Q7：复核期即可看图定夺）', () => {
  beforeEach(() => {
    mocks.submitReview.mockResolvedValue({ task_id: 'T888' })
  })
  afterEach(() => {
    cleanup()
    vi.clearAllMocks()
  })

  it('详情带 input_uris + review.artifacts → 渲染图像区（图层按钮 + 中断载荷标注图）', async () => {
    mocks.fetchTaskDetail.mockResolvedValue({
      status: 'awaiting_review',
      standardized: [
        { defect_id: 'd1', defect_name_cn: '疑似过热', defect_type: 'overheat', severity: 'high', confidence: 0.62, location: { type: 'bbox', bbox: [10, 10, 90, 90] } },
      ],
      input_uris: ['/api/v1/tasks/T888/files/a.jpg?kind=inputs'],
      review: { question: 'q', artifacts: [{ name: 'artifact_result_image', mime: 'image/jpeg', uri: '/api/v1/tasks/T888/files/r.jpg?kind=artifacts' }] },
    })
    render(<ReviewCard taskId="T888" payload={PAYLOAD} onSubmitted={vi.fn()} />)
    expect(await screen.findByText('缺陷框叠加')).toBeInTheDocument()
    expect(screen.getByText('原图')).toBeInTheDocument()
    expect(screen.getByText('算法标注图')).toBeInTheDocument()
  })

  it('详情无图无产物（存量数据）→ 不渲染图像区，复核面板照常', async () => {
    mocks.fetchTaskDetail.mockResolvedValue(makeDetail('awaiting_review'))
    render(<ReviewCard taskId="T888" payload={PAYLOAD} onSubmitted={vi.fn()} />)
    expect(await screen.findByText('提交复核 · 继续流水线')).toBeInTheDocument()
    expect(screen.queryByText('缺陷框叠加')).toBeNull()
  })
})

describe('ReviewCard 人工标注入口（Q5：卡片就地打开编辑 Modal）', () => {
  beforeEach(() => {
    mocks.submitReview.mockResolvedValue({ task_id: 'T888' })
    mocks.fetchTaskDetail.mockResolvedValue({
      status: 'awaiting_review',
      standardized: DEFECTS,
      input_uris: ['/api/v1/tasks/T888/files/a.jpg?kind=inputs'],
    })
  })
  afterEach(() => {
    cleanup()
    vi.clearAllMocks()
  })

  it('图像区「人工标注」按钮打开编辑 Modal（传当前 taskId）', async () => {
    render(<ReviewCard taskId="T888" payload={PAYLOAD} onSubmitted={vi.fn()} />)
    expect(screen.queryByTestId('defect-edit-modal')).toBeNull()
    fireEvent.click(await screen.findByText(/人工标注/))
    expect(screen.getByTestId('defect-edit-modal')).toHaveTextContent('T888')
  })

  it('落定态不渲染图像区与人工标注入口', async () => {
    mocks.fetchTaskDetail.mockResolvedValue({ status: 'completed', standardized: DEFECTS, input_uris: ['/a.jpg'] })
    render(<ReviewCard taskId="T888" payload={PAYLOAD} onSubmitted={vi.fn()} />)
    expect(await screen.findByText(/该任务已完成终裁复核/)).toBeInTheDocument()
    expect(screen.queryByText(/人工标注/)).toBeNull()
  })
})
