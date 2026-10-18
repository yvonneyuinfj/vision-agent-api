import { useEffect, useRef } from 'react'

interface TabsProps {
  items: string[]
  value: number
  onChange: (index: number) => void
}

function placeGlider(container: HTMLDivElement, index: number, instant: boolean): void {
  const glider = container.querySelector<HTMLSpanElement>('.tab-glider')
  const tab = container.querySelectorAll<HTMLButtonElement>('.tab')[index]
  if (!glider || !tab) return
  if (instant) glider.classList.add('no-anim')
  glider.style.width = `${tab.offsetWidth}px`
  glider.style.transform = `translateX(${tab.offsetLeft}px)`
  if (instant) {
    requestAnimationFrame(() => {
      requestAnimationFrame(() => glider.classList.remove('no-anim'))
    })
  }
}

/* 设计还原自 temp/assets/app.js：tab-glider 跟随激活项滑动，
   mount 立即定位（无动画），点击滑动，resize 防抖重定位。 */
export default function Tabs({ items, value, onChange }: TabsProps) {
  const ref = useRef<HTMLDivElement>(null)
  const mounted = useRef(false)

  useEffect(() => {
    // 首次 mount 无动画定位；之后 value 变化带动画从上一项滑到当前项
    placeGlider(ref.current as HTMLDivElement, value, !mounted.current)
    mounted.current = true
  }, [value])

  useEffect(() => {
    let timer: number | undefined
    const onResize = () => {
      window.clearTimeout(timer)
      timer = window.setTimeout(() => {
        const el = ref.current
        if (!el) return
        const tabs = el.querySelectorAll<HTMLButtonElement>('.tab')
        const idx = Math.max(0, Array.from(tabs).indexOf(el.querySelector('.tab.active') as HTMLButtonElement))
        placeGlider(el, idx, true)
      }, 120)
    }
    window.addEventListener('resize', onResize)
    return () => {
      window.removeEventListener('resize', onResize)
      window.clearTimeout(timer)
    }
  }, [])

  return (
    <div ref={ref} className="tabs">
      <span className="tab-glider no-anim" />
      {items.map((label, i) => (
        <button
          key={label}
          type="button"
          className={i === value ? 'tab active' : 'tab'}
          onClick={() => onChange(i)}
        >
          {label}
        </button>
      ))}
    </div>
  )
}