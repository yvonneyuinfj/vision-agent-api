/* 像素级图像处理：脱敏位图生成 + 取色采样。
   依赖浏览器 Canvas 2D（jsdom 不可用，验证走浏览器）；所有失败路径都有降级，不给 UI 崩。 */

import type { AnnotationPoint } from './annotationTypes'

export interface PixelSource {
  canvas: HTMLCanvasElement
  /** 源画布 x = 图像自然 x * ratioX（画布按需等比缩小过） */
  ratioX: number
  ratioY: number
}

export type MaskKind = 'mosaic' | 'blur'

/** 源画布最大边长封顶（防超大原图吃内存） */
const MAX_CANVAS_SIDE = 2048
/** 脱敏位图输出最大边长 */
const MAX_MASK_SIDE = 1024
/** 马赛克单元格在 100% 缩放下的目标边长（图像像素） */
const MOSAIC_CELL_SCREEN_PX = 8
/** 高斯模糊在 100% 缩放下的目标半径（图像像素） */
const BLUR_RADIUS_SCREEN_PX = 8

function makeCanvas(w: number, h: number): { canvas: HTMLCanvasElement; ctx: CanvasRenderingContext2D } | null {
  const canvas = document.createElement('canvas')
  canvas.width = Math.max(1, Math.floor(w))
  canvas.height = Math.max(1, Math.floor(h))
  const ctx = canvas.getContext('2d')
  return ctx ? { canvas, ctx } : null
}

/** 从已加载的位图建像素源（等比缩放到封顶边长）；不可用时返回 null */
export function buildPixelSource(image: HTMLImageElement | HTMLCanvasElement): PixelSource | null {
  try {
    // img 优先解码后的原始尺寸；canvas 直接取画布尺寸
    const w = image instanceof HTMLImageElement ? image.naturalWidth || image.width : image.width
    const h = image instanceof HTMLImageElement ? image.naturalHeight || image.height : image.height
    if (!w || !h) return null
    const ratio = Math.min(1, MAX_CANVAS_SIDE / Math.max(w, h))
    const canvas = document.createElement('canvas')
    canvas.width = Math.max(1, Math.round(w * ratio))
    canvas.height = Math.max(1, Math.round(h * ratio))
    const ctx = canvas.getContext('2d')
    if (!ctx) return null
    ctx.drawImage(image, 0, 0, canvas.width, canvas.height)
    return { canvas, ratioX: canvas.width / w, ratioY: canvas.height / h }
  } catch (err) {
    console.error('标注像素源构建失败（可能跨域/解码失败）', err)
    return null
  }
}

/** 采样一个图像自然坐标的像素；源不可读（跨域污染等）返回 null */
export function samplePixel(source: PixelSource, point: AnnotationPoint): { r: number; g: number; b: number } | null {
  try {
    const sx = Math.floor(point.x * source.ratioX)
    const sy = Math.floor(point.y * source.ratioY)
    const out = makeCanvas(1, 1)
    if (!out) return null
    out.ctx.drawImage(source.canvas, sx, sy, 1, 1, 0, 0, 1, 1)
    const data = out.ctx.getImageData(0, 0, 1, 1).data
    return { r: data[0], g: data[1], b: data[2] }
  } catch (err) {
    console.error('像素采样失败（源图不可读）', err)
    return null
  }
}

/** 降级遮罩：读不到源像素时仍保证敏感信息被遮盖 */
function fallbackMask(kind: MaskKind): string {
  const size = 48
  const out = makeCanvas(size, size)
  if (!out) return ''
  if (kind === 'mosaic') {
    const cell = 8
    for (let y = 0; y < size; y += cell) {
      for (let x = 0; x < size; x += cell) {
        const even = ((x + y) / cell) % 2 === 0
        out.ctx.fillStyle = even ? '#3a3e46' : '#2c2f36'
        out.ctx.fillRect(x, y, cell, cell)
      }
    }
  } else {
    out.ctx.fillStyle = 'rgba(15, 17, 23, 0.72)'
    out.ctx.fillRect(0, 0, size, size)
  }
  return out.canvas.toDataURL('image/png')
}

