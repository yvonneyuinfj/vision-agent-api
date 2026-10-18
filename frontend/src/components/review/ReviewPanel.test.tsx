import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, waitFor, cleanup, act } from '@testing-library/react'
import ReviewPanel from './ReviewPanel'
import type { DefectItem } from '../../api/types'

// vi.mock 工厂被提升到文件顶部，跨作用域引用需 vi.hoisted
const mocks = vi.hoisted(() => ({
  submitReview: vi.fn(),
}))

vi.mock('../../store/useTaskStore', () => ({
  // 组件以 selector 形式消费 store：useTaskStore((s) => s.submitReview)
  useTaskStore: vi.fn((selector: (state: unknown) => unknown) =>
    selector({ submitReview: mocks.submitReview }),
  ),
}))

/** 与设计稿 A1 同构的检出缺陷：初始「缺陷类型」「严重度=high」 */
const DEFECTS: DefectItem[] = [
  { defect_id: 'd1', defect_name_cn: '疑似过热', defect_type: 'overheat', severity: 'high', confidence: 0.62, location: null },
]

describe('ReviewPanel', () => {
  beforeEach(() => {
    mocks.submitReview.mockResolvedValue({ task_id: 't1' })
  })

  afterEach(() => {
    cleanup()
    vi.clearAllMocks()
  })

  it('渲染四张复核动作卡（同意 / 改判 / 退回重检 / 驳回）与底部双按钮', () => {
    render(<ReviewPanel taskId="t1" defects={DEFECTS} onSubmitted={vi.fn()} />)
    expect(screen.getByText('同意 AI 判级')).toBeInTheDocument()
    expect(screen.getByText('改判 / 修正')).toBeInTheDocument()
    // 「退回重检」出现两处：动作卡 + 底部 danger 快捷按钮
    expect(screen.getAllByText('退回重检').length).toBeGreaterThanOrEqual(2)
    expect(screen.getByText('驳回')).toBeInTheDocument()
    expect(screen.getByText('提交复核 · 继续流水线')).toBeInTheDocument()
    // 设计稿原文：改判卡描述为缺陷类型 + 严重度
    expect(screen.getByText('修改缺陷类型与严重度；在图像上补框漏检、删除误检。')).toBeInTheDocument()
  })

  it('未选中动作时提交按钮无操作（action 为 null 不触发接口）', async () => {
    render(<ReviewPanel taskId="t1" defects={DEFECTS} onSubmitted={vi.fn()} />)
    fireEvent.change(screen.getByPlaceholderText(/复核意见/), { target: { value: '确认无误' } })
    fireEvent.click(screen.getByText('提交复核 · 继续流水线'))
    expect(mocks.submitReview).not.toHaveBeenCalled()
  })

  it('选中改判卡展开设计稿表单：缺陷类型 + 严重度 下拉', () => {
    render(<ReviewPanel taskId="t1" defects={DEFECTS} onSubmitted={vi.fn()} />)
    expect(screen.queryByLabelText('缺陷类型')).toBeNull()
    fireEvent.click(screen.getByText('改判 / 修正'))
    expect(screen.getByLabelText('缺陷类型')).toBeInTheDocument()
    expect(screen.getByLabelText('严重度')).toBeInTheDocument()
    // 严重度候选与初始值取自 AI 检出
    expect(screen.getByLabelText('严重度')).toHaveValue('high')
    expect(screen.getByText('改判为 High 后，将自动附加工卡建议并通知工艺负责人。')).toBeInTheDocument()
  })

  it('意见必填：空意见提交被拦截且不调用接口', async () => {
    render(<ReviewPanel taskId="t1" defects={DEFECTS} onSubmitted={vi.fn()} />)
    fireEvent.click(screen.getByText('同意 AI 判级'))
    fireEvent.click(screen.getByText('提交复核 · 继续流水线'))
    expect(await screen.findByText(/请填写复核意见/)).toBeInTheDocument()
    expect(mocks.submitReview).not.toHaveBeenCalled()
  })

  it('选中同意卡 + 填写意见后提交：意见原样提交并回调', async () => {
    const onSubmitted = vi.fn()
    render(<ReviewPanel taskId="t1" defects={DEFECTS} onSubmitted={onSubmitted} />)
    fireEvent.click(screen.getByText('同意 AI 判级'))
    fireEvent.change(screen.getByPlaceholderText(/复核意见/), { target: { value: '确认无误' } })
    fireEvent.click(screen.getByText('提交复核 · 继续流水线'))
    await waitFor(() =>
      expect(mocks.submitReview).toHaveBeenCalledWith('t1', {
        action: 'confirm',
        opinion: '确认无误',
        override_grade: null,
      }),
    )
    await waitFor(() => expect(onSubmitted).toHaveBeenCalled())
    expect(await screen.findByText(/复核已提交/)).toBeInTheDocument()
  })

  it('改判：严重度 high → 判级 fail，缺陷类型 / 严重度原文带入 override_grade', async () => {
    render(<ReviewPanel taskId="t1" defects={DEFECTS} onSubmitted={vi.fn()} />)
    fireEvent.click(screen.getByText('改判 / 修正'))
    fireEvent.change(screen.getByPlaceholderText(/复核意见/), { target: { value: '改判为不合格' } })
    fireEvent.click(screen.getByText('提交复核 · 继续流水线'))
    await waitFor(() =>
      expect(mocks.submitReview).toHaveBeenCalledWith('t1', {
        action: 'revise',
        opinion: '改判为不合格',
        override_grade: { level: 'fail', summary: '改判为不合格', severity: 'high', defect_type: '疑似过热' },
      }),
    )
  })

  it('改判：严重度改为 low → 判级 pass', async () => {
    render(<ReviewPanel taskId="t1" defects={DEFECTS} onSubmitted={vi.fn()} />)
    fireEvent.click(screen.getByText('改判 / 修正'))
    fireEvent.change(screen.getByLabelText('严重度'), { target: { value: 'low' } })
    fireEvent.change(screen.getByPlaceholderText(/复核意见/), { target: { value: '误检，降级' } })
    fireEvent.click(screen.getByText('提交复核 · 继续流水线'))
    await waitFor(() =>
      expect(mocks.submitReview).toHaveBeenCalledWith(
        't1',
        expect.objectContaining({ action: 'revise', override_grade: expect.objectContaining({ level: 'pass', severity: 'low' }) }),
      ),
    )
  })

  it('退回重检：选中卡 + 填原因 + 意见提交，原因并入意见', async () => {
    render(<ReviewPanel taskId="t1" defects={DEFECTS} onSubmitted={vi.fn()} />)
    // 「退回重检」出现两处（动作卡 + 底部按钮），点第一张动作卡
    fireEvent.click(screen.getAllByText('退回重检')[0])
    fireEvent.change(screen.getByLabelText('退回原因'), { target: { value: '图像模糊 / 对焦失败' } })
    fireEvent.change(screen.getByPlaceholderText(/复核意见/), { target: { value: '请重新采集' } })
    fireEvent.click(screen.getByText('提交复核 · 继续流水线'))
    await waitFor(() =>
      expect(mocks.submitReview).toHaveBeenCalledWith('t1', {
        action: 'requeue',
        opinion: '图像模糊 / 对焦失败；请重新采集',
        override_grade: null,
      }),
    )
  })

  it('底部「退回重检」danger 按钮快捷提交 requeue', async () => {
    render(<ReviewPanel taskId="t1" defects={DEFECTS} onSubmitted={vi.fn()} />)
    fireEvent.change(screen.getByPlaceholderText(/复核意见/), { target: { value: '重新采集' } })
    // 两张「退回重检」（动作卡 + 底部按钮），取按钮（在 .btn 内）提交
    const buttons = screen.getAllByText('退回重检')
    fireEvent.click(buttons[buttons.length - 1])
    await waitFor(() =>
      expect(mocks.submitReview).toHaveBeenCalledWith('t1', {
        action: 'requeue',
        opinion: '重新采集',
        override_grade: null,
      }),
    )
  })
})
// ---------- 缺陷修订联动（Q3/Q4：revise 全量上送 + 意见留痕 + 一致性强提示不拦截） ----------
import { useDefectEditStore } from '../../store/useDefectEditStore'

