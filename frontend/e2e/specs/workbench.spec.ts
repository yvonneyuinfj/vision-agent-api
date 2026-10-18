/** 待复核队列（决策 4）+ 结果页复核分支（D5 改判 / D6 驳回）。
 *  队列叶只做「选任务 + 跳转」，复核动作统一在 /tasks/{id} 第 ③ 区完成；
 *  「退回重检」后端尚未支持（会置为失败），不在用例内点击。 */
import { expect, test } from '@playwright/test'
import { createAbnormalTask, waitForStatus } from '../helpers/api'
import { SAMPLE_IMAGE } from '../helpers/fixtures'

test.describe('待复核队列与复核分支', () => {
  test('D4：队列叶选中自建任务 → 进入三分区结果页 → 确认提交 → 任务离队完成', async ({ page }) => {
    const taskId = await createAbnormalTask(SAMPLE_IMAGE)
    await waitForStatus(taskId, ['awaiting_review'])

    await page.goto(`/tasks/review?task=${taskId}`)

    // 队列叶：自建任务出现在队列里且为选中态，右侧给出真实预览口径
    const row = page.locator('.queue-row', { hasText: taskId })
    await expect(row).toHaveClass(/on/)
    await expect(page.getByText('待复核队列')).toBeVisible()
    await expect(page.getByText('进入三分区结果页')).toBeVisible()
    // 决策 4：队列叶不复制第二套结果视图，复核动作不在本页
    await expect(page.getByText('同意 AI 判级')).toHaveCount(0)

    // 跳转结果页，在第 ③ 区就地复核
    await page.getByRole('link', { name: '进入三分区结果页' }).click()
    await expect(page).toHaveURL(new RegExp(`/tasks/${taskId}$`))
    const panel = page.locator('.zone', { has: page.getByText('人工复核结论', { exact: true }) })
    await panel.getByRole('radio', { name: /同意 AI 判级/ }).click()
    await panel.locator('textarea').fill('E2E：队列跳转后确认')
    await panel.getByRole('button', { name: '提交复核 · 继续流水线' }).click()
    await expect(page.getByText('复核已提交')).toBeVisible({ timeout: 90_000 })

    await waitForStatus(taskId, ['completed'], 90_000)
    // 任务离队：队列叶不再把它作为选中项
    await page.goto('/tasks/review')
    await expect(page.locator('.queue-row', { hasText: taskId })).toHaveCount(0)
  })

  test('D5：改判分支（严重度 Low → 判级覆盖为 pass）', async ({ page }) => {
    const taskId = await createAbnormalTask(SAMPLE_IMAGE)
    await waitForStatus(taskId, ['awaiting_review'])

    await page.goto(`/tasks/${taskId}`)
    const panel = page.locator('.zone', { has: page.getByText('人工复核结论', { exact: true }) })
    await expect(panel).toBeVisible()

    await panel.getByRole('radio', { name: /改判/ }).click()
    await panel.getByLabel('严重度').selectOption('low')
    await panel.locator('textarea').fill('E2E：改判为低风险')
    await panel.getByRole('button', { name: '提交复核 · 继续流水线' }).click()
    await expect(page.getByText('复核已提交')).toBeVisible({ timeout: 90_000 })

    const detail = await waitForStatus(taskId, ['completed'], 90_000)
    expect(detail.grade?.level).toBe('pass')
  })

  test('D6：驳回分支（任务终止为 failed）', async ({ page }) => {
    const taskId = await createAbnormalTask(SAMPLE_IMAGE)
    await waitForStatus(taskId, ['awaiting_review'])

    await page.goto(`/tasks/${taskId}`)
    const panel = page.locator('.zone', { has: page.getByText('人工复核结论', { exact: true }) })
    await expect(panel).toBeVisible()

    // 动作卡的可访问名以 antd 图标（role=img aria-label="stop"）开头，故不能用 ^驳回 锚定
    await panel.getByRole('radio', { name: /驳回/ }).click()
    await expect(page.getByText('驳回后任务将终止流转')).toBeVisible()
    await panel.locator('textarea').fill('E2E：数据不符合规范')
    await panel.getByRole('button', { name: '提交复核 · 继续流水线' }).click()
    await expect(page.getByText('复核已提交')).toBeVisible({ timeout: 90_000 })

    const detail = await waitForStatus(taskId, ['failed'], 60_000)
    expect(detail.review?.action).toBe('reject')
  })
})
