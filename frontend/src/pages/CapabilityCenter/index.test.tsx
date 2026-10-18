import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

/* 能力中心三页对齐结构原型后的冒烟测试：
 * 只断言结构与内置文案；实时数据经 api 模块 mock 注入（页面自身零 mock）。 */

const { fetchCapabilitiesMock, fetchCapabilityHealthMock } = vi.hoisted(() => ({
  fetchCapabilitiesMock: vi.fn(),
  fetchCapabilityHealthMock: vi.fn(),
}))

vi.mock('../../api/capabilities', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/capabilities')>()
  return { ...actual, fetchCapabilities: fetchCapabilitiesMock }
})
vi.mock('../../api/system', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/system')>()
  return { ...actual, fetchCapabilityHealth: fetchCapabilityHealthMock }
})

import { CapabilityGuidePage, CapabilityGeneralPage, CapabilityProPage } from './index'
import { useSystemStore } from '../../store/useSystemStore'
import type { CapabilitySummary } from '../../api/capabilities'

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
  note: '',
  ...over,
})

function renderPro() {
  return render(
    <MemoryRouter initialEntries={['/capability/pro']}>
      <CapabilityProPage />
    </MemoryRouter>,
  )
}

describe('CapabilityProPage（专业能力说明：卡片堆叠）', () => {
  beforeEach(() => {
    fetchCapabilitiesMock.mockReset()
    fetchCapabilityHealthMock.mockReset()
    fetchCapabilityHealthMock.mockResolvedValue([])
  })

  it('每能力一张卡 + 页头实时计数提示（就绪/降级），非表格', async () => {
    fetchCapabilitiesMock.mockResolvedValue([
      cap({}),
      cap({
        capability_id: 'cap-ir-01',
        tool_name: 'infrared-temp',
        display_name: '热像超温检测',
        scene: 'infrared',
        capability_type: 'measurement',
        status: 'maintenance',
      }),
    ])
    const { container } = renderPro()
    await waitFor(() => expect(fetchCapabilitiesMock).toHaveBeenCalled())

    expect(await screen.findByText('共 2 项 · 就绪 1 · 降级 1')).toBeInTheDocument()
    expect(container.querySelectorAll('table')).toHaveLength(0)
    const cards = container.querySelectorAll('.stack > .card')
    expect(cards).toHaveLength(2)

    // 三段内置文案 + 进入工作台链接（每卡各一组）
    expect(screen.getAllByText('检测对象与判定依据')).toHaveLength(2)
    expect(screen.getAllByText('可确认参数')).toHaveLength(2)
    expect(screen.getAllByText('输入要求与局限')).toHaveLength(2)
    expect(screen.getByText(/渗透显像后的表面开口缺陷/)).toBeInTheDocument()
    const links = screen.getAllByRole('link', { name: '进入工作台' })
    expect(links[0]).toHaveAttribute('href', '/detect/fpi')
    expect(links[1]).toHaveAttribute('href', '/detect/infrared')
  })

  it('冻结场景参数 chip 取自 paramContract；未冻结场景声明契约未冻结；页脚实时字段', async () => {
    fetchCapabilitiesMock.mockResolvedValue([
      cap({}),
      cap({
        capability_id: 'cap-ir-01',
        tool_name: 'infrared-temp',
        display_name: '热像超温检测',
        scene: 'infrared',
        capability_type: 'measurement',
        status: 'disabled',
        model_version: 'm2',
      }),
    ])
    fetchCapabilityHealthMock.mockResolvedValue([
      { tool_name: 'infrared-temp', healthy: false, message: '连接超时', latency_ms: null },
    ])
    renderPro()
    // fpi 契约已冻结 → 参数 chip（来自前端契约，不臆造）
    expect(await screen.findByText('置信度阈值')).toBeInTheDocument()
    expect(screen.getByText('单图最大检出数')).toBeInTheDocument()
    // infrared 契约草案 → 未冻结声明
    expect(screen.getByText(/专业参数契约未冻结/)).toBeInTheDocument()
    // 页脚实时字段来自 /capabilities 与 /capabilities/health
    expect(screen.getByText('infrared-temp')).toBeInTheDocument()
    expect(screen.getByText('m2')).toBeInTheDocument()
    expect(screen.getByText(/异常：连接超时/)).toBeInTheDocument()
    // 底部内置基线免责
    expect(screen.getByText(/内置基线/)).toBeInTheDocument()
    expect(screen.getByText(/不提供判据文字/)).toBeInTheDocument()
  })

  it('注册表为空如实声明，不放假数据', async () => {
    fetchCapabilitiesMock.mockResolvedValue([])
    renderPro()
    expect(await screen.findByText(/注册表为空/)).toBeInTheDocument()
    expect(document.querySelectorAll('.stack > .card')).toHaveLength(0)
  })
})

describe('CapabilityGeneralPage（通用能力说明：6 张卡）', () => {
  beforeEach(() => {
    useSystemStore.setState({ caps: null, ragflow: null, vlmAvailable: null })
  })

  it('条目集合、顺序、头部状态标签与页脚端点行与原型一致', () => {
    const { container } = render(
      <MemoryRouter>
        <CapabilityGeneralPage />
      </MemoryRouter>,
    )
    const titles = [...container.querySelectorAll('.dcard-t')].map((n) => n.textContent)
    expect(titles).toEqual([
      '智能助手对话',
      'AI 解释与依据',
      '知识检索与引用',
      '报告生成',
      '任务编排',
      '大模型降级',
    ])
    const tags = [...container.querySelectorAll('.dcard .tag')].map((n) => n.textContent)
    expect(tags).toEqual(['核心', '核心', '可用', '受约束', '可用', '受约束'])
    expect(screen.getByText('POST /sessions/{id}/messages')).toBeInTheDocument()
    expect(screen.getByText('GET /reports/{task_id}')).toBeInTheDocument()
    expect(screen.getByText('POST /tasks')).toBeInTheDocument()
    expect(screen.getByText('GET /health → llm')).toBeInTheDocument()
  })

  it('知识检索卡按 ragflow 探测结果给实时可用性', () => {
    useSystemStore.setState({
      ragflow: { configured: true, reachable: true, doc_count: 12, latest_doc_time: null },
      vlmAvailable: true,
      caps: [],
    })
    render(
      <MemoryRouter>
        <CapabilityGeneralPage />
      </MemoryRouter>,
    )
    expect(screen.getAllByText('可用').length).toBeGreaterThanOrEqual(2)
    expect(screen.getByText('已配置 · 索引 12 篇')).toBeInTheDocument()
    expect(screen.getByText('VLM 可用：降级路径待命')).toBeInTheDocument()
  })
})

describe('CapabilityGuidePage（样例与指南：3 组内置指引）', () => {
  it('渲染三组内置指引，不含快捷键内容', () => {
    const { container } = render(
      <MemoryRouter>
        <CapabilityGuidePage />
      </MemoryRouter>,
    )
    expect(screen.getByText('标准检测流程（五步）')).toBeInTheDocument()
    expect(screen.getByText('判定与置信度口径')).toBeInTheDocument()
    expect(screen.getByText('结果与报告约束')).toBeInTheDocument()
    expect(screen.getByText(/① 选择入口/)).toBeInTheDocument()
    expect(screen.getByText(/联合研判不相加各专业置信度/)).toBeInTheDocument()
    expect(container.querySelectorAll('kbd')).toHaveLength(0)
  })
})
