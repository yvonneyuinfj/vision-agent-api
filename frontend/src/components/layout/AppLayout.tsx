import { useEffect, useMemo, useState } from 'react'
import { Link, Outlet, useLocation, useNavigate } from 'react-router-dom'
import Sidebar from './Sidebar'
import { useTaskStore } from '../../store/useTaskStore'
import { useSystemStore } from '../../store/useSystemStore'
import { useAuthStore } from '../../store/useAuthStore'
import { useTheme } from '../../hooks/useTheme'
import { clusterText, ragflowText } from '../../utils/systemStatus'
import { readPref, writePref } from '../../utils/storage'
import { SIDE_RAIL_KEY, titleOf, trailOf } from '../../constants/nav'
import { Icon } from '../ui/Icon'

export default function AppLayout() {
  const location = useLocation()
  const navigate = useNavigate()
  const tasks = useTaskStore((s) => s.tasks)
  const fetchList = useTaskStore((s) => s.fetchList)
  const { theme, toggleTheme } = useTheme()
  const username = useAuthStore((s) => s.username)
  const signOut = useAuthStore((s) => s.signOut)

  /* 侧栏收成图标栏：与分组折叠同样是用户偏好，存 localStorage */
  const [rail, setRail] = useState(() => readPref(SIDE_RAIL_KEY) === '1')
  const toggleRail = () =>
    setRail((prev) => {
      writePref(SIDE_RAIL_KEY, prev ? '0' : '1')
      return !prev
    })

  const handleLogout = async () => {
    await signOut()
    navigate('/login', { replace: true })
  }

  /* 待复核徽标：/tasks 的 100 条窗口内计数（接口无全量聚合），首屏无数据时主动取一次 */
  useEffect(() => {
    if (!tasks.length) void fetchList()
  }, [tasks.length, fetchList])
  const reviewCount = useMemo(
    () => tasks.filter((t) => t.status === 'awaiting_review').length,
    [tasks],
  )

  // 系统状态探测：挂载 + 路由切换各请求一次（无定时器，store 内 in-flight 去重）；失败静默降级为"未知"
  const caps = useSystemStore((s) => s.caps)
  const ragflow = useSystemStore((s) => s.ragflow)
  const probe = useSystemStore((s) => s.probe)
  useEffect(() => {
    void probe()
  }, [location.pathname, probe])

  const cluster = clusterText(caps)
  const ragflowState = ragflowText(ragflow)
  // 环境徽标聚合色调：任一子系统异常 → 红；集群健康 → 绿；未知 → 灰
  const envTone: 'ok' | 'error' | 'muted' =
    cluster.tone === 'error' || ragflowState.tone === 'error'
      ? 'error'
      : cluster.tone === 'ok'
        ? 'ok'
        : 'muted'
  const ready = caps ? ` · ${caps.filter((c) => c.healthy).length}/${caps.length} 能力就绪` : ''

  const crumbs = trailOf(location.pathname)
  const pageTitle = titleOf(location.pathname)
  useEffect(() => {
    document.title = `${pageTitle} · 工业图像智能检测智能体`
  }, [pageTitle])

  return (
    <div className={rail ? 'frame side-rail' : 'frame'}>
      <Sidebar rail={rail} onToggleRail={toggleRail} reviewCount={reviewCount} />

      <div className="main">
        <header className="topbar">
          <span className="crumb">
            {crumbs.map((c, i) => (
              <span key={c} style={{ display: 'contents' }}>
                {i > 0 && <span aria-hidden="true">/</span>}
                <b className={i === crumbs.length - 1 ? 'cur' : undefined}>{c}</b>
              </span>
            ))}
          </span>
          <span className="grow" />

          {/* 环境健康：点击进入运行监控（数据与运行监控页同源） */}
          <Link
            className={`env-switch ${envTone}`}
            to="/admin/monitor"
            title={`${cluster.label}｜${ragflowState.label}`}
          >
            <span className={`env-dot${envTone === 'ok' ? ' pulse' : ''}`} />
            生产环境{envTone !== 'muted' ? ready : ''}
          </Link>

          <div className="action-group">
            <button
              className="icon-btn"
              data-action="toggle-theme"
              title="切换明暗主题"
              aria-label="切换明暗主题"
              onClick={toggleTheme}
            >
              {/* 单枚图标即当前主题：浅色显月亮（点了去深色），深色显太阳 */}
              <Icon name={theme === 'dark' ? 'sun' : 'moon'} />
            </button>
          </div>

          <div className="user-divider" aria-hidden="true" />
          <div className="avatar">
            <div className="avatar-img">{username.slice(0, 1) || '—'}</div>
            <div className="avatar-meta">
              <b>{username || '未登录'}</b>
              {/* 决策 10：/auth/me 只返回 logged_in 与 username，无角色与数据范围，故不做假权限 */}
              <span>未启用角色区分</span>
            </div>
          </div>
          <button className="icon-btn" title="退出登录" aria-label="退出登录" onClick={handleLogout}>
            <Icon name="exit" />
          </button>
        </header>

        <main className="content">
          <Outlet />
        </main>
      </div>
    </div>
  )
}
