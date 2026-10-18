import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, fireEvent, screen } from '@testing-library/react'
import { useDraggable } from './useDraggable'

/* jsdom 25 无 PointerEvent 构造器（testing-library 会回退到无坐标的 Event），
   用 MouseEvent 子类补齐，保持 clientX/clientY 可达 */
class MockPointerEvent extends MouseEvent {
  pointerId: number
  pointerType: string
  constructor(type: string, init: PointerEventInit = {}) {
    super(type, init)
    this.pointerId = init.pointerId ?? 0
    this.pointerType = init.pointerType ?? 'mouse'
  }
}

/* jsdom 无 Pointer Capture 实现，stub 掉避免报错 */
beforeEach(() => {
  Element.prototype.setPointerCapture = vi.fn()
  Element.prototype.releasePointerCapture = vi.fn()
  Element.prototype.hasPointerCapture = vi.fn(() => true)
  window.PointerEvent = MockPointerEvent as unknown as typeof PointerEvent
})

/* 一个消费 hook 的宿主 div：把 offset 拼进 transform，验证端到端拖动手势。
   showButton=true 时挂一个 antd 风格 Button 子元素，验证「点击不被拖动吞掉」；
   showHandle=true 时挂一个 data-drag-handle 把手（内含交互元素），验证把手优先于交互排除 */
function Host({ showButton = false, showHandle = false }: { showButton?: boolean; showHandle?: boolean }) {
  const { offset, handlers, style } = useDraggable()
  return (
    <div
      data-testid="host"
      {...handlers}
      style={{ ...style, transform: `translate(${offset.x}px, ${offset.y}px)` }}
    >
      {showHandle && (
        <span data-drag-handle>
          <button type="button">把手</button>
        </span>
      )}
      {showButton && <button type="button">点我</button>}
    </div>
  )
}

function pointer(el: HTMLElement, type: 'down' | 'move' | 'up', x: number, y: number) {
  const opts = { pointerId: 1, button: 0, clientX: x, clientY: y }
  if (type === 'down') fireEvent.pointerDown(el, opts)
  else if (type === 'move') fireEvent.pointerMove(el, opts)
  else fireEvent.pointerUp(el, opts)
}

/* 模拟元素相对父容器的布局，供边界 clamp 用例使用 */
function stubLayout(el: HTMLElement) {
  const parent = {
    left: 100,
    top: 50,
    right: 500,
    bottom: 400,
    getBoundingClientRect: () => ({ left: 100, top: 50, right: 500, bottom: 400 } as DOMRect),
  }
  Object.defineProperty(el, 'offsetParent', { value: parent, configurable: true })
  /* host 相对 parent 位于 left:8 / top:8，尺寸 80×30 */
  el.getBoundingClientRect = () =>
    ({ left: 108, top: 58, right: 188, bottom: 88, x: 108, y: 58, width: 80, height: 30, toJSON: () => ({}) }) as DOMRect
}

