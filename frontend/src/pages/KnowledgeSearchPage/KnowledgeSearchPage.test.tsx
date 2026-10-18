import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import KnowledgeSearchPage from './index'

const { mockUseKbChat } = vi.hoisted(() => ({ mockUseKbChat: vi.fn() }))
vi.mock('./useKbChat', () => ({ useKbChat: mockUseKbChat }))

const config = {
  name: 'build_chat', dataset_ids: ['ds1'], llm_id: 'qwen', llm_setting: {},
  prompt_config: { quote: true, prologue: '你好', system: '请参考 {knowledge}' }, similarity_threshold: 0.6, vector_similarity_weight: 0.7, top_n: 5, top_k: 1024,
}

function chatState(overrides = {}) {
  return {
    info: { ready: true, chat_id: 'chat1', error: null, config },
    sessions: [{ id: 's1', chat_id: 'chat1', name: '标准查询', messages: [] }],
    activeId: 's1',
    messages: [
      { key: 'opening', role: 'assistant', content: '你好！我是你的助理。' },
      { key: 'm1:user', id: 'm1', role: 'user', content: '裂纹如何判级？' },
      {
        key: 'm1:assistant', id: 'm1', role: 'assistant', content: '按标准执行##0$$。',
        reference: { chunks: [{ id: 'c1', content: '完整引用内容', document_id: 'd1', document_name: '孔探手册.pdf', similarity: 0.91 }] },
      },
    ],
    loading: false, streaming: false, error: null,
    datasets: [{ id: 'ds1', name: '航发判级标准', document_count: 12, chunk_count: 318, description: '现行规范与案例' }],
    datasetsLoading: false, datasetsError: null, refreshDatasets: vi.fn(),
    openSession: vi.fn(), newSession: vi.fn(), send: vi.fn(), retry: vi.fn(), clear: vi.fn(),
    renameSession: vi.fn(), removeSession: vi.fn(), saveConfig: vi.fn(), stop: vi.fn(), refresh: vi.fn(),
    ...overrides,
  }
}

// 页面容器按 default 导出 import Tabs（用户并行组件，未跟踪文件）：mock 成按钮组，
// 测试不依赖其真实实现，只验证 items 渲染与 onChange(index) 透传
vi.mock('../../components/ui/Tabs', () => ({
  default: ({ items, value, onChange }: { items: string[]; value: number; onChange: (i: number) => void }) => (
    <div className="tabs-mock">
      {items.map((label, i) => (
        <button key={label} type="button" onClick={() => onChange(i)}>{label}</button>
      ))}
      <span data-testid="tabs-value">{value}</span>
    </div>
  ),
}))

