import { describe, it, expect, vi, afterEach } from 'vitest'
import { loadKonvaImage } from './useKonvaImage'

// mock 贴近 utif2 真实语义：decodeImage 把宽高写回 IFD，toRGBA8 返回纯像素数组
vi.mock('utif2', () => ({
  decode: vi.fn(() => [{}]),
  decodeImage: vi.fn((_b: unknown, ifd: { width?: number; height?: number }) => {
    ifd.width = 4
    ifd.height = 2
  }),
  toRGBA8: vi.fn(() => new Uint8Array(32)),
}))

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('loadKonvaImage', () => {
  it('非 TIFF 扩展走 Image 加载，失败返回 null', async () => {
    // jsdom 不会真正加载图片资源（onload/onerror 均不触发），
    // 用 stub Image 模拟解码失败路径：设置 src 即触发 onerror
    class MockImage {
      onload: (() => void) | null = null
      onerror: (() => void) | null = null
      naturalWidth = 0
      naturalHeight = 0
      set src(_v: string) {
        this.onerror?.()
      }
    }
    vi.stubGlobal('Image', MockImage)

    const result = await loadKonvaImage('/api/v1/tasks/t/files/a.png?kind=artifacts')
    expect(result).toBeNull()
  })

  it('TIFF 扩展走 UTIF 解码分支', async () => {
    // Node fetch 不支持相对 URL，且 jsdom 无 2d context，均需 stub
    vi.stubGlobal('fetch', vi.fn(async () => ({ arrayBuffer: async () => new ArrayBuffer(16) }) as Response))
    const fakeCtx = {
      createImageData: vi.fn(() => ({ width: 4, height: 2, data: new Uint8Array(32) })),
      putImageData: vi.fn(),
    }
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(fakeCtx as unknown as CanvasRenderingContext2D)

    const utif = await import('utif2')
    const result = await loadKonvaImage('/x.tif')
    expect(utif.decode).toHaveBeenCalled()
    expect(result).toBeInstanceOf(HTMLCanvasElement)
  })
})