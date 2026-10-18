import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Button, Form, Input, message } from 'antd'
import { UserOutlined, LockOutlined } from '@ant-design/icons'
/* 品牌资产：与 AppLayout 一致 */
import logoImg from '../../assets/icons/logo.png'
import { login } from '../../api/auth'
import { applyLoggedIn, useAuthStore } from '../../store/useAuthStore'
import { useTheme } from '../../hooks/useTheme'
import './index.css'

interface LoginForm {
  username: string
  password: string
}

/** 登录页：未登录时的唯一入口。风格贴合 base.css / AppLayout（玻璃面板 + 品牌蓝）。 */
export default function LoginPage() {
  const navigate = useNavigate()
  const [loading, setLoading] = useState(false)
  const loggedIn = useAuthStore((s) => s.loggedIn)
  const { theme, toggleTheme } = useTheme()

  // 已登录直接回首页
  useEffect(() => {
    if (loggedIn) navigate('/', { replace: true })
  }, [loggedIn, navigate])

  const onFinish = async (values: LoginForm) => {
    setLoading(true)
    try {
      const r = await login(values.username.trim(), values.password)
      applyLoggedIn(r)
      message.success('登录成功')
      navigate('/', { replace: true })
    } catch (e) {
      // 401 / 网络错误已由 http 拦截器统一提示（用户名或密码错误 / 网络失败）
      // 这里仅兜底，避免对已提示的消息二次弹窗
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="auth-wrap">
      <div className="auth-bg" aria-hidden="true" />
      <div className="auth-card glass-panel">
        <div className="auth-head">
          <img className="auth-logo" src={logoImg} alt="" aria-hidden="true" />
          <div className="auth-brand">
            <h1>工业图像智能检测智能体</h1>
            <p>INDUSTRIAL IMAGE INSPECTION AGENT</p>
          </div>
        </div>

        <div className="auth-title">
          <span className="auth-title-main">登录系统</span>
          <span className="auth-title-sub">请使用受权账号登录</span>
        </div>

        <Form<LoginForm> name="login" size="large" onFinish={onFinish} className="auth-form">
          <Form.Item name="username" rules={[{ required: true, message: '请输入账号' }]}>
            <Input prefix={<UserOutlined style={{ color: 'var(--faint)' }} />} placeholder="账号" autoComplete="username" />
          </Form.Item>
          <Form.Item name="password" rules={[{ required: true, message: '请输入密码' }]}>
            <Input.Password
              prefix={<LockOutlined style={{ color: 'var(--faint)' }} />}
              placeholder="密码"
              autoComplete="current-password"
            />
          </Form.Item>
          <Form.Item>
            <Button type="primary" htmlType="submit" block loading={loading} className="auth-submit">
              登 录
            </Button>
          </Form.Item>
        </Form>

        <button className="auth-theme-toggle" onClick={toggleTheme} title="切换明暗主题">
          {theme === 'dark' ? '☀' : '☾'}
        </button>
      </div>
    </div>
  )
}