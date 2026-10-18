import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { message } from 'antd'
import { useCapabilities } from '../../hooks/useCapabilities'
import { LoadError, ModuleShell } from '../../components/common/ModuleShell'
import {
  ADAPTER_TYPE_TEXT,
  CAPABILITY_TYPE_TEXT,
  type CapabilityStatus,
  type CapabilitySummary,
} from '../../api/capabilities'
import { fetchTaskList } from '../../api/tasks'
import type { TaskSummary } from '../../api/types'
import { WORKBENCH_MAP, type WorkbenchDef } from '../../constants/workbench'
import { CapabilityStatusModal } from './CapabilityStatusModal'
import { ToolRegistryModal } from './ToolRegistryModal'
import { Table } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import './index.css'

/* 工具服务中心（方案 §8.1 / §8.3 工具接入）：注册与版本同源同表（都取 GET /capabilities），
   故合并为一页「工具注册与版本」（决策 24）。写侧批次 3 已做实：登记 / 字段级编辑 / 启停 / 干跑
   （决策 27–50），另保留 POST /capabilities/reload 作为手工强制重载（决策 38）。
   页面只留台账与就近提示；长口径公告已移入 docs（决策 51-⑦）。 */

const sceneLabel = (scene: string) =>
  (WORKBENCH_MAP as Record<string, WorkbenchDef>)[scene]?.label ?? scene

const EMPTY = '—'

/** 刷新注册表：让后端立即重读 DB（决策 38 的手动兜底），二次确认后才发请求 */
function ReloadButton({
  reload,
  refreshing,
}: {
  reload: () => Promise<{ ok: boolean; count?: number; message?: string }>
  refreshing: boolean
}) {
  const [armed, setArmed] = useState(false)

  async function click() {
    if (!armed) {
      setArmed(true)
      window.setTimeout(() => setArmed(false), 4000)
      return
    }
    setArmed(false)
    const r = await reload()
    if (r.ok) message.success(`注册表已刷新：${r.count} 条能力`)
    else message.error(r.message ?? '刷新失败')
  }

  return (
    <button
      type="button"
      className={armed ? 'btn btn-sm btn-danger' : 'btn btn-sm'}
      disabled={refreshing}
      onClick={() => void click()}
    >
      {refreshing ? <span className="spin" /> : null}
      {armed ? '确认刷新（重读 DB）' : '重新加载能力注册表'}
    </button>
  )
}

