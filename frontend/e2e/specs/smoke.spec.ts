/** 冒烟：侧栏外壳 / 首页（批次2 已并入 Dashboard）/ 知识检索可开 + 关键元素 + 无未捕获页面错误。 */
import { expect, test } from '@playwright/test'

test.describe('冒烟', () => {
  test('S1：核心页面可开 + 关键元素 + 无页面错误', async ({ page }) => {
    const pageErrors: string[] = []
    page.on('pageerror', (e) => pageErrors.push(e.message))

    // 首页：双业务入口 hero + KPI + 六场景卡 + 队列/分布/态势（原 Dashboard 并入此处）
    await page.goto('/')
    await expect(page.locator('.home-hero-title')).toHaveText('工业图像智能检测智能体')
    await expect(page.getByRole('link', { name: /新建检测任务/ })).toBeVisible()
    await expect(page.locator('.kpi-label').first()).toBeVisible()
    await expect(page.locator('.page-title', { hasText: '专业工作台' })).toBeVisible()
    await expect(page.locator('.card-title', { hasText: '待复核队列' })).toBeVisible()
    await expect(page.locator('.card-title', { hasText: '场景分布' })).toBeVisible()
    await expect(page.locator('.card-title', { hasText: '平台态势' })).toBeVisible()

    // 侧栏外壳：分组头 + 叶 + 建设中壳页标记（决策 1/9）
    const side = page.locator('aside.side')
    await expect(side).toHaveAttribute('aria-label', '主导航')
    await expect(side.locator('.grp-h', { hasText: '任务中心' })).toBeVisible()
    await expect(side.getByRole('link', { name: /结果显示与复核/ })).toBeVisible()
    await expect(side.locator('.sflag').first()).toBeVisible()

    // 旧路由重定向（决策 19）：书签与站内旧跳转仍可达，不 404
    await page.goto('/dashboard')
    await expect(page).toHaveURL(/\/$/)
    await page.goto('/settings')
    await expect(page).toHaveURL(/\/admin\/monitor$/)

    // 知识检索：会话输入 + 本轮新增的检索口径条
    await page.goto('/knowledge/search')
    await expect(page.getByPlaceholder('输入问题，Enter 发送，Shift + Enter 换行')).toBeVisible()
    await expect(page.locator('.kb-chat-scope')).toBeVisible()

    expect(pageErrors, `页面出现未捕获错误：${pageErrors.join('；')}`).toEqual([])
  })
})
