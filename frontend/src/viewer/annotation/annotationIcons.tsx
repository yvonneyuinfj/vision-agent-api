/* 标注工具内联 SVG 图标集：16×16 描边风格（stroke=currentColor），
   继承按钮文字色，激活态跟随 antd primary 色，无需外部图标库。 */

import type { ReactNode } from 'react'

interface IconFrameProps {
  children: ReactNode
  size?: number
}

function IconFrame({ children, size = 14 }: IconFrameProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.5}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      {children}
    </svg>
  )
}

export function IconRect() {
  return <IconFrame><rect x="2.5" y="3.5" width="11" height="9" rx="1.2" /></IconFrame>
}

export function IconEllipse() {
  return <IconFrame><ellipse cx="8" cy="8" rx="5.5" ry="4.5" /></IconFrame>
}

export function IconLine() {
  return <IconFrame><path d="M2.5 13.5 L13.5 2.5" /></IconFrame>
}

export function IconArrow() {
  return (
    <IconFrame>
      <path d="M2.5 13.5 L13 3" />
      <path d="M13 3 L8.9 2.8" />
      <path d="M13 3 L13.2 7.1" />
    </IconFrame>
  )
}

export function IconPencil() {
  return (
    <IconFrame>
      <path d="M3.2 12.8 L3.9 9.6 L11.3 2.2 C11.7 1.8 12.4 1.8 12.8 2.2 L13.8 3.2 C14.2 3.6 14.2 4.3 13.8 4.7 L6.4 12.1 Z" />
      <path d="M2.6 13.4 L5.8 10.2" />
    </IconFrame>
  )
}

export function IconMarker() {
  return (
    <IconFrame>
      <rect x="6" y="4.4" width="4" height="5.2" rx="0.8" />
      <path d="M4.8 4.4 h6.4" />
      <path d="M6 9.6 L8 12.6 L10 9.6" />
      <path d="M2.6 14 h10.8" strokeWidth={2.4} opacity={0.5} />
    </IconFrame>
  )
}

export function IconText() {
  return <IconFrame><path d="M3.2 3.8 h9.6 M8 3.8 V12.6" /></IconFrame>
}

export function IconBadge() {
  return (
    <IconFrame>
      <circle cx="8" cy="8" r="5.6" />
      <text x="8" y="8.2" fontSize="7.5" fontWeight="700" fill="currentColor" stroke="none" textAnchor="middle" dominantBaseline="central">1</text>
    </IconFrame>
  )
}

export function IconMosaic() {
  return (
    <IconFrame>
      <rect x="2.4" y="2.4" width="11.2" height="11.2" rx="2" />
      <rect x="4.2" y="4.2" width="3.4" height="3.4" fill="currentColor" stroke="none" />
      <rect x="9" y="9" width="3.4" height="3.4" fill="currentColor" stroke="none" />
      <rect x="9" y="4.2" width="3.4" height="3.4" />
      <rect x="4.2" y="9" width="3.4" height="3.4" />
    </IconFrame>
  )
}

export function IconBlur() {
  return (
    <IconFrame>
      <circle cx="8" cy="8" r="1.4" fill="currentColor" stroke="none" />
      <circle cx="8" cy="8" r="3.4" strokeDasharray="2.2 1.8" />
      <circle cx="8" cy="8" r="5.6" strokeDasharray="2.2 1.8" opacity="0.55" />
    </IconFrame>
  )
}

export function IconEraser() {
  return (
    <IconFrame>
      <path d="M5.2 11.8 L7.2 8.4 L12.2 11.2 L10.2 14.6 Z" />
      <path d="M13.6 12 h1" strokeDasharray="2 1.8" />
      <path d="M15.2 13.4 h0.4" strokeDasharray="1.4 1.6" />
    </IconFrame>
  )
}

export function IconPicker() {
  return (
    <IconFrame>
      <rect x="5.6" y="2.4" width="4.8" height="5.6" rx="0.8" />
      <path d="M5.6 8 L8 11.4 L10.4 8" />
      <circle cx="8" cy="13.4" r="1.1" fill="currentColor" stroke="none" />
    </IconFrame>
  )
}

export function IconDashToggle({ dashed }: { dashed: boolean }) {
  return (
    <IconFrame>
      <path d="M2.5 8 H13.5" strokeDasharray={dashed ? '3.5 2.6' : undefined} />
    </IconFrame>
  )
}

export function IconStroke() {
  return (
    <IconFrame>
      <path d="M2.5 4.5 h11" strokeWidth={1} />
      <path d="M2.5 8 h11" strokeWidth={2.2} />
      <path d="M2.5 11.8 h11" strokeWidth={3.6} />
    </IconFrame>
  )
}

export function IconGroupNew() {
  return (
    <IconFrame>
      <circle cx="6.8" cy="6.8" r="4.6" />
      <text x="6.8" y="7" fontSize="6.5" fontWeight="700" fill="currentColor" stroke="none" textAnchor="middle" dominantBaseline="central">1</text>
      <circle cx="12.6" cy="11.4" r="2.4" strokeDasharray="2 1.6" />
      <path d="M12.6 10 V12.8 M11.2 11.4 H14" />
    </IconFrame>
  )
}

export function IconUndo() {
  return (
    <IconFrame>
      <path d="M4.2 7.6 C6 3.9 10.2 3.5 12.7 5.8" />
      <path d="M12.7 5.8 L9.7 5.3 M12.7 5.8 L12.2 8.7" />
      <path d="M4.2 7.6 V3.4" />
    </IconFrame>
  )
}

export function IconRedo() {
  return (
    <IconFrame>
      <path d="M11.8 7.6 C10 3.9 5.8 3.5 3.3 5.8" />
      <path d="M3.3 5.8 L6.3 5.3 M3.3 5.8 L3.8 8.7" />
      <path d="M11.8 7.6 V3.4" />
    </IconFrame>
  )
}

export function IconDelete() {
  return (
    <IconFrame>
      <path d="M3 4.5 h10" />
      <path d="M6.2 4.5 V3.3 C6.2 2.8 6.6 2.4 7.2 2.4 h1.6 C9.4 2.4 9.8 2.8 9.8 3.3 V4.5" />
      <path d="M4.6 4.5 L5.3 13.6 h5.4 L11.4 4.5" />
      <path d="M6.7 7.2 V10.6 M9.3 7.2 V10.6" />
    </IconFrame>
  )
}

export function IconClear() {
  return (
    <IconFrame>
      <rect x="2.4" y="2.4" width="11.2" height="11.2" rx="2" />
      <path d="M5.7 5.7 L10.3 10.3 M10.3 5.7 L5.7 10.3" />
    </IconFrame>
  )
}

export function IconPanelToggle() {
  return (
    <IconFrame>
      <rect x="1.6" y="2.6" width="12.8" height="10.8" rx="1.6" />
      <path d="M6.4 9.8 L7 7.6 L12.1 2.5 C12.5 2.1 13.2 2.1 13.6 2.5 L13.5 2.6" />
      <path d="M4.8 11.4 L6.2 10.6" />
    </IconFrame>
  )
}