/** 任务列表页（深度）：自建任务可见 + 关键词检索 + 状态服务端筛选（仅断言自建任务）。 */
import { expect, test, type Page } from '@playwright/test'
import { createAbnormalTask, waitForStatus } from '../helpers/api'
import { SAMPLE_IMAGE } from '../helpers/fixtures'

/** antd Select 打开下拉并选项（列表页只有场景 / 状态两个下拉是服务端筛选）。
 *  真正接收点击的是外层 .ant-select-selector：内层 input 被选中项文字遮挡，且贴顶时会被 sticky 顶栏拦住。 */
async function pickOption(page: Page, id: string, label: string) {
  const select = page.locator(`.ant-select:has(#${id})`)
  await select.scrollIntoViewIfNeeded()
  await select.locator('.ant-select-selector').click()
  await page
    .locator('.ant-select-dropdown:visible .ant-select-item-option', { hasText: label })
    .first()
    .click()
}

test.describe('任务列表页', () => {
  test('D2：自建任务可见 + 检索 + 待复核筛选', async ({ page }) => {
    const taskId = await createAbnormalTask(SAMPLE_IMAGE)
    await waitForStatus(taskId, ['awaiting_review'])

    await page.goto('/tasks')

    // 关键词检索（仅当前 100 条窗口内本地匹配）：任务号命中唯一行
    const search = page.getByRole('main').getByPlaceholder('任务 ID / 场景 / 能力（仅本窗口）')
    await search.fill(taskId)
    const row = page.locator('tr', { has: page.locator('td.task-id', { hasText: taskId }) })
    await expect(row).toBeVisible()
    await expect(row.getByText('待复核')).toBeVisible()
    await expect(row.getByRole('link', { name: '去复核' })).toBeVisible()

    // 状态筛选：走服务端 status 参数，选中自建任务仍在结果里
    await search.fill('')
    await pickOption(page, 'lp-status', '待复核')
    await expect(page.locator('td.task-id', { hasText: taskId })).toBeVisible()

    // 取消口径常驻：无取消接口就不伪造 CANCELLED
    await expect(page.getByRole('button', { name: '取消' })).toBeDisabled()
    // 新建任务与三个筛选控件同排、同高（用户反馈 5）
    const bar = page.locator('.filter-bar')
    const btn = bar.getByRole('link', { name: '新建任务' })
    await expect(btn).toBeVisible()
    const geo = await bar.evaluate((el) => {
      const b = el.querySelector('.filter-bar .btn')!.getBoundingClientRect()
      const s = el.querySelector('.ant-select')!.getBoundingClientRect()
      const i = el.querySelector('.filter-bar .input')!.getBoundingClientRect()
      return { btn: b, sel: s, inp: i }
    })
    expect(Math.round(geo.btn.height)).toBe(Math.round(geo.sel.height))
    expect(Math.round(geo.inp.height)).toBe(Math.round(geo.sel.height))
    // 同一水平行：按钮底边与控件底边对齐
    expect(Math.abs(geo.btn.bottom - geo.sel.bottom)).toBeLessThanOrEqual(2)
    expect(Math.abs(geo.btn.bottom - geo.inp.bottom)).toBeLessThanOrEqual(2)
    // 窗口口径既不常驻筛选行、也不由分页条承载（决策 51-⑪ 取代决策 26 的"分页条常驻"半句）
    await expect(bar.locator('.hint')).toHaveCount(0)
    await expect(page.locator('.pager')).toContainText('共')
    await expect(page.locator('.pager')).not.toContainText('最近 100 条')
  })
})
