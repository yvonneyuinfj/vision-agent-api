import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import SubmitPage from './index'

const { navigate: navigateMock, submitTask: submitTaskMock } = vi.hoisted(() => ({
  navigate: vi.fn(),
  submitTask: vi.fn(),
}))

vi.mock('react-router-dom', async (importOriginal) => {
  const actual = await importOriginal<typeof import('react-router-dom')>()
  return { ...actual, useNavigate: () => navigateMock }
})
vi.mock('../../api/tasks', () => ({ submitTask: submitTaskMock }))

// 场景目录（决策 52）：本文件按"目录＝现有六条"取数，第七个场景的呈现另见 SubmitPage.catalog.test.tsx
vi.mock('../../api/scenes', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/scenes')>()
  return {
    ...actual,
    fetchScenes: vi.fn(async () => [
      { scene: 'fpi', label: '荧光渗透检测', short: '荧光', en: 'FPI', params: 'frozen', input: '仅需检测图像 · 暗场', auto_recognize: true },
      { scene: 'borescope', label: '孔探检测', short: '孔探', en: 'BORESCOPE', params: 'frozen', input: '图像 / 录像均可', auto_recognize: true },
      { scene: 'infrared', label: '热部图像检测', short: '红外', en: 'INFRARED', params: 'draft', input: '图像 + 辐射值 CSV', auto_recognize: true },
      { scene: 'dr', label: 'X射线检测', short: 'X射线', en: 'DR', params: 'draft', input: '图像 + 参考图 + 标定 JSON/XML', auto_recognize: true },
      { scene: 'fracture', label: '断口分析', short: '断口', en: 'FRACTURE', params: 'frozen', input: '仅需检测图像', auto_recognize: true },
      { scene: 'metallography', label: '金相分析', short: '金相', en: 'METALLOGRAPHY', params: 'frozen', input: '仅需检测图像 · 附放大倍数', auto_recognize: true },
    ]),
  }
})

// 能力注册表：确认卡「能力」行的真实来源（孔探 1 个 / 红外 1 个 / 其余场景无注册工具）
vi.mock('../../hooks/useCapabilities', () => {
  const caps = [
    {
      capability_id: 'borescope_defect_detection_v1', tool_name: 'borescope_defect_detection',
      tool_version: '1.0.0', model_version: '1.0.0', display_name: '孔探缺陷检测', scene: 'borescope',
      capability_type: 'defect_detection', adapter_type: 'http', status: 'enabled', note: '',
    },
    {
      capability_id: 'infrared_temp_v1', tool_name: 'infrared_temp',
      tool_version: '1.0.0', model_version: '1.0.0', display_name: '热部温度检出', scene: 'infrared',
      capability_type: 'measurement', adapter_type: 'http', status: 'enabled', note: '',
    },
  ]
  return {
    useCapabilities: () => ({ caps, health: null, loading: false, error: null, refreshing: false, refresh: vi.fn(), reload: vi.fn() }),
  }
})

function uploadFiles(container: HTMLElement, idx: number, files: File[]) {
  const inputs = container.querySelectorAll<HTMLInputElement>('input[type="file"]')
  fireEvent.change(inputs[idx], { target: { files } })
}

function makeFile(name: string): File {
  return new File(['x'], name)
}

function sceneCard(container: HTMLElement, code: string): HTMLElement {
  const card = container.querySelector<HTMLElement>(`.scene-card[data-code="${code}"]`)
  if (!card) throw new Error(`缺少场景卡 ${code}`)
  return card
}

function modeChip(container: HTMLElement, key: string): HTMLElement {
  const chip = container.querySelector<HTMLElement>(`.chip[data-mode="${key}"]`)
  if (!chip) throw new Error(`缺少模式 chip ${key}`)
  return chip
}

function renderPage(entry = '/tasks/new') {
  return render(
    <MemoryRouter initialEntries={[entry]}>
      <SubmitPage />
    </MemoryRouter>,
  )
}

