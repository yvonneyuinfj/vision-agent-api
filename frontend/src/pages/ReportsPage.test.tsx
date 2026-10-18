import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, fireEvent, cleanup } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import ReportsPage from './ReportsPage'
import { fetchTaskList } from '../api/tasks'
import type { TaskSummary } from '../api/types'

/* 报告与综合研判（决策 7）：清单 = 窗口内已完成任务；正文按任务读取。
   断言重点 = grid-23 两张右栏卡（综合研判承接 / 报告固定字段）+ 只保留接口真有数据的列 +
   不伪造报告四态生命周期 / 审批流 / business_ref 聚合。 */

vi.mock('../api/tasks', () => ({ fetchTaskList: vi.fn() }))

const listMock = fetchTaskList as unknown as ReturnType<typeof vi.fn>

const task = (
  id: string,
  over: Partial<TaskSummary> = {},
): TaskSummary =>
  ({
    task_id: id,
    scene: 'borescope',
    mode: 'single',
    status: 'completed',
    capability_used: 'borescope_defect_detect',
    created_at: '2026-08-18T01:30:00Z',
    updated_at: '2026-08-18T01:40:00Z',
    finished_at: '2026-08-18T01:36:00Z',
    parent_task_id: null,
    ...over,
  }) as TaskSummary

const ROWS: TaskSummary[] = [
  task('QC-R1'),
  task('QC-R2', { scene: null, mode: 'joint' }),
  task('QC-R2-S1', { scene: 'dr', mode: 'joint', parent_task_id: 'QC-R2' }),
  task('QC-R3', { scene: 'metallography', mode: 'agent' }),
  task('QC-WAIT', { status: 'awaiting_review', finished_at: null }),
  task('QC-FAIL', { status: 'failed' }),
]

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/reports']}>
      <ReportsPage />
    </MemoryRouter>,
  )
}

beforeEach(() => {
  listMock.mockReset().mockResolvedValue(ROWS)
})

afterEach(() => {
  cleanup()
})

