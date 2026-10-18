/** E2E 注册表基座：把能力行临时绑到 mock 推理（:8002），跑完复原。
 *
 *  为什么需要：批次 3 之后 `/capabilities` 注册表 6 行全指 5 个真工具（18011-18016）。
 *  真工具不吃 `mock_status / mock_severity`，e2e 的 7 条确定性用例（待复核 / 改判 / 驳回）
 *  因此造不出目标状态。`helpers/env.ts` 的"注册表全指 mock :8002"契约由本模块在运行期重建。
 *
 *  为什么必须保持 enabled：`assemble.py` 只挂 enabled 行，置 maintenance 会直接把工具卸载，
 *  任务连工具都不调（实测 status=failed、capability_used=null）。因此绑 mock 走决策 41 的正门：
 *  对 mock 跑一次**完整试运行**拿回执，再带 `expected_snapshot` PATCH endpoint。
 *
 *  复原反过来**不跑完整试运行**：真工具各吃自己的输入契约（实测 DR 要 `images` 字段、
 *  荧光要 parameters JSON、金相 artifacts 回相对 URI），默认单图形态的干跑必然红。
 *  改为置 maintenance（门禁只作用 enabled 行）→ 写回真实端点 → 再 enabled，
 *  由决策 45 的启用前置 health 探测确认真工具在线。
 *
 *  跳过带请求头的行：headers 只回掩码（决策 43），换走就回不来。infrared 的 X-API-Key 即此类。
 *  快照落 test-results/.e2e-registry-snapshot.json（gitignore 内）；崩溃后可跑
 *  `npx tsx e2e/restore-registry.ts` 手工复原。 */
