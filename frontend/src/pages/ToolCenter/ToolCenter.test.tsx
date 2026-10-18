import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

/* 工具注册与版本（合并后单页，决策 24 + 批次 3 写侧 决策 27–50）：
 * 列集合 / 两个写入口 / 行内启停确认 / 门禁未过不可保存 / 掩码回显 / 引用窗口口径 / 底部壳说明。
 * 页面零 mock：能力、任务窗口与写侧接口经 api 模块注入。 */

const {
  fetchCapabilitiesMock,
  fetchCapabilityHealthMock,
  fetchTaskListMock,
  fetchCapabilityDetailMock,
  patchCapabilityMock,
  dryRunCapabilityMock,
  setCapabilityStatusMock,
} = vi.hoisted(() => ({
  fetchCapabilitiesMock: vi.fn(),
  fetchCapabilityHealthMock: vi.fn(),
  fetchTaskListMock: vi.fn(),
  fetchCapabilityDetailMock: vi.fn(),
  patchCapabilityMock: vi.fn(),
  dryRunCapabilityMock: vi.fn(),
  setCapabilityStatusMock: vi.fn(),
}))

vi.mock('../../api/capabilities', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/capabilities')>()
  return {
    ...actual,
    fetchCapabilities: fetchCapabilitiesMock,
    reloadCapabilities: vi.fn(),
    fetchCapabilityDetail: fetchCapabilityDetailMock,
    patchCapability: patchCapabilityMock,
    dryRunCapability: dryRunCapabilityMock,
    setCapabilityStatus: setCapabilityStatusMock,
  }
})
vi.mock('../../api/system', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/system')>()
  return { ...actual, fetchCapabilityHealth: fetchCapabilityHealthMock }
})
vi.mock('../../api/tasks', () => ({ fetchTaskList: fetchTaskListMock }))

import { ToolRegistryPage } from './index'
import type { CapabilityDetail, CapabilitySummary } from '../../api/capabilities'
import type { CapabilityHealth } from '../../api/system'
import type { TaskSummary } from '../../api/types'

const cap = (over: Partial<CapabilitySummary>): CapabilitySummary => ({
  capability_id: 'cap-fpi-01',
  tool_name: 'fpi-detect',
  tool_version: 'v1',
  model_version: 'm1',
  display_name: '渗透检测·表面开口缺陷',
  scene: 'fpi',
  capability_type: 'defect_detection',
  adapter_type: 'http',
  status: 'enabled',
  note: '显像后的开口缺陷',
  ...over,
})

const caps: CapabilitySummary[] = [
  cap({}),
  cap({
    capability_id: 'cap-ir-01',
    tool_name: 'infrared-temp',
    tool_version: 'v2',
    model_version: 'm2',
    display_name: '热像超温检测',
    scene: 'infrared',
    capability_type: 'measurement',
    adapter_type: 'model_service',
    status: 'maintenance',
    note: '',
  }),
]

const detail = (over: Partial<CapabilityDetail>): CapabilityDetail => ({
  capability_id: 'cap-fpi-01',
  tool_name: 'fpi-detect',
  scene: 'fpi',
  capability_type: 'defect_detection',
  adapter_type: 'http',
  endpoint: 'http://127.0.0.1:18016',
  health_endpoint: null,
  tool_version: 'v1',
  model_version: 'm1',
  api_version: 'v1',
  input_schema: null,
  output_schema: { code: 0, result: { status: 'unqualified', defects: [] } },
  timeout_s: 60,
  max_retry: 2,
  status: 'enabled',
  description: '渗透检测·表面开口缺陷',
  model_weights_sha256: null,
  meta: {
    default_params: { part_id: 'P-1' },
    input_required: { image_field: 'image' },
    note: '显像后的开口缺陷',
  },
  headers_masked: { 'X-API-Key': '****abcd' },
  change_log: [
    {
      at: '2026-09-28T10:00:00+00:00',
      action: 'update',
      operator: 'qcadmin',
      reason: '端点由 8002 迁至 18016',
      fields: ['endpoint'],
    },
  ],
  version: 21,
  snapshot: 'e63c24f34d07e1a4',
  editable: true,
  ...over,
})

const health: CapabilityHealth[] = [
  { tool_name: 'fpi-detect', healthy: true, message: 'ok', latency_ms: 12 },
  { tool_name: 'infrared-temp', healthy: false, message: 'timeout', latency_ms: null },
]

