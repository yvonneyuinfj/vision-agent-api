import { useLocation } from 'react-router-dom'
import { ModuleShell } from '../components/common/ModuleShell'
import { SHELLS } from '../constants/shells'

/** 建设中壳页的统一入口：路径 → SHELLS 文案（决策 9）。 */
export default function ShellPage() {
  const { pathname } = useLocation()
  const spec = SHELLS[pathname]
  if (!spec) return <NotFound path={pathname} />
  return <ModuleShell {...spec} />
}

export function NotFound({ path }: { path: string }) {
  return (
    <div className="shell-page">
      <div className="shell-hd">
        <div className="grow">
          <div className="page-title" style={{ fontSize: 17 }}>
            页面不存在
          </div>
          <div className="page-sub mono">{path}</div>
        </div>
      </div>
      <div className="shell-bd">
        <dl className="spec">
          <div>
            <dt>原因</dt>
            <dd>该路径未登记在导航与路由表中；请从左侧菜单进入，或回到首页。</dd>
          </div>
        </dl>
      </div>
    </div>
  )
}
