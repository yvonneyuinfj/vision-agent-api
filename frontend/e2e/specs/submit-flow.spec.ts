/** 提交链路（深度）：D1 UI 真实提交 → 任务号弹窗 → 列表可见；D7 表单校验拦截。 */
import { expect, test } from '@playwright/test'
import { SAMPLE_IMAGE } from '../helpers/fixtures'

test.describe('提交页', () => {
  test('D1：UI 提交（场景 + 图像）→ 任务号弹窗 → 任务列表可见', async ({ page }) => {
    await page.goto('/tasks/new')

    // 固定流水线（默认）+ 孔探场景
    await page.locator('.scene-card[data-code="borescope"]').click()
    await expect(page.locator('.scene-card[data-code="borescope"]')).toHaveClass(/selected/)

    // 上传样本图（Dropzone 内的文件输入）
    await page.locator('input[type="file"]').first().setInputFiles(SAMPLE_IMAGE)
    await expect(page.getByText('class1_1.jpg').first()).toBeVisible()

    // 提交 → 成功弹窗展示任务号
    await page.getByRole('button', { name: '提交检测任务' }).click()
    const modal = page.locator('.modal-mask.open')
    await expect(modal.getByText('任务已提交，正在排队推理')).toBeVisible()
    const taskId = ((await modal.locator('b.mono').textContent()) ?? '').trim()
    expect(taskId).toMatch(/^T\d+$/)

    // 前往任务列表 → 自建任务行可见
    await modal.getByRole('button', { name: '前往任务列表' }).click()
    await page.waitForURL('**/tasks')
    await expect(page.locator('td.task-id', { hasText: taskId })).toBeVisible()
  })

  test('D7：表单校验拦截（缺场景缺图像不可提交）', async ({ page }) => {
    await page.goto('/tasks/new')

    await page.getByRole('button', { name: '提交检测任务' }).click()

    // antd message.warning 拼接校验错误；成功弹窗不得出现
    await expect(page.getByText('请上传检测图像或视频')).toBeVisible()
    await expect(page.getByText('请选择检测场景')).toBeVisible()
    await expect(page.locator('.modal-mask.open')).toHaveCount(0)
  })
})
