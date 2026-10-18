import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import WorkbenchPage from './WorkbenchPage'
import type { CapabilitySummary } from '../api/capabilities'
import type { TaskSummary } from '../api/types'

const { navigateSpy, submitTaskMock } = vi.hoisted(() => ({
  navigateSpy: vi.fn(),
  submitTaskMock: vi.fn(),
}))

vi.mock('react-router-dom', async (importOriginal) => {
  const actual = await importOriginal<typeof import('react-router-dom')>()
  return { ...actual, useNavigate: () => navigateSpy }
})
vi.mock('../api/tasks', () => ({ submitTask: submitTaskMock }))

/** 注册表（GET /capabilities）与任务窗口（GET /tasks?limit=100）的可控替身：
 *  页面在渲染期读取这两个变量，故用例内重新赋值即刻生效。 */
let capList: CapabilitySummary[] = []
let capsFlags: { loading: boolean; error: string | null } = { loading: false, error: null }
vi.mock('../hooks/useCapabilities', () => ({
  useCapabilities: () => ({
    caps: capList,
    health: null,
    loading: capsFlags.loading,
    error: capsFlags.error,
    refreshing: false,
    refresh: vi.fn(),
    reload: vi.fn(),
  }),
}))

let taskList: TaskSummary[] = []
const fetchListSpy = vi.fn(async () => {})
vi.mock('../store/useTaskStore', () => ({
  useTaskStore: (s: (st: unknown) => unknown) =>
    s({ tasks: taskList, loading: false, error: null, fetchList: fetchListSpy }),
}))

const cap = (over: Partial<CapabilitySummary> = {}): CapabilitySummary =>
  ({
    capability_id: 'fpi_defect_detection_v1',
    tool_name: 'fpi_defect_detection',
    tool_version: '1.0.0',
    model_version: '1.0.0',
    display_name: '荧光渗透缺陷检测',
    scene: 'fpi',
    capability_type: 'defect_detection',
    adapter_type: 'http',
    status: 'enabled',
    note: '联调用 Mock 服务',
    ...over,
  }) as CapabilitySummary

const task = (over: Partial<TaskSummary> = {}): TaskSummary =>
  ({
    task_id: 'T1',
    status: 'completed',
    scene: 'fpi',
    created_at: '2026-09-20T10:00:00',
    updated_at: '2026-09-20T10:02:00',
    finished_at: '2026-09-20T10:02:00',
    mode: 'single',
    ...over,
  }) as TaskSummary

function renderAt(path = '/detect/fpi') {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/detect/:scene" element={<WorkbenchPage />} />
      </Routes>
    </MemoryRouter>,
  )
}

function upload(container: HTMLElement, idx: number, files: File[]) {
  const inputs = container.querySelectorAll<HTMLInputElement>('input[type="file"]')
  fireEvent.change(inputs[idx], { target: { files } })
}

const hasFileRow = (container: HTMLElement, name: string) =>
  Array.from(container.querySelectorAll('.fname')).some((n) => n.textContent === name)

const taskIds = (container: HTMLElement) =>
  Array.from(container.querySelectorAll('.tbl.compact .task-id')).map((n) => n.textContent)

const sceneBar = (container: HTMLElement) => container.querySelector<HTMLElement>('.scenebar')!

