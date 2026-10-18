/** 从项目根 .env 解析数据库连接（DATABASE_URL=mysql+aiomysql://user:pwd@host:port/db）。
 *  E2E 与后端共用同一事实源，避免连接参数漂移。 */
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

export interface DbConfig {
  host: string
  port: number
  user: string
  password: string
  database: string
}

const HERE = fileURLToPath(new URL('.', import.meta.url))
const ENV_PATH = resolve(HERE, '../../../.env')

export function dbConfig(): DbConfig {
  const raw = readFileSync(ENV_PATH, 'utf-8')
  const line = raw
    .split('\n')
    .map((l) => l.trim())
    .find((l) => l.startsWith('DATABASE_URL='))
  if (!line) throw new Error(`DATABASE_URL 未找到：${ENV_PATH}`)
  const url = line.slice('DATABASE_URL='.length).trim()
  // mysql+aiomysql://user:pwd@host:port/db
  const m = url.match(/^mysql(?:\+\w+)?:\/\/([^:]+):([^@]*)@([^:/]+):(\d+)\/(.+)$/)
  if (!m) throw new Error(`DATABASE_URL 解析失败：${url}`)
  return {
    user: m[1],
    password: m[2],
    host: m[3],
    port: Number(m[4]),
    database: m[5],
  }
}

/** E2E 目标后端地址（与 playwright.config 的 baseURL 同源） */
export const BASE_URL = process.env.E2E_BASE_URL || 'http://127.0.0.1:8000'

/** mock 推理服务健康地址（能力注册表全部指向本机 8002） */
export const MOCK_HEALTH_URL = 'http://127.0.0.1:8002/health'
