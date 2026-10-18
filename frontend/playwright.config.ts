/** Playwright E2E 配置（会话共识：真实 :8000 后端 + 自建任务 + 串行单浏览器）。
 *  - workers=1：共用开发库，串行防互扰；
 *  - 失败留 trace/截图；HTML 报告不自动打开；
 *  - globalSetup 健康检查后端与 mock 推理，未就绪快速失败；登录并临时把注册表绑到 mock；
 *  - globalTeardown 复原注册表真实端点与启用态；
 *  - storageState：globalSetup 登录后写入会话 Cookie，浏览器上下文免走登录页。 */
import { defineConfig } from '@playwright/test'
import { AUTH_STATE_PATH } from './e2e/helpers/auth'
import { existsSync } from 'node:fs'

export default defineConfig({
  testDir: './e2e/specs',
  globalSetup: './e2e/global-setup.ts',
  globalTeardown: './e2e/global-teardown.ts',
  timeout: 150_000,
  expect: { timeout: 15_000 },
  workers: 1,
  fullyParallel: false,
  retries: 0,
  reporter: [['list'], ['html', { open: 'never', outputFolder: 'e2e/html-report' }]],
  outputDir: './e2e/test-results',
  use: {
    baseURL: process.env.E2E_BASE_URL || 'http://127.0.0.1:8000',
    storageState: existsSync(AUTH_STATE_PATH) ? AUTH_STATE_PATH : undefined,
    viewport: { width: 1600, height: 1000 },
    actionTimeout: 10_000,
    navigationTimeout: 20_000,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  projects: [{ name: 'chromium', use: { browserName: 'chromium' } }],
})