const task = (over: Partial<TaskSummary>): TaskSummary => ({
  task_id: 'T-1', scene: 'fpi', mode: 'single', status: 'completed', capability_used: 'fpi-detect',
  created_at: '2026-09-01T00:00:00Z', updated_at: '2026-09-01T00:00:00Z', finished_at: null,
  ...over,
})

const tableHeaders = () =>
  screen.getAllByRole('columnheader').map((th) => th.textContent?.trim() ?? '')

function renderPage() {
  return render(
    <MemoryRouter>
      <ToolRegistryPage />
    </MemoryRouter>,
  )
}

const type = (el: HTMLElement, value: string) =>
  fireEvent.change(el, { target: { value } })

/** 打开首行的编辑表单（决策 44：行内「编辑」与页头「添加工具」共用同一个 Modal） */
async function openEditor() {
  renderPage()
  await waitFor(() => expect(fetchTaskListMock).toHaveBeenCalled())
  fireEvent.click(screen.getAllByText('编辑')[0])
  await waitFor(() => expect(fetchCapabilityDetailMock).toHaveBeenCalledWith('cap-fpi-01'))
  return screen.findByDisplayValue('http://127.0.0.1:18016')
}

describe('ToolRegistryPage（工具注册与版本 · 单页）', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    fetchCapabilitiesMock.mockResolvedValue(caps)
    fetchCapabilityHealthMock.mockResolvedValue(health)
    fetchCapabilityDetailMock.mockResolvedValue(detail({}))
    patchCapabilityMock.mockResolvedValue({
      capability_id: 'cap-fpi-01',
      fields: ['endpoint'],
      capabilities: 6,
    })
    dryRunCapabilityMock.mockResolvedValue({
      passed: true,
      steps: [
        { name: 'health', ok: true, message: 'HTTP 200，12ms', latency_ms: 12 },
        { name: 'inference', ok: true, message: 'HTTP 200，8548ms', latency_ms: 8548 },
        { name: 'artifacts', ok: true, message: '4 个产物范围 GET 通过', latency_ms: null },
      ],
      sample: { code: 0, result: { status: 'unqualified', defects: [] }, artifacts: [] },
      gate_errors: [],
      snapshot: 'e63c24f34d07e1a4',
    })
    setCapabilityStatusMock.mockResolvedValue({
      capability_id: 'cap-fpi-01',
      status: 'disabled',
      capabilities: 6,
    })
    fetchTaskListMock.mockResolvedValue([
      task({}),
      task({ task_id: 'T-2' }),
      task({ task_id: 'T-3', capability_used: 'infrared-temp' }),
      task({ task_id: 'T-4', capability_used: null }),
    ])
  })

  it('注册与版本并排一张表，操作列在「健康」与「说明」之间', async () => {
    renderPage()
    await waitFor(() => expect(fetchTaskListMock).toHaveBeenCalledWith(100))
    expect(tableHeaders()).toEqual([
      '工具', '专业', '类型', '适配器', '工具版本', '模型版本', '近 100 条任务引用', '健康', '操作', '说明',
    ])
    // 台账行只认工具身份（tool_name + 能力 ID）；简介属工作台/首页，不进这张表（决策 51）
    expect(screen.getByText('fpi-detect')).toBeInTheDocument()
    expect(screen.getByText('cap-fpi-01')).toBeInTheDocument()
    expect(screen.queryByText('渗透检测·表面开口缺陷')).not.toBeInTheDocument()
    expect(screen.getByText('显像后的开口缺陷')).toBeInTheDocument()
    expect(screen.getByText('2 项')).toBeInTheDocument()
    expect(screen.getByText('1 项')).toBeInTheDocument()
  })

  it('健康列一状态一色、统一 .tag 胶囊，停用/维护不冒充探测结果（决策 51-⑦）', async () => {
    const { container } = renderPage()
    await waitFor(() => expect(fetchTaskListMock).toHaveBeenCalled())
    const pills = Array.from(container.querySelectorAll('.ant-table-tbody .tag')).map(
      (t) => `${t.textContent?.trim()}|${t.className}`,
    )
    expect(pills).toEqual(
      expect.arrayContaining(['健康 12ms|tag t-success', '维护中|tag t-review']),
    )
    // 不再混用第二套 antd Tag：一列只允许一种胶囊体系
    expect(container.querySelectorAll('.ant-table-tbody .ant-tag')).toHaveLength(0)
  })

  it('两个写入口：页头「添加工具」+ 每行「编辑」，另保留手工重载（决策 44）', async () => {
    renderPage()
    await waitFor(() => expect(fetchCapabilitiesMock).toHaveBeenCalled())
    expect(screen.getByRole('button', { name: /重新加载能力注册表/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '添加工具' })).toBeInTheDocument()
    expect(screen.getAllByText('编辑')).toHaveLength(2)
    expect(screen.getByText('工具注册与版本')).toBeInTheDocument()
  })

  it('行内启停：停用需填变更原因并二次确认，确认文案写明卸载后果（决策 45）', async () => {
    renderPage()
    await waitFor(() => expect(fetchCapabilitiesMock).toHaveBeenCalled())
    fireEvent.click(screen.getByText('停用'))
    expect(await screen.findByText(/停用即从 ToolRegistry 卸载/)).toBeInTheDocument()
    const confirm = screen.getByRole('button', { name: /确认停用/ })
    expect(confirm).toBeDisabled()
    type(screen.getByPlaceholderText(/现场检修/), '现场检修需暂时下线')
    fireEvent.click(confirm)
    await waitFor(() =>
      expect(setCapabilityStatusMock).toHaveBeenCalledWith('cap-fpi-01', {
        status: 'disabled',
        change_reason: '现场检修需暂时下线',
        operator: null,
      }),
    )
  })

  it('门禁未过不可保存：改了端点未跑完整试运行 → 保存禁用；成功试运行后放开（决策 27/41）', async () => {
    const endpoint = await openEditor()
    type(endpoint, 'http://127.0.0.1:19999')
    // 掩码值不回传明文：改端点时必须重填请求头，否则回执无法与提交快照对齐
    type(screen.getByPlaceholderText('X-API-Key: ****abcd'), 'new-secret')
    type(screen.getByPlaceholderText(/厂商交付新模型版本/), '换端点联调')

    const save = screen.getByRole('button', { name: '保存' })
    expect(save).toBeDisabled()
    expect(screen.getByRole('status')).toHaveTextContent('完整试运行')

    type(screen.getByPlaceholderText(/local-tools\/fluorescence/), 'local-tools/fluorescence/server-bundle/test01.jpg')
    fireEvent.click(screen.getByRole('button', { name: '完整试运行' }))
    await waitFor(() => expect(dryRunCapabilityMock).toHaveBeenCalled())
    expect(dryRunCapabilityMock.mock.calls[0][0]).toMatchObject({
      endpoint: 'http://127.0.0.1:19999',
      headers: { 'X-API-Key': 'new-secret' },
      image_path: 'local-tools/fluorescence/server-bundle/test01.jpg',
    })
    await waitFor(() => expect(screen.getByRole('button', { name: '保存' })).toBeEnabled())
  })

  it('编辑态回显掩码：读方向只给键名 + 末 4 位，明文不落表单（决策 43）', async () => {
    await openEditor()
    const masked = await screen.findByPlaceholderText('X-API-Key: ****abcd')
    expect(masked).toHaveValue('')
    expect(screen.getByText(/掩码值不回传/)).toBeInTheDocument()
    expect(screen.getByText(/端点由 8002 迁至 18016/)).toBeInTheDocument()
  })

  it('口径脚注保留、长公告已移出页面；底部「版本登记与退役」壳仍零按钮（决策 9/46/51-⑦）', async () => {
    const { container } = renderPage()
    await waitFor(() => expect(fetchTaskListMock).toHaveBeenCalled())
    expect(container).toHaveTextContent('最近 100 条任务')
    expect(container).toHaveTextContent('不是全量历史')
    expect(container).toHaveTextContent('未登记 Schema 版本的工具拒绝联调（方案 §9.1）')
    // 两段页面级长文（写侧口径 / 注册工具 ≠ 新增工作台）移入 docs，页面只留就近提示
    expect(container).not.toHaveTextContent('写侧口径')
    expect(container).not.toHaveTextContent('注册工具 ≠ 新增工作台')
    expect(screen.getByText('版本登记与退役')).toBeInTheDocument()
    const shell = screen.getByText('版本登记与退役').closest('.shell-page') as HTMLElement
    expect(shell).toHaveTextContent('G3-04 模型发布与回滚 · G3-05 版本切换与回滚')
    expect(shell).toHaveTextContent('建设方案 §8.1 工具服务中心对象表 · §9.1 工具侧统一接口基线')
    expect(shell.querySelectorAll('button')).toHaveLength(0)
  })

  it('任务窗口读取失败：给出重读入口，不填 0', async () => {
    fetchTaskListMock.mockRejectedValue(new Error('网络中断'))
    renderPage()
    expect(await screen.findByText(/任务窗口读取失败：网络中断/)).toBeInTheDocument()
  })
})
