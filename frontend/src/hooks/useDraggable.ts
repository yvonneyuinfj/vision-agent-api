import { useRef, useState, type CSSProperties, type PointerEvent as ReactPointerEvent } from 'react'

export interface DragOffset {
  x: number
  y: number
}

export interface DragHandlers {
  onPointerDown: (e: ReactPointerEvent<HTMLDivElement>) => void
  onPointerMove: (e: ReactPointerEvent<HTMLDivElement>) => void
  onPointerUp: (e: ReactPointerEvent<HTMLDivElement>) => void
  onPointerCancel: (e: ReactPointerEvent<HTMLDivElement>) => void
}

/** 小于该距离视为点击而非拖动（保护子元素 onClick 不被拖动吞掉）。
 *  10px 覆盖触控板/鼠标点击时的自然抖动（实测按压抖动可达 ~9px）；
 *  抖动内不激活拖动、不捕获指针，click 才能照常派发到子元素（如 FilmStrip 缩略图） */
const DRAG_THRESHOLD = 10

/* 这些元素上的指针按下不启动拖动：点击优先（按钮/输入等） */
const INTERACTIVE_SELECTOR =
  'button, a, input, select, textarea, [role="button"], [data-no-drag]'

/* 显式拖把手：标记 data-drag-handle 的元素（及内部）永远启动拖动，优先于交互排除 */
const DRAG_HANDLE_SELECTOR = '[data-drag-handle]'

function isDragHandle(target: EventTarget | null): boolean {
  return target instanceof Element && !!target.closest(DRAG_HANDLE_SELECTOR)
}

const EMPTY: DragOffset = { x: 0, y: 0 }

interface DragStart {
  px: number
  py: number
  ox: number
  oy: number
  active: boolean
  /* 相对父容器（offsetParent = 可视边界）当前还能平移的区间 */
  minDx: number | null
  maxDx: number | null
  minDy: number | null
  maxDy: number | null
}

/* 元素小于父容器才 clamp；无父容器或元素大于父容器时该轴不限制 */
function clampAxis(v: number, min: number | null, max: number | null): number {
  if (min == null || max == null || min > max) return v
  return Math.min(max, Math.max(min, v))
}

/**
 * 让任意 overlay 元素可被鼠标/触摸拖动改变位置：
 * - 只产出 translate(dx,dy) 偏移量，元素初始位置仍由外部 CSS（top/left 等）决定，
 *   因此可自由拼接已有 transform（如 FilmStrip 的 translateX(-50%)）
 * - 按下先记录、不立即进入拖动也不捕获指针：位移超过 DRAG_THRESHOLD 才激活
 *   拖动并此时 setPointerCapture。纯点击（含 ≤阈值 的抖动）全程无捕获，
 *   click 正常派发到原始按下元素，不吞子元素点击
 * - 可交互元素（button 等）内的按下永远不会启动拖动
 * - 位移被 clamp 在父容器（offsetParent）可视范围内，不越界
 * - 状态为不可变 DragOffset，未拖动时常量 EMPTY（引用稳定，不触发视觉重渲染）
 */
export function useDraggable(): {
  offset: DragOffset
  handlers: DragHandlers
  style: CSSProperties
} {
  const [offset, setOffset] = useState<DragOffset>(EMPTY)
  const [dragging, setDragging] = useState(false)
  const start = useRef<DragStart | null>(null)

  function onPointerDown(e: ReactPointerEvent<HTMLDivElement>) {
    // 仅鼠标左键 / 触摸 / 触控笔，屏蔽右键菜单等
    if (e.pointerType === 'mouse' && e.button !== 0) return
    // 点击优先区：工具按钮、缩略图内交互元素等不参与拖动；
    // 显式把手（data-drag-handle）内部除外——把手上永远可拖
    if (!isDragHandle(e.target) && (e.target as HTMLElement).closest?.(INTERACTIVE_SELECTOR)) return
    const el = e.currentTarget
    const parent = el.offsetParent as HTMLElement | null
    const pRect = parent ? parent.getBoundingClientRect() : null
    const rRect = el.getBoundingClientRect()
    start.current = {
      px: e.clientX,
      py: e.clientY,
      ox: offset.x,
      oy: offset.y,
      active: false,
      minDx: pRect ? pRect.left - rRect.left : null,
      maxDx: pRect ? pRect.right - rRect.right : null,
      minDy: pRect ? pRect.top - rRect.top : null,
      maxDy: pRect ? pRect.bottom - rRect.bottom : null,
    }
    // 注意：此处刻意不 setPointerCapture。按下即捕获会把后续 click 重定向
    // 到容器自身，子元素（缩略图）的 onClick 永远收不到事件。
    // 捕获推迟到拖动真正激活时（见 onPointerMove），纯点击全程无捕获。
  }

  function onPointerMove(e: ReactPointerEvent<HTMLDivElement>) {
    const s = start.current
    if (!s) return
    const dx = e.clientX - s.px
    const dy = e.clientY - s.py
    if (!s.active) {
      if (Math.hypot(dx, dy) <= DRAG_THRESHOLD) return
      s.active = true
      setDragging(true)
      // 激活后才捕获：保证拖动期间 move/up 稳定派发到本元素。
      // 激活前的 move 依赖冒泡即可——此时指针必在元素内（位移未超阈值）。
      // capture 是增强特性而非必需：失败（如无 active pointer）时静默降级
      try {
        e.currentTarget.setPointerCapture(e.pointerId)
      } catch {
        /* noop */
      }
    }
    setOffset({ x: clampAxis(s.ox + dx, s.minDx, s.maxDx), y: clampAxis(s.oy + dy, s.minDy, s.maxDy) })
  }

  function endDrag(e: ReactPointerEvent<HTMLDivElement>) {
    if (!start.current) return
    start.current = null
    setDragging(false)
    if (e.currentTarget.hasPointerCapture?.(e.pointerId)) {
      e.currentTarget.releasePointerCapture(e.pointerId)
    }
  }

  return {
    offset,
    handlers: {
      onPointerDown,
      onPointerMove,
      onPointerUp: endDrag,
      onPointerCancel: endDrag,
    },
    style: {
      cursor: dragging ? 'grabbing' : 'grab',
      touchAction: 'none',
    },
  }
}