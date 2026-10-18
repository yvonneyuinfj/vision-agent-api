/** 写侧表单/启停的错误文案归一：后端业务错误带中文 message（HTTP 400/409），
 *  axios 层错误只有 status，需兜底，否则用户只看到"网络请求失败"。 */
export function writeErrorMessage(error: unknown): string | null {
  const e = error as {
    code?: number
    message?: string
    isAxiosError?: boolean
    response?: { status?: number; data?: { message?: string } }
  }
  const bizMessage = e?.response?.data?.message
  if (bizMessage) return bizMessage
  if (typeof e?.message === 'string' && e.message.trim()) return e.message
  if (e?.isAxiosError) return `请求失败（HTTP ${e.response?.status ?? '无响应'}）`
  return null
}

/** 停用/维护中的确认文案（决策 45 要求把卸载后果写进二次确认） */
export const DISABLE_WARNING =
  '停用即从 ToolRegistry 卸载（assemble.py 只挂 enabled，_on_refresh 整体重建）：在途任务下一次调用返回 tool not registered；' +
  '停用后 health_all() 只探 enabled，该行健康列显示「未探测」。'

export const ENABLE_WARNING =
  '启用＝该行立即进入场景路由并参与健康巡检。后端会先跑一次 health 探测，未通过则拒绝启用（不落库）。'