export function ToolRegistryPage() {
  const { caps, health, loading, error, refreshing, reload, refresh } = useCapabilities()
  const { refs, error: refError, loading: refLoading, reload: reloadRefs } = useRefWindow()
  const [modal, setModal] = useState<{ open: boolean; capabilityId: string | null }>({
    open: false,
    capabilityId: null,
  })
  const [statusReq, setStatusReq] = useState<{
    row: CapabilitySummary
    target: CapabilityStatus
  } | null>(null)

  const columns: ColumnsType<CapabilitySummary> = [
    {
      // 台账行只认工具身份：`detect/<tool_name>` 是调用与任务引用的键，能力 ID 作副行。
      // 工具简介（description）属工作台/首页，不进台账表；技术事实见「说明」列与编辑表单。
      title: '工具',
      dataIndex: 'tool_name',
      width: 220,
      fixed: 'left',
      render: (_, r) => (
        <div>
          <div className="mono" style={{ fontWeight: 550 }}>
            {r.tool_name}
          </div>
          <div className="mono faint" style={{ fontSize: 11 }}>
            {r.capability_id}
          </div>
        </div>
      ),
    },
    {
      title: '专业',
      dataIndex: 'scene',
      width: 108,
      render: (v: string) => (
        <Link to={`/detect/${v}`} className="mono">
          {sceneLabel(v)}
        </Link>
      ),
    },
    {
      title: '类型',
      dataIndex: 'capability_type',
      width: 88,
      render: (v: CapabilitySummary['capability_type']) => CAPABILITY_TYPE_TEXT[v],
    },
    {
      title: '适配器',
      dataIndex: 'adapter_type',
      width: 100,
      render: (v: CapabilitySummary['adapter_type']) => (
        <span className="mono" style={{ fontSize: 12 }}>
          {ADAPTER_TYPE_TEXT[v]}
        </span>
      ),
    },
    {
      title: '工具版本',
      dataIndex: 'tool_version',
      width: 92,
      render: (v: string) => (
        <span className="mono" style={{ fontSize: 12 }}>
          {v || EMPTY}
        </span>
      ),
    },
    {
      title: '模型版本',
      dataIndex: 'model_version',
      width: 92,
      render: (v: string) => (
        <span className="mono" style={{ fontSize: 12 }}>
          {v || EMPTY}
        </span>
      ),
    },
    {
      title: '近 100 条任务引用',
      key: 'refs',
      width: 132,
      render: (_, r) => {
        if (refLoading) return <span className="faint">统计中…</span>
        if (refs === null) return <span className="faint">{EMPTY}</span>
        return <span className="num">{refs[r.tool_name] ?? 0} 项</span>
      },
    },
    {
      title: '健康',
      key: 'health',
      width: 132,
      render: (_, r) => {
        // 注册状态优先（停用/维护中时探测无意义）；整列统一用全站 `.tag` 胶囊，一个状态一个色
        if (r.status === 'disabled') return <span className="tag t-pending">已停用</span>
        if (r.status === 'maintenance') return <span className="tag t-review">维护中</span>
        const h = health?.find((x) => x.tool_name === r.tool_name) ?? null
        if (!h) return <span className="tag t-child">未探测</span>
        return (
          <>
            <span className={`tag ${h.healthy ? 't-success' : 't-failed'}`}>
              {h.healthy ? (h.latency_ms != null ? `健康 ${h.latency_ms}ms` : '健康') : '降级'}
            </span>
            {!h.healthy && <span className="hint"> {h.message}</span>}
          </>
        )
      },
    },
    {
      // 操作列放在「健康」与「说明」之间：首列已 fixed:'left'，再固定右侧会与之叠压
      title: '操作',
      key: 'ops',
      width: 126,
      render: (_, r) => (
        <span className="reg-op">
          <button
            type="button"
            className="lnk"
            onClick={() => setModal({ open: true, capabilityId: r.capability_id })}
          >
            编辑
          </button>
          <button
            type="button"
            className="lnk"
            onClick={() =>
              setStatusReq({ row: r, target: r.status === 'enabled' ? 'disabled' : 'enabled' })
            }
          >
            {r.status === 'enabled' ? '停用' : '启用'}
          </button>
        </span>
      ),
    },
    {
      title: '说明',
      dataIndex: 'note',
      render: (v: string) => (
        <span className="muted" style={{ fontSize: 12 }}>
          {v || EMPTY}
        </span>
      ),
    },
  ]

  const probed = health !== null
  const readyCount = health?.filter((h) => h.healthy).length ?? 0
  const degradedCount = (health?.length ?? 0) - readyCount
  const toolNames = new Set(caps.map((c) => c.tool_name))
  const enabledScenes = caps
    .filter((c) => c.status === 'enabled')
    .map((c) => ({ scene: c.scene, capability_id: c.capability_id }))

  return (
    <>
      <div className="page-head">
        <div>
          <div className="page-title">工具注册与版本</div>
          <div className="page-sub">
            已登记能力 {caps.length} 项 · 覆盖 {toolNames.size} 个工具名
            {probed ? ` · 健康 ${readyCount} · 降级 ${degradedCount}` : ' · 健康探测未完成'}
            （来源：能力注册表，非独立台账）
          </div>
        </div>
        <div className="row">
          <span className="hint mono">GET /capabilities</span>
          <ReloadButton reload={reload} refreshing={refreshing} />
          <button
            type="button"
            className="btn btn-sm btn-primary"
            onClick={() => setModal({ open: true, capabilityId: null })}
          >
            添加工具
          </button>
        </div>
      </div>
      <div className="stack">
        {error && <LoadError message={error} onRetry={() => void refresh()} />}
        <section className="card">
          <div className="card-h">
            <div className="card-title">
              已注册工具与版本台账 <span className="en">TOOL REGISTRY &amp; VERSIONS</span>
            </div>
            <span className="hint">共 {caps.length} 条 · 健康探测逐个工具发起</span>
          </div>
          <div className="card-b table-pad">
            <Table<CapabilitySummary>
              size="small"
              tableLayout="fixed"
              rowKey={(r) => r.capability_id}
              loading={loading}
              dataSource={[...caps].sort((a, b) => a.tool_name.localeCompare(b.tool_name))}
              columns={columns}
              pagination={false}
              scroll={{ x: 'max-content' }}
              locale={{ emptyText: '注册表为空：后端尚无能力记录（需先执行 scripts/sync_capabilities.py）' }}
            />
          </div>
          <div className="card-b" style={{ borderTop: '1px solid var(--border)' }}>
            <div className="hint" style={{ lineHeight: 1.75 }}>
              版本口径：工具与模型版本随任务冻结，接口只下发<b>版本字符串</b>；模型权重哈希（
              <span className="mono">model_weights_sha256</span>
              ）可在编辑表单填写，来源数据集与评估记录仍未随摘要下发。
              字段删除 / 单位变化 / 枚举语义变化须<b>升主版本</b>，未登记 Schema 版本的工具拒绝联调（方案 §9.1）。
              <br />
              「近 100 条任务引用」只统计<b>最近 100 条任务</b>窗口（
              <span className="mono">GET /tasks?limit=100</span>，按 <span className="mono">capability_used</span>
              匹配），<b>不是全量历史</b>；接口在任务侧仅回写工具名，故同一工具的多个版本行共享同一引用计数，
              版本粒度的引用需任务侧冻结版本查询接口。
              {refError && (
                <>
                  <br />
                  <span className="hint warn">
                    任务窗口读取失败：{refError}（引用数显示为 {EMPTY}）
                    <button type="button" className="lnk" onClick={() => void reloadRefs()}>
                      重新读取
                    </button>
                  </span>
                </>
              )}
            </div>
          </div>
        </section>
        <ModuleShell {...VERSION_SHELL} />
      </div>
      <ToolRegistryModal
        open={modal.open}
        capabilityId={modal.capabilityId}
        enabledScenes={enabledScenes}
        onClose={() => setModal({ open: false, capabilityId: null })}
        onSaved={() => void refresh()}
      />
      <CapabilityStatusModal
        target={statusReq?.target ?? null}
        capability={
          statusReq
            ? {
                capability_id: statusReq.row.capability_id,
                tool_name: statusReq.row.tool_name,
                status: statusReq.row.status,
              }
            : null
        }
        onClose={() => setStatusReq(null)}
        onDone={() => void refresh()}
      />
    </>
  )
}

