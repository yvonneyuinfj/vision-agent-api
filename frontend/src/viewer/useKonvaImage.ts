import { useEffect, useState } from 'react'

export async function loadKonvaImage(src: string): Promise<HTMLCanvasElement | null> {
  const lower = src.toLowerCase()
  if (lower.endsWith('.tif') || lower.endsWith('.tiff')) {
    try {
      const UTIF = await import('utif2')
      const res = await fetch(src)
      const buf = await res.arrayBuffer()
      const ifds = UTIF.decode(buf)
      if (!ifds.length) return null
      // utif2 的 decodeImage 将 width/height/data 原地写入 IFD，toRGBA8 返回纯 RGBA 像素数组
      UTIF.decodeImage(buf, ifds[0])
      const rgba = UTIF.toRGBA8(ifds[0])
      const { width, height } = ifds[0]
      const canvas = document.createElement('canvas')
      canvas.width = width
      canvas.height = height
      const ctx = canvas.getContext('2d')
      if (!ctx) return null
      const imgData = ctx.createImageData(width, height)
      imgData.data.set(rgba)
      ctx.putImageData(imgData, 0, 0)
      return canvas
    } catch {
      return null
    }
  }
  return new Promise((resolve) => {
    const img = new Image()
    img.onload = () => {
      const canvas = document.createElement('canvas')
      canvas.width = img.naturalWidth
      canvas.height = img.naturalHeight
      const ctx = canvas.getContext('2d')
      ctx?.drawImage(img, 0, 0)
      resolve(canvas)
    }
    img.onerror = () => resolve(null)
    img.src = src
  })
}

/** 在 React 组件中异步加载图像为 Konva 可用 canvas */
export function useKonvaImage(src: string | null) {
  const [image, setImage] = useState<HTMLCanvasElement | null>(null)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    setImage(null)
    setFailed(false)
    if (!src) return
    let alive = true
    loadKonvaImage(src).then((c) => {
      if (!alive) return
      if (c) setImage(c)
      else setFailed(true)
    })
    return () => { alive = false }
  }, [src])

  return { image, failed }
}