/** 详情页（深度）：待复核任务内嵌复核面板 → 就地提交 → 状态翻转直至完成。 */
import { expect, test } from '@playwright/test'
import { createAbnormalTask, waitForStatus } from '../helpers/api'
import { SAMPLE_IMAGE } from '../helpers/fixtures'

test.describe('任务详情页', () => {
  test('D3：待复核任务内嵌复核 → 提交 → 状态翻转', async ({ page }) => {
    const taskId = await createAbnormalTask(SAMPLE_IMAGE)
    await waitForStatus(taskId, ['awaiting_review'])

    await page.goto(`/tasks/${taskId}`)

    // 第 ③ 区（人工复核结论）内嵌复核面板：awaiting_review + grade 时渲染
    const panel = page.locator('.zone', { has: page.getByText('人工复核结论', { exact: true }) })
    await expect(panel).toBeVisible()

    // 同意 AI 判级 + 必填意见 + 提交
    await panel.getByRole('radio', { name: /同意 AI 判级/ }).click()
    await panel.locator('textarea').fill('E2E：确认 AI 判级无误')
    await panel.getByRole('button', { name: '提交复核 · 继续流水线' }).click()
    // POST /review 同步阻塞到流水线跑完（含报告节点），成功 toast 随后才弹出，给足窗口
    await expect(page.getByText('复核已提交')).toBeVisible({ timeout: 90_000 })

    // 流水线续跑直至完成（报告节点含 LLM，给足窗口）；面板随状态翻转收起
    await waitForStatus(taskId, ['completed'], 90_000)
    await page.reload()
    await expect(
      page.locator('.zone', { has: page.getByRole('radio', { name: /同意 AI 判级/ }) }),
    ).toHaveCount(0)
    await expect(page.getByText('已完成').first()).toBeVisible()
  })
})
