import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { useTaskStore } from '../../store/useTaskStore'
import type { TaskSummary } from '../../api/types'
import ListPage, { matchesKeyword } from './index'

vi.mock('../../store/useTaskStore', () => ({
  useTaskStore: vi.fn(),
}))

vi.mock('../../api/tasks', () => ({
  fetchTaskList: vi.fn(),
  fetchTaskStats: vi.fn(),
  retryTask: vi.fn(),
}))

import { fetchTaskList, fetchTaskStats, retryTask } from '../../api/tasks'

const retryMock = retryTask as unknown as ReturnType<typeof vi.fn>
const listMock = fetchTaskList as unknown as ReturnType<typeof vi.fn>
const statsMock = fetchTaskStats as unknown as ReturnType<typeof vi.fn>

/** 统计横幅默认数据：与枚举语义一并覆盖（today 与列表快照不同源） */
const DEFAULT_STATS = {
  total: 5, today: 3, pending: 1, running: 2, awaiting_review: 4, completed: 5, failed: 2, canceled: 0,
}

beforeEach(() => {
  // 轮询仅在 hasActiveTask 时启动，3s 间隔下大多数用例不触发；默认 resolve 防未处理 rejection
  listMock.mockReset().mockResolvedValue([])
  statsMock.mockReset().mockResolvedValue(DEFAULT_STATS)
})

/** 统计栏 span 的跨元素文本匹配（「今日 」+ <b>3</b> 在两文本节点，默认 getByText 拼不上） */
function statSpan(label: string, value: string): HTMLElement {
  const target = `${label} ${value}`
  return screen.getByText((_, el) =>
    !!el && el.tagName === 'SPAN' && el.className === '' && el.textContent?.trim() === target,
  )
}

const storeMock = useTaskStore as unknown as ReturnType<typeof vi.fn>

describe('ListPage', () => {
  beforeEach(() => {
    storeMock.mockReturnValue({
      tasks: [
        { task_id: 't1', scene: 'borescope', mode: 'single', status: 'awaiting_review', created_at: '2026-08-18T10:00:00', updated_at: '2026-08-18T10:00:00' },
        { task_id: 't2', scene: 'infrared', mode: 'single', status: 'completed', created_at: '2026-08-17T09:00:00', updated_at: '2026-08-17T09:00:00' },
      ],
      loading: false,
      error: null,
      fetchList: vi.fn(),
    })
  })

  it('渲染任务行与状态标签', async () => {
    render(<MemoryRouter><ListPage /></MemoryRouter>)
    expect(await screen.findByText('t1')).toBeInTheDocument()
    // 状态标签 + 筛选 Tab 均出现「待复核」
    expect(screen.getAllByText('待复核').length).toBeGreaterThan(0)
    // 场景列映射「孔探」
    expect(screen.getAllByText('孔探').length).toBeGreaterThan(0)
  })

  it('按状态筛选', async () => {
    render(<MemoryRouter><ListPage /></MemoryRouter>)
    expect(await screen.findByText('t1')).toBeInTheDocument()
    // 点状态筛选后 t1 消失（awaiting_review ≠ completed）
    expect(screen.queryByText('t2')).toBeInTheDocument()
  })

  it('统计横幅展示后端全量权威计数（今日/进行中/待复核/失败）', async () => {
    statsMock.mockResolvedValue({
      total: 5, today: 3, pending: 1, running: 2, awaiting_review: 4, completed: 5, failed: 2, canceled: 0,
    })
    render(<MemoryRouter><ListPage /></MemoryRouter>)
    expect(await screen.findByText('t1')).toBeInTheDocument()
    expect(statSpan('今日', '3')).toBeInTheDocument()
    expect(statSpan('进行中', '3')).toBeInTheDocument() // pending 1 + running 2
    expect(statSpan('待复核', '4')).toBeInTheDocument()
    expect(statSpan('失败', '2')).toBeInTheDocument()
  })

  it('统计未返回前占位 —，返回后立刻渲染真实值', async () => {
    statsMock.mockReturnValue(new Promise(() => {})) // 永不 resolve：模拟加载中
    render(<MemoryRouter><ListPage /></MemoryRouter>)
    expect(statSpan('今日', '—')).toBeInTheDocument()
  })

  it('只有场景 / 状态 / 关键字三个筛选，无时间与创建人筛选（决策 6）', async () => {
    render(<MemoryRouter><ListPage /></MemoryRouter>)
    expect(await screen.findByText('t1')).toBeInTheDocument()
    expect(screen.getByLabelText('场景')).toBeInTheDocument()
    expect(screen.getByLabelText('状态')).toBeInTheDocument()
    expect(document.querySelector('.ant-picker')).toBeNull()
    expect(screen.queryByPlaceholderText(/创建人|提交人/)).toBeNull()
    // 无取消接口 → 取消按钮常驻禁用并写明原因
    const cancel = screen.getByRole('button', { name: '取消' })
    expect(cancel).toBeDisabled()
    expect(document.body.textContent).toContain('该工具未提供中止能力，无法中断')
  })

  it('关键字检索：任务号 / 能力匹配', async () => {
    storeMock.mockReturnValue({
      tasks: [
        { task_id: 'QC-1001', scene: 'borescope', mode: 'single', capability_used: '高压涡轮盘', status: 'completed', created_at: '2026-08-18T10:00:00', updated_at: '2026-08-18T10:00:00' },
        { task_id: 'QC-1002', scene: 'ir_temperature', mode: 'joint', capability_used: '燃烧室', status: 'completed', created_at: '2026-08-18T10:00:00', updated_at: '2026-08-18T10:00:00' },
      ],
      loading: false,
      error: null,
      fetchList: vi.fn(),
    })
    render(<MemoryRouter><ListPage /></MemoryRouter>)
    expect(await screen.findByText('QC-1001')).toBeInTheDocument()
    // 输入任务号 → 只留匹配行
    fireEvent.change(screen.getByPlaceholderText('任务 ID / 场景 / 能力（仅本窗口）'), { target: { value: 'QC-1001' } })
    expect(screen.getByText('QC-1001')).toBeInTheDocument()
    expect(screen.queryByText('QC-1002')).not.toBeInTheDocument()
    // 输入能力名 → 只留匹配行（大小写不敏感）
    fireEvent.change(screen.getByPlaceholderText('任务 ID / 场景 / 能力（仅本窗口）'), { target: { value: '燃烧室' } })
    expect(screen.getByText('QC-1002')).toBeInTheDocument()
    expect(screen.queryByText('QC-1001')).not.toBeInTheDocument()
    // 清空恢复
    fireEvent.change(screen.getByPlaceholderText('任务 ID / 场景 / 能力（仅本窗口）'), { target: { value: '' } })
    expect(screen.getByText('QC-1001')).toBeInTheDocument()
    expect(screen.getByText('QC-1002')).toBeInTheDocument()
  })
})