describe('KnowledgeSearchPage chat mode', () => {
  beforeEach(() => {
    localStorage.clear()
    mockUseKbChat.mockReturnValue(chatState())
  })

  it('渲染多会话、对话输入和完整知识引用', () => {
    render(<KnowledgeSearchPage />)
    expect(screen.getByText('历史会话')).toBeInTheDocument()
    expect(screen.getAllByText('标准查询')).toHaveLength(2)
    expect(screen.getByPlaceholderText(/Enter 发送/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /完整引用/ })).toBeInTheDocument()
    expect(screen.getByText('孔探手册.pdf')).toBeInTheDocument()
    expect(screen.getByText('完整引用内容')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '查看原始文档' })).toBeInTheDocument()
  })

  it('兼容旧版与新版 RAGFlow 引用标记，并可跳转到对应引用', () => {
    const state = chatState({
      messages: [{
        key: 'm2:assistant',
        id: 'm2',
        role: 'assistant',
        content: '旧版引用##0$$，新版引用[ID:2]，越界引用[ID:9]。',
        reference: {
          chunks: [
            { id: 'c0', content: '第一条引用内容', document_name: '文档一.pdf' },
            { id: 'c1', content: '第二条引用内容', document_name: '文档二.pdf' },
            { id: 'c2', content: '第三条引用内容', document_name: '文档三.pdf' },
          ],
        },
      }],
    })
    mockUseKbChat.mockReturnValue(state)

    const { container } = render(<KnowledgeSearchPage />)
    const citationLinks = container.querySelectorAll<HTMLAnchorElement>('.chat-message__content a[href^="ref://"]')

    expect(citationLinks).toHaveLength(2)
    expect(citationLinks[0]).toHaveAttribute('href', 'ref://c0')
    expect(citationLinks[0]).toHaveTextContent('1')
    expect(citationLinks[1]).toHaveAttribute('href', 'ref://c2')
    expect(citationLinks[1]).toHaveTextContent('3')
    expect(screen.getByText(/越界引用\[ID:9\]/)).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '收起知识引用' }))
    expect(container.querySelector('.kb-chat-shell')).toHaveClass('is-citations-collapsed')
    const collapsedCitationLinks = container.querySelectorAll<HTMLAnchorElement>('.chat-message__content a[href^="ref://"]')
    fireEvent.click(collapsedCitationLinks[1])
    expect(container.querySelector('.kb-chat-shell')).not.toHaveClass('is-citations-collapsed')
    expect(screen.getByText('引用 3')).toBeInTheDocument()
    expect(screen.getByText('第三条引用内容')).toBeInTheDocument()
  })

  it('多轮追问缺少 reference 时安全继承上一轮引用', () => {
    const reference = {
      chunks: [{ id: 'source-0', content: 'TC4 温度敏感性原文', document_name: 'TC4研究.pdf' }],
    }
    mockUseKbChat.mockReturnValue(chatState({
      messages: [
        { key: 'm1:assistant', id: 'm1', role: 'assistant', content: '作者信息[ID:0]', reference },
        { key: 'm2:user', id: 'm2', role: 'user', content: '对温度敏感吗？' },
        { key: 'm2:assistant', id: 'm2', role: 'assistant', content: '对温度敏感[ID:0]' },
        { key: 'm3:assistant', id: 'm3', role: 'assistant', content: '无法确认[ID:1]' },
      ],
    }))

    const { container } = render(<KnowledgeSearchPage />)
    const links = container.querySelectorAll<HTMLAnchorElement>('.chat-message__content a[href="ref://source-0"]')
    expect(links).toHaveLength(2)
    expect(screen.getByText(/无法确认\[ID:1\]/)).toBeInTheDocument()
  })

  it('Enter 发送消息，Shift+Enter 不发送', () => {
    const state = chatState()
    mockUseKbChat.mockReturnValue(state)
    render(<KnowledgeSearchPage />)
    const input = screen.getByPlaceholderText(/Enter 发送/)
    fireEvent.change(input, { target: { value: '新的问题' } })
    fireEvent.keyDown(input, { key: 'Enter', shiftKey: true })
    expect(state.send).not.toHaveBeenCalled()
    fireEvent.keyDown(input, { key: 'Enter', isComposing: true })
    expect(state.send).not.toHaveBeenCalled()
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(state.send).toHaveBeenCalledWith('新的问题')
  })

  it('支持重新生成和清空会话', () => {
    const state = chatState()
    mockUseKbChat.mockReturnValue(state)
    render(<KnowledgeSearchPage />)
    const retryButton = screen.getAllByRole('button', { name: '重新生成' })[0]
    expect(retryButton.querySelector('svg')).toBeInTheDocument()
    expect(screen.queryByText('重新生成')).not.toBeInTheDocument()
    fireEvent.click(retryButton)
    expect(state.retry).toHaveBeenCalledWith('m1')
    fireEvent.click(screen.getByRole('button', { name: '清空会话' }))
    expect(state.clear).toHaveBeenCalled()
  })

  it('可行内重命名当前会话，并支持 Enter 保存与 Escape 取消', async () => {
    const state = chatState()
    state.renameSession.mockResolvedValue(undefined)
    mockUseKbChat.mockReturnValue(state)
    render(<KnowledgeSearchPage />)

    fireEvent.click(screen.getByRole('button', { name: '重命名会话“标准查询”' }))
    const input = screen.getByRole('textbox', { name: '会话名称' })
    expect(input).toHaveFocus()
    fireEvent.change(input, { target: { value: 'GH738 超高周疲劳研究' } })
    fireEvent.keyDown(input, { key: 'Enter' })
    await waitFor(() => expect(state.renameSession).toHaveBeenCalledWith('s1', 'GH738 超高周疲劳研究'))
    await waitFor(() => expect(screen.getByRole('button', { name: '重命名会话“标准查询”' })).toHaveFocus())

    fireEvent.click(screen.getByRole('button', { name: '重命名会话“标准查询”' }))
    fireEvent.change(screen.getByRole('textbox', { name: '会话名称' }), { target: { value: '不保存的名称' } })
    fireEvent.keyDown(screen.getByRole('textbox', { name: '会话名称' }), { key: 'Escape' })
    expect(screen.queryByRole('textbox', { name: '会话名称' })).not.toBeInTheDocument()
    expect(state.renameSession).toHaveBeenCalledTimes(1)
  })

  it('重命名失败时保留输入并提供原位错误', async () => {
    const state = chatState()
    state.renameSession.mockRejectedValue(new Error('会话名称保存失败，请重试'))
    mockUseKbChat.mockReturnValue(state)
    render(<KnowledgeSearchPage />)

    fireEvent.click(screen.getByRole('button', { name: '重命名会话“标准查询”' }))
    const input = screen.getByRole('textbox', { name: '会话名称' })
    fireEvent.change(input, { target: { value: 'GH738 研究' } })
    fireEvent.click(screen.getByRole('button', { name: '保存' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('会话名称保存失败，请重试')
    expect(screen.getByRole('textbox', { name: '会话名称' })).toHaveValue('GH738 研究')
  })

  it('左右面板可独立折叠、恢复并记住状态', () => {
    const { container, unmount } = render(<KnowledgeSearchPage />)
    const shell = container.querySelector('.kb-chat-shell')

    const sessionsToggle = screen.getByRole('button', { name: '收起历史会话' })
    expect(sessionsToggle).toHaveAttribute('aria-expanded', 'true')
    fireEvent.click(sessionsToggle)
    expect(shell).toHaveClass('is-sessions-collapsed')
    expect(localStorage.getItem('aeroqc:kbChatSessionsCollapsed')).toBe('1')
    expect(container.querySelector('.chat-session__main')).not.toBeVisible()
    expect(screen.getByRole('button', { name: '展开历史会话' })).toHaveAttribute('aria-expanded', 'false')

    fireEvent.click(screen.getByRole('button', { name: '收起知识引用' }))
    expect(shell).toHaveClass('is-citations-collapsed')
    expect(localStorage.getItem('aeroqc:kbChatCitationsCollapsed')).toBe('1')
    expect(screen.queryByRole('button', { name: '查看原始文档' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '展开知识引用' })).toHaveAttribute('aria-expanded', 'false')

    unmount()
    const { container: restoredContainer } = render(<KnowledgeSearchPage />)
    const restoredShell = restoredContainer.querySelector('.kb-chat-shell')
    expect(restoredShell).toHaveClass('is-sessions-collapsed', 'is-citations-collapsed')
    fireEvent.click(screen.getByRole('button', { name: '展开历史会话' }))
    expect(restoredShell).not.toHaveClass('is-sessions-collapsed')
    expect(restoredContainer.querySelector('.chat-session__main')).toBeVisible()
  })

  it('可在运行中查看并更新助手配置', async () => {
    const state = chatState()
    state.saveConfig.mockResolvedValue(undefined)
    mockUseKbChat.mockReturnValue(state)
    render(<KnowledgeSearchPage />)
    fireEvent.click(screen.getByRole('button', { name: '助手配置' }))
    const dialog = screen.getByRole('dialog', { name: /配置知识助手/ })
    expect(dialog).toBeInTheDocument()
    expect(within(dialog).getByText('航发判级标准')).toBeInTheDocument()
    expect(within(dialog).getByRole('radio', { name: /平衡/ })).toBeChecked()
    const nameInput = within(dialog).getByDisplayValue('build_chat')
    fireEvent.change(nameInput, { target: { value: '判级问答助手' } })
    fireEvent.click(within(dialog).getByRole('button', { name: '保存并应用' }))
    await waitFor(() => expect(state.saveConfig).toHaveBeenCalledWith(expect.objectContaining({
      name: '判级问答助手',
      dataset_ids: ['ds1'],
    })))
  })

  it('数据集异常时保留现有选择并提供重试', () => {
    const state = chatState({ datasets: [], datasetsError: '连接失败' })
    mockUseKbChat.mockReturnValue(state)
    render(<KnowledgeSearchPage />)
    fireEvent.click(screen.getByRole('button', { name: '助手配置' }))
    const dialog = screen.getByRole('dialog', { name: /配置知识助手/ })
    expect(within(dialog).getByText('暂时无法识别的知识库')).toBeInTheDocument()
    fireEvent.click(within(dialog).getByRole('button', { name: '重新加载' }))
    expect(state.refreshDatasets).toHaveBeenCalled()
  })

  /* 原型 /knowledge/search 右栏两卡（概况 / 无依据行为）并入引用面板：内容必须真实来自接口 */
  it('引用面板给出知识库概况与无依据行为声明', () => {
    render(<KnowledgeSearchPage />)
    const kb = document.querySelector('.citation-panel__kb') as HTMLElement
    expect(kb).toBeTruthy()
    expect(kb.textContent).toContain('航发判级标准')
    expect(kb.textContent).toContain('12 篇')
    // 概况来源只声明真实接口，不承诺不存在的 doc 级索引状态
    expect(kb.textContent).toContain('文档级解析 / 索引状态无接口')
    expect(document.querySelector('.citation-panel__policy-box')?.textContent).toContain('不得生成肯定结论')
  })

  it('数据集读取失败时概况卡如实说明而非空表', () => {
    mockUseKbChat.mockReturnValue(chatState({ datasets: [], datasetsError: 'RAGFlow 未就绪' }))
    render(<KnowledgeSearchPage />)
    const kb = document.querySelector('.citation-panel__kb') as HTMLElement
    expect(kb.textContent).toContain('RAGFlow 未就绪')
    expect(kb.querySelector('ul')).toBeNull()
  })
})
