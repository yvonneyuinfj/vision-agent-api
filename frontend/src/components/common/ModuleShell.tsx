import type { ReactNode } from 'react'
import { Icon } from '../ui/Icon'

/* 反引号片段 → 等宽 code：壳页与批注文案里的接口名统一走这里，避免整段 HTML 注入 */
export function InlineCode({ text }: { text: string }): ReactNode {
  return (
    <>
      {text.split('`').map((seg, i) => (i % 2 ? <code className="mono" key={i}>{seg}</code> : <span key={i}>{seg}</span>))}
    </>
  )
}

export function Notice({
  tone = 'warn',
  children,
}: {
  tone?: 'warn' | 'info' | 'gray'
  children: ReactNode
}) {
  return (
    <div className={`notice ${tone}`}>
      <span className="n-ico">
        <Icon name={tone === 'info' || tone === 'gray' ? 'info' : 'warn'} size={14} />
      </span>
      <span>{children}</span>
    </div>
  )
}

/** 只读页取数失败：说明原因 + 恢复入口（决策：错误须含恢复路径） */
export function LoadError({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <Notice tone="warn">
      <span>
        <b>读取失败：</b>
        {message}
        <br />
        <button type="button" className="lnk" onClick={onRetry}>
          重新读取
        </button>
      </span>
    </Notice>
  )
}

/** 建设中壳页（决策 9）：只有方案条款，无按钮、无空表格、无 mock 数据 */
export function ModuleShell({
  title,
  sub,
  goal,
  clause,
  api,
  pkg,
  next,
  notice,
  extra,
}: {
  title: string
  sub: string
  goal: string
  clause: string
  api: string
  pkg: string
  next: string
  notice?: string
  extra?: ReactNode
}) {
  return (
    <div className="shell-page">
      <div className="shell-hd">
        <span className="uc">
          <Icon name="lock" size={19} />
        </span>
        <div className="grow">
          <div className="page-title" style={{ fontSize: 17 }}>
            {title}
          </div>
          <div className="page-sub">{sub}</div>
        </div>
        <span className="tag t-pending">建设中</span>
      </div>
      <div className="shell-bd">
        <dl className="spec">
          <div>
            <dt>本页目标</dt>
            <dd>{goal}</dd>
          </div>
          <div>
            <dt>方案条款</dt>
            <dd>
              <InlineCode text={clause} />
            </dd>
          </div>
          <div>
            <dt>依赖接口（当前不存在）</dt>
            <dd>
              <InlineCode text={api} />
            </dd>
          </div>
          <div>
            <dt>对应工作包</dt>
            <dd>
              <em>{pkg}</em>
            </dd>
          </div>
          <div>
            <dt>接口就绪后本页提供</dt>
            <dd>{next}</dd>
          </div>
        </dl>
      </div>
      {notice && (
        <div className="mt-16">
          <Notice tone="info">{notice}</Notice>
        </div>
      )}
      {extra}
    </div>
  )
}
