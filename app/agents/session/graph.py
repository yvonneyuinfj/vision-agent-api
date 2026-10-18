"""会话图装配：session_agent ⇄ session_tools，文本回复即收尾。"""
from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage
from langgraph.graph import END, StateGraph

from app.agents.session.nodes import build_session_agent_node, build_session_tools_node
from app.agents.session.state import SessionState
from app.agents.session.tools import build_session_lc_tools, build_session_tool_impls
from app.services.deps import AgentContext


def _route_after_agent(state: dict[str, Any]) -> str:
    """路由函数返回映射键（契约：不返回目标节点名）。"""
    messages = state.get("messages") or []
    last = messages[-1] if messages else None
    if isinstance(last, AIMessage) and last.tool_calls:
        return "tools"
    return "end"


def build_session_graph(
    ctx: AgentContext,
    session_repo: Any,  # SessionRepo
    task_service: Any,  # TaskService
    checkpointer: Any | None = None,
    live: Any = None,  # SessionEventBus：模型逐 token 直播帧通道（首字延迟=模型TTFB）
):
    lc_tools = build_session_lc_tools()
    impls = build_session_tool_impls(ctx, session_repo, task_service)

    g = StateGraph(SessionState)
    # repo 透传给节点：会话智能体自身的思考/工具调用也落活动流（优化轮反馈修复）
    g.add_node("session_agent", build_session_agent_node(ctx, lc_tools, repo=session_repo, live=live))
    g.add_node("session_tools", build_session_tools_node(impls, repo=session_repo))
    g.set_entry_point("session_agent")
    g.add_conditional_edges(
        "session_agent", _route_after_agent, {"tools": "session_tools", "end": END}
    )
    g.add_edge("session_tools", "session_agent")
    return g.compile(checkpointer=checkpointer)
