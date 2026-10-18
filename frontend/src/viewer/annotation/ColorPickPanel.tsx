/* 放大镜取色面板：随光标悬浮显示像素放大预览，单击锁定后给出
   HEX / RGB / HSL 三种格式一键复制；锁定色同时成为当前笔色。 */

import { useEffect, useRef } from 'react'
import { Button, Space, message } from 'antd'
import { CopyOutlined } from '@ant-design/icons'
import { useAnnotationStore } from './useAnnotationStore'
import type { PixelSource } from './annotationPixels'

interface Props {
  source: PixelSource
}

/** 放大镜：15 × 15 像素网格，每格 9px */
const CELL_PX = 9
const GRID_SIZE = 15
const MAG_CANVAS_PX = CELL_PX * GRID_SIZE

const CROSSHAIR = '#ff6b81'

async function copyValue(label: string, value: string) {
  try {
    await navigator.clipboard.writeText(value)
    message.success(`已复制 ${label}（${value}）`)
  } catch (err) {
    message.error(`复制失败，请手动复制：${value}`)
    console.error('剪贴板写入失败', err)
  }
}

export default function ColorPickPanel({ source }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const pickSample = useAnnotationStore((s) => s.pickSample)
  const pickedColor = useAnnotationStore((s) => s.pickedColor)

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas || !pickSample) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return

    const half = GRID_SIZE / 2
    const swSrc = GRID_SIZE * source.ratioX
    const shSrc = GRID_SIZE * source.ratioY
    const sxSrc = (pickSample.nx - half) * source.ratioX
    const sySrc = (pickSample.ny - half) * source.ratioY
    // 边缘像素不足时偏移绘制起点，画面仍然铺满
    const dxOff = Math.min(0, sxSrc)
    const dyOff = Math.min(0, sySrc)
    const pxPerSrc = MAG_CANVAS_PX / swSrc

    ctx.imageSmoothingEnabled = false
    ctx.fillStyle = '#05070f'
    ctx.fillRect(0, 0, MAG_CANVAS_PX, MAG_CANVAS_PX)
    try {
      ctx.drawImage(
        source.canvas,
        sxSrc - dxOff, sySrc - dyOff, swSrc, shSrc,
        -dxOff * pxPerSrc, -dyOff * pxPerSrc, MAG_CANVAS_PX, MAG_CANVAS_PX,
      )
    } catch (err) {
      console.error('放大镜采样失败（源图不可读）', err)
    }
    // 中心十字线
    ctx.strokeStyle = CROSSHAIR
    ctx.lineWidth = 1
    const c = MAG_CANVAS_PX / 2
    ctx.beginPath()
    ctx.moveTo(c - 5, c); ctx.lineTo(c + 5, c)
    ctx.moveTo(c, c - 5); ctx.lineTo(c, c + 5)
    ctx.stroke()
  }, [pickSample, source])

  const value = pickedColor ?? pickSample
  if (!value) return null

  const rows: Array<{ label: string; value: string }> = [
    { label: 'HEX', value: value.hex },
    { label: 'RGB', value: value.rgb },
    { label: 'HSL', value: value.hsl },
  ]

  return (
    <div
      className="glass-float"
      style={{
        position: 'absolute',
        left: pickSample ? pickSample.screenX + 18 : 12,
        top: pickSample ? pickSample.screenY + 18 : 12,
        zIndex: 20,
        borderRadius: 10,
        padding: 8,
        display: 'flex',
        flexDirection: 'column',
        gap: 6,
        pointerEvents: 'auto',
        minWidth: 180,
      }}
    >
      <canvas
        ref={canvasRef}
        width={MAG_CANVAS_PX}
        height={MAG_CANVAS_PX}
        style={{ width: MAG_CANVAS_PX, height: MAG_CANVAS_PX, borderRadius: 6, border: '1px solid var(--border-strong)', display: 'block' }}
      />
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <span
          aria-label="picked-swatch"
          style={{ width: 18, height: 18, borderRadius: 5, background: value.hex, border: '1px solid var(--border-strong)', flexShrink: 0 }}
        />
        <span style={{ fontSize: 12, color: 'var(--fg)', fontFamily: 'ui-monospace, Menlo, monospace' }}>
          {pickedColor ? '已锁定（点击已同步为笔色）' : '移动取色 · 点击锁定'}
        </span>
      </div>
      <Space direction="vertical" size={2}>
        {rows.map((row) => (
          <div key={row.label} style={{ display: 'flex', alignItems: 'center', gap: 8, justifyContent: 'space-between' }}>
            <span style={{ fontSize: 11, color: 'var(--muted)', width: 26 }}>{row.label}</span>
            <code style={{ fontSize: 12, color: 'var(--fg)', fontFamily: 'ui-monospace, Menlo, monospace' }}>{row.value}</code>
            <Button size="small" type="text" icon={<CopyOutlined />} onClick={() => copyValue(row.label, row.value)} aria-label={`copy-${row.label}`} />
          </div>
        ))}
      </Space>
    </div>
  )
}