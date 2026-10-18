/** 工具注册写侧（决策 27/41/43/44/45）：两个写入口、身份列全锁、
 *  启停二次确认的后果文案、保存门禁的两道锁，以及连通性检查不发放回执。
 *  全程不保存、不启停——真注册表是开发资产，用例只验证门禁与文案，不改动它。 */
import { expect, test } from '@playwright/test'
import { BASE_URL } from '../helpers/env'

test.describe('工具注册写侧', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/tool/registry')
    // 本页有两个 .page-title（台账与「版本登记与退役」壳说明），取页头那枚
    await expect(page.locator('.page-head .page-title')).toHaveText('工具注册与版本')
    await expect(page.locator('.ant-table-row').first()).toBeVisible()
  })

  test('W1：页头「添加工具」与行内「编辑」是两个写入口，编辑态身份列全锁', async ({ page }) => {
    await expect(page.getByRole('button', { name: '添加工具' })).toBeVisible()
    // 取 infrared 行：它是当前唯一登记了请求头的行，能顺带验证掩码不回读（决策 43）
    const row = page.locator('.ant-table-row', { hasText: 'infrared' }).first()
    const edit = row.getByRole('button', { name: '编辑' })
    await expect(edit).toBeVisible()

    await edit.click()
    const modal = page.locator('.ant-modal-content').last()
    await expect(modal).toBeVisible()
    // 决策 36：capability_id / tool_name 创建后不可改，编辑态只读灰底 + 明示提示
    await expect(modal.getByText('能力 ID')).toBeVisible()
    await expect(modal.getByText('创建后不可改')).toBeVisible()
    const capInput = modal.locator('input[disabled]').first()
    await expect(capInput).not.toBeEditable()
    // 决策 43：读方向只回「**** + 末 4 位」，掩码以占位形式挂在请求头输入框上（值形如 `X-API-Key: ****-key`），
    // 输入框自身没有明文——明文只写入、不回显。
    const masked = modal.locator('input[placeholder*="****"]').first()
    await expect(masked).toHaveAttribute('placeholder', /\*{4}/)
    await expect(masked).toHaveValue('')
    // 动作条三枚：完整试运行 · 测试连通性 · 保存（决策 44）
    await expect(modal.getByRole('button', { name: /完\s*整\s*试\s*运\s*行/ })).toBeVisible()
    await expect(modal.getByRole('button', { name: /测\s*试\s*连\s*通\s*性/ })).toBeVisible()
    await expect(modal.getByRole('button', { name: /^保\s*存$/ })).toBeVisible()
    await modal.getByRole('button', { name: /关\s*闭|取\s*消/ }).click()
  })

  test('W2：启停为独立二次确认，后果文案写明卸载与不再被探测，未填原因不可提交', async ({ page }) => {
    const toggle = page.locator('.ant-table-row', { hasText: 'borescope' }).first()
      .getByRole('button', { name: /停\s*用|启\s*用/ })
    const enabling = (await toggle.textContent())?.includes('启用')
    await toggle.click()

    const modal = page.locator('.ant-modal-content').last()
    await expect(modal.getByText(/停用能力|启用能力/)).toBeVisible()
    if (enabling) {
      await expect(modal.getByText(/health 探测，未通过则拒绝启用/)).toBeVisible()
    } else {
      // 决策 45：确认文案必须把停用后果说全
      await expect(modal.getByText('tool not registered')).toBeVisible()
      await expect(modal.getByText(/该行健康列显示「未探测」/)).toBeVisible()
    }
    const confirm = modal.locator('button.btn-danger, button.btn-primary').last()
    await expect(confirm).toBeDisabled()
    await modal.getByPlaceholder(/现场检修需暂时下线该工具/).fill('e2e 只验证门禁，不提交')
    await expect(confirm).toBeEnabled()
    // 关键：不点提交，启停留给人工
    await modal.getByRole('button', { name: /取\s*消/ }).click()
    await expect(modal).toBeHidden()
  })

  test('W3：端点变更后保存被门禁锁死，「测试连通性」不发放回执', async ({ page }) => {
    // 取 borescope 行：e2e 期间基座把它绑到 mock（:8002），所以任何别的端点都是"改动"
    await page.locator('.ant-table-row', { hasText: 'borescope' }).first()
      .getByRole('button', { name: '编辑' }).click()
    const modal = page.locator('.ant-modal-content').last()
    const endpoint = modal.getByPlaceholder(/http:\/\/127\.0\.0\.1:\d+/).first()
    // 变更原因先填好：门禁提示按顺序只显式第一条，不填则「变更原因必填」会盖住端点门禁
    await modal.getByPlaceholder(/厂商交付新模型版本/).fill('e2e 只验证门禁，不保存')
    // 端点填被测后端自身的 API 根：连通性档探 `<endpoint> + 健康路径(/health)`＝`$BASE_URL/api/v1/health`，
    // 与被测环境同进程、必然 200（不依赖真工具在不在）；同时它与基座绑定的 mock 端点不同
    // ⇒ 表单脏 ⇒ 保存门禁显式。
    await endpoint.fill(`${BASE_URL}/api/v1`)

    const save = modal.getByRole('button', { name: /^保\s*存$/ })
    await expect(save).toBeDisabled()
    await expect(modal.getByText(/需先「完整试运行」并通过后才能保存/)).toBeVisible()

    await modal.getByRole('button', { name: /测\s*试\s*连\s*通\s*性/ }).click()
    // 决策 41 第二道锁：连通性档只跑 /health，后端明说它不发放保存回执
    await expect(modal.getByText('不产生保存回执')).toBeVisible({ timeout: 30_000 })
    await expect(save).toBeDisabled()

    await modal.getByRole('button', { name: /关\s*闭|取\s*消/ }).click()
  })

  test('W4：台账表不再呈现工具简介，身份列看 tool_name 与 capability_id', async ({ page }) => {
    // 决策 51-⑥：简介属工作台/首页，台账只看身份与「说明」
    const firstRow = page.locator('.ant-table-row').first()
    await expect(firstRow.locator('td').first().locator('.mono').first()).toBeVisible()
    await expect(firstRow).not.toContainText('，找裂纹')
    await expect(page.getByRole('columnheader', { name: /工具$/ })).toBeVisible()
  })
})
