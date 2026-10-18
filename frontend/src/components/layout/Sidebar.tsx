import { useEffect, useRef, useState } from 'react'
import { Link, useLocation } from 'react-router-dom'
import logoImg from '../../assets/icons/logo.png'
import { Icon } from '../ui/Icon'
import { readPref, writePref } from '../../utils/storage'
import {
  CLOSED_GROUPS_KEY,
  NAV,
  activeLeaf,
  groupOf,
  sectionLeaves,
  type NavLeaf,
  type NavTag,
} from '../../constants/nav'

const isTag = (e: NavLeaf | NavTag): e is NavTag => 'tag' in e

function readClosed(): string[] {
  try {
    const raw = JSON.parse(readPref(CLOSED_GROUPS_KEY) || '[]')
    return Array.isArray(raw) ? raw.filter((x): x is string => typeof x === 'string') : []
  } catch {
    return []
  }
}

/** 侧栏：7 个一级分组各自可折叠（决策 21）。
 *  折叠态存 localStorage；深链命中的分组自动展开，同页重绘不反悔用户手动折叠。
 *  底部只保留图标栏开关——批量折叠/展开与建设中占比统计不进产品。 */
export default function Sidebar({
  rail,
  onToggleRail,
  reviewCount,
}: {
  rail: boolean
  onToggleRail: () => void
  reviewCount: number
}) {
  const { pathname } = useLocation()
  const [closed, setClosed] = useState<Set<string>>(() => new Set(readClosed()))
  const lastPath = useRef<string | null>(null)
  const current = activeLeaf(pathname)

  useEffect(() => {
    writePref(CLOSED_GROUPS_KEY, JSON.stringify([...closed]))
  }, [closed])

  useEffect(() => {
    if (lastPath.current === pathname) return
    lastPath.current = pathname
    const g = groupOf(pathname)
    if (!g) return
    setClosed((prev) => {
      if (!prev.has(g)) return prev
      const next = new Set(prev)
      next.delete(g)
      return next
    })
  }, [pathname])

  const toggle = (g: string) =>
    setClosed((prev) => {
      const next = new Set(prev)
      if (next.has(g)) next.delete(g)
      else next.add(g)
      return next
    })

  return (
    <aside className="side" aria-label="主导航">
      <div className="logo-area">
        <img className="logo-img" src={logoImg} alt="" aria-hidden="true" />
        <span className="logo-text">
          工业图像智能检测
          <span className="logo-sub">INDUSTRIAL IMAGE INSPECTION AGENT</span>
        </span>
      </div>

      {NAV.map((sec) => {
        const leaves = sectionLeaves(sec)
        const shells = leaves.filter((l) => l.shell).length
        if (!sec.title) {
          return (
            <nav className="nav" key="top">
              {leaves.map((leaf) => (
                <LeafLink key={leaf.to} leaf={leaf} on={current?.to === leaf.to} reviewCount={reviewCount} />
              ))}
            </nav>
          )
        }
        const isClosed = closed.has(sec.title)
        return (
          <div className={`grp${isClosed ? ' closed' : ''}`} key={sec.title}>
            <button
              type="button"
              className="grp-h"
              aria-expanded={!isClosed}
              onClick={() => toggle(sec.title!)}
              title={`${isClosed ? '展开' : '折叠'} ${sec.title}（${leaves.length} 页${shells ? `，${shells} 项建设中` : ''}）`}
            >
              <span className="ico">
                <Icon name={sec.icon ?? 'caps'} size={15} />
              </span>
              <span className="txt">{sec.title}</span>
              <span className="gcount">
                {leaves.length}
                {shells ? `·建${shells}` : ''}
              </span>
              <span className="chev">
                <Icon name="chev" size={12} />
              </span>
            </button>
            <nav className="nav">
              {sec.items.map((e) =>
                isTag(e) ? (
                  <div key={e.label}>
                    <div className="sub-label">
                      <span className="txt">{e.label}</span>
                    </div>
                    {e.subs.map((leaf) => (
                      <LeafLink
                        key={leaf.to}
                        leaf={leaf}
                        sub
                        on={current?.to === leaf.to}
                        reviewCount={reviewCount}
                      />
                    ))}
                  </div>
                ) : (
                  <LeafLink key={e.to} leaf={e} on={current?.to === e.to} reviewCount={reviewCount} />
                ),
              )}
            </nav>
          </div>
        )
      })}

      <div className="side-foot">
        <button
          type="button"
          className="rail-btn"
          onClick={onToggleRail}
          title={rail ? '展开侧栏' : '折叠侧栏'}
          aria-label={rail ? '展开侧栏' : '折叠侧栏'}
          aria-expanded={!rail}
        >
          <Icon name="chev" size={14} />
          <span className="txt">{rail ? '展开侧栏' : '折叠侧栏'}</span>
        </button>
      </div>
    </aside>
  )
}

function LeafLink({
  leaf,
  on,
  sub = false,
  reviewCount,
}: {
  leaf: NavLeaf
  on: boolean
  sub?: boolean
  reviewCount: number
}) {
  return (
    <Link
      to={leaf.to}
      className={sub ? `sub${on ? ' on' : ''}` : on ? 'on' : undefined}
      title={leaf.label}
      aria-current={on ? 'page' : undefined}
    >
      <span className="ico">
        <Icon name={leaf.icon} size={15} />
      </span>
      <span className="txt">{leaf.label}</span>
      {leaf.shell && <span className="sflag">建</span>}
      {leaf.badge === 'review' && reviewCount > 0 && <span className="nav-badge">{reviewCount}</span>}
    </Link>
  )
}
