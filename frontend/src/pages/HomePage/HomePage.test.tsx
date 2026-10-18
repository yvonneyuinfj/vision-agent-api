import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, act } from '@testing-library/react'
import HomePage from './index'
import type { CapabilitySummary } from '../../api/capabilities'
import type { CapabilityHealth, RagflowStatus } from '../../api/system'
import type { TaskStats, TaskSummary, TaskStatus } from '../../api/types'

/* ---------------- 路由 / store / api mock（形状逐字段对齐现有接口） ---------------- */

vi.mock('react-router-dom', () => ({
  Link: ({ to, children, className }: { to: string; children: React.ReactNode; className?: string }) => (
    <a href={to} className={className}>{children}</a>
  ),
}))

const { tasksRef, systemRef } = vi.hoisted(() => ({
  tasksRef: { value: [] as TaskSummary[] },
  systemRef: {
    value: {
      caps: null as CapabilityHealth[] | null,
      ragflow: null as RagflowStatus | null,
      vlmAvailable: null as boolean | null,
    },
  },
}))

const fetchListSpy = vi.fn(async () => {})
vi.mock('../../store/useTaskStore', () => ({
  useTaskStore: (s: (st: unknown) => unknown) => s({ tasks: tasksRef.value, fetchList: fetchListSpy }),
}))

const probeSpy = vi.fn(async () => {})
vi.mock('../../store/useSystemStore', () => ({
  useSystemStore: (s: (st: unknown) => unknown) => s({ ...systemRef.value, probe: probeSpy }),
}))

const STATS: TaskStats = {
  total: 40, today: 12, pending: 1, running: 2, awaiting_review: 7,
  completed: 30, failed: 3, canceled: 0,
}
const fetchTaskStatsSpy = vi.fn(async (): Promise<TaskStats> => STATS)

const cap = (over: Partial<CapabilitySummary>): CapabilitySummary => ({
  capability_id: 'fpi_defect_detection_v1', tool_name: 'fpi_defect_detection', tool_version: '1.0.0',
  model_version: '1.0.0', display_name: '荧光渗透缺陷检测', scene: 'fpi', capability_type: 'defect_detection',
  adapter_type: 'http', status: 'enabled', note: '表面荧光迹痕缺陷识别', ...over,
}) as CapabilitySummary

const CAP_LIST: CapabilitySummary[] = [
  cap({}),
  cap({ capability_id: 'borescope_pore_v1', scene: 'borescope', note: '孔探孔隙类缺陷检测' }),
  cap({ capability_id: 'thermal_anomaly_v1', scene: 'infrared', note: '温度异常检测与热图重建' }),
  cap({ capability_id: 'dr_weld_v1', scene: 'dr', status: 'maintenance', note: '焊缝气孔夹渣判读' }),
  cap({ capability_id: 'fracture_failure_v1', scene: 'fracture', note: '断口形貌与失效原因分析' }),
  cap({ capability_id: 'metal_log_v1', scene: 'metallography', capability_type: 'classification', note: '晶界与相分布分析' }),
]
const fetchCapabilitiesSpy = vi.fn(async (): Promise<CapabilitySummary[]> => CAP_LIST)

vi.mock('../../api/tasks', () => ({
  fetchTaskStats: () => fetchTaskStatsSpy(),
}))
vi.mock('../../api/capabilities', () => ({
  fetchCapabilities: () => fetchCapabilitiesSpy(),
  CAPABILITY_TYPE_TEXT: { defect_detection: '缺陷检测', measurement: '测量', classification: '分类' },
}))

/* ---------------- 工具 ---------------- */

const makeTask = (taskId: string, status: TaskStatus, scene: string | null = 'fpi'): TaskSummary => ({
  task_id: taskId,
  scene,
  mode: 'single',
  status,
  capability_used: null,
  created_at: '2026-09-09T08:00:00Z',
  updated_at: '2026-09-09T08:10:00Z',
  finished_at: null,
})

/** 渲染并等三个只读请求（任务窗口 / 统计 / 能力注册表）落地，避免未包 act 的状态更新噪声 */
async function renderHome() {
  render(<HomePage />)
  await act(async () => {
    await new Promise((r) => window.setTimeout(r, 0))
  })
}

/** KPI 四张卡的完整文案（避免与条形图数字撞车） */
const kpiTexts = (): string[] =>
  Array.from(document.querySelectorAll('.kpi')).map((n) => n.textContent ?? '')

/** 场景分布条形行文案（MeterRows：blab / meter / bnum / bpct） */
const meterTexts = (): string[] =>
  Array.from(document.querySelectorAll('.brow')).map((n) => n.textContent ?? '')

beforeEach(() => {
  vi.clearAllMocks()
  tasksRef.value = []
  systemRef.value = { caps: null, ragflow: null, vlmAvailable: null }
  fetchTaskStatsSpy.mockResolvedValue(STATS)
  fetchCapabilitiesSpy.mockResolvedValue(CAP_LIST)
})

