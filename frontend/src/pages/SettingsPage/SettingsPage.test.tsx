import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

/* 运行监控页（决策 11：/settings 拆解后的运行状态部分）。
 * 页面零 mock：所有实时数据经 api 模块注入；无接口的子系统断言为「—」+ 诚实说明。 */

const { fetchCapabilitiesMock, fetchCapabilityHealthMock, fetchRagflowMock, fetchServerDateMock, fetchTaskStatsMock } =
  vi.hoisted(() => ({
    fetchCapabilitiesMock: vi.fn(),
    fetchCapabilityHealthMock: vi.fn(),
    fetchRagflowMock: vi.fn(),
    fetchServerDateMock: vi.fn(),
    fetchTaskStatsMock: vi.fn(),
  }))

vi.mock('../../api/capabilities', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/capabilities')>()
  return { ...actual, fetchCapabilities: fetchCapabilitiesMock, reloadCapabilities: vi.fn() }
})
vi.mock('../../api/system', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/system')>()
  return {
    ...actual,
    fetchCapabilityHealth: fetchCapabilityHealthMock,
    fetchRagflowStatus: fetchRagflowMock,
    fetchServerDate: fetchServerDateMock,
  }
})
vi.mock('../../api/tasks', () => ({ fetchTaskStats: fetchTaskStatsMock }))

import SettingsPage from './index'
import { useSystemStore } from '../../store/useSystemStore'
import type { CapabilitySummary } from '../../api/capabilities'
import type { CapabilityHealth, RagflowStatus, ServerDate } from '../../api/system'
import type { TaskStats } from '../../api/types'

const caps: CapabilityHealth[] = [
  { tool_name: 'fpi-detect', healthy: true, message: 'ok', latency_ms: 12 },
  { tool_name: 'infrared-temp', healthy: false, message: 'timeout', latency_ms: null },
]
const ragflow: RagflowStatus = {
  configured: true, reachable: true, doc_count: 2847, latest_doc_time: '2026-09-01', message: 'ok',
}
const health: ServerDate & { status?: string; database?: string } = {
  server_date: '2026-09-09', vlm_available: true, status: 'healthy', database: 'ok',
}
const stats: TaskStats = {
  total: 12, today: 2, pending: 0, running: 1, awaiting_review: 3, completed: 8, failed: 0, canceled: 0,
}
const registry: CapabilitySummary[] = [
  {
    capability_id: 'cap-fpi-01', tool_name: 'fpi-detect', tool_version: 'v1', model_version: 'm1',
    display_name: '内窥镜拍到的叶片画面，找裂纹、烧蚀、腐蚀与孔隙并框出位置', scene: 'fpi', capability_type: 'defect_detection',
    adapter_type: 'http', status: 'enabled', note: '',
  },
  {
    capability_id: 'cap-ir-01', tool_name: 'infrared-temp', tool_version: 'v2', model_version: 'm2',
    display_name: '热像超温检测', scene: 'infrared', capability_type: 'measurement',
    adapter_type: 'model_service', status: 'maintenance', note: '测温标定复核中',
  },
]

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/admin/monitor']}>
      <SettingsPage />
    </MemoryRouter>,
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  fetchCapabilitiesMock.mockResolvedValue(registry)
  fetchCapabilityHealthMock.mockResolvedValue(caps)
  fetchRagflowMock.mockResolvedValue(ragflow)
  fetchServerDateMock.mockResolvedValue(health)
  fetchTaskStatsMock.mockResolvedValue(stats)
  // 真实 store 的 probe 会覆盖注入状态，替换为 noop 后按用例显式驱动
  useSystemStore.setState({ caps: null, ragflow: null, vlmAvailable: null, probe: async () => {} } as never)
})

