/** 结果与报告黄金路径（批次2-④ 换接口的回归锁）：
 *  复核门任务 → 队列叶选中 → 三分区结果页复核 → 报告页走 GET /reports/{task_id}。 */
import { expect, test } from '@playwright/test'
import { createAbnormalTask, waitForStatus } from '../helpers/api'
import { SAMPLE_IMAGE } from '../helpers/fixtures'

test.describe('复核 → 报告 黄金路径', () => {
  test('R1：队列 → 结果页三分区 → 提交复核 → 报告页取真实报告记录', async ({ page }) => {
    const taskId = await createAbnormalTask(SAMPLE_IMAGE)
    await waitForStatus(taskId, ['awaiting_review'])

    // 队列叶：只选人，不复制结果视图（决策 4）
    await page.goto(`/tasks/review?task=${taskId}`)
    const row = page.locator('.queue-row', { hasText: taskId })
    await expect(row).toHaveClass(/on/)
    await page.getByRole('link', { name: '进入三分区结果页' }).click()
    await expect(page).toHaveURL(new RegExp(`/tasks/${taskId}$`))

    // 三分区齐备：模型输出 / AI 解释 / 人工复核结论（决策 5）
    const zones = page.locator('.zone')
    await expect(zones).toHaveCount(3)
    await expect(zones.nth(0).locator('.zone-h')).toContainText('模型输出（结构化结果）')
    await expect(zones.nth(1).locator('.zone-h')).toContainText('AI 解释与依据')
    // 缺陷图层图例：三类严重度 + 人工新增（与标注画布同一配色口径）
    await expect(page.locator('.legend-sw')).toHaveCount(4)

    const panel = zones.nth(2)
    await panel.getByRole('radio', { name: /同意 AI 判级/ }).click()
    await panel.locator('textarea').fill('E2E：黄金路径确认')
    await panel.getByRole('button', { name: '提交复核 · 继续流水线' }).click()
    await expect(page.getByText('复核已提交')).toBeVisible({ timeout: 90_000 })

    await waitForStatus(taskId, ['completed'], 90_000)

    // 报告页：正文来自 GET /reports/{task_id}，无记录时明确声明降级而非伪造
    await page.goto(`/tasks/${taskId}/report`)
    await expect(page.locator('h1.paper-title')).toHaveText('智能质检报告')
    await expect(page.getByText('报告正文未生成')).toHaveCount(0)
    await expect(page.getByRole('button', { name: '打印 / 存为 PDF' })).toBeVisible()
  })
})
