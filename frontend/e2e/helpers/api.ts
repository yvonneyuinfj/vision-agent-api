/** E2E REST 助手：统一外壳解包 + 任务/会话操作 + 状态轮询。
 *  全部打真实后端（默认 :8000），测试自建任务、只断言自建对象。 */
import { readFileSync } from 'node:fs'
import { BASE_URL } from './env'
import { cookieHeader, login, type Session } from './auth'

interface Envelope<T> {
  code: number
  message: string
  result: T | null
}

let session: Session | null = null
/** REST 助手与浏览器共用同一会话：惰性登录一次，失败即暴露凭据问题 */
async function authHeader(): Promise<Record<string, string>> {
  session ??= await login()
  return { Cookie: cookieHeader(session) }
}

async function http<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE_URL}${path}`, {
    ...init,
    headers: { ...(await authHeader()), ...(init?.headers as Record<string, string>) },
  })
  const body = (await res.json()) as Envelope<T>
  if (!res.ok || body.code !== 0) {
    throw new Error(`API ${path} 失败：HTTP ${res.status} code=${body.code} ${body.message}`)
  }
  return body.result as T
}

export interface TaskDetail {
  task_id: string
  status: string
  scene: string | null
  mode: string
  grade: { level?: string; summary?: string } | null
  review: { action?: string; opinion?: string } | null
  report: { uri?: string } | null
}

export interface SessionMessageView {
  message_id: string
  role: string
  kind: string
  content: string
  task_id: string | null
}

/** 提交任务（multipart；parameters 传 mock 控制参数，如 mock_status=abnormal） */
export async function createTask(opts: {
  scene: string
  imagePath: string
  parameters?: Record<string, unknown>
}): Promise<string> {
  const form = new FormData()
  const bytes = readFileSync(opts.imagePath)
  const name = opts.imagePath.split('/').pop() ?? 'sample.jpg'
  form.append('files', new Blob([bytes]), name)
  form.append('scene', opts.scene)
  form.append('mode', 'single')
  if (opts.parameters) form.append('parameters', JSON.stringify(opts.parameters))
  const res = await fetch(`${BASE_URL}/api/v1/tasks`, {
    method: 'POST',
    headers: await authHeader(),
    body: form,
  })
  const body = (await res.json()) as Envelope<{ task_id: string }>
  if (!res.ok || body.code !== 0) {
    throw new Error(`提交任务失败：HTTP ${res.status} code=${body.code} ${body.message}`)
  }
  return body.result!.task_id
}

/** 建会话（唯一标题便于 UI 定位会话项） */
export async function createSession(title: string): Promise<string> {
  const res = await http<{ session_id: string }>('/api/v1/agent/sessions', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ title }),
  })
  return res.session_id
}

export async function getTask(taskId: string): Promise<TaskDetail> {
  return http<TaskDetail>(`/api/v1/tasks/${taskId}`)
}

export async function getSessionMessages(sessionId: string): Promise<SessionMessageView[]> {
  return http<SessionMessageView[]>(`/api/v1/agent/sessions/${sessionId}/messages`)
}

/** 轮询任务直至命中目标状态（默认 500ms 间隔） */
export async function waitForStatus(
  taskId: string,
  statuses: string[],
  timeoutMs = 60_000,
): Promise<TaskDetail> {
  const deadline = Date.now() + timeoutMs
  for (;;) {
    const detail = await getTask(taskId)
    if (statuses.includes(detail.status)) return detail
    if (Date.now() > deadline) {
      throw new Error(`等待任务 ${taskId} 进入 [${statuses}] 超时（当前 ${detail.status}）`)
    }
    await new Promise((r) => setTimeout(r, 500))
  }
}

/** 轮询会话消息直至出现指定 kind 的消息（落定事件异步，给足窗口） */
export async function waitForMessage(
  sessionId: string,
  kind: string,
  timeoutMs = 30_000,
): Promise<SessionMessageView> {
  const deadline = Date.now() + timeoutMs
  for (;;) {
    const msgs = await getSessionMessages(sessionId)
    const hit = msgs.find((m) => m.kind === kind)
    if (hit) return hit
    if (Date.now() > deadline) throw new Error(`等待会话 ${sessionId} 出现 ${kind} 消息超时`)
    await new Promise((r) => setTimeout(r, 500))
  }
}

/** 确定性建数：abnormal + 严重度强制 high → 命中规则「max_severity >= 3」→
 *  判级 review → 人工复核门（interrupt）。不带 mock_severity 时严重度随机，
 *  低严重度会直通完成、不进复核，故必须显式锁定。 */
export function createAbnormalTask(imagePath: string, scene = 'borescope'): Promise<string> {
  return createTask({
    scene,
    imagePath,
    parameters: {
      mock_status: 'abnormal',
      mock_severity: 'high',
      confidence_threshold: 0.5,
      max_defects: 1,
      return_annotated: true,
    },
  })
}

/** 确定性建数：normal 直通完成（供报告页用例） */
export function createNormalTask(imagePath: string, scene = 'borescope'): Promise<string> {
  return createTask({
    scene,
    imagePath,
    parameters: { mock_status: 'normal', return_annotated: true },
  })
}
