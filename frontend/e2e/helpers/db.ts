/** DB 注入助手：模拟「任务由助手会话派发」的归属消息（task_card）。
 *
 *  对话内终裁复核卡的弹出依赖会话归属（find_session_by_task 按 task_id 定位
 *  会话），真实链路里归属消息由 launch_inspection 落库；E2E 不依赖
 *  VLM/LLM，直接注入一条同构的 task_card 消息，落定回注即可确定性弹卡。
 *  仅测试用途，生产代码无此路径。 */
import { randomBytes } from 'node:crypto'
import mysql from 'mysql2/promise'
import { dbConfig } from './env'

/** 与后端 new_message_id() 同构：M + 18 位 hex */
function newMessageId(): string {
  return `M${randomBytes(9).toString('hex')}`
}

/** 会话消息落库时间：UTC（与后端 utcnow 一致），DATETIME(6) 微秒精度 */
function utcNow6(): string {
  return `${new Date().toISOString().replace('T', ' ').replace('Z', '')}000`
}

export async function attachTaskToSession(sessionId: string, taskId: string): Promise<void> {
  const cfg = dbConfig()
  const conn = await mysql.createConnection({
    host: cfg.host,
    port: cfg.port,
    user: cfg.user,
    password: cfg.password,
    database: cfg.database,
  })
  try {
    await conn.execute(
      `INSERT INTO session_messages (id, session_id, role, kind, content, images_json, task_id, created_at)
       VALUES (?, ?, 'assistant', 'task_card', ?, NULL, ?, ?)`,
      [newMessageId(), sessionId, `已派发质检任务 ${taskId}（E2E 注入）`, taskId, utcNow6()],
    )
    // 会话活跃时间顶到最新，保证会话列表按更新时间倒序时排在最上（UI 定位更稳）
    await conn.execute('UPDATE agent_sessions SET updated_at = ? WHERE id = ?', [utcNow6(), sessionId])
  } finally {
    await conn.end()
  }
}