/* ---------- 共用数据源与说明块 ---------- */

/** 引用窗口：GET /tasks?limit=100 反查工具被哪些任务引用（接口只回写 tool_name） */
function useRefWindow() {
  const [refs, setRefs] = useState<Record<string, number> | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const tasks: TaskSummary[] = await fetchTaskList(100)
      const map: Record<string, number> = {}
      for (const t of tasks) {
        const key = t.capability_used
        if (key) map[key] = (map[key] ?? 0) + 1
      }
      setRefs(map)
    } catch (e) {
      setRefs(null)
      setError(e instanceof Error ? e.message : '任务窗口读取失败')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  return { refs, error, loading, reload: load }
}

/** 版本登记与退役壳说明（决策 9：登记/编辑/启停已做实，剩余只给条款与工作包号） */
const VERSION_SHELL = {
  title: '版本登记与退役',
  sub: 'ToolVersion / ModelVersion 的验证、启用、退役流转与 Schema 台账，本轮不实现',
  goal:
    '登记 API 版本与参数/结果 Schema、依赖与运行包；已被任务引用的版本不可覆盖；升级走"登记 → 验证 → 启用 → 退役"。',
  clause: '建设方案 §8.1 工具服务中心对象表 · §9.1 工具侧统一接口基线',
  api: 'Schema 版本台账、ModelVersion 评估记录、每环境 ACTIVE 版本切换与回滚',
  pkg: 'G3-04 模型发布与回滚 · G3-05 版本切换与回滚',
  next:
    'Schema 版本上传与校验、退役流转与引用保护提示、每工具每环境的默认版本切换与回滚',
}
