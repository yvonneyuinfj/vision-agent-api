"""会话轨状态：消息流 + 循环预算。"""
from __future__ import annotations

from typing import Annotated, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages


class SessionState(TypedDict, total=False):
    """会话线程状态（thread_id = session_id，跨回合由 checkpointer 持久化）。"""

    messages: Annotated[list[AnyMessage], add_messages]
    session_id: str
    loop_count: int  # 当前回合内的工具循环计数（预算见 settings.session_max_turns）
    # 本回合经直播帧（总线 publish）逐字推过的最终文本回复消息 id：
    # 落库复用同 id，SSE 落库路径据此跳过重推。每回合入图时重置为空串。
    streamed_reply_id: str