describe('WorkbenchPage', () => {
  beforeEach(() => {
    submitTaskMock.mockReset()
    submitTaskMock.mockResolvedValue({ task_id: 'T-NEW', mode: 'single' })
    navigateSpy.mockReset()
    fetchListSpy.mockClear()
    capList = [cap()]
    capsFlags = { loading: false, error: null }
    taskList = [task({})]
  })

  it('未知场景 → 404 壳，不渲染上传/参数/提交卡', () => {
    const { container } = renderAt('/detect/nope')
    expect(screen.getByText(/页面不存在/)).toBeInTheDocument()
    expect(container.querySelector('.scenebar')).toBeNull()
    expect(screen.queryByText('样本上传')).not.toBeInTheDocument()
  })

  it('场景头卡字段全取注册表真实值：能力 ID@版本、模型、就绪态、能力类型、执行链', () => {
    const { container } = renderAt()
    const bar = sceneBar(container)
    expect(within(bar).getByText('荧光渗透检测')).toBeInTheDocument()
    expect(within(bar).getByText('FPI', { selector: '.tag' })).toBeInTheDocument()
    expect(within(bar).getByText('能力就绪')).toBeInTheDocument()
    // 首行只呈现工具简介（description）；台账 note 的技术术语不外泄给运营
    expect(within(bar).getByText('荧光渗透缺陷检测。')).toBeInTheDocument()
    expect(within(bar).queryByText(/联调用 Mock 服务/)).not.toBeInTheDocument()
    expect(within(bar).getByText('fpi_defect_detection_v1@1.0.0')).toBeInTheDocument()
    expect(within(bar).getByText('1.0.0')).toBeInTheDocument()
    expect(within(bar).getByText('参数契约已冻结')).toBeInTheDocument()
    expect(within(bar).getByText('能力类型 缺陷检测')).toBeInTheDocument()
    expect(within(bar).getByText('专业依据与判定标准').closest('a')).toHaveAttribute('href', '/capability/pro')
    // 真实执行链（app/agents/graph.py）：不是「调工具 → 出报告」
    expect(within(bar).getByText(/能力选择 → 输入校验 → 调用工具/)).toBeInTheDocument()
    expect(within(bar).getByText(/停在待复核/)).toBeInTheDocument()
  })

  it('决策 51-⑪：页内场景切换行已移除，六个工作台只由侧栏与场景卡可达', () => {
    const { container } = renderAt('/detect/dr')
    expect(container.querySelector('.scene-switch')).toBeNull()
    expect(container.querySelector('nav[aria-label="专业工作台场景切换"]')).toBeNull()
  })

  it('注册表无该场景工具 → 标未注册，不编造能力 ID 与版本', () => {
    capList = []
    const { container } = renderAt()
    const bar = sceneBar(container)
    expect(within(bar).getByText('未注册')).toBeInTheDocument()
    expect(within(bar).getByText('注册表内该场景暂无已登记工具')).toBeInTheDocument()
    expect(within(bar).getByText('能力类型 —')).toBeInTheDocument()
    expect(within(bar).getByText(/不编造能力 ID 与版本/)).toBeInTheDocument()
    expect(bar.textContent).toContain('模型 —')
    expect(bar.textContent).not.toMatch(/@\d/)
  })

  it('注册表读取中与读取失败分档陈述，都不当成「能力降级」', () => {
    capList = []
    capsFlags = { loading: true, error: null }
    const { container } = renderAt()
    const bar = sceneBar(container)
    expect(within(bar).getByText('能力读取中')).toBeInTheDocument()
    expect(within(bar).getByText('能力注册表读取中…')).toBeInTheDocument()
    expect(within(bar).queryByText(/暂无已登记工具/)).not.toBeInTheDocument()
  })

  it('读取失败：头卡与提交摘要同口径声明失败原因', () => {
    capList = []
    capsFlags = { loading: false, error: '注册表 500' }
    const { container } = renderAt()
    const bar = sceneBar(container)
    expect(within(bar).getByText('能力状态未知')).toBeInTheDocument()
    expect(within(bar).getByText('能力注册表读取失败')).toBeInTheDocument()
    expect(bar.textContent).toContain('能力注册表读取失败：注册表 500')
    expect(container.querySelector('.sum-row b')!.textContent).toBe('能力注册表读取失败')
  })

  it('非 enabled 状态写「能力降级 · 维护中」，不冒充就绪', () => {
    capList = [cap({ status: 'maintenance' })]
    const { container } = renderAt()
    const bar = sceneBar(container)
    expect(within(bar).getByText('能力降级 · 维护中')).toBeInTheDocument()
    expect(within(bar).queryByText('能力就绪')).not.toBeInTheDocument()
  })

  it('同场景多工具：声明工具按注册存在并指向台账，工作台仍只有六个', () => {
    capList = [cap(), cap({ capability_id: 'fpi_defect_detection_v2', tool_version: '2.0.0' })]
    const { container } = renderAt()
    expect(sceneBar(container).textContent).toContain('本场景在注册表内挂了 2 个工具')
    expect(sceneBar(container).textContent).toContain('注册新工具不会新增工作台')
    expect(container.querySelector('a[href="/tool/registry"]')).not.toBeNull()
    // 六个工作台的可达性由侧栏与场景卡断言（HomePage / SettingsPage 用例），本页不再自带切换行
    expect(container.querySelectorAll('.scene-switch')).toHaveLength(0)
  })

  it('冻结契约：参数表 + 「本次将提交的 parameters」预览等于实际下发载荷（只含改动项）', () => {
    const { container } = renderAt()
    expect(container.querySelector('.param-tbl')).not.toBeNull()
    const preview = container.querySelector('details.trace pre.json')!
    expect(preview.textContent).toBe('{}')
    expect(screen.getByText(/不发送 parameters 字段/)).toBeInTheDocument()

    fireEvent.change(container.querySelector<HTMLInputElement>('input[type="number"]')!, {
      target: { value: '0.8' },
    })
    expect(preview.textContent).toContain('"confidence_threshold": 0.8')
    expect(preview.textContent).not.toContain('max_defects')
    expect(screen.getByText(/以上 1 项为改动项/)).toBeInTheDocument()
  })

  it('提交：场景与模式锁定，成功后按原型进任务详情（不用弹窗挡跳转）', async () => {
    const { container } = renderAt()
    upload(container, 0, [new File(['x'], 'a.jpg')])
    await waitFor(() => expect(hasFileRow(container, 'a.jpg')).toBe(true))
    expect(screen.getByText('格式与大小校验通过')).toBeInTheDocument()
    expect(container.querySelector('.sum-row b.num')!.textContent).toBe('1')

    fireEvent.click(screen.getByText('发起荧光渗透检测任务'))
    await waitFor(() => expect(submitTaskMock).toHaveBeenCalledTimes(1))
    const arg = submitTaskMock.mock.calls[0][0]
    expect(arg.scene).toBe('fpi')
    expect(arg.mode).toBe('single')
    expect(arg.scenes).toEqual(['fpi'])
    expect(arg.parameters).toEqual({})
    expect(arg.files).toHaveLength(1)
    await waitFor(() => expect(navigateSpy).toHaveBeenCalledWith('/tasks/T-NEW'))
    expect(screen.queryByText('任务已提交，正在排队推理')).not.toBeInTheDocument()
  })

  it('改过的参数才进 parameters；越界值就近报错并拦截提交', async () => {
    const { container } = renderAt()
    upload(container, 0, [new File(['x'], 'a.jpg')])
    const threshold = container.querySelector<HTMLInputElement>('input[type="number"]')!
    fireEvent.change(threshold, { target: { value: '1.5' } })
    expect(await screen.findByText(/不得大于 1/)).toBeInTheDocument()
    fireEvent.click(screen.getByText('发起荧光渗透检测任务'))
    await waitFor(() => expect(submitTaskMock).not.toHaveBeenCalled())

    fireEvent.change(threshold, { target: { value: '0.8' } })
    fireEvent.click(screen.getByText('发起荧光渗透检测任务'))
    await waitFor(() => expect(submitTaskMock).toHaveBeenCalledTimes(1))
    expect(submitTaskMock.mock.calls[0][0].parameters).toEqual({ confidence_threshold: 0.8 })
  })

  it('未冻结契约（热部图像）：不给参数表也不给载荷预览，只声明未冻结 + 禁用字段与理由', () => {
    const { container } = renderAt('/detect/infrared')
    expect(container.querySelector('.param-tbl')).toBeNull()
    expect(container.querySelector('details.trace')).toBeNull()
    expect(container.querySelector('.notice')!.textContent).toContain('尚未冻结')
    expect(within(container.querySelector('.scene-chips')!).getByText('参数契约未冻结')).toBeInTheDocument()
    expect(screen.getByText(/前端不预先编造字段/)).toBeInTheDocument()
    expect(screen.getByText('禁用原因：该参数未在已冻结 Schema 中登记')).toBeInTheDocument()
    expect(container.querySelectorAll('.input[disabled]')).toHaveLength(2)
    // 原型 §7.2 的六个参数字段无接口来源（/capabilities 不返回 input_schema），页面不得出现
    for (const invented of [
      '检测标准',
      '缺陷类别勾选',
      '最小关注尺寸',
      '测量基准',
      '报告模板',
      '任务备注',
      'EN 5152',
    ]) {
      expect(container.textContent).not.toContain(invented)
    }
    // 联动块按场景展开：主上传 + 辐射值 CSV
    expect(container.querySelectorAll('input[type="file"]')).toHaveLength(2)
  })

  it('未冻结场景提交：parameters 恒为 null（不发该字段），成功后同样跳详情', async () => {
    const { container } = renderAt('/detect/infrared')
    upload(container, 0, [new File(['x'], 'a.jpg')])
    upload(container, 1, [new File(['t,c\n1,2'], 'rad.csv')])
    await waitFor(() => expect(hasFileRow(container, 'rad.csv')).toBe(true))
    fireEvent.click(screen.getByText('发起热部图像检测任务'))
    await waitFor(() => expect(submitTaskMock).toHaveBeenCalledTimes(1))
    expect(submitTaskMock.mock.calls[0][0].parameters).toBeNull()
    await waitFor(() => expect(navigateSpy).toHaveBeenCalledWith('/tasks/T-NEW'))
  })

  it('前置校验拦截：无主输入 / 缺辐射值 CSV 都不发请求', async () => {
    const { container } = renderAt('/detect/infrared')
    fireEvent.click(screen.getByText('发起热部图像检测任务'))
    expect(await screen.findByText(/请上传检测图像或视频/)).toBeInTheDocument()
    await waitFor(() => expect(submitTaskMock).not.toHaveBeenCalled())

    upload(container, 0, [new File(['x'], 'a.jpg')])
    expect(within(container).getByText(/缺辐射值 CSV/)).toBeInTheDocument()
    fireEvent.click(screen.getByText('发起热部图像检测任务'))
    await waitFor(() => expect(submitTaskMock).not.toHaveBeenCalled())
  })

  it('本场景最近任务：按场景过滤 + 窗口内每页 5 行 + 紧凑三列（耗时右对齐）', async () => {
    taskList = [
      ...Array.from({ length: 12 }, (_, i) => task({ task_id: `F${i + 1}` })),
      task({ task_id: 'B1', scene: 'borescope' }),
    ]
    const { container } = renderAt()
    expect(taskIds(container)).toEqual(['F1', 'F2', 'F3', 'F4', 'F5'])
    expect(container.textContent).not.toContain('B1')
    expect(container.querySelector('.tbl.compact')).not.toBeNull()
    expect(container.querySelector('.tbl.compact td.cost')).not.toBeNull()

    fireEvent.click(within(container.querySelector('.pager')!).getByText('2'))
    await waitFor(() => expect(taskIds(container)).toEqual(['F6', 'F7', 'F8', 'F9', 'F10']))
    // 表尾口径注与分页条的「本场景窗口内」一并移出（决策 51-⑪）：分页条只报「共 N 条」，口径在边界文档表 A/B
    expect(container.querySelector('.pager')!.textContent).not.toContain('本场景窗口内')
    expect(container.querySelector('.recent-foot')).toBeNull()
    expect(container.textContent).not.toContain('窄容器用紧凑三列布局')
    expect(container.querySelector('a[href="/tasks"]')).not.toBeNull()
  })

  it('窗口内无本场景任务：空态而非假行；窗口未取到时去拉 GET /tasks', () => {
    taskList = []
    const { container } = renderAt()
    expect(container.querySelector('.tbl.compact')).toBeNull()
    expect(container.querySelector('.empty')!.textContent).toContain('窗口内暂无荧光渗透检测任务')
    expect(container.querySelector('.pinfo')!.textContent).toContain('共')
    expect(fetchListSpy).toHaveBeenCalled()
  })

  it('提交卡口径：自动进详情 + GET /tasks/{id} 轮询 + 幂等待补项', () => {
    const { container } = renderAt()
    const info = Array.from(container.querySelectorAll('.notice')).find((n) =>
      n.textContent?.includes('自动进入任务详情'),
    )!
    expect(info.textContent).toContain('GET /tasks/{id}')
    expect(container.textContent).toContain('Idempotency-Key')
    expect(container.textContent).toMatch(/重复点击会产生新任务/)
    expect(container.querySelector('.sum-row b.num')!.textContent).toBe('0')
    expect(container.textContent).toContain('单场景')
  })

  it('卡序与原型一致：1 样本上传 → 2 专业参数（左）｜3 提交 → 本场景最近任务（右）', () => {
    const { container } = renderAt()
    expect([...container.querySelectorAll('.card-title')].map((t) => t.textContent)).toEqual([
      '1样本上传INPUT ASSETS',
      '2专业参数PARAMETERS',
      '3提交SUBMIT',
      '本场景最近任务RECENT',
    ])
  })

  it('无 mock 数据：原型占位样本名 / 假体积 / 假分辨率 / 假 KPI 不出现', () => {
    const { container } = renderAt()
    const text = container.textContent!
    for (const fake of ['sample_', '2.4 MB', '8.1 MB', '2048×1536', '4096×3072', '✓ 校验通过', '哈希']) {
      expect(text).not.toContain(fake)
    }
    expect(container.querySelectorAll('.file-row')).toHaveLength(0)
  })
})