describe('useDraggable', () => {
  it('初始位置不位移（translate 0,0）', () => {
    render(<Host />)
    const el = screen.getByTestId('host')
    expect(el).toHaveStyle({ transform: 'translate(0px, 0px)' })
  })

  it('按下并拖动后按位移量更新，抬起后保持新位置', () => {
    render(<Host />)
    const el = screen.getByTestId('host') as HTMLElement
    pointer(el, 'down', 100, 50)
    pointer(el, 'move', 140, 80)
    pointer(el, 'move', 150, 90)
    expect(el).toHaveStyle({ transform: 'translate(50px, 40px)' })
    pointer(el, 'up', 150, 90)
    // 抬起后仍保持最终位移
    expect(el).toHaveStyle({ transform: 'translate(50px, 40px)' })
  })

  it('抬起后再移动不再产生位移', () => {
    render(<Host />)
    const el = screen.getByTestId('host') as HTMLElement
    pointer(el, 'down', 0, 0)
    pointer(el, 'up', 0, 0)
    pointer(el, 'move', 50, 50)
    expect(el).toHaveStyle({ transform: 'translate(0px, 0px)' })
  })

  it('非左键（button=2）不启动拖动', () => {
    render(<Host />)
    const el = screen.getByTestId('host') as HTMLElement
    fireEvent.pointerDown(el, { pointerId: 1, button: 2, clientX: 0, clientY: 0 })
    fireEvent.pointerMove(el, { pointerId: 1, button: 2, clientX: 80, clientY: 80 })
    expect(el).toHaveStyle({ transform: 'translate(0px, 0px)' })
  })

  /* ---- 回归：点击不能被拖动吞掉 ---- */

  it('在可交互元素（button）内按下移动不启动拖动，按钮点击保留', () => {
    render(<Host showButton />)
    const btn = screen.getByRole('button') as HTMLElement
    fireEvent.pointerDown(btn, { pointerId: 1, button: 0, clientX: 10, clientY: 10 })
    fireEvent.pointerMove(btn, { pointerId: 1, button: 0, clientX: 60, clientY: 60 })
    expect(Element.prototype.setPointerCapture).not.toHaveBeenCalled()
    expect(screen.getByTestId('host')).toHaveStyle({ transform: 'translate(0px, 0px)' })
  })

  it('data-drag-handle 内的交互元素按下也启动拖动（把手优先于交互排除）', () => {
    render(<Host showHandle />)
    const btn = screen.getByRole('button') as HTMLElement
    fireEvent.pointerDown(btn, { pointerId: 1, button: 0, clientX: 10, clientY: 10 })
    fireEvent.pointerMove(btn, { pointerId: 1, button: 0, clientX: 60, clientY: 60 })
    expect(Element.prototype.setPointerCapture).toHaveBeenCalledTimes(1)
    expect(screen.getByTestId('host')).toHaveStyle({ transform: 'translate(50px, 50px)' })
  })

  it('按下即抬起（未达到拖动阈值）不捕获指针，click 保留在原始元素上', () => {
    render(<Host />)
    const el = screen.getByTestId('host') as HTMLElement
    pointer(el, 'down', 0, 0)
    pointer(el, 'up', 0, 0)
    // 延迟捕获：未激活拖动则全程不 setPointerCapture，
    // 浏览器 click 天然派发到原始按下元素（捕获会把 click 重定向到容器）
    expect(Element.prototype.setPointerCapture).not.toHaveBeenCalled()
    expect(el).toHaveStyle({ transform: 'translate(0px, 0px)' })
  })

  it('点击抖动 8.5px（阈值 10px 内）不启动拖动、不捕获指针，子元素点击保留', () => {
    render(<Host />)
    const el = screen.getByTestId('host') as HTMLElement
    pointer(el, 'down', 0, 0)
    pointer(el, 'move', 6, 6) // 距离 ≈8.5px ≤ 10：仍是点击语义（实测按压抖动幅度）
    pointer(el, 'up', 6, 6)
    expect(Element.prototype.setPointerCapture).not.toHaveBeenCalled()
    expect(el).toHaveStyle({ transform: 'translate(0px, 0px)' })
  })

  it('setPointerCapture 失败（无 active pointer）不阻塞拖动，捕获只是增强', () => {
    Element.prototype.setPointerCapture = vi.fn(() => { throw new Error('NotFoundError') })
    render(<Host />)
    const el = screen.getByTestId('host') as HTMLElement
    pointer(el, 'down', 0, 0)
    expect(() => pointer(el, 'move', 40, 30)).not.toThrow()
    expect(el).toHaveStyle({ transform: 'translate(40px, 30px)' })
  })

  it('移动不超过点击容差（10px）不启动拖动，超过后启动且光标变 grabbing', () => {
    render(<Host />)
    const el = screen.getByTestId('host') as HTMLElement
    pointer(el, 'down', 10, 10)
    expect(el).toHaveStyle({ cursor: 'grab' })
    // dx=3, dy=2 → 距离 ~3.6px ≤ 10：仍是点击语义
    pointer(el, 'move', 13, 12)
    expect(el).toHaveStyle({ transform: 'translate(0px, 0px)' })
    expect(el).toHaveStyle({ cursor: 'grab' })
    // 距离 ~14.1px 超过阈值 → 拖动激活（此时才捕获指针）
    pointer(el, 'move', 20, 20)
    expect(el).toHaveStyle({ transform: 'translate(10px, 10px)' })
    expect(el).toHaveStyle({ cursor: 'grabbing' })
    expect(Element.prototype.setPointerCapture).toHaveBeenCalledTimes(1)
  })

  /* ---- 回归：拖动限制在父容器（可视区域）内 ---- */

  it('拖动不越出父容器边界（clamp 在四条边上）', () => {
    render(<Host />)
    const el = screen.getByTestId('host') as HTMLElement
    stubLayout(el)
    // 右/下越界：maxDx = 500-188 = 312，maxDy = 400-88 = 312
    pointer(el, 'down', 0, 0)
    pointer(el, 'move', 999, 999)
    expect(el).toHaveStyle({ transform: 'translate(312px, 312px)' })
    pointer(el, 'up', 999, 999)
    // 左/上越界：minDx = 100-108 = -8，minDy = 50-58 = -8
    pointer(el, 'down', 999, 999)
    pointer(el, 'move', 0, 0)
    expect(el).toHaveStyle({ transform: 'translate(-8px, -8px)' })
  })

  it('拖动期间光标 grabbing、静止时 grab，触摸设备取消原生滚动', () => {
    render(<Host />)
    const el = screen.getByTestId('host') as HTMLElement
    expect(el).toHaveStyle({ cursor: 'grab' })
    // jsdom computed style 不支持 touch-action，直接读内联样式
    expect(el.style.touchAction).toBe('none')
    pointer(el, 'down', 0, 0)
    pointer(el, 'move', 20, 0)
    expect(el).toHaveStyle({ cursor: 'grabbing' })
    pointer(el, 'up', 20, 0)
    expect(el).toHaveStyle({ cursor: 'grab' })
  })
})
