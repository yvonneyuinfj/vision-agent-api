"""质检助手会话领域模型（会话轨：会话 + 消息流）。"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

# 会话状态
SESSION_ACTIVE = "active"
SESSION_ARCHIVED = "archived"

# 消息角色
ROLE_USER = "user"
ROLE_ASSISTANT = "assistant"
ROLE_EVENT = "event"  # 任务落定等系统事件（唤醒会话智能体，不直接展示原文）

# 消息形态
KIND_TEXT = "text"
KIND_IMAGE = "image"          # 带图消息（送检图像）
KIND_TASK_CARD = "task_card"  # 任务卡片（launch_inspection 派生的质检任务）
KIND_GUIDANCE = "guidance"    # 场景引导（识别失败/场景外的引导分支）
KIND_EVENT = "event"          # 事件消息（任务落定回报等）
KIND_ACTIVITY = "activity"    # 任务执行活动流（append_step 旁路，优化轮 D2；content 为 JSON {node,message}）——共识轮后不再渲染，仅历史数据
KIND_CHAIN = "chain"          # ReAct 思考链行（共识轮；content 为 JSON {type: thought|tool_call, ...}；tool_call 行由 tool_result 事件原地更新）
KIND_ROUTING = "routing"      # 场景路由轻量单行（共识轮：系统输出不套折叠行）
KIND_SCENE_CARD = "scene_card"  # 场景确认交互卡片（优化轮 D4；content 为 JSON {mode,candidates,reasoning,image}）
KIND_REVIEW_CARD = "review_card"  # 终裁复核交互卡片：任务暂停（awaiting_review）时弹出，对话内就地复核（四动作+意见）；content 为 JSON 快照 {grade_level,grade_summary,defect_count,defects}
# 流式协议（对齐 design-demos/思维链_玻璃疏行演绎.html）：卡片只推不改，
# 完成 / 报告就绪各落一张新卡，绝不在原卡上改写；content 为落卡时刻的状态快照。
KIND_DONE_CARD = "done_card"      # 完成卡：结论 + 报告状态（generating/ready）；content 为 JSON 快照
KIND_REPORT_CARD = "report_card"  # 报告就绪卡：报告全文入口（点击展开）；content 为 JSON 快照 {report_uri,analysis_lead,...}
KIND_DOC_CARD = "doc_card"        # 文档生成卡：会话内 generate_ppt 产出的 .pptx 下载入口；content 为 JSON 快照 {task_id,status,filename,artifact_uri,slide_count,engine}


class SessionMessageView(BaseModel):
    """消息视图（API 返回）。"""

    message_id: str
    session_id: str
    role: str
    kind: str = KIND_TEXT
    content: str = ""
    images: list[dict[str, Any]] = Field(default_factory=list)
    task_id: str | None = None
    task: dict[str, Any] | None = None  # 任务卡片快照（状态/判级等，服务端填充）
    created_at: str | None = None


class SessionView(BaseModel):
    """会话视图（列表/详情）。"""

    session_id: str
    title: str | None = None
    status: str = SESSION_ACTIVE
    created_at: str | None = None
    updated_at: str | None = None
    last_message: str | None = None  # 列表页摘要：最后一条消息截断
    messages: list[SessionMessageView] = Field(default_factory=list)  # 仅详情返回
