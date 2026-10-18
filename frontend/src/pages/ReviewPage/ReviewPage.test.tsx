import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, fireEvent, cleanup } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import ReviewPage from './index'
import { fetchTaskDetail } from '../../api/tasks'
import type { TaskSummary, TaskDetail } from '../../api/types'

/** 页面用选择器订阅 store，mock 需还原 (selector) => selector(state) 语义 */
let storeState: { tasks: TaskSummary[]; loading: boolean; error: null; fetchList: () => Promise<void> }
vi.mock('../../store/useTaskStore', () => ({
  useTaskStore: (sel: (s: typeof storeState) => unknown) => sel(storeState),
}))

vi.mock('../../api/tasks', () => ({
  fetchTaskDetail: vi.fn(),
}))

const detailMock = fetchTaskDetail as unknown as ReturnType<typeof vi.fn>

const T1: TaskSummary = {
  task_id: 'QC-1',
  scene: 'borescope',
  mode: 'joint',
  capability_used: '涡轮盘',
  status: 'awaiting_review',
  created_at: '2026-08-18T09:30:00Z',
  updated_at: '2026-08-18T09:31:00Z',
  finished_at: null,
} as TaskSummary

const T2: TaskSummary = { ...T1, task_id: 'QC-2', created_at: '2026-08-18T09:40:00Z' }

function detailFor(taskId: string): TaskDetail {
  return {
    task_id: taskId,
    scene: 'borescope',
    mode: 'joint',
    capability_used: '涡轮盘',
    status: 'awaiting_review',
    created_at: '2026-08-18T09:30:00Z',
    input_uris: [`/api/v1/tasks/_upload/files/${taskId}.jpg?kind=inputs`],
    standardized: [
      { defect_id: 'd1', defect_type: 'scratch', defect_name_cn: '擦伤', confidence: 0.62, severity: 'medium', location: null },
      { defect_id: 'd2', defect_type: 'crack', defect_name_cn: '裂纹', confidence: 0.41, severity: 'high', location: null },
    ],
    grade: { level: 'review', summary: '疑似过热区域待专家确认' },
  } as unknown as TaskDetail
}

function renderPage(entry = '/tasks/review') {
  return render(
    <MemoryRouter initialEntries={[entry]}>
      <ReviewPage />
    </MemoryRouter>,
  )
}

beforeEach(() => {
  storeState = {
    tasks: [T1, T2],
    loading: false,
    error: null,
    fetchList: vi.fn(async () => {}),
  }
  detailMock.mockReset().mockImplementation(async (id: string) => detailFor(id))
})

afterEach(() => {
  cleanup()
})

describe('ReviewPage（待复核队列，决策 4）', () => {
  it('渲染队列与复核要点，不渲染第二套结果视图', async () => {
    renderPage()
    expect(await screen.findByText('待复核队列')).toBeInTheDocument()
    expect(screen.getAllByText('QC-2').length).toBeGreaterThan(0)
    expect(screen.getByText('QC-1')).toBeInTheDocument()
    expect(screen.getByText('改判与驳回必须填写复核意见，意见随本次复核结论一并留痕')).toBeInTheDocument()
    // 决策 4：复核动作与查看器只在 /tasks/:id 一处
    expect(screen.queryByText('同意 AI 判级')).toBeNull()
    expect(screen.queryByText('人工复核工作台')).toBeNull()
  })

  it('无 ?task= 时选中队列最新一项并拉取明细', async () => {
    renderPage()
    expect(await screen.findByText('低于阈值')).toBeInTheDocument()
    await waitFor(() => expect(detailMock).toHaveBeenCalledWith('QC-2'))
    expect(screen.getByText('进入三分区结果页')).toBeInTheDocument()
    // 检出 2 项，其中 1 项低于 0.60
    expect(screen.getByText('2 项')).toBeInTheDocument()
    expect(screen.getByText('1 项')).toBeInTheDocument()
  })

  it('?task= 指定选中项', async () => {
    renderPage('/tasks/review?task=QC-1')
    await waitFor(() => expect(detailMock).toHaveBeenCalledWith('QC-1'))
    expect(document.querySelector('.queue-row.on')?.textContent).toContain('QC-1')
  })

  it('点击队列行切换选中项', async () => {
    renderPage('/tasks/review?task=QC-1')
    const row2 = Array.from(document.querySelectorAll<HTMLButtonElement>('.queue-row'))
      .find((r) => r.textContent?.includes('QC-2'))
    fireEvent.click(row2!)
    await waitFor(() => expect(detailMock).toHaveBeenCalledWith('QC-2'))
  })

  it('J/K 键盘切换选中项', async () => {
    renderPage('/tasks/review?task=QC-2')
    fireEvent.keyDown(window, { key: 'j' })
    await waitFor(() => expect(document.querySelector('.queue-row.on')?.textContent).toContain('QC-1'))
    fireEvent.keyDown(window, { key: 'k' })
    await waitFor(() => expect(document.querySelector('.queue-row.on')?.textContent).toContain('QC-2'))
  })

  it('队列清空后展示空态', async () => {
    storeState.tasks = []
    renderPage()
    expect(await screen.findByText('暂无待复核任务 · 队列已清空')).toBeInTheDocument()
    expect(screen.getByText('队列为空，无需复核。')).toBeInTheDocument()
  })

  it('明细加载失败不白屏，回到占位态', async () => {
    detailMock.mockRejectedValue(new Error('网络错误'))
    renderPage()
    expect(await screen.findByText('任务明细加载中…')).toBeInTheDocument()
    expect(screen.getByText('待复核队列')).toBeInTheDocument()
  })

  it('每页 8 条：窗口内真分页，分页条只报「共 N 条」（决策 51-⑪）', async () => {
    const many: TaskSummary[] = Array.from({ length: 10 }, (_, i) => ({
      ...T1,
      task_id: `QC-${100 + i}`,
      created_at: `2026-08-18T09:${String(30 + i).padStart(2, '0')}:00Z`,
    }))
    storeState = { ...storeState, tasks: many }
    renderPage()
    await screen.findByText('待复核队列')
    expect(document.querySelectorAll('.queue-row').length).toBe(8)
    const pager = document.querySelector('.pager')
    expect(pager?.textContent).toContain('10')
    expect(pager?.textContent).toContain('共')
    expect(pager?.textContent).not.toContain('窗口内分页')
  })
})
