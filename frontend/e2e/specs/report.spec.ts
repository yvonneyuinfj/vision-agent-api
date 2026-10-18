/** 报告页（深度）：normal 任务直通完成 → 报告正文渲染 + 导出入口。 */
import { expect, test } from '@playwright/test'
import { createNormalTask, waitForStatus } from '../helpers/api'
import { SAMPLE_IMAGE } from '../helpers/fixtures'

test.describe('质检报告页', () => {
  test('D8：报告页渲染 + 下载入口', async ({ page }) => {
    const taskId = await createNormalTask(SAMPLE_IMAGE)
    // 直通流水线（含报告节点；LLM 分析失败自动降级，不阻断出报告）
    await waitForStatus(taskId, ['completed'], 90_000)

    await page.goto(`/tasks/${taskId}/report`)

    await expect(page.locator('h1.paper-title')).toHaveText('智能质检报告')
    await expect(page.getByRole('link', { name: '下载 .md' })).toBeVisible()
    await expect(page.getByRole('button', { name: '打印 / 存为 PDF' })).toBeVisible()
  })
})
