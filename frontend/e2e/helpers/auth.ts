/** E2E 登录态：后端 auth_enabled 默认 True，未登录会被 SPA 守卫弹到 /login，
 *  因此登录一次拿到 httpOnly 会话 Cookie，同时喂给浏览器上下文与 REST 助手。
 *  凭据取 E2E_AUTH_USERNAME / E2E_AUTH_PASSWORD，回落到 frontend/.env.e2e（gitignore 内），
 *  再回落到后端 config.py 的开发默认值。 */
import { readFileSync, existsSync } from 'node:fs'
import { resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { BASE_URL } from './env'

const HERE = fileURLToPath(new URL('.', import.meta.url))
export const AUTH_STATE_PATH = resolve(HERE, '../test-results/.e2e-auth-state.json')

const DEFAULT_COOKIE_NAME = 'qc_session'

function fileEnv(key: string): string | undefined {
  const path = resolve(HERE, '../../.env.e2e')
  if (!existsSync(path)) return undefined
  const line = readFileSync(path, 'utf-8')
    .split('\n')
    .map((l) => l.trim())
    .find((l) => l.startsWith(`${key}=`) && !l.startsWith('#'))
  return line ? line.slice(key.length + 1).trim() : undefined
}

function credential(key: string, fallback: string): string {
  return process.env[key] || fileEnv(key) || fallback
}

export interface Session {
  cookieName: string
  cookieValue: string
  username: string
}

export async function login(): Promise<Session> {
  const username = credential('E2E_AUTH_USERNAME', 'qcadmin')
  const password = credential('E2E_AUTH_PASSWORD', 'Qk9pXmVz2Lq7Wr4!FzT')
  const res = await fetch(`${BASE_URL}/api/v1/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }),
  })
  if (!res.ok) {
    throw new Error(
      `E2E 登录失败：HTTP ${res.status} ${await res.text()}\n` +
        '请核对后端 AUTH_USERNAME / AUTH_PASSWORD，或设置 E2E_AUTH_USERNAME / E2E_AUTH_PASSWORD。',
    )
  }
  const raw = res.headers.get('set-cookie')
  if (!raw) {
    throw new Error(
      'E2E 登录成功但未拿到 Set-Cookie：后端 auth_enabled 可能为 False，' +
        '此时应去掉 Cookie 注入而非伪造会话。',
    )
  }
  const [pair] = raw.split(';')
  const idx = pair.indexOf('=')
  return {
    cookieName: pair.slice(0, idx).trim(),
    cookieValue: pair.slice(idx + 1).trim(),
    username,
  }
}

export function cookieHeader(session: Session): string {
  return `${session.cookieName}=${session.cookieValue}`
}

/** 写 Playwright storageState：浏览器上下文自带会话，免走过场登录 UI。 */
export async function writeAuthState(session: Session): Promise<void> {
  const url = new URL(BASE_URL)
  const state = {
    cookies: [
      {
        name: session.cookieName,
        value: session.cookieValue,
        domain: url.hostname,
        path: '/',
        httpOnly: true,
        secure: false,
        sameSite: 'Lax' as const,
        expires: Math.floor(Date.now() / 1000) + 12 * 3600,
      },
    ],
    origins: [],
  }
  const { mkdirSync, writeFileSync } = await import('node:fs')
  mkdirSync(resolve(AUTH_STATE_PATH, '..'), { recursive: true })
  writeFileSync(AUTH_STATE_PATH, JSON.stringify(state, null, 2))
}
