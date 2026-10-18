/* 侧栏与外壳图标：与 docs/切图 常规态同风格的线性 SVG（1.7 stroke，currentColor 随主题）。
   批次 1 评审 R-1 的落地形态：新模块图标不再补切 PNG，统一用本文件。 */
import type { ReactNode } from 'react'

export type IconName =
  | 'home' | 'sparkle' | 'chat' | 'tasks' | 'caps' | 'model' | 'book' | 'plug' | 'gear'
  | 'moon' | 'sun' | 'bell' | 'exit' | 'warn' | 'info' | 'lock' | 'chev' | 'flask' | 'search'
  /* 导航叶图标：侧栏收成图标栏后每个叶都要有可点图形，故逐叶配线性图标 */
  | 'list' | 'plus' | 'check' | 'doc' | 'shield' | 'grid' | 'bolt' | 'template' | 'compass'
  | 'inbox' | 'cloud' | 'wrench' | 'box' | 'user' | 'building' | 'sliders' | 'activity'
  | 'image' | 'pen' | 'database' | 'cpu' | 'chart' | 'rocket' | 'history'
  /* 内置六场景的检出图标；场景目录新增的场景在导航里回退 flask（决策 52） */
  | 'droplet' | 'scope' | 'thermal' | 'xray' | 'crack' | 'grain'