describe('SubmitPage', () => {
  beforeEach(() => {
    submitTaskMock.mockReset()
    submitTaskMock.mockResolvedValue({ task_id: 't-123', mode: 'single' })
    navigateMock.mockReset()
  })

  it('页面标题为「新建任务」，且不出现无接口来源的队列/等待指标', () => {
    render(<MemoryRouter><SubmitPage /></MemoryRouter>)
    expect(screen.getByText('新建任务')).toBeInTheDocument()
    expect(screen.queryByText('发起质检任务')).not.toBeInTheDocument()
    expect(screen.queryByText(/队列深度/)).not.toBeInTheDocument()
    expect(screen.queryByText(/预计等待/)).not.toBeInTheDocument()
  })

  it('工作台带入 ?scene= → 预选并锁定，可更换回多选', async () => {
    const { container } = renderPage('/tasks/new?scene=borescope')
    expect(container.querySelector('.scene-grid')).toBeNull()
    const locked = container.querySelector<HTMLElement>('.locked-scene')!
    expect(locked.textContent).toContain('孔探')
    expect(within(locked).getByText(/已锁定/)).toBeInTheDocument()

    // 锁定态下提交：scene 即带入场景
    uploadFiles(container, 0, [makeFile('a.jpg')])
    fireEvent.click(screen.getByText('提交检测任务'))
    await waitFor(() => expect(submitTaskMock).toHaveBeenCalledTimes(1))
    expect(submitTaskMock.mock.calls[0][0].scene).toBe('borescope')

    // 更换场景 → 回到六卡多选
    fireEvent.click(screen.getByText('更换场景'))
    await waitFor(() => expect(container.querySelectorAll('.scene-card').length).toBe(6))
    expect(submitTaskMock.mock.calls[0][0].scene).toBe('borescope')
  })

  it('非法 ?scene= 值按未带入处理（不臆造场景）', () => {
    const { container } = renderPage('/tasks/new?scene=nope')
    expect(container.querySelector('.locked-scene')).toBeNull()
    expect(container.querySelectorAll('.scene-card').length).toBe(6)
  })

  it('卡序对齐原型：场景与模式 → 上传样本 → 参数 → 右侧提交前确认', () => {
    const { container } = render(<MemoryRouter><SubmitPage /></MemoryRouter>)
    const titles = [...container.querySelectorAll('.card-title')].map((t) => t.textContent)
    expect(titles).toEqual([
      '1场景与模式SCOPE',
      '2上传样本ASSETS',
      '3参数PARAMETERS',
      '提交前确认REVIEW',
    ])
  })

  it('模式三档 chips；单场景档提交 mode=single', async () => {
    const { container } = render(<MemoryRouter><SubmitPage /></MemoryRouter>)
    const chips = [...container.querySelectorAll<HTMLElement>('.chip[data-mode]')]
    expect(chips.map((c) => c.dataset.mode)).toEqual(['single', 'parallel', 'joint'])
    expect(chips.map((c) => c.textContent)).toEqual(['单场景', '多场景并行', '联合研判（父任务 + 各专业子任务）'])
    expect(chips[0].className).toContain('active')

    fireEvent.click(sceneCard(container, 'fpi'))
    uploadFiles(container, 0, [makeFile('a.jpg')])
    fireEvent.click(screen.getByText('提交检测任务'))
    await waitFor(() => expect(submitTaskMock).toHaveBeenCalledTimes(1))
    const arg = submitTaskMock.mock.calls[0][0]
    expect(arg.mode).toBe('single')
    expect(arg.scene).toBe('fpi')
  })

  it('跨场景两档都提交后端真实存在的 mode=joint（接口无 parallel 模式）', async () => {
    const { container } = render(<MemoryRouter><SubmitPage /></MemoryRouter>)
    fireEvent.click(sceneCard(container, 'borescope'))
    fireEvent.click(sceneCard(container, 'infrared'))
    fireEvent.click(modeChip(container, 'parallel'))
    uploadFiles(container, 0, [makeFile('a.jpg')])
    uploadFiles(container, 1, [makeFile('rad.csv')])
    fireEvent.click(screen.getByText('提交检测任务'))

    await waitFor(() => expect(submitTaskMock).toHaveBeenCalledTimes(1))
    const arg = submitTaskMock.mock.calls[0][0]
    expect(arg.mode).toBe('joint')
    expect(arg.scene).toBeNull()
    expect(arg.scenes).toEqual(['borescope', 'infrared'])
  })

  it('冻结场景给参数表，只把改动过的项写进 parameters', async () => {
    const { container } = renderPage('/tasks/new?scene=fpi')
    expect(container.querySelector('.param-tbl')).not.toBeNull()
    fireEvent.change(container.querySelector<HTMLInputElement>('input[type="number"]')!, { target: { value: '0.9' } })
    uploadFiles(container, 0, [makeFile('a.jpg')])
    fireEvent.click(screen.getByText('提交检测任务'))
    await waitFor(() => expect(submitTaskMock).toHaveBeenCalledTimes(1))
    expect(submitTaskMock.mock.calls[0][0].parameters).toEqual({ confidence_threshold: 0.9 })
  })

  it('未冻结场景（红外）不提供参数表单；混合选择只声明未冻结', async () => {
    const { container } = render(<MemoryRouter><SubmitPage /></MemoryRouter>)
    fireEvent.click(sceneCard(container, 'infrared'))
    expect(container.querySelector('.param-tbl')).toBeNull()
    expect(container.querySelector('.notice')!.textContent).toContain('尚未冻结')
    fireEvent.click(sceneCard(container, 'borescope'))
    expect(container.querySelector('.param-tbl')).toBeNull()
    expect(container.querySelector('.notice')!.textContent).toContain('尚未冻结')
  })

  it('未选场景时参数卡只声明「专业参数契约未冻结」', () => {
    render(<MemoryRouter><SubmitPage /></MemoryRouter>)
    expect(screen.getByText('专业参数契约未冻结')).toBeInTheDocument()
    expect(document.querySelector('.param-tbl')).toBeNull()
  })

  it('初始仅主上传区：无联动块，业务元数据字段不渲染，真实按钮文案存在', () => {
    const { container } = render(<MemoryRouter><SubmitPage /></MemoryRouter>)
    expect(container.querySelectorAll('input[type="file"]').length).toBe(1)
    expect(screen.queryByText('辐射值 CSV')).not.toBeInTheDocument()
    expect(screen.queryByText('参考图 + 标定信息')).not.toBeInTheDocument()
    expect(screen.queryByText('发动机编号')).not.toBeInTheDocument()
    expect(screen.queryByText('部件编号')).not.toBeInTheDocument()
    expect(screen.queryByText('图像业务编号')).not.toBeInTheDocument()
    expect(screen.queryByText(/置信度阈值/)).not.toBeInTheDocument()
  })

  it('物料要求折进场景卡（无独立 SCENE REQUIREMENTS 表），保密确认默认勾选', () => {
    const { container } = render(<MemoryRouter><SubmitPage /></MemoryRouter>)
    expect(container.querySelector('table.req-matrix')).toBeNull()
    expect(sceneCard(container, 'infrared').textContent).toContain('像素级温度矩阵')
    expect(sceneCard(container, 'dr').textContent).toContain('标定文件由探伤设备导出')
    expect(sceneCard(container, 'fpi').textContent).toContain('勿做亮度修正')
    expect((screen.getByRole('checkbox') as HTMLInputElement).checked).toBe(true)
  })

  it('确认卡含模式/能力摘要行与请求体预览，且预览就是实际下发字段', () => {
    const { container } = render(<MemoryRouter><SubmitPage /></MemoryRouter>)
    const card = container.querySelector<HTMLElement>('.submit-confirm')!
    expect(within(card).getByText('提交前确认')).toBeInTheDocument()
    const labels = [...card.querySelectorAll('.sum-row .muted')].map((n) => n.textContent)
    expect(labels).toEqual(['场景', '模式', '样本', '能力', '参数'])

    fireEvent.click(sceneCard(container, 'borescope'))
    expect(within(card).getByText('单场景')).toBeInTheDocument()
    expect(within(card).getByText('1 个已登记')).toBeInTheDocument()
    const preview = card.querySelector('details.trace pre.req-json')!.textContent!
    expect(preview).toContain('"scene": "borescope"')
    expect(preview).toContain('"mode": "single"')
    expect(preview).toContain('"files": "<multipart ×0>"')
    expect(within(card).getByText('请求体预览')).toBeInTheDocument()
  })

  it('选择红外 → 展开 CSV 联动块并提示缺失；切 X 射线 → 参考图+标定上传区', () => {
    const { container } = render(<MemoryRouter><SubmitPage /></MemoryRouter>)
    fireEvent.click(sceneCard(container, 'infrared'))
    expect(container.querySelectorAll('input[type="file"]').length).toBe(2)
    expect(within(container).getByText(/缺辐射值 CSV/)).toBeInTheDocument()
    fireEvent.click(sceneCard(container, 'dr'))
    expect(container.querySelectorAll('input[type="file"]').length).toBe(4)
    expect(within(container).getByText(/缺参考图/)).toBeInTheDocument()
    expect(within(container).getByText(/缺标定 JSON\/XML/)).toBeInTheDocument()
  })

  it('未选场景 / 未传文件直接提交 → 前置校验拦截且 submitTask 不被调用', async () => {
    render(<MemoryRouter><SubmitPage /></MemoryRouter>)
    fireEvent.click(screen.getByText('提交检测任务'))
    expect(submitTaskMock).not.toHaveBeenCalled()
    // antd message 经 rc-notification 异步挂载，须等待渲染
    expect(await screen.findByText(/请选择检测场景/)).toBeInTheDocument()
    expect(await screen.findByText(/请上传检测图像或视频/)).toBeInTheDocument()
  })

  it('多场景档只选 1 个场景 → 拦截并说明需要 ≥2 个场景', async () => {
    const { container } = render(<MemoryRouter><SubmitPage /></MemoryRouter>)
    fireEvent.click(sceneCard(container, 'fpi'))
    fireEvent.click(modeChip(container, 'joint'))
    uploadFiles(container, 0, [makeFile('a.jpg')])
    fireEvent.click(screen.getByText('提交检测任务'))
    expect(submitTaskMock).not.toHaveBeenCalled()
    expect(await screen.findByText(/多场景模式请至少选择 2 个检测场景/)).toBeInTheDocument()
  })

  it('不支持的格式 → 文件行实时报错并拦截提交', async () => {
    const { container } = render(<MemoryRouter><SubmitPage /></MemoryRouter>)
    fireEvent.click(sceneCard(container, 'fpi'))
    uploadFiles(container, 0, [makeFile('a.bmp')])
    await waitFor(() => expect(within(container).getByText(/不支持的文件格式：a.bmp/)).toBeInTheDocument())
    fireEvent.click(screen.getByText('提交检测任务'))
    expect(submitTaskMock).not.toHaveBeenCalled()
  })

  it('单场景：红外 + 图像 + CSV 校验齐备 → single 提交，成功弹窗展示任务号', async () => {
    const { container } = render(<MemoryRouter><SubmitPage /></MemoryRouter>)
    fireEvent.click(sceneCard(container, 'infrared'))
    uploadFiles(container, 0, [makeFile('a.jpg')])
    uploadFiles(container, 1, [makeFile('rad.csv')])
    await waitFor(() => expect(within(container).getByText('rad.csv')).toBeInTheDocument())
    expect(within(container).getByText(/已选场景的配套文件均齐备/)).toBeInTheDocument()
    fireEvent.click(screen.getByText('提交检测任务'))

    await waitFor(() => expect(submitTaskMock).toHaveBeenCalledTimes(1))
    const arg = submitTaskMock.mock.calls[0][0]
    expect(arg.scene).toBe('infrared')
    expect(arg.mode).toBe('single')
    expect(arg.scenes).toEqual(['infrared'])
    expect(arg.files).toHaveLength(2)
    expect(await screen.findByText('任务已提交，正在排队推理')).toBeInTheDocument()
    expect(await screen.findByText('t-123')).toBeInTheDocument()
  })

  it('多场景：孔探 + 红外 → joint 提交（scene 为 null，scenes 按选择序）', async () => {
    const { container } = render(<MemoryRouter><SubmitPage /></MemoryRouter>)
    fireEvent.click(sceneCard(container, 'borescope'))
    fireEvent.click(sceneCard(container, 'infrared'))
    uploadFiles(container, 0, [makeFile('a.jpg')])
    uploadFiles(container, 1, [makeFile('rad.csv')])
    await waitFor(() => expect(within(container).getAllByText('rad.csv').length).toBeGreaterThan(0))
    fireEvent.click(screen.getByText('提交检测任务'))

    await waitFor(() => expect(submitTaskMock).toHaveBeenCalledTimes(1))
    const arg = submitTaskMock.mock.calls[0][0]
    expect(arg.scene).toBeNull()
    expect(arg.mode).toBe('joint')
    expect(arg.scenes).toEqual(['borescope', 'infrared'])
    expect(arg.files).toHaveLength(2)
  })
})