/* ---------------- 1 · hero ---------------- */

describe('HomePage · hero（决策 11 合并页首屏）', () => {
  it('两个入口标签 + h1 + 一句说明 + 双 CTA 指向 /tasks/new 与 /detect/assistant', async () => {
    await renderHome()
    expect(screen.getByRole('heading', { level: 1, name: '工业图像智能检测智能体' })).toBeInTheDocument()
    expect(screen.getByText('两条业务入口 · 一个任务事实源')).toBeInTheDocument()
    expect(screen.getByText('G1-01 骨架')).toBeInTheDocument()
    expect(screen.getByText(/用自然语言向智能助手描述需求/)).toBeInTheDocument()
    expect(screen.getByText('新建检测任务').closest('a')).toHaveAttribute('href', '/tasks/new')
    expect(screen.getByText('智能助手').closest('a')).toHaveAttribute('href', '/detect/assistant')
  })
})

/* ---------------- 2 · KPI 横幅 ---------------- */

describe('HomePage · KPI 横幅', () => {
  it('四项读数全部来自 GET /tasks/stats 与能力注册表（无环比、无并发上限等造数）', async () => {
    tasksRef.value = [makeTask('QC-A', 'awaiting_review'), makeTask('QC-R', 'running')]
    await renderHome()
    await waitFor(() => expect(fetchTaskStatsSpy).toHaveBeenCalled())

    const cards = await waitFor(() => {
      const found = kpiTexts()
      expect(found).toHaveLength(4)
      return found
    })
    expect(cards[0]).toContain('待复核')
    expect(cards[0]).toContain('7项') // stats.awaiting_review
    expect(cards[0]).toContain('最近 100 条窗口内 1 条') // 窗口聚合，真实可算
    expect(cards[1]).toContain('执行中')
    expect(cards[1]).toContain('2项') // stats.running
    expect(cards[1]).toContain('排队 1 项')
    expect(cards[1]).not.toContain('并发上限') // 接口无此项
    expect(cards[2]).toContain('累计完成')
    expect(cards[2]).toContain('30项') // stats.completed
    expect(cards[2]).toContain('今日新增 12 项') // stats.today（近 7 日无接口口径，改用真字段）
    expect(cards[2]).not.toContain('环比')
    expect(cards[3]).toContain('可用能力')
    expect(cards[3]).toContain('5/ 6') // dr = maintenance → 未就绪
    expect(cards[3]).toContain('X射线检测 未就绪')
  })

  it('接口未返回 → 读数降级为 —，不填占位数字', async () => {
    fetchTaskStatsSpy.mockRejectedValue(new Error('boom'))
    fetchCapabilitiesSpy.mockRejectedValue(new Error('boom'))
    await renderHome()
    await waitFor(() => expect(fetchTaskStatsSpy).toHaveBeenCalled())
    const cards = await waitFor(() => {
      const found = kpiTexts()
      expect(found).toHaveLength(4)
      return found
    })
    expect(cards[0]).toContain('—')
    expect(cards[0]).toContain('统计读取中…')
    expect(cards[3]).toContain('—')
    expect(cards[3]).toContain('能力注册表读取中…')
  })
})

/* ---------------- 3 · 专业工作台六卡 ---------------- */

describe('HomePage · 专业工作台六卡', () => {
  it('六个场景齐全并链到 /detect/:scene，就绪/降级取真实 status', async () => {
    await renderHome()
    await waitFor(() => expect(fetchCapabilitiesSpy).toHaveBeenCalled())

    const expected: Array<[string, string]> = [
      ['荧光渗透检测', '/detect/fpi'],
      ['孔探检测', '/detect/borescope'],
      ['热部图像检测', '/detect/infrared'],
      ['X射线检测', '/detect/dr'],
      ['断口分析', '/detect/fracture'],
      ['金相分析', '/detect/metallography'],
    ]
    for (const [label, href] of expected) {
      const card = screen.getByText(label).closest('a')
      expect(card).toBeInTheDocument()
      expect(card).toHaveAttribute('href', href)
    }
    expect(screen.getAllByText('就绪')).toHaveLength(5)
    expect(screen.getByText('降级')).toBeInTheDocument() // dr.status = maintenance
    expect(screen.getByText('DR · dr_weld_v1')).toBeInTheDocument() // EN 名 + 真实 capability_id
    expect(screen.getAllByText('参数表已上线')).toHaveLength(4) // 决策 3 冻结档位
    expect(screen.getAllByText('契约未冻结')).toHaveLength(2)
    expect(screen.getAllByText('缺陷检测')).toHaveLength(5) // capability_type 真字段
    expect(screen.getByText('分类')).toBeInTheDocument() // 金相为分类型能力
    expect(screen.getByText('专业能力说明').closest('a')).toHaveAttribute('href', '/capability/pro')
  })

  it('注册表读取失败 → 六卡标「未知」，不伪装成服务降级', async () => {
    fetchCapabilitiesSpy.mockRejectedValue(new Error('boom'))
    await renderHome()
    await waitFor(() => expect(fetchCapabilitiesSpy).toHaveBeenCalled())
    expect(screen.getAllByText('未知').length).toBeGreaterThanOrEqual(6)
  })

  it('注册表内该场景无工具 → 「未注册」（与读取失败区分）', async () => {
    fetchCapabilitiesSpy.mockResolvedValue([cap({})])
    await renderHome()
    await waitFor(() => expect(screen.getAllByText('未注册')).toHaveLength(5))
  })
})

