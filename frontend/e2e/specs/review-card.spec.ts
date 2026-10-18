/** 对话内终裁复核卡（新功能核心，深度全链）：
 *  API 建会话 + abnormal 任务 + DB 注入 task_card 模拟会话归属
 *  → 落定回注弹卡 → 卡片元素断言 → 就地复核提交 → 卡片落定 → 任务 completed 闭环。
 *  弹卡发生在落定回注（不依赖 LLM），仅助手对话文案轨涉及 LLM，不影响断言。 */
import { expect, test } from '@playwright/test'
import { createAbnormalTask, createSession, waitForMessage, waitForStatus } from '../helpers/api'
import { attachTaskToSession } from '../helpers/db'
import { SAMPLE_IMAGE } from '../helpers/fixtures'

test.describe('质检助手 · 终裁复核卡', () => {
  test('D9：注入归属 → 弹卡 → 就地提交 → 落定 → 任务完成', async ({ page }) => {
    // ---- 确定性建数：会话 + 复核门任务 + 会话归属消息 ----
    const sessionTitle = `E2E 复核卡 ${Date.now()}`
    const sessionId = await createSession(sessionTitle)
    const taskId = await createAbnormalTask(SAMPLE_IMAGE)
    await attachTaskToSession(sessionId, taskId)

    await waitForStatus(taskId, ['awaiting_review'])
    // 落定回注写出复核卡消息后才进 UI
    await waitForMessage(sessionId, 'review_card')

    // ---- UI：打开助手页选中该会话，复核卡弹出（决策 11：/assistant 重定向到 /detect/assistant）----
    await page.goto('/detect/assistant')
    await page.locator('.session-item', { hasText: sessionTitle }).click()

    const card = page.locator('.bubble.review-card', { hasText: taskId })
    await expect(card).toBeVisible({ timeout: 20_000 })
    await expect(card.locator('.rc-badge')).toHaveText('终裁复核')
    await expect(card.locator('.rc-why')).toContainText('任务已暂停并进入人工复核门')
    // 四张动作卡 + 意见输入 + 工作台入口
    await expect(card.getByText('同意 AI 判级', { exact: true })).toBeVisible()
    await expect(card.getByText('改判 / 修正', { exact: true })).toBeVisible()
    await expect(card.getByText('退回重检', { exact: true }).first()).toBeVisible()
    await expect(card.getByText('驳回', { exact: true })).toBeVisible()
    await expect(card.locator('textarea[placeholder="复核意见（必填）"]')).toBeVisible()
    await expect(card.getByRole('button', { name: '打开复核工作台 →' })).toBeVisible()

    // ---- 就地复核：同意 + 必填意见 + 提交 ----
    await card.getByRole('radio', { name: /同意 AI 判级/ }).click()
    await card.locator('textarea').fill('E2E：会话内终裁复核确认')
    await card.getByRole('button', { name: '提交复核 · 继续流水线' }).click()

    // 卡片本地即时落定；任务快照随后翻转（SSE 摘要比对自动重推）
    await expect(card.getByText('已提交终裁复核，流水线继续生成报告。')).toBeVisible({ timeout: 90_000 })

    // ---- 闭环：流水线续跑至完成 ----
    await waitForStatus(taskId, ['completed'], 90_000)
  })
})
