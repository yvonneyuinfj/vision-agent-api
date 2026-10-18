import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, fireEvent, cleanup } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import SamplesPage from './SamplesPage'
import { fetchReviewMatrix, type MatrixBucket, type ReviewMatrix } from '../api/reviewMatrix'
import type { TaskSummary } from '../api/types'

/* 反馈样本管理（决策 7）：矩阵是唯一聚合数据源，争议样本只能来自 /tasks 的 100 条窗口。
   断言重点 = 原型结构（grid-23 + 四张卡）+ 全部数字取真实字段 + 口径声明不被删改。 */

vi.mock('../api/reviewMatrix', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../api/reviewMatrix')>()),
  fetchReviewMatrix: vi.fn(),
}))

let storeState: { tasks: TaskSummary[]; loading: boolean; error: null; fetchList: () => Promise<void> }
vi.mock('../store/useTaskStore', () => ({
  useTaskStore: (sel: (s: typeof storeState) => unknown) => sel(storeState),
}))

const matrixMock = fetchReviewMatrix as unknown as ReturnType<typeof vi.fn>

const bucket = (over: Partial<MatrixBucket> = {}): MatrixBucket => ({
  tasks: 0,
  actions: { confirmed: 0, revised: 0, rejected: 0 },
  agent_requests: 0,
  defects: {
    added: 0,
    excluded: 0,
    reclassified: 0,
    severity_changed: 0,
    moved: 0,
    removed: 0,
  },
  revised_rate: null,
  ...over,
})

const MATRIX: ReviewMatrix = {
  sample_count: 47,
  confidence_threshold: 0.6,
  total: bucket({
    tasks: 47,
    actions: { confirmed: 28, revised: 11, rejected: 5 },
    agent_requests: 4,
    defects: {
      added: 12,
      excluded: 5,
      reclassified: 3,
      severity_changed: 2,
      moved: 1,
      removed: 4,
    },
    revised_rate: 0.25,
  }),
  by_scene: {
    borescope: bucket({ tasks: 21, actions: { confirmed: 14, revised: 4, rejected: 3 } }),
    metallography: bucket({ tasks: 8, actions: { confirmed: 6, revised: 1, rejected: 1 } }),
  },
  low_confidence_band: bucket({
    tasks: 13,
    actions: { confirmed: 7, revised: 5, rejected: 1 },
    revised_rate: 0.3846,
  }),
}

const task = (
  id: string,
  status: TaskSummary['status'],
  scene: string | null = 'borescope',
): TaskSummary =>
  ({
    task_id: id,
    scene,
    mode: 'single',
    status,
    capability_used: 'borescope_defect_detect',
    created_at: '2026-08-18T09:30:00Z',
    updated_at: '2026-08-18T09:40:00Z',
    finished_at: null,
  }) as TaskSummary

function cardByTitle(text: string): HTMLElement {
  const title = Array.from(document.querySelectorAll('.card-title')).find((n) =>
    n.textContent?.includes(text),
  )
  if (!title) throw new Error(`未找到卡片：${text}`)
  return title.closest('.card') as HTMLElement
}

/** 条形行（去掉表头行）的某列文本 */
function barCells(card: HTMLElement, selector: string): string[] {
  return Array.from(card.querySelectorAll(`.brow:not(.bhead) ${selector}`)).map((n) =>
    (n.textContent ?? '').trim(),
  )
}

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/tasks/samples']}>
      <SamplesPage />
    </MemoryRouter>,
  )
}

beforeEach(() => {
  storeState = {
    tasks: [
      task('QC-D1', 'awaiting_review'),
      task('QC-D2', 'awaiting_review', 'metallography'),
      task('QC-X', 'completed'),
    ],
    loading: false,
    error: null,
    fetchList: vi.fn(async () => {}),
  }
  matrixMock.mockReset().mockResolvedValue(MATRIX)
})

afterEach(() => {
  cleanup()
})