describe('SettingsPage（运行监控）', () => {
  it('六张状态卡 + 等宽端点行；对象存储无接口时显示「—」并注明不估算', async () => {
    const { container } = renderPage()
    expect(await screen.findByText('后端服务')).toBeInTheDocument()
    expect(container.querySelectorAll('.grid-12 > .card')).toHaveLength(6)
    for (const name of ['后端服务', '推理集群（VLM 多模态）', '对象存储', '知识检索 RAGFlow', '能力就绪', '任务量（stats）']) {
      expect(screen.getByText(name)).toBeInTheDocument()
    }
    // 端点行：有接口的写路径，无接口的写「—」
    expect(screen.getByText('/ragflow/status')).toBeInTheDocument()
    expect(screen.getByText('/capabilities/health')).toBeInTheDocument()
    expect(screen.getByText('/tasks/stats')).toBeInTheDocument()
    expect(screen.getByText('对象存储').closest('.card')).toHaveTextContent('无接口，不估算')
  })

  it('实测数据聚合：RAGFlow 已连接、能力就绪 1/1 降级、任务量取自 /tasks/stats', async () => {
    useSystemStore.setState({ caps, ragflow, vlmAvailable: true } as never)
    renderPage()
    expect(await screen.findByText('已连接')).toBeInTheDocument()
    expect(screen.getByText('1 就绪 / 1 降级')).toBeInTheDocument()
    expect(screen.getByText('索引文档 2847 篇 · 最近入库 2026-09-01')).toBeInTheDocument()
    expect(screen.getByText('总 12')).toBeInTheDocument()
    expect(screen.getByText('今日 2 · 待复核 3 · 执行中 1 · 失败 0')).toBeInTheDocument()
  })

  it('探测失败即降级为未知与「不估算」，不编造数字', async () => {
    fetchServerDateMock.mockRejectedValue(new Error('boom'))
    fetchTaskStatsMock.mockRejectedValue(new Error('boom'))
    renderPage()
    expect(await screen.findByText('统计接口未返回：不估算')).toBeInTheDocument()
    expect(screen.getByText('后端服务').closest('.card')).toHaveTextContent('探测未返回：不估算')
    expect(screen.getByText('能力就绪').closest('.card')).toHaveTextContent('探测未返回：不估算')
    // 未取到数据的卡不留任何伪数字
    expect(screen.getByText('任务量（stats）').closest('.card')).toHaveTextContent('—')
  })

  it('场景能力状态卡：六张卡齐全并链到 /detect/:scene，就绪/降级/未注册三态分明 + 工具注册入口', async () => {
    renderPage()
    await screen.findByText('场景能力状态')
    const links = within(document.querySelector('.scene-cards') as HTMLElement).getAllByRole('link')
    expect(links).toHaveLength(6)
    for (const [label, href] of [
      ['荧光渗透检测', '/detect/fpi'],
      ['孔探检测', '/detect/borescope'],
      ['热部图像检测', '/detect/infrared'],
      ['X射线检测', '/detect/dr'],
      ['断口分析', '/detect/fracture'],
      ['金相分析', '/detect/metallography'],
    ] as Array<[string, string]>) {
      const link = screen.getByText(label).closest('a')
      expect(link).toBeInTheDocument()
      expect(link).toHaveAttribute('href', href)
    }
    // 状态按 /capabilities 真实 status：enabled→就绪、maintenance→降级、注册表内无该场景→未注册
    expect(screen.getByText('内窥镜拍到的叶片画面，找裂纹、烧蚀、腐蚀与孔隙并框出位置')).toBeInTheDocument()
    expect(screen.getByText('FPI · cap-fpi-01')).toBeInTheDocument()
    expect(screen.getByText('热像超温检测')).toBeInTheDocument()
    expect(screen.getByText('INFRARED · cap-ir-01')).toBeInTheDocument()
    expect(screen.getByText('降级')).toBeInTheDocument()
    expect(screen.getAllByText('未注册')).toHaveLength(4)

    expect(screen.getByText('图像 / 录像均可')).toBeInTheDocument() // 未注册场景回退到输入物料口径，不编造能力名
    expect(screen.getByRole('link', { name: '工具注册' })).toHaveAttribute('href', '/tool/registry')
  })

  it('决策 51-⑩：能力明细表（低配台账）已从本页移除，台账字段只在 /tool/registry 出现', async () => {
    useSystemStore.setState({ caps } as never)
    renderPage()
    await screen.findByText('场景能力状态')
    expect(screen.queryByText('能力明细')).not.toBeInTheDocument()
    expect(screen.queryByText('fpi-detect@v1')).not.toBeInTheDocument()
    expect(screen.queryByText(/探测异常：timeout/)).not.toBeInTheDocument()
    expect(document.querySelectorAll('table')).toHaveLength(0)
  })

  it('决策 11：本页不再有外观（主题）与关于两个 section', async () => {
    const { container } = renderPage()
    await screen.findByText('后端服务')
    expect(screen.queryByText('外观')).not.toBeInTheDocument()
    expect(screen.queryByText('主题')).not.toBeInTheDocument()
    expect(screen.queryByText(/关于/)).not.toBeInTheDocument()
    expect(container.querySelectorAll('.set-row')).toHaveLength(0)
  })

  it('底部「运行状态与告警」壳说明（G4-02）：只给条款与依赖接口，无按钮', async () => {
    renderPage()
    expect(await screen.findByText('运行状态与告警')).toBeInTheDocument()
    const shell = screen.getByText('运行状态与告警').closest('.shell-page') as HTMLElement
    expect(shell).toHaveTextContent('G4-02 运维监控与告警')
    expect(shell).toHaveTextContent('建设方案 §8.2 管理后台 · §11 部署与运维')
    expect(shell.querySelectorAll('button')).toHaveLength(0)
  })

  it('立即重新探测：挂载探测一次，点击后再探测一次', async () => {
    const probe = vi.fn(async () => {})
    useSystemStore.setState({ probe } as never)
    renderPage()
    await waitFor(() => expect(probe).toHaveBeenCalledTimes(1))
    fireEvent.click(screen.getByRole('button', { name: /立即重新探测/ }))
    await waitFor(() => expect(probe).toHaveBeenCalledTimes(2))
  })
})
