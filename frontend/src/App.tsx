import { BrowserRouter, Navigate, Route, Routes, useLocation } from 'react-router-dom'
import AppLayout from './components/layout/AppLayout'
import RequireAuth from './components/auth/RequireAuth'
import LoginPage from './pages/LoginPage'
import HomePage from './pages/HomePage'
import AssistantPage from './pages/AssistantPage'
import WorkbenchPage from './pages/WorkbenchPage'
import ListPage from './pages/ListPage'
import SubmitPage from './pages/SubmitPage'
import ReviewPage from './pages/ReviewPage'
import DetailPage from './pages/DetailPage'
import ReportPage from './pages/ReportPage'
import SamplesPage from './pages/SamplesPage'
import ReportsPage from './pages/ReportsPage'
import SettingsPage from './pages/SettingsPage'
import KnowledgeSearchPage from './pages/KnowledgeSearchPage'
import KnowledgeIngestPage from './pages/KnowledgeIngestPage'
import { CapabilityGuidePage, CapabilityGeneralPage, CapabilityProPage } from './pages/CapabilityCenter'
import { ToolRegistryPage } from './pages/ToolCenter'
import ShellPage, { NotFound } from './pages/ShellPage'
import { SHELLS } from './constants/shells'

/* 决策 19：旧路由重定向。侧栏树（constants/nav.ts）为唯一导航来源，
   旧链接、书签与站内旧跳转仍可达，不出现 404。 */
const REDIRECTS: Array<[string, string]> = [
  ['/dashboard', '/'],
  ['/assistant', '/detect/assistant'],
  ['/settings', '/admin/monitor'],
  ['/tasks/list', '/tasks'],
  ['/tasks/submit', '/tasks/new'],
  /* 工具注册与工具版本同源同表，合并为一叶后旧地址仍可达（决策 24） */
  ['/tool/versions', '/tool/registry'],
]

function CatchAll() {
  const { pathname } = useLocation()
  return <NotFound path={pathname} />
}

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        {/* 登录页：无侧栏外壳，独立布局；未登录访问受保护路由会被守卫带到此处 */}
        <Route path="/login" element={<LoginPage />} />

        <Route
          element={
            <RequireAuth>
              <AppLayout />
            </RequireAuth>
          }
        >
          <Route path="/" element={<HomePage />} />

          {/* 智能检测：助手 + 场景目录里的工作台（归类标签不占路由，决策 22 / 52） */}
          <Route path="/detect/assistant" element={<AssistantPage />} />
          <Route path="/detect/:scene" element={<WorkbenchPage />} />

          {/* 任务中心：参数路由更具体者须先于 /tasks/:taskId */}
          <Route path="/tasks" element={<ListPage />} />
          <Route path="/tasks/new" element={<SubmitPage />} />
          <Route path="/tasks/review" element={<ReviewPage />} />
          <Route path="/tasks/samples" element={<SamplesPage />} />
          <Route path="/reports" element={<ReportsPage />} />
          <Route path="/tasks/:taskId/report" element={<ReportPage />} />
          <Route path="/tasks/:taskId" element={<DetailPage />} />

          {/* 能力中心：三个有数据源的说明页 + 两个壳 */}
          <Route path="/capability/pro" element={<CapabilityProPage />} />
          <Route path="/capability/general" element={<CapabilityGeneralPage />} />
          <Route path="/capability/guide" element={<CapabilityGuidePage />} />

          {/* 知识中心 */}
          <Route path="/knowledge/search" element={<KnowledgeSearchPage />} />
          <Route path="/knowledge/ingest" element={<KnowledgeIngestPage />} />

          {/* 工具服务中心：注册与版本合并为一页做实（决策 9 例外 + 决策 24） */}
          <Route path="/tool/registry" element={<ToolRegistryPage />} />

          {/* 后台管理 · 运行监控（唯一有数据源的管理叶） */}
          <Route path="/admin/monitor" element={<SettingsPage />} />

          {/* 16 个零接口叶：只呈现方案条款（决策 9） */}
          {Object.keys(SHELLS).map((to) => (
            <Route key={to} path={to} element={<ShellPage />} />
          ))}

          {REDIRECTS.map(([from, to]) => (
            <Route key={from} path={from} element={<Navigate to={to} replace />} />
          ))}

          <Route path="*" element={<CatchAll />} />
        </Route>
      </Routes>
    </BrowserRouter>
  )
}