describe('SamplesPage（反馈样本管理，决策 7）', () => {
  it('KPI 四张卡按原型口径取矩阵真实字段', async () => {
    renderPage()
    await screen.findByText('复核动作分布')
    const kpis = Array.from(document.querySelectorAll('.kpi'))
    expect(kpis.map((k) => k.querySelector('.kpi-label')?.textContent)).toEqual([
      '复核样本总数',
      '置信阈值',
      '低置信带',
      '人工改判率',
    ])
    expect(kpis[0].textContent).toContain('47')
    expect(kpis[1].textContent).toContain('0.60')
    expect(kpis[2].textContent).toContain('13')
    expect(kpis[2].textContent).toContain('确认 7 · 改判 5 · 驳回 1')
    expect(kpis[3].textContent).toContain('25.0')
    // 改判率分母是真实已决数（28+11+5），不是示意值
    expect(kpis[3].textContent).toContain('改判 11 / 已决 44')
  })

  it('拒绝原型示意值：无环比、无 326/231/38/27/30、无 12.7%', async () => {
    renderPage()
    await screen.findByText('复核动作分布')
    expect(screen.queryByText(/环比/)).toBeNull()
    for (const fake of ['326', '231', '38', '27', '30', '12.7'])
      expect(screen.queryByText(fake)).toBeNull()
  })

  it('grid-23：左栏动作分布 + 争议样本，右栏分场景复核量 + 缺陷修订差异', async () => {
    renderPage()
    await screen.findByText('复核动作分布')
    const stacks = Array.from(document.querySelectorAll('.grid-23 > .stack'))
    expect(stacks).toHaveLength(2)
    expect(stacks[0].textContent).toContain('复核动作分布')
    expect(stacks[0].textContent).toContain('争议样本')
    expect(stacks[1].textContent).toContain('分场景复核量')
    expect(stacks[1].textContent).toContain('缺陷修订差异')
  })

  it('动作分布用条形（非表格）且只有矩阵真实的三桶', async () => {
    renderPage()
    await screen.findByText('复核动作分布')
    const card = cardByTitle('复核动作分布')
    expect(card.querySelector('table')).toBeNull()
    expect(barCells(card, '.blab')).toEqual(['确认', '改判', '驳回'])
    expect(barCells(card, '.bnum')).toEqual(['28', '11', '5'])
    expect(card.querySelectorAll('.meter-fill')).toHaveLength(3)
    // 矩阵无退回重检桶 → 不补第四行
    expect(barCells(card, '.bpct')).toEqual(['63.6%', '25.0%', '11.4%'])
  })

  it('分场景复核量 / 缺陷修订差异条形同样取真实计数', async () => {
    renderPage()
    await screen.findByText('缺陷修订差异')
    const sceneCard = cardByTitle('分场景复核量')
    expect(barCells(sceneCard, '.blab')).toEqual(['孔探', '金相'])
    expect(barCells(sceneCard, '.bnum')).toEqual(['21', '8'])
    const diffCard = cardByTitle('缺陷修订差异')
    expect(barCells(diffCard, '.blab')).toEqual([
      '人工补检（漏检）',
      '人工排除（误检）',
      '改缺陷类别',
      '改严重度',
      '移框',
      '删除条目',
    ])
    expect(barCells(diffCard, '.bnum')).toEqual(['12', '5', '3', '2', '1', '4'])
  })

  it('无按类别计数接口 → 不渲染缺陷类别分布卡', async () => {
    renderPage()
    await screen.findByText('复核动作分布')
    expect(screen.queryByText('缺陷类别分布')).toBeNull()
    expect(screen.queryByText('烧蚀')).toBeNull()
    expect(screen.queryByText('夹杂物')).toBeNull()
  })

  it('争议样本 = 窗口内待人工裁决任务，分页条保留窗口口径', async () => {
    renderPage()
    await screen.findByText('争议样本（本窗口客户端过滤）')
    const card = cardByTitle('争议样本')
    expect(card.querySelector('.task-id')?.textContent).toContain('QC-D1')
    expect(card.textContent).toContain('QC-D2')
    expect(card.textContent).not.toContain('QC-X')
    const pager = card.querySelector('.pager')
    expect(pager?.textContent).toContain('共')
    expect(pager?.textContent).not.toContain('接口无 offset')
    expect(pager?.textContent).not.toContain('非全量')
    // 矩阵聚合不含未裁决任务：口径说明保留
    expect(card.querySelector('.card-title')?.textContent).toContain('本窗口客户端过滤')
  })

  it('候选池缺口声明保留（G3-01）', async () => {
    renderPage()
    await screen.findByText('争议样本（本窗口客户端过滤）')
    const notice = cardByTitle('争议样本').querySelector('.notice.gray')
    expect(notice?.textContent).toContain('不计入候选池统计')
    expect(notice?.textContent).toContain('CandidateSample')
    expect(notice?.textContent).toContain('G3-01')
  })

  it('矩阵读取失败给恢复入口，页面不白屏', async () => {
    matrixMock.mockRejectedValue(new Error('502 复核矩阵不可用'))
    renderPage()
    expect(await screen.findByText('502 复核矩阵不可用')).toBeInTheDocument()
    expect(screen.getByText('复核动作分布')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '重新读取' }))
    await waitFor(() => expect(matrixMock).toHaveBeenCalledTimes(2))
  })
})