import { copyFileSync, existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { BASE_URL } from './env'
import { cookieHeader, login } from './auth'

const HERE = fileURLToPath(new URL('.', import.meta.url))
export const SNAPSHOT_PATH = resolve(HERE, '../test-results/.e2e-registry-snapshot.json')
const API = `${BASE_URL}/api/v1`
const OPERATOR = 'e2e-harness'

/** 干跑样例物料被钉死在 local-tools/ 与 data/ 之下（决策 34），
 *  e2e 夹具在 docs/image，故先复制一份进 data/e2e/ 再喂给干跑。 */
const SAMPLE_SRC = resolve(HERE, '../../../docs/image/class1_1.jpg')
const SAMPLE_REL = 'data/e2e/class1_1.jpg'
const MOCK_ENDPOINT = 'http://127.0.0.1:8002'
const MOCK_HEALTH = `${MOCK_ENDPOINT}/health`

interface Detail {
  capability_id: string
  endpoint: string
  health_endpoint: string | null
  capability_type: string
  status: string
  headers_masked: Record<string, string>
  timeout_s: number
}

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API}${path}`, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      Cookie: cookieHeader(await login()),
      ...(init?.headers as Record<string, string>),
    },
  })
  const text = await res.text()
  let body: { code: number; message: string; result: T | null }
  try {
    body = JSON.parse(text)
  } catch {
    throw new Error(`E2E 注册表操作 ${path} 返回非信封（HTTP ${res.status}）：${text.slice(0, 120)}`)
  }
  if (!res.ok || body.code !== 0) {
    throw new Error(`E2E 注册表操作 ${path} 失败：HTTP ${res.status} code=${body.code} ${body.message}`)
  }
  return body.result as T
}

const detail = (capabilityId: string) => api<Detail>(`/capabilities/${capabilityId}`)

/** 完整试运行：三段全过才发回执；返回快照供 PATCH 过门禁 */
async function fullDryRun(endpoint: string, d: Detail): Promise<string> {
  const run = await api<{ passed: boolean; snapshot: string; steps: { name: string; ok: boolean; message: string }[] }>(
    '/capabilities/dry-run',
    {
      method: 'POST',
      body: JSON.stringify({
        endpoint,
        headers: {},
        image_path: SAMPLE_REL,
        capability_type: d.capability_type,
        timeout_s: d.timeout_s,
      }),
    },
  )
  if (!run.passed || !run.snapshot) {
    throw new Error(
      `完整试运行未通过（${endpoint}）：` +
        run.steps.map((s) => `${s.name}=${s.ok ? 'ok' : s.message}`).join(' · '),
    )
  }
  return run.snapshot
}

async function setStatus(capabilityId: string, status: string, reason: string): Promise<void> {
  await api(`/capabilities/${capabilityId}/status`, {
    method: 'POST',
    body: JSON.stringify({ status, change_reason: reason, operator: OPERATOR }),
  })
}

/** 绑 mock：完整试运行拿回执 → 带快照 PATCH（行始终 enabled） */
async function bindMock(d: Detail): Promise<void> {
  const snapshot = await fullDryRun(MOCK_ENDPOINT, d)
  const fresh = await detail(d.capability_id)
  await api(`/capabilities/${d.capability_id}`, {
    method: 'PATCH',
    body: JSON.stringify({
      expected_version: fresh.version,
      endpoint: MOCK_ENDPOINT,
      health_endpoint: MOCK_HEALTH,
      expected_snapshot: snapshot,
      change_reason: 'e2e 基座：临时绑 mock 以确定性造待复核',
      operator: OPERATOR,
    }),
  })
}

/** 复原：维护态绕开门禁写回真实端点 → enabled 时由 health 探测把关 */
async function restoreReal(d: Detail): Promise<void> {
  await setStatus(d.capability_id, 'maintenance', 'e2e 基座：复原期间置维护态')
  const fresh = await detail(d.capability_id)
  await api(`/capabilities/${d.capability_id}`, {
    method: 'PATCH',
    body: JSON.stringify({
      expected_version: fresh.version,
      endpoint: d.endpoint,
      health_endpoint: d.health_endpoint,
      change_reason: 'e2e 基座：复原真实工具端点',
      operator: OPERATOR,
    }),
  })
  await setStatus(d.capability_id, 'enabled', 'e2e 基座：复原启用态并重跑真实健康探测')
}

function stageSample(): void {
  const dest = resolve(HERE, `../../../${SAMPLE_REL}`)
  mkdirSync(dirname(dest), { recursive: true })
  copyFileSync(SAMPLE_SRC, dest)
}

export async function bindRegistryToMock(): Promise<void> {
  const rows = await api<{ items: { capability_id: string }[] }>('/capabilities')
  stageSample()
  // 累积而非整体重写：上一次运行绑过的行本次会跳过，其真实端点必须仍留在快照里
  const existing = readSnapshot()
  const originals: Detail[] = existing.originals
  const known = new Set(originals.map((o) => o.capability_id))
  const stuck: string[] = []
  mkdirSync(dirname(SNAPSHOT_PATH), { recursive: true })
  for (const r of rows.items) {
    const d = await detail(r.capability_id)
    if (d.endpoint === MOCK_ENDPOINT) {
      // 快照里没有＝上次运行没留下真实端点，复原无从下手，如实报出而不是塞个假的
      if (!known.has(d.capability_id)) stuck.push(d.capability_id)
      continue
    }
    if (Object.keys(d.headers_masked || {}).length > 0) {
      console.log(`  跳过 ${d.capability_id}（带请求头，掩码不可回读）`)
      continue
    }
    if (d.status !== 'enabled') {
      console.log(`  跳过 ${d.capability_id}（当前 ${d.status}，非 enabled 不参与路由）`)
      continue
    }
    originals.push(d)
    known.add(d.capability_id)
    writeFileSync(SNAPSHOT_PATH, JSON.stringify({ originals, boundAt: new Date().toISOString() }, null, 2))
    await bindMock(d)
  }
  if (stuck.length) {
    throw new Error(
      `以下能力行已指向 mock 但快照里没有真实端点，无法自动复原：${stuck.join('、')}。\n` +
        '请手工把它们写回 scripts/sync_capabilities.py 内置表对应的端点后再跑 e2e。',
    )
  }
  console.log(`  e2e 注册表基座：${originals.length} 行已绑 mock ${MOCK_ENDPOINT}`)
}

function readSnapshot(): { originals: Detail[] } {
  if (!existsSync(SNAPSHOT_PATH)) return { originals: [] }
  try {
    const parsed = JSON.parse(readFileSync(SNAPSHOT_PATH, 'utf-8')) as { originals?: Detail[] }
    return { originals: parsed.originals ?? [] }
  } catch {
    return { originals: [] }
  }
}

export async function restoreRegistry(): Promise<void> {
  if (!existsSync(SNAPSHOT_PATH)) {
    console.log('  e2e 注册表基座：无快照，跳过复原')
    return
  }
  const { originals } = JSON.parse(readFileSync(SNAPSHOT_PATH, 'utf-8')) as { originals: Detail[] }
  if (!originals.length) return
  for (const d of originals) {
    // 真工具没监听时启用前置探测会拒绝——只告警不抛出，否则一条失败会把整批复原带崩
    try {
      await restoreReal(d)
      console.log(`  已复原 ${d.capability_id} → ${d.endpoint}`)
    } catch (e) {
      console.warn(`  复原 ${d.capability_id} 失败：${e instanceof Error ? e.message : e}`)
    }
  }
  writeFileSync(SNAPSHOT_PATH, JSON.stringify({ originals: [], restoredAt: new Date().toISOString() }))
}