/* ---------------- 4 · 队列 / 分布 / 态势 ---------------- */

describe('HomePage · 待复核队列与右栏', () => {
  it('紧凑任务表最多 5 行，行链到详情；口径与复核台入口常驻', async () => {
    tasksRef.value = Array.from({ length: 7 }, (_, i) => makeTask(`QC-${i}`, 'awaiting_review'))
    await renderHome()
    await waitFor(() => expect(fetchListSpy).toHaveBeenCalled())

    expect(screen.getAllByText(/^QC-\d$/)).toHaveLength(5)
    expect(screen.getByText('QC-0').closest('a')).toHaveAttribute('href', '/tasks/QC-0')
    expect(screen.getByText('进入复核台').closest('a')).toHaveAttribute('href', '/tasks/review')
    expect(screen.getByText(/最近 100 条内 7 条 · 仅列前 5 条/)).toBeInTheDocument()
  })

  it('窗口内无待复核任务 → 空态文案，不出现假行', async () => {
    tasksRef.value = [makeTask('QC-OK', 'completed')]
    await renderHome()
    expect(screen.getByText('最近 100 条窗口内暂无待复核任务')).toBeInTheDocument()
  })

  it('场景分布：条数由 100 条窗口聚合，并常驻「非全量」口径说明（决策 20）', async () => {
    tasksRef.value = [
      makeTask('QC-1', 'completed', 'borescope'),
      makeTask('QC-2', 'running', 'borescope'),
      makeTask('QC-3', 'failed', 'dr'),
    ]
    await renderHome()
    const notice = document.querySelector('.notice.gray')
    expect(notice).not.toBeNull()
    expect(notice?.textContent).toContain('仅统计')
    expect(notice?.textContent).toContain('最近 100 条任务')
    expect(notice?.textContent).toContain('非全量')
    expect(notice?.textContent).toContain('GET /tasks')
    const rows = meterTexts()
    expect(rows.some((r) => r.includes('孔探') && r.includes('2') && r.includes('66.7%'))).toBe(true)
    expect(rows.some((r) => r.includes('X射线') && r.includes('1') && r.includes('33.3%'))).toBe(true)
    expect(screen.getByText('最近 100 条')).toBeInTheDocument()
  })

  it('平台态势：只呈现真实探测结果，链到运行监控', async () => {
    systemRef.value = {
      caps: [
        { tool_name: 'fpi_defect_detection', healthy: true, message: 'ok', latency_ms: 120 },
        { tool_name: 'dr_weld_detection', healthy: false, message: 'timeout', latency_ms: null },
      ],
      ragflow: { configured: true, reachable: true, doc_count: 37, latest_doc_time: null },
      vlmAvailable: false,
    }
    await renderHome()
    expect(await screen.findByText('可达 · 知识库 37 篇文档')).toBeInTheDocument()
    expect(screen.getByText(/1 \/ 2 项健康 · dr_weld_detection/)).toBeInTheDocument()
    expect(screen.getByText('不可用 · 助手输入置灰')).toBeInTheDocument()
    expect(screen.getByText('运行监控').closest('a')).toHaveAttribute('href', '/admin/monitor')
    expect(screen.queryByText(/1,284/)).not.toBeInTheDocument() // 原型的造数不得进入实现
  })
})

/* ---------------- 5 · 边界声明与数据接线 ---------------- */

describe('HomePage · 本轮边界声明已移出页面（决策 51-⑨）', () => {
  it('首页不再挂"本轮不做的能力"公告，差距清单只在 docs 维护', async () => {
    await renderHome()
    expect(screen.queryByText('本轮不做的能力（后端接口不存在）：')).not.toBeInTheDocument()
    expect(document.body).not.toHaveTextContent('任务取消 · business_ref 跨任务聚合')
    expect(document.body).not.toHaveTextContent('docs/前端改造_v0.5_落地边界与待补接口.md')
  })
})

describe('HomePage · 数据接线', () => {
  it('挂载即拉取任务窗口、统计、能力注册表并触发一次系统探测', async () => {
    await renderHome()
    await waitFor(() => {
      expect(fetchListSpy).toHaveBeenCalled()
      expect(fetchTaskStatsSpy).toHaveBeenCalled()
      expect(fetchCapabilitiesSpy).toHaveBeenCalled()
      expect(probeSpy).toHaveBeenCalled()
    })
  })
})
