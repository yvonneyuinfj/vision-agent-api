import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import AppLayout from './AppLayout'
import { useTaskStore } from '../../store/useTaskStore'
import { useSystemStore } from '../../store/useSystemStore'
import type { TaskSummary } from '../../api/types'
import { fetchCapabilityHealth, fetchRagflowStatus } from '../../api/system'

// 默认实现给健康的探测结果：页面级共享探测 store 在挂载即 probe，
// 裸 vi.fn() 会解析为 undefined，导致状态聚合崩溃
vi.mock('../../api/system', () => ({
  fetchRagflowStatus: vi.fn(async () => ({
    configured: true,
    reachable: true,
    doc_count: 5,
    latest_doc_time: '2026-09-01',
    message: 'ok',
  })),
  fetchCapabilityHealth: vi.fn(async () => [
    { tool_name: 'tool_0', healthy: true, message: 'ok', latency_ms: 10 },
  ]),
  fetchServerDate: vi.fn(async () => ({ server_date: '2026-09-09', vlm_available: true })),
}))

const makeTask = (taskId: string, status: TaskSummary['status']): TaskSummary => ({
  task_id: taskId,
  scene: null,
  mode: 'single',
  status,
  capability_used: null,
  created_at: '2026-09-09T00:00:00Z',
  updated_at: '2026-09-09T00:00:00Z',
  finished_at: null,
})

/** 环境徽标 title = `推理集群 · X｜RAGFlow · Y`（新ui.html：状态收纳进徽标 tooltip） */
function envPill(container: HTMLElement): HTMLElement {
  const pill = container.querySelector('.env-switch')
  expect(pill).not.toBeNull()
  return pill as HTMLElement
}

beforeEach(() => {
  useTaskStore.setState({ tasks: [], detail: null, loading: false, error: null })
  // 探测 store 为模块级单例：重置初值，避免用例间状态泄漏
  useSystemStore.setState({ caps: null, ragflow: null, vlmAvailable: null })
})

describe('AppLayout（侧栏外壳）', () => {
  it('导航在侧栏整棵树里；顶栏只剩面包屑 + 环境徽标 + 主题 + 用户', () => {
    const { container } = render(
      <MemoryRouter>
        <AppLayout />
      </MemoryRouter>,
    )
    // 一级模块与叶子都在 .side 内（决策 2）
    const side = container.querySelector('.side') as HTMLElement
    expect(side).not.toBeNull()
    for (const label of ['智能检测', '任务中心', '能力中心', '模型工坊', '知识中心', '工具服务中心', '后台管理']) {
      expect(within(side).getByText(label)).toBeInTheDocument()
    }
    expect(within(side).getByText('荧光渗透检测')).toBeInTheDocument()
    // 顶栏不再承载导航
    const top = container.querySelector('.topbar') as HTMLElement
    expect(top.querySelector('nav')).toBeNull()
    expect(within(top).getByText('首页')).toBeInTheDocument()
  })

  it('按 awaiting_review 数量渲染待复核徽标', () => {
    useTaskStore.setState({
      tasks: [
        makeTask('t1', 'awaiting_review'),
        makeTask('t2', 'awaiting_review'),
        makeTask('t3', 'completed'),
      ],
    })
    render(
      <MemoryRouter>
        <AppLayout />
      </MemoryRouter>,
    )
    expect(screen.getAllByText('2').length).toBeGreaterThanOrEqual(1)
  })
})

function mockSystem(
  ragflow: { configured: boolean; reachable: boolean } | null,
  caps: { healthy: boolean }[] | null,
) {
  vi.mocked(fetchRagflowStatus).mockResolvedValue({
    configured: ragflow?.configured ?? false,
    reachable: ragflow?.reachable ?? false,
    doc_count: 5,
    latest_doc_time: '2026-09-01',
    message: 'ok',
  })
  vi.mocked(fetchCapabilityHealth).mockResolvedValue(
    (caps ?? []).map((c, i) => ({
      tool_name: `tool_${i}`,
      healthy: c.healthy,
      message: 'ok',
      latency_ms: 10,
    })),
  )
}