const editStore = () => useDefectEditStore.getState()

describe('ReviewPanel 缺陷修订联动', () => {
  beforeEach(() => {
    mocks.submitReview.mockResolvedValue({ task_id: 't1' })
    editStore().openEditor('t1', 'input-0', DEFECTS)
  })
  afterEach(() => {
    cleanup()
    vi.clearAllMocks()
    editStore().closeEditor()
  })

  it('修订后显示 chip 摘要；选择改判后给出判级一致性强提示（不拦截）', () => {
    act(() => editStore().toggleReject('d1'))
    render(<ReviewPanel taskId="t1" defects={DEFECTS} onSubmitted={vi.fn()} />)
    expect(screen.getByText('排除 1')).toBeInTheDocument()
    fireEvent.click(screen.getByText('改判 / 修正'))
    expect(screen.getByText(/判级一致性提示/)).toBeInTheDocument()
    expect(screen.getByText(/不强制拦截/)).toBeInTheDocument()
  })

  it('revise 提交：defects 全量上送（含 human_rejected 标记），意见尾缀修订摘要，成功后清场', async () => {
    act(() => editStore().toggleReject('d1'))
    render(<ReviewPanel taskId="t1" defects={DEFECTS} onSubmitted={vi.fn()} />)
    fireEvent.click(screen.getByText('改判 / 修正'))
    fireEvent.change(screen.getByPlaceholderText('复核意见（必填）'), { target: { value: '排除一处伪显示' } })
    fireEvent.click(screen.getByText('提交复核 · 继续流水线'))
    await waitFor(() => expect(mocks.submitReview).toHaveBeenCalledTimes(1))
    const [tid, payload] = mocks.submitReview.mock.calls[0]
    expect(tid).toBe('t1')
    expect(payload.action).toBe('revise')
    expect(payload.opinion).toBe('排除一处伪显示；[缺陷修订] 排除 1')
    expect(payload.defects).toEqual([
      expect.objectContaining({ defect_id: 'd1', human_rejected: true }),
    ])
    // 提交成功即结束编辑会话（草稿已上送，不残留到下一任务）
    await waitFor(() => expect(editStore().open).toBe(false))
  })

  it('confirm 提交：修订不随载荷上送（契约：仅 revise 携带 defects）', async () => {
    act(() => editStore().toggleReject('d1'))
    render(<ReviewPanel taskId="t1" defects={DEFECTS} onSubmitted={vi.fn()} />)
    // 非改判动作给出「修订不会生效」提醒
    fireEvent.click(screen.getByText('同意 AI 判级'))
    expect(screen.getByText(/缺陷修订仅随「改判 \/ 修正」提交生效/)).toBeInTheDocument()
    fireEvent.change(screen.getByPlaceholderText('复核意见（必填）'), { target: { value: '同意' } })
    fireEvent.click(screen.getByText('提交复核 · 继续流水线'))
    await waitFor(() => expect(mocks.submitReview).toHaveBeenCalledTimes(1))
    const payload = mocks.submitReview.mock.calls[0][1]
    expect(payload.action).toBe('confirm')
    expect(payload.defects).toBeUndefined()
    expect(payload.opinion).toBe('同意')
  })

  it('人工新增条目未选类型：revise 提交被拦截（数据有效性，非一致性）', async () => {
    act(() => editStore().addDefect([10, 10, 60, 60]))
    render(<ReviewPanel taskId="t1" defects={DEFECTS} onSubmitted={vi.fn()} />)
    fireEvent.click(screen.getByText('改判 / 修正'))
    fireEvent.change(screen.getByPlaceholderText('复核意见（必填）'), { target: { value: '补漏检' } })
    fireEvent.click(screen.getByText('提交复核 · 继续流水线'))
    expect(await screen.findByText(/未选择类型的人工新增条目/)).toBeInTheDocument()
    expect(mocks.submitReview).not.toHaveBeenCalled()
  })

  it('清除修订：chip 消失，会话结束', () => {
    act(() => editStore().toggleReject('d1'))
    render(<ReviewPanel taskId="t1" defects={DEFECTS} onSubmitted={vi.fn()} />)
    expect(screen.getByText('排除 1')).toBeInTheDocument()
    fireEvent.click(screen.getByText('清除修订'))
    expect(screen.queryByText('排除 1')).toBeNull()
    expect(editStore().open).toBe(false)
  })
})
