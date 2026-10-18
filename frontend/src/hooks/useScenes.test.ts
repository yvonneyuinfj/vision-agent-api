import { describe, it, expect, beforeEach, vi } from 'vitest'
import { renderHook, waitFor } from '@testing-library/react'
import { WORKBENCHES } from '../constants/workbench'

/** 目录取数与回退（决策 52）：取到就用目录，取不到用内置六条——
 *  导航与页面不能因为一次读取失败就空掉，也不能把失败说成"该场景不存在"。
 *  hook 模块级缓存了请求，每条用例重置换例。 */
const fetchScenes = vi.fn()

beforeEach(async () => {
  vi.resetModules()
  vi.doMock('../api/scenes', async () => {
    const actual = await vi.importActual<typeof import('../api/scenes')>('../api/scenes')
    return { ...actual, fetchScenes: fetchScenes }
  })
  fetchScenes.mockReset()
})

async function load() {
  const { useScenes } = await import('./useScenes')
  return renderHook(() => useScenes())
}

describe('useScenes', () => {
  it('取到目录即用目录条目，含第七个场景与 auto_recognize', async () => {
    fetchScenes.mockResolvedValue([
      {
        scene: 'coating',
        label: '涂层检测',
        short: '涂层',
        en: 'COATING',
        params: 'draft',
        input: '仅需检测图像',
        auto_recognize: false,
      },
    ])
    const { result } = await load()
    await waitFor(() => expect(result.current.fetched).not.toBeNull())
    expect(result.current.scenes).toEqual([
      {
        scene: 'coating',
        label: '涂层检测',
        en: 'COATING',
        params: 'draft',
        input: '仅需检测图像',
        autoRecognize: false,
      },
    ])
    expect(result.current.error).toBeNull()
  })

  it('读取失败回退内置六条并带上错误文案', async () => {
    fetchScenes.mockRejectedValue(new Error('后端未部署该接口'))
    const { result } = await load()
    await waitFor(() => expect(result.current.error).not.toBeNull())
    expect(result.current.fetched).toBeNull()
    expect(result.current.scenes).toEqual(WORKBENCHES)
    expect(result.current.error).toContain('后端未部署该接口')
  })

  it('目录为空数组＝接口异常，不呈现零个工作台', async () => {
    fetchScenes.mockResolvedValue([])
    const { result } = await load()
    await waitFor(() => expect(result.current.fetched === null).toBe(true))
    expect(result.current.scenes).toHaveLength(WORKBENCHES.length)
  })
})
