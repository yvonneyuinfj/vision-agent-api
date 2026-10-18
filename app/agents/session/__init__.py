"""质检助手会话轨（session track）。

会话智能体：帮助用户完成送检引导、场景确认、任务派发与结果回报。
与任务轨 ReAct（app.agents.react）同构但更轻：

    START → session_agent ⇄ session_tools →（文本回复）→ END

- 判级/复核等硬约束在任务轨内执行；会话轨只负责对话编排。
- 任务落定事件由 task_service.on_settled 回注：以「任务事件」消息
  唤醒会话线程，智能体主动向用户回报。
- 会话线程持久化复用同一 checkpointer（thread_id = session_id，S 前缀
  与任务线程 T 前缀天然区分）。
"""