describe('ReportsPage（报告与综合研判，决策 7）', () => {
  it('grid-23：左栏报告清单，右栏综合研判承接 + 报告固定字段', async () => {
    renderPage()
    await screen.findByText('报告清单')
    const grid = document.querySelector('.grid-23')
    expect(grid).not.toBeNull()
    expect(grid?.textContent).toContain('报告清单')
    const stack = grid?.querySelector(':scope > .stack')
    expect(stack?.textContent).toContain('综合研判承接')
    expect(stack?.textContent).toContain('报告固定字段（方案 §7.4）')
  })

  it('清单只出接口真有数据的列：不渲染恒为「—」的结论摘要 / 能力 / 耗时列', async () => {
    renderPage()
    await screen.findByText('报告清单')
    const heads = Array.from(document.querySelectorAll('.report-list thead th')).map((n) =>
      (n.textContent ?? '').trim(),
    )
    expect(heads).toEqual(['任务 ID', '场景', '状态', '生成时间', '查看'])
    expect(heads).not.toContain('结论摘要')
    expect(heads).not.toContain('主力能力')
    expect(heads).not.toContain('耗时')
    // 生成时间取 finished_at（北京时间墙钟）
    expect(document.querySelector('.report-list tbody')?.textContent).toContain('08-18 09:36')
    // 联合子任务的父子承接用真实 parent_task_id 标注
    expect(document.querySelector('.report-list tbody')?.textContent).toContain('子')
  })

  it('清单 = 窗口内已完成任务；分页条只报「共 N 条」，窗口口径不再上页（决策 51-⑪）', async () => {
    renderPage()
    await screen.findByText('报告清单')
    const body = document.querySelector('.report-list tbody')
    expect(body?.textContent).toContain('QC-R1')
    expect(body?.textContent).toContain('QC-R2-S1')
    expect(body?.querySelectorAll('tr')).toHaveLength(4)
    expect(body?.textContent).not.toContain('QC-WAIT')
    expect(body?.textContent).not.toContain('QC-FAIL')
    expect(listMock).toHaveBeenCalledWith(100)
    const pager = document.querySelector('.report-list .pager')?.textContent ?? ''
    expect(pager).toContain('共')
    expect(pager).toContain('条')
    expect(pager).not.toContain('接口无 offset')
    expect(pager).not.toContain('窗口 =')
  })

  it('状态列呈现任务真实状态，不做报告四态 / 审批流', async () => {
    renderPage()
    await screen.findByText('报告清单')
    const firstRow = document.querySelector('.report-list tbody tr')
    expect(firstRow?.textContent).toContain('已完成')
    expect(screen.queryByText('草稿')).toBeNull()
    expect(screen.queryByText('已批准')).toBeNull()
    expect(screen.queryByText('待签发')).toBeNull()
    expect(document.querySelector('.report-list')?.textContent).toContain('报告四态无接口，不做')
  })

  it('综合研判承接：business_ref 缺口声明 + 三行真实聚合（可用/可用/待后端）', async () => {
    renderPage()
    await screen.findByText('综合研判承接')
    const card = Array.from(document.querySelectorAll('.card-title'))
      .find((n) => n.textContent?.includes('综合研判承接'))!
      .closest('.card') as HTMLElement
    expect(card.textContent).toContain('方案 §10.2 的跨任务综合研判本轮不建页面')
    expect(card.querySelector('code')?.textContent).toContain('business_ref')
    expect(card.textContent).toContain('G1-02')
    const rows = Array.from(card.querySelectorAll('.dcard'))
    expect(rows.map((r) => r.querySelector('.tag')?.textContent)).toEqual(['可用', '可用', '待后端'])
    expect(rows[0].textContent).toContain('mode=joint 父任务 + 子任务')
    expect(rows[1].textContent).toContain('同一任务的多次复核')
    expect(rows[2].textContent).toContain('需 business_ref 落库与查询接口')
    // 不建独立综合研判页：右栏无聚合入口按钮
    expect(card.querySelectorAll('button, a')).toHaveLength(0)
  })

  it('报告固定字段 = 9 个只读 chip（非链接、无跳转），并声明两处取数缺口', async () => {
    renderPage()
    await screen.findByText('报告固定字段（方案 §7.4）')
    const chips = Array.from(document.querySelectorAll('.report-fields .chip'))
    expect(chips.map((c) => c.textContent)).toEqual([
      '业务对象',
      '任务与样本',
      '工具/模型版本',
      '参数',
      '原始结果',
      '人工结论',
      '引用依据',
      'AI 建议',
      '签发记录',
    ])
    // 只读：非交互元素、无 title 之外的行为、页内无对应链接
    chips.forEach((c) => expect(c.tagName).toBe('SPAN'))
    expect(document.querySelector('.report-fields')?.querySelectorAll('a, button')).toHaveLength(0)
    const hint = document.querySelector('.report-fields .hint')
    expect(hint?.textContent).toContain('原始响应未落库')
    expect(hint?.textContent).toContain('模板版本')
  })

  it('轨别 Tab 只在窗口内客户端过滤 mode（真实字段），换 Tab 回到第一页', async () => {
    renderPage()
    await screen.findByText('报告清单')
    fireEvent.click(screen.getByRole('button', { name: '联合综合研判' }))
    await waitFor(() =>
      expect(document.querySelectorAll('.report-list tbody tr')).toHaveLength(2),
    )
    const body = document.querySelector('.report-list tbody')
    expect(body?.textContent).toContain('QC-R2')
    expect(body?.textContent).not.toContain('QC-R1')
    expect(body?.textContent).toContain('多场景') // scene=null 如实标注，不臆造场景
    fireEvent.click(screen.getByRole('button', { name: '全部' }))
    await waitFor(() =>
      expect(document.querySelectorAll('.report-list tbody tr')).toHaveLength(4),
    )
  })

  it('清单读取失败给恢复入口', async () => {
    listMock.mockRejectedValue(new Error('502 报告清单不可用'))
    renderPage()
    expect(await screen.findByText('502 报告清单不可用')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '重新读取' }))
    await waitFor(() => expect(listMock).toHaveBeenCalledTimes(2))
  })
})
