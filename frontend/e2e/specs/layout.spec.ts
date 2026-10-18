/** 助手页等高布局：grid 两列等高、填满剩余视口、页面与外壳均无横向溢出。
 *  高度口径 = 100vh - var(--top-h) - 140px（页面 chrome：面包屑页头 + 卡片外边距 + 底部留白）；
 *  --top-h 在侧栏外壳改造后收窄为 56px，故从 CSS 变量读取而不是写死数字。 */
import { expect, test } from '@playwright/test'
import { createSession } from '../helpers/api'

test.describe('质检助手 · 等高布局', () => {
  test('D10：两列等高且填满剩余高度，页面无纵向滚动 / 无横向溢出', async ({ page }) => {
    // 会话列表非空，确保布局按真实内容渲染
    await createSession(`E2E 布局 ${Date.now()}`)

    await page.goto('/detect/assistant')
    const left = page.locator('.chat-panel')
    const right = page.locator('.assistant-side')
    await expect(left).toBeVisible()
    await expect(right).toBeVisible()
    await expect(page.locator('.session-list')).toBeVisible()

    const m = await page.evaluate(() => {
      const topH = parseFloat(
        getComputedStyle(document.documentElement).getPropertyValue('--top-h'),
      )
      const l = document.querySelector('.chat-panel')!.getBoundingClientRect()
      const r = document.querySelector('.assistant-side')!.getBoundingClientRect()
      const overflowX = (sel: string) => {
        const el = document.querySelector(sel)!
        return el.scrollWidth - el.clientWidth
      }
      return {
        lh: l.height,
        rh: r.height,
        vh: window.innerHeight,
        topH,
        scrollH: document.documentElement.scrollHeight,
        bodyX: overflowX('body'),
        frameX: overflowX('.frame'),
        contentX: overflowX('.content'),
      }
    })

    // grid stretch：两列等高
    expect(Math.abs(m.lh - m.rh)).toBeLessThanOrEqual(2)
    // 填满剩余视口高度（min-height 420 兜底，矮窗口不出纵向滚动反而溢出，故取较大者）
    const expected = Math.max(m.vh - m.topH - 140, 420)
    expect(Math.abs(m.lh - expected)).toBeLessThanOrEqual(6)
    // 页面无纵向滚动
    expect(m.scrollH).toBeLessThanOrEqual(m.vh + 6)
    // 无横向溢出（本轮「耗时列撑破卡片」类问题的回归锁）
    expect(m.bodyX).toBeLessThanOrEqual(1)
    expect(m.frameX).toBeLessThanOrEqual(1)
    expect(m.contentX).toBeLessThanOrEqual(1)
  })
})
