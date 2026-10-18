/** 导航可达性（决策 1 / 9 / 19）：侧栏树的每个叶都能直达；
 *  建设中壳页给出方案条款（对象 / 操作 / 依赖接口 / 工作包），已建叶有真实内容而非壳页兜底。
 *  叶清单直接取自 src/constants/nav.ts，避免用例与导航树漂移。 */
import { expect, test } from '@playwright/test'
import { LEAVES } from '../../src/constants/nav'
import { BASE_URL } from '../helpers/env'

/** 已建页必须出现的内容容器（壳页与 404 兜底都不会有） */
const REAL_CONTENT = '.card, .tbl, .kpi-grid, .grid-3, .grid-12, .grid-23, .home-hero, .dcard, .zone'
/** 页面标题：原型里六个工作台以场景条（scenebar）而非 page-head 起头，其余页均用 .page-title */
const HEADING = '.page-title, .scenebar'

test.describe('导航与壳页', () => {
  test('N1：全部导航叶直达 + 壳页带条款 + 无未捕获页面错误', async ({ page }) => {
    const pageErrors: string[] = []
    page.on('pageerror', (e) => pageErrors.push(e.message))

    expect(LEAVES.length).toBe(36)
    expect(LEAVES.filter((l) => l.shell).length).toBe(16)
    // 每个叶都带图标：图标栏态下二级项要能凭图形辨认
    expect(LEAVES.filter((l) => !l.icon).map((l) => l.to)).toEqual([])

    for (const leaf of LEAVES) {
      await page.goto(leaf.to)
      // 直达而非重定向 / 404 兜底
      expect(page.url()).toContain(leaf.to)
      const title = page.locator(HEADING).first()
      await expect(title).toBeVisible()
      await expect(title).not.toHaveText('页面不存在')

      if (leaf.shell) {
        const shell = page.locator('.shell-page').first()
        await expect(shell).toBeVisible()
        await expect(shell.locator('.tag', { hasText: '建设中' })).toBeVisible()
        // 决策 9：壳页只给方案条款，不放空表格或假按钮
        await expect(shell.locator('.spec dt', { hasText: '方案条款' })).toBeVisible()
        await expect(shell.locator('.spec dt', { hasText: '依赖接口' })).toBeVisible()
        await expect(shell.locator('table, button')).toHaveCount(0)
      } else {
        // 已建页允许内嵌局部"建设中"（如运行监控的告警子区），但整页必须有真实内容
        await expect(page.locator(REAL_CONTENT).first()).toBeVisible()
      }
    }

    expect(pageErrors, `页面出现未捕获错误：${pageErrors.join('；')}`).toEqual([])
  })

  test('N2：旧路由重定向到新的侧栏树（决策 19、24）', async ({ page }) => {
    const cases: Array<[string, string]> = [
      ['/dashboard', '/'],
      ['/assistant', '/detect/assistant'],
      ['/settings', '/admin/monitor'],
      ['/tasks/list', '/tasks'],
      ['/tasks/submit', '/tasks/new'],
      ['/tool/versions', '/tool/registry'],
    ]
    for (const [from, to] of cases) {
      await page.goto(from)
      // 断言用 BASE_URL 而非写死 :8000——E2E_BASE_URL 可以指向另一台被测后端
      await expect(page).toHaveURL(`${BASE_URL}${to}`)
    }
  })

  test('N3：侧栏收成图标栏后每个导航叶仍可见且带图形图标', async ({ page }) => {
    await page.goto('/')
    await page.locator('.rail-btn').click()
    await expect(page.locator('.frame')).toHaveClass(/side-rail/)
    const links = page.locator('aside.side a')
    await expect(links).toHaveCount(LEAVES.length)
    await expect(links.first().locator('svg')).toBeVisible()
    // 品牌文字与叶标签隐藏，logo 图片仍在
    await expect(page.locator('aside.side .logo-img')).toBeVisible()
    await expect(page.locator('aside.side .logo-text')).toBeHidden()
  })
})
