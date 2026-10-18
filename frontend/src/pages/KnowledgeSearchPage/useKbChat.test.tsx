import { act, renderHook, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../../api/kb', () => ({
  createChatSession: vi.fn(),
  deleteChatMessage: vi.fn(),
  deleteChatSession: vi.fn(),
  fetchChatDatasets: vi.fn(),
  fetchChatInfo: vi.fn(),
  fetchChatSession: vi.fn(),
  listChatSessions: vi.fn(),
  renameChatSession: vi.fn(),
  streamChatCompletion: vi.fn(),
  updateChatConfig: vi.fn(),
}))

import {
  fetchChatDatasets,
  fetchChatInfo,
  fetchChatSession,
  listChatSessions,
  renameChatSession,
  streamChatCompletion,
} from '../../api/kb'
import { deriveSessionTitle, useKbChat } from './useKbChat'

const defaultSession = {
  id: 's1',
  chat_id: 'chat1',
  name: '新会话 14:23',
  messages: [{ role: 'assistant' as const, content: '你好' }],
}

describe('useKbChat session naming', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(fetchChatInfo).mockResolvedValue({ ready: true, chat_id: 'chat1', error: null, config: null })
    vi.mocked(fetchChatDatasets).mockResolvedValue([])
    vi.mocked(listChatSessions).mockResolvedValue([defaultSession])
    vi.mocked(fetchChatSession).mockResolvedValue(defaultSession)
    vi.mocked(renameChatSession).mockImplementation(async (id, name) => ({ ...defaultSession, id, name }))
    vi.mocked(streamChatCompletion).mockImplementation(async (_sessionId, _question, _messageId, onChunk) => {
      onChunk({ answer: '回答', final: true })
    })
  })

  it('从首条问题生成简洁标题并持久化，不阻塞回答', async () => {
    let finishRename: (() => void) | undefined
    vi.mocked(renameChatSession).mockImplementationOnce((id, name) => new Promise((resolve) => {
      finishRename = () => resolve({ ...defaultSession, id, name })
    }))
    const { result } = renderHook(() => useKbChat())
    await waitFor(() => expect(result.current.activeId).toBe('s1'))

    await act(async () => {
      await result.current.send('请问高温合金GH738超高周疲劳行为研究的作者是谁？')
    })

    expect(renameChatSession).toHaveBeenCalledWith('s1', '高温合金GH738超高周疲劳行为研究的作者是谁')
    expect(streamChatCompletion).toHaveBeenCalled()
    expect(result.current.streaming).toBe(false)
    await act(async () => finishRename?.())
  })

  it('打开已有内容的默认名称会话时补齐标题', async () => {
    vi.mocked(fetchChatSession).mockResolvedValue({
      ...defaultSession,
      messages: [
        { role: 'assistant', content: '你好' },
        { role: 'user', content: 'GH738超高周疲劳行为研究的作者是谁？' },
      ],
    })

    renderHook(() => useKbChat())

    await waitFor(() => expect(renameChatSession).toHaveBeenCalledWith(
      's1',
      'GH738超高周疲劳行为研究的作者是谁',
    ))
  })

  it('标题清理换行、Markdown 和尾部标点，并限制长度', () => {
    expect(deriveSessionTitle('帮我一下， **分析**\n GH738 疲劳行为？')).toBe('分析 GH738 疲劳行为')
    expect(Array.from(deriveSessionTitle('这是一个非常长的会话问题用于验证标题不会无限制撑开历史会话列表和顶部工具栏')).length).toBeLessThanOrEqual(28)
  })
})
