import { useEffect, useState } from 'react'
import { Navigate, useLocation } from 'react-router-dom'
import { useAuthStore } from '../../store/useAuthStore'

/** 路由守卫：未登录时重定向到登录页；已登录才渲染受保护内容。 */
export default function RequireAuth({ children }: { children: React.ReactNode }) {
  const { loggedIn, init } = useAuthStore()
  const [probing, setProbing] = useState(true)
  const location = useLocation()

  // mount 时探测会话（后端下发 httpOnly cookie）；探测完成前不渲染跳转，避免误跳登录页
  useEffect(() => {
    let alive = true
    ;(async () => {
      await init()
      if (alive) setProbing(false)
    })()
    return () => {
      alive = false
    }
  }, [init])

  if (probing) {
    // 会话探测中：短暂空白，避免闪烁回登录页
    return <div className="app-boot-splash" style={{ minHeight: '100vh' }} />
  }

  if (!loggedIn) {
    return <Navigate to="/login" replace state={{ from: location.pathname }} />
  }

  return <>{children}</>
}