/** 全局前置：健康检查后端（:8000）与 mock 推理（:8002）、登录一次写入会话态、
 *  并把注册表临时绑到 mock（批次 3 后默认指真工具，确定性用例会失去造数手段）。
 *  E2E 打真实环境，环境未就绪时快速失败并给出启动指引，避免整批用例闷头超时。 */
import { BASE_URL, MOCK_HEALTH_URL } from './helpers/env'
import { login, writeAuthState } from './helpers/auth'
import { bindRegistryToMock } from './helpers/registry'

async function probe(url: string, what: string, hint: string): Promise<void> {
  try {
    const res = await fetch(url, { signal: AbortSignal.timeout(5_000) })
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
  } catch (e) {
    throw new Error(
      `E2E 前置检查失败：${what}（${url}）不可达 —— ${e instanceof Error ? e.message : e}。\n${hint}`,
    )
  }
}

export default async function globalSetup(): Promise<void> {
  await probe(
    `${BASE_URL}/api/v1/health`,
    '后端服务',
    '请先启动后端：uv run uvicorn app.main:app --port 8000',
  )
  await probe(
    MOCK_HEALTH_URL,
    'mock 推理服务',
    '请先启动 mock：uv run uvicorn mock_service.main:app --port 8002',
  )
  await writeAuthState(await login())
  await bindRegistryToMock()
}