const PATH: Record<IconName, string> = {
  home: 'M3 10.5 12 3l9 7.5M5.5 9v11h13V9',
  sparkle: 'M12 3v4M12 17v4M4.9 7.5l3 3M16.1 13.5l3 3M3 12h4M17 12h4M4.9 16.5l3-3M16.1 10.5l3-3',
  chat: 'M21 12a8 8 0 1 1-3.2-6.4M21 5v4h-4',
  tasks: 'M9 4h6v3H9zM7 6h10v14H7zM10 11h5M10 15h5',
  caps: 'M12 3 4 7v10l8 4 8-4V7zM12 12v9M4 7l8 5 8-5',
  model: 'M4 7c0-1.7 3.6-3 8-3s8 1.3 8 3-3.6 3-8 3-8-1.3-8-3ZM4 7v10c0 1.7 3.6 3 8 3s8-1.3 8-3V7M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3',
  book: 'M4 4h7a2 2 0 0 1 2 2v14a1.5 1.5 0 0 0-1.5-1.5H4zM20 4h-7a2 2 0 0 0-2 2v14a1.5 1.5 0 0 1 1.5-1.5H20z',
  plug: 'M9 3v5M15 3v5M6 8h12v4a6 6 0 0 1-12 0zM12 18v3',
  gear: 'M12 8.5a3.5 3.5 0 1 0 0 7 3.5 3.5 0 0 0 0-7ZM12 2v2.5M12 19.5V22M2 12h2.5M19.5 12H22M4.9 4.9 6.7 6.7M17.3 17.3l1.8 1.8M19.1 4.9l-1.8 1.8M6.7 17.3l-1.8 1.8',
  moon: 'M20.4 14.2A8.5 8.5 0 0 1 9.8 3.6a8.5 8.5 0 1 0 10.6 10.6Z',
  sun: 'M12 2.5V5M12 19v2.5M4.6 4.6 6.4 6.4M17.6 17.6l1.8 1.8M2.5 12H5m14 0h2.5M4.6 19.4l1.8-1.8M17.6 6.4l1.8-1.8',
  bell: 'M6 9a6 6 0 1 1 12 0c0 5 2 6 2 6H4s2-1 2-6M10 19a2.2 2.2 0 0 0 4 0',
  exit: 'M9 4H6a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h3M16 17l5-5-5-5M21 12H9',
  warn: 'M12 4.5 2.8 20h18.4ZM12 10v4.5M12 17.4v.2',
  info: 'M12 3.5a8.5 8.5 0 1 0 0 17 8.5 8.5 0 0 0 0-17ZM12 11v5.5M12 8v.3',
  lock: 'M6 11h12v9H6zM9 11V8a3 3 0 0 1 6 0v3',
  chev: 'M6 9.5 12 15.5l6-6',
  flask: 'M9 3h6M10 3v6.2L4.8 19a2 2 0 0 0 1.7 3h11a2 2 0 0 0 1.7-3L14 9.2V3M7 15h10',
  search: 'M11 4a7 7 0 1 0 0 14 7 7 0 0 0 0-14ZM16.2 16.2 21 21',
  list: 'M8 6h13M8 12h13M8 18h13M3.6 6h.01M3.6 12h.01M3.6 18h.01',
  plus: 'M12 5v14M5 12h14',
  check: 'M4.5 12.5 9 17 19.5 6.5',
  doc: 'M6 3h8l4 4v14H6zM14 3v4h4M9 12h6M9 16h6',
  shield: 'M12 3.2 5 5.8v6.4c0 4 2.9 7 7 8.6 4.1-1.6 7-4.6 7-8.6V5.8Z',
  grid: 'M4 4h7v7H4zM13 4h7v7h-7zM4 13h7v7H4zM13 13h7v7h-7z',
  bolt: 'M13.5 3 6 13.5h5L10.5 21 18 10.5h-5Z',
  template: 'M12 3 3 7.5 12 12l9-4.5ZM5 12.5 12 16l7-3.5M5 16.5 12 20l7-3.5',
  compass: 'M12 3.5a8.5 8.5 0 1 0 0 17 8.5 8.5 0 0 0 0-17ZM15.8 8.2l-2.3 5.3-5.3 2.3 2.3-5.3Z',
  inbox: 'M4 4h16v10H4zM4 14l3 6h10l3-6M9 8.5h6',
  cloud: 'M7.5 18a4.5 4.5 0 0 1-.5-9 5.5 5.5 0 0 1 10.6 1.3A3.6 3.6 0 0 1 17 18Z',
  wrench: 'M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.5-3.5a5.8 5.8 0 0 1-7.7 7.7l-6.4 6.4a2 2 0 1 1-2.9-2.9l6.4-6.4a5.8 5.8 0 0 1 7.7-7.7Z',
  box: 'M12 3 20 7v10l-8 4-8-4V7ZM4 7l8 4.5L20 7M12 11.5V21',
  user: 'M12 4a3.8 3.8 0 1 0 0 7.6A3.8 3.8 0 0 0 12 4ZM4.5 20a7.5 7.5 0 0 1 15 0',
  building: 'M5 21V4h9v17M14 9h5v12M8 8h3M8 12h3M8 16h3M17 13h1M17 17h1',
  sliders: 'M4 7h16M4 12h16M4 17h16M9 5v4M15 10v4M7 15v4',
  activity: 'M3 12h4l2.5-7 4 14 2.5-7h5',
  image: 'M4 5h16v14H4zM4 15.5 9 11l5 5M14 13.5 16.5 11 20 14.5M8.5 8.2a1.1 1.1 0 1 0 0 2.2 1.1 1.1 0 0 0 0-2.2Z',
  pen: 'M4 20h4L20 8l-4-4L4 16ZM14.5 5.5 18.5 9.5',
  database: 'M12 3c4.4 0 8 1.3 8 3s-3.6 3-8 3-8-1.3-8-3 3.6-3 8-3ZM20 9v3c0 1.7-3.6 3-8 3s-8-1.3-8-3V9M20 14v3c0 1.7-3.6 3-8 3s-8-1.3-8-3v-3',
  cpu: 'M6 6h12v12H6zM9.5 9.5h5v5h-5zM10 3v3M14 3v3M10 18v3M14 18v3M3 10h3M3 14h3M18 10h3M18 14h3',
  chart: 'M4 20h16M7.5 16.5v-6M12 16.5v-11M16.5 16.5v-4',
  rocket: 'M12 3c3 2.4 4.5 5.6 4.5 9L14 15h-4L7.5 12C7.5 8.6 9 5.4 12 3ZM10 15l-2.5 4 3.5-1.5M14 15l2.5 4-3.5-1.5M12 16v5',
  history: 'M4 12a8 8 0 1 0 8-8 8 8 0 0 0-6.9 4M4 4v4h4M12 8v4.5l3 1.8',
  droplet: 'M12 3.2 7 9.6a6.2 6.2 0 1 0 10 0Z',
  scope: 'M4 8.5A2.5 2.5 0 0 1 6.5 6h2L10 4h4l1.5 2h2A2.5 2.5 0 0 1 20 8.5v9a2.5 2.5 0 0 1-2.5 2.5h-11A2.5 2.5 0 0 1 4 17.5ZM12 9.2a3.8 3.8 0 1 0 0 7.6 3.8 3.8 0 0 0 0-7.6Z',
  thermal: 'M3 8.5c2-2 4-2 6 0s4 2 6 0 4-2 6 0M3 14c2-2 4-2 6 0s4 2 6 0 4-2 6 0M3 19.5c2-2 4-2 6 0s4 2 6 0 4-2 6 0',
  xray: 'M4.5 4.5 10 10M19.5 4.5 14 10M4.5 19.5 10 14M19.5 19.5 14 14M12 10.4a1.6 1.6 0 1 0 0 3.2 1.6 1.6 0 0 0 0-3.2Z',
  crack: 'M14 3 9.5 9.2l3.8 1.6-3.4 4.3 2.6 1.2L9 21',
  grain: 'M12 3 17 6.4 15.9 12.3 11 14.8 6.2 11.4 7.3 5.9ZM11 14.8 9.8 20.5M15.9 12.3 20.5 14.6M6.2 11.4 3 14',
}

export function Icon({ name, size = 16, className }: { name: IconName; size?: number; className?: string }): ReactNode {
  return (
    <svg
      className={className}
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.7}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d={PATH[name]} />
    </svg>
  )
}
