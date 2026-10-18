import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, within } from '@testing-library/react'
import AppLayout from './AppLayout'
import { LEAVES } from '../../constants/nav'

// vi.mock 工厂提升到文件顶部，可变数据经 vi.hoisted 暴露
const { tasksRef, locRef, navigateRef, toggleTheme } = vi.hoisted(() => ({
  tasksRef: { value: [] as Array<Record<string, unknown>> },
  locRef: { value: { pathname: '/' } },
  navigateRef: { value: vi.fn() },
  toggleTheme: { value: vi.fn() },
}))

// AppLayout/Sidebar 只用这些路由能力：Link 渲染为普通 a，Outlet 空占位，useLocation 可变路径
vi.mock('react-router-dom', () => ({
  Link: ({
    to, title, children, className, ...rest
  }: {
    to: string
    title?: string
    children: React.ReactNode
    className?: string
    'aria-current'?: 'page'
  }) => (
    <a href={to} title={title} className={className} aria-current={rest['aria-current']}>{children}</a>
  ),
  Outlet: () => <div data-testid="outlet" />,
  useLocation: () => locRef.value,
  useNavigate: () => navigateRef.value,
}))

// store 按 selector 用法返回（zustand selector 契约）
vi.mock('../../store/useTaskStore', () => ({
  useTaskStore: (s: (st: { tasks: unknown; fetchList: () => Promise<void> }) => unknown) =>
    s({ tasks: tasksRef.value, fetchList: () => Promise.resolve() }),
}))

vi.mock('../../store/useSystemStore', () => ({
  useSystemStore: (s: (st: unknown) => unknown) =>
    s({ caps: null, ragflow: null, vlmAvailable: null, probe: () => Promise.resolve() }),
}))

vi.mock('../../store/useAuthStore', () => ({
  useAuthStore: (s: (st: { username: string; signOut: () => Promise<void> }) => unknown) =>
    s({ username: '于XX', signOut: () => Promise.resolve() }),
}))

vi.mock('../../hooks/useTheme', () => ({
  useTheme: () => ({ theme: 'light', toggleTheme: toggleTheme.value }),
}))

/* 待复核任务的最小 TaskSummary（外壳只读 status/task_id） */
const awaiting = (taskId: string) => ({
  task_id: taskId,
  scene: null,
  mode: 'single' as const,
  status: 'awaiting_review' as const,
  capability_used: null,
  created_at: '2026-09-09T10:00:00',
  updated_at: '2026-09-09T10:00:00',
  finished_at: null,
})

beforeEach(() => {
  tasksRef.value = []
  locRef.value = { pathname: '/' }
  navigateRef.value.mockClear()
  toggleTheme.value.mockClear()
})

describe('AppLayout 外壳（侧栏整棵树 + 顶栏状态区）', () => {
  it('渲染品牌、侧栏与内容出口', () => {
    const { container } = render(<AppLayout />)
    expect(container.querySelector('.frame')).not.toBeNull()
    expect(container.querySelector('.side')).not.toBeNull()
    expect(screen.getByText('工业图像智能检测')).toBeInTheDocument()
    expect(screen.getByTestId('outlet')).toBeInTheDocument()
  })

  it('37 个导航叶全部可达，建设中叶带「建」标', () => {
    const { container } = render(<AppLayout />)
    const side = container.querySelector('.side') as HTMLElement
    for (const leaf of LEAVES) {
      expect(side.querySelector(`a[href="${leaf.to}"]`)).not.toBeNull()
    }
    // 建设中叶带「建」标（占比统计不进侧栏，只在该叶上标注）
    expect(side.querySelectorAll('.sflag')).toHaveLength(16)
    expect(side.textContent).not.toContain('建设中 16 / 37 项')
    expect(side.textContent).not.toContain('全部折叠')
  })

  it('当前路由高亮对应叶，并给出面包屑与文档标题', () => {
    locRef.value = { pathname: '/detect/fpi' }
    const { container } = render(<AppLayout />)
    const link = container.querySelector('a[href="/detect/fpi"]') as HTMLAnchorElement
    expect(link).toHaveClass('on')
    expect(link).toHaveAttribute('aria-current', 'page')
    expect(container.querySelector('.crumb')?.textContent).toContain('专业工作台')
    expect(document.title).toBe('荧光渗透检测 · 工业图像智能检测智能体')
  })

  it('参数路由归属到最近静态叶：/tasks/{id} 高亮结果显示与复核', () => {
    locRef.value = { pathname: '/tasks/T1' }
    const { container } = render(<AppLayout />)
    expect(container.querySelector('a[href="/tasks/review"]')).toHaveClass('on')
    expect(document.title).toBe('结果与复核 · 工业图像智能检测智能体')
  })

  it('待复核徽标计数来自 /tasks 窗口；无待复核不渲染', () => {
    tasksRef.value = [awaiting('t1'), awaiting('t2'), { ...awaiting('t3'), status: 'completed' }]
    const { container } = render(<AppLayout />)
    const badge = container.querySelector('.nav-badge')
    expect(badge?.textContent).toBe('2')

    tasksRef.value = [{ ...awaiting('t1'), status: 'completed' }]
    const { container: c2 } = render(<AppLayout />)
    expect(c2.querySelector('.nav-badge')).toBeNull()
  })

  it('分组各自折叠：点标题切换 aria-expanded 与 closed', () => {
    const { container } = render(<AppLayout />)
    const grp = container.querySelector('.grp') as HTMLElement
    const head = within(grp).getByRole('button')
    expect(head).toHaveAttribute('aria-expanded', 'true')
    fireEvent.click(head)
    expect(grp).toHaveClass('closed')
    expect(head).toHaveAttribute('aria-expanded', 'false')
    fireEvent.click(head)
    expect(grp).not.toHaveClass('closed')
  })

  it('图标栏开关切换 frame 的 side-rail 类', () => {
    const { container } = render(<AppLayout />)
    expect(container.querySelector('.frame')).not.toHaveClass('side-rail')
    fireEvent.click(screen.getByRole('button', { name: '折叠侧栏' }))
    expect(container.querySelector('.frame')).toHaveClass('side-rail')
  })

  it('环境徽标点击进入运行监控，并聚合集群/RAGFlow 状态', () => {
    const { container } = render(<AppLayout />)
    const pill = container.querySelector('.env-switch') as HTMLAnchorElement
    expect(pill.getAttribute('href')).toBe('/admin/monitor')
    expect(pill).toHaveClass('muted')
    expect(pill.getAttribute('title')).toBe('推理集群 · 未知｜RAGFlow · 未知')
  })

  it('用户区显示登录名并声明未启用角色区分（决策 10）', () => {
    render(<AppLayout />)
    const top = screen.getByRole('banner')
    expect(within(top).getByText('于XX')).toBeInTheDocument()
    expect(within(top).getByText('未启用角色区分')).toBeInTheDocument()
  })

  it('主题切换与退出登录', async () => {
    render(<AppLayout />)
    fireEvent.click(screen.getByRole('button', { name: '切换明暗主题' }))
    expect(toggleTheme.value).toHaveBeenCalledTimes(1)

    fireEvent.click(screen.getByRole('button', { name: '退出登录' }))
    await Promise.resolve()
    expect(navigateRef.value).toHaveBeenCalledWith('/login', { replace: true })
  })
})
