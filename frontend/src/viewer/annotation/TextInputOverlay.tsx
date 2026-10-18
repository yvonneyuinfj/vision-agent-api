/* 文本标注输入浮层：锚定在图像自然坐标（视口坐标由 Viewer 实时换算）。
   Enter 提交、Shift+Enter 换行、Esc 取消；失焦时自动提交。 */

import { useEffect, useRef, useState } from 'react'
import type { AnnotationPoint } from './annotationTypes'

interface Props {
  viewport: AnnotationPoint | null
  onSubmit: (text: string) => void
  onCancel: () => void
}

export default function TextInputOverlay({ viewport, onSubmit, onCancel }: Props) {
  const [value, setValue] = useState('')
  const ref = useRef<HTMLTextAreaElement>(null)
  const doneRef = useRef(false)

  useEffect(() => {
    ref.current?.focus()
  }, [])

  useEffect(() => {
    if (!viewport) return undefined
    return () => {
      doneRef.current = true
    }
  }, [viewport])

  if (!viewport) return null

  function finish() {
    if (doneRef.current) return
    doneRef.current = true
    const text = value.trim()
    if (text) onSubmit(text)
    else onCancel()
  }

  return (
    <div
      className="glass-float"
      style={{
        position: 'absolute',
        left: viewport.x,
        top: viewport.y,
        zIndex: 20,
        borderRadius: 8,
        padding: '2px 8px',
        maxWidth: 280,
        pointerEvents: 'auto',
      }}
      onMouseDown={(e) => e.stopPropagation()}
    >
      <textarea
        ref={ref}
        value={value}
        onChange={(e) => setValue(e.target.value)}
        placeholder="输入标注文字，Enter 确认，Esc 取消"
        rows={Math.min(4, Math.max(1, value.split('\n').length))}
        onKeyDown={(e) => {
          e.stopPropagation()
          if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault()
            finish()
          } else if (e.key === 'Escape') {
            e.preventDefault()
            doneRef.current = true
            onCancel()
          }
        }}
        onBlur={finish}
        style={{
          background: 'transparent',
          border: 'none',
          outline: 'none',
          color: 'var(--fg)',
          fontSize: 14,
          lineHeight: 1.5,
          resize: 'none',
          minWidth: 160,
          minHeight: 24,
          fontFamily: 'inherit',
        }}
      />
    </div>
  )
}