describe('AppLayout 系统状态探测', () => {
  // 注：不用 vi.useFakeTimers() —— RTL waitFor 在 vitest 下检测不到假 timer，
  // 其内部轮询会被冻结导致 findByText 死等超时；改用真实 timers（初次探针为微任务，天然可等）
  afterEach(() => {
    vi.clearAllMocks()
  })

  it('全部健康时环境徽标为绿色 ok 态，title 展示正常/已连接', async () => {
    mockSystem({ configured: true, reachable: true }, [{ healthy: true }])
    const { container } = render(
      <MemoryRouter>
        <AppLayout />
      </MemoryRouter>,
    )
    await waitFor(() => expect(envPill(container)).toHaveClass('ok'))
    expect(envPill(container)).toHaveAttribute('title', '推理集群 · 正常｜RAGFlow · 已连接')
  })

  it('能力异常时环境徽标为 error 态', async () => {
    mockSystem({ configured: true, reachable: true }, [{ healthy: false }])
    const { container } = render(
      <MemoryRouter>
        <AppLayout />
      </MemoryRouter>,
    )
    await waitFor(() => expect(envPill(container)).toHaveClass('error'))
    expect(envPill(container).getAttribute('title')).toContain('推理集群 · 异常')
  })

  it('RAGFlow 未配置时 title 展示未配置（徽标仍随集群为 ok）', async () => {
    mockSystem({ configured: false, reachable: false }, [{ healthy: true }])
    const { container } = render(
      <MemoryRouter>
        <AppLayout />
      </MemoryRouter>,
    )
    await waitFor(() => expect(envPill(container).getAttribute('title')).toContain('RAGFlow · 未配置'))
  })

  it('RAGFlow 已配置但不可达时 title 展示断开且徽标 error', async () => {
    mockSystem({ configured: true, reachable: false }, [{ healthy: true }])
    const { container } = render(
      <MemoryRouter>
        <AppLayout />
      </MemoryRouter>,
    )
    await waitFor(() => expect(envPill(container).getAttribute('title')).toContain('RAGFlow · 断开'))
    expect(envPill(container)).toHaveClass('error')
  })

  it('探测失败时 title 展示未知（不弹错误）', async () => {
    vi.mocked(fetchRagflowStatus).mockRejectedValue(new Error('network down'))
    vi.mocked(fetchCapabilityHealth).mockRejectedValue(new Error('network down'))
    const { container } = render(
      <MemoryRouter>
        <AppLayout />
      </MemoryRouter>,
    )
    await waitFor(() =>
      expect(envPill(container)).toHaveAttribute('title', '推理集群 · 未知｜RAGFlow · 未知'),
    )
    expect(envPill(container)).toHaveClass('muted')
  })

  it('挂载探测一次；无 30s 轮询定时器；路由切换（点导航）再探测一次', async () => {
    // 契约验证：状态探测只在挂载与路由切换时各发一次请求，绝不注册轮询定时器
    const setIntervalSpy = vi.spyOn(globalThis, 'setInterval')
    try {
      mockSystem({ configured: true, reachable: true }, [{ healthy: true }])
      render(
        <MemoryRouter initialEntries={['/']}>
          <AppLayout />
        </MemoryRouter>,
      )
      await waitFor(() => expect(fetchRagflowStatus).toHaveBeenCalledTimes(1))
      expect(setIntervalSpy).not.toHaveBeenCalledWith(expect.any(Function), 30_000)

      await fireEvent.click(screen.getByText('任务列表'))
      await waitFor(() => expect(fetchRagflowStatus).toHaveBeenCalledTimes(2))
    } finally {
      setIntervalSpy.mockRestore()
    }
  })

  it('能力列表为空数组时推理集群显示未知（length 0 分支）', async () => {
    mockSystem({ configured: true, reachable: true }, [])
    const { container } = render(
      <MemoryRouter>
        <AppLayout />
      </MemoryRouter>,
    )
    await waitFor(() =>
      expect(envPill(container)).toHaveAttribute('title', '推理集群 · 未知｜RAGFlow · 已连接'),
    )
  })
})