/**
 * 生成脱敏位图（对应 start/end 定义的图像区域）：
 * mosaic = 像素化；blur = 高斯模糊（模糊取样向外扩一圈，避免边缘褪色）。
 * 源不可读时走降级遮罩。scaleAtCreation 用于把「100% 缩放下的目标粒度」换算到当前图像坐标。
 */
export function buildMaskDataUrl(
  source: PixelSource | null,
  start: AnnotationPoint,
  end: AnnotationPoint,
  kind: MaskKind,
  scaleAtCreation: number,
): string {
  const w = Math.max(1, end.x - start.x)
  const h = Math.max(1, end.y - start.y)
  if (!source) return fallbackMask(kind)

  const outScale = Math.min(1, MAX_MASK_SIDE / Math.max(w, h))
  const ow = Math.max(1, Math.round(w * outScale))
  const oh = Math.max(1, Math.round(h * outScale))

  try {
    if (kind === 'mosaic') {
      const cell = Math.max(2, Math.round((MOSAIC_CELL_SCREEN_PX * outScale) / Math.max(scaleAtCreation, 0.01)))
      // 像素化 = 缩到 1/cell 再用最近邻放大
      const smallW = Math.max(1, Math.floor(ow / cell))
      const smallH = Math.max(1, Math.floor(oh / cell))
      const small = makeCanvas(smallW, smallH)
      if (!small) return fallbackMask(kind)
      small.ctx.drawImage(
        source.canvas,
        start.x * source.ratioX, start.y * source.ratioY,
        w * source.ratioX, h * source.ratioY,
        0, 0, smallW, smallH,
      )
      const out = makeCanvas(ow, oh)
      if (!out) return fallbackMask(kind)
      out.ctx.imageSmoothingEnabled = false
      out.ctx.drawImage(small.canvas, 0, 0, ow, oh)
      return out.canvas.toDataURL('image/png')
    }

    // blur：四周外扩模糊半径取样，模糊后裁回中心，边缘不褪色
    const radius = Math.max(1, (BLUR_RADIUS_SCREEN_PX * outScale) / Math.max(scaleAtCreation, 0.01))
    const m = Math.min(Math.ceil(radius) + 1, 64)
    const expand = m / outScale
    const sx0 = Math.max(0, start.x - expand)
    const sy0 = Math.max(0, start.y - expand)
    const sx1 = Math.min(source.canvas.width / source.ratioX, end.x + expand)
    const sy1 = Math.min(source.canvas.height / source.ratioY, end.y + expand)
    const srcW = Math.max(1, sx1 - sx0)
    const srcH = Math.max(1, sy1 - sy0)
    const padW = ow + m * 2
    const padH = oh + m * 2

    const pad = makeCanvas(padW, padH)
    if (!pad) return fallbackMask(kind)
    pad.ctx.drawImage(
      source.canvas,
      sx0 * source.ratioX, sy0 * source.ratioY,
      srcW * source.ratioX, srcH * source.ratioY,
      0, 0, padW, padH,
    )

    const blurred = makeCanvas(padW, padH)
    if (!blurred) return fallbackMask(kind)
    blurred.ctx.filter = `blur(${radius}px)`
    blurred.ctx.drawImage(pad.canvas, 0, 0)

    const out = makeCanvas(ow, oh)
    if (!out) return fallbackMask(kind)
    out.ctx.drawImage(blurred.canvas, m, m, ow, oh, 0, 0, ow, oh)
    return out.canvas.toDataURL('image/png')
  } catch (err) {
    console.error('脱敏位图生成失败，使用降级遮罩', err)
    return fallbackMask(kind)
  }
}