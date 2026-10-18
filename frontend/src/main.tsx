import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
import { initTheme, ThemeProvider } from './hooks/useTheme'
// base.css：AeroQC 设计系统（热重写目标），自带 token 与全局 reset
import './styles/base.css'
// tokens.css 提供 antd 局部覆写（拖拽区/表格头等），全部走 base.css token
import './styles/tokens.css'
// global.css：滚动条重塑等非主题通用件，不再覆盖 body 背景
import './styles/global.css'
// glass.css：毛玻璃面板三要素（半透明底 + blur + 高光描边），随主题 token 联动
import './styles/glass.css'
// shell.css：v0.5 外壳（左侧栏 + 面包屑顶栏）与整棵 IA 复用的公共件
import './styles/shell.css'

initTheme()

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <ThemeProvider>
      <App />
    </ThemeProvider>
  </React.StrictMode>,
)