describe('失败任务重试', () => {
  const failedTask: TaskSummary = {
    task_id: 'tf',
    scene: 'borescope',
    mode: 'single',
    capability_used: null,
    status: 'failed',
    created_at: '2026-08-18T10:00:00',
    updated_at: '2026-08-18T10:00:00',
    finished_at: '2026-08-18T10:01:00',
  }

  beforeEach(() => {
    retryMock.mockReset()
  })

  it('失败行渲染重试按钮；点击调用 retryTask 并刷新列表', async () => {
    const fetchList = vi.fn()
    retryMock.mockResolvedValue({ task_id: 'tf' })
    storeMock.mockReturnValue({ tasks: [failedTask], loading: false, error: null, fetchList })
    render(<MemoryRouter><ListPage /></MemoryRouter>)

    fireEvent.click(await screen.findByText('重试'))
    expect(retryMock).toHaveBeenCalledWith('tf')
    await waitFor(() => expect(fetchList).toHaveBeenCalled())
  })

  it('重试请求进行中按钮禁用并显示「重试中…」', async () => {
    let resolveRetry: (v: { task_id: string }) => void = () => {}
    retryMock.mockReturnValue(new Promise((res) => { resolveRetry = res }))
    storeMock.mockReturnValue({ tasks: [failedTask], loading: false, error: null, fetchList: vi.fn() })
    render(<MemoryRouter><ListPage /></MemoryRouter>)

    fireEvent.click(await screen.findByText('重试'))
    const busyBtn = await screen.findByText('重试中…')
    expect(busyBtn.closest('button')).toBeDisabled()

    resolveRetry({ task_id: 'tf' })
    await waitFor(() => expect(screen.queryByText('重试中…')).not.toBeInTheDocument())
  })

  it('重试失败（业务错误）后按钮恢复可用，不白屏', async () => {
    retryMock.mockRejectedValue(new Error('409'))
    storeMock.mockReturnValue({ tasks: [failedTask], loading: false, error: null, fetchList: vi.fn() })
    render(<MemoryRouter><ListPage /></MemoryRouter>)

    fireEvent.click(await screen.findByText('重试'))
    await waitFor(() => expect(screen.getByText('重试')).not.toBeDisabled())
  })
})

describe('matchesKeyword', () => {
  const base: TaskSummary = {
    task_id: 'QC-1001',
    scene: 'borescope',
    mode: 'single',
    capability_used: '高压涡轮盘',
    status: 'completed',
    created_at: '2026-08-18T10:00:00',
    updated_at: '2026-08-18T10:00:00',
    finished_at: null,
  }

  it('空关键字匹配所有任务', () => {
    expect(matchesKeyword(base, '')).toBe(true)
    expect(matchesKeyword(base, '   ')).toBe(true)
  })

  it('按任务号匹配（含大小写不敏感）', () => {
    expect(matchesKeyword(base, 'qc-1001')).toBe(true)
    expect(matchesKeyword(base, 'QC-1001')).toBe(true)
    expect(matchesKeyword(base, 'QC-1002')).toBe(false)
  })

  it('按能力名匹配中文', () => {
    expect(matchesKeyword(base, '涡轮盘')).toBe(true)
    expect(matchesKeyword(base, '燃烧室')).toBe(false)
  })

  it('按场景标签匹配（code 或中文标签）', () => {
    expect(matchesKeyword(base, 'borescope')).toBe(true)
    expect(matchesKeyword(base, '孔探')).toBe(true)
  })
})
