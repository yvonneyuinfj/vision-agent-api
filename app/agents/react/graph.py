"""ReAct 图构建：真自主智能体单场景质检（双轨并存的 agent 轨）。

拓扑（决策 9：手工 ReAct 循环，出口安检一等公民）：

    START → bootstrap_agent → react_agent
    react_agent →|有工具调用| react_tools | 错误 → __fail__ | 纯文本 → nudge
    nudge → react_agent（预算由 loop_count 兜底，超限在 react_agent 收敛错误）
    react_tools →|已提交| exit_gate | 错误 → __fail__ | 否则回 react_agent
    exit_gate →|拒检/不完备| __fail__ | 通过 → grading（复用：规则终裁）
    grading →|review/fail| human_review（复用：HITL 门）| 否则 generate_report
    human_review / generate_report → END；__fail__ → explain_error → END

联合变体（joint=True，决策 10）在自主区多一个分支：

    react_tools →|await_children_requested| await_children
    await_children（interrupt 暂停，子任务全落定后被唤醒）→ react_agent

复用固定图的确定性节点：grading / human_review / generate_report /
explain_error（判级终裁、复核门、报告骨架、失败解释全部运行时强制）。
"""
from __future__ import annotations

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph

from app.agents.graph import route_after_grading
from app.agents.nodes.explain_error import explain_error_node
from app.agents.nodes.generate_report import generate_report_node
from app.agents.nodes.grading import grading_node
from app.agents.nodes.human_review import human_review_node
from app.agents.react.nodes import (
    _build_agent_node,
    _build_tools_node,
    await_children_node,
    bootstrap_agent_node,
    exit_gate_node,
    nudge_node,
)
from app.agents.react.state import ReactState
from app.agents.react.tools import ReactToolbox, build_react_tools
from app.core.logger import get_logger
from app.services.deps import AgentContext

logger = get_logger("app.agents.react.graph")


def _route_error_or(ok_target: str):
    """路由闭包工厂：错误收敛优先返回映射键 "error"，否则返回映射键 "ok"。

    注意：条件边路由函数返回的是 **映射键**（{"error","ok"}），由
    add_conditional_edges 的映射表再解析到目标节点；不能直接返回节点名，
    否则 LangGraph 会拿节点名去查映射表抛 KeyError（ok_target 仅供调用点可读）。
    """

    def _route(state: dict) -> str:
        return "error" if state.get("error") else "ok"

    return _route


def route_after_agent(state: dict) -> str:
    """agent 之后：错误 → __fail__；有工具调用 → tools；纯文本 → nudge。"""
    if state.get("error"):
        return "error"
    messages = state.get("messages") or []
    if messages:
        last = messages[-1]
        if getattr(last, "tool_calls", None):
            return "tools"
    return "nudge"


def route_after_tools(state: dict) -> str:
    """tools 之后：错误 → __fail__；已提交 → 出口安检；否则回 agent 循环。"""
    if state.get("error"):
        return "error"
    if state.get("submitted"):
        return "exit"
    return "agent"


def route_after_tools_joint(state: dict) -> str:
    """联合轨 tools 之后：在单场景键集上增加等待分支（映射键 "await"）。

    await_children_requested 仅由联合工具箱的 await_children 置位，
    单场景图不受影响。
    """
    if state.get("error"):
        return "error"
    if state.get("submitted"):
        return "exit"
    if state.get("await_children_requested"):
        return "await"
    return "agent"


def _make_node(node_fn, ctx: AgentContext):
    async def _wrapper(state, _config=None):
        return await node_fn(state, ctx)
    return _wrapper


def build_react_graph(
    ctx: AgentContext,
    checkpointer: BaseCheckpointSaver | None = None,
    *,
    joint: bool = False,
    toolbox: ReactToolbox | None = None,
):
    """ReAct 质检图（含同一套人工复核门与报告生成）。

    joint=False：单场景轨（标准三工具）。
    joint=True：联合轨——额外注入联合工具箱（由调用方传入，以便装配期
    bind task_service）与 await_children 等待节点（决策 10）。
    """
    if toolbox is None:
        toolbox = build_react_tools(ctx)
    n = lambda fn: _make_node(fn, ctx)  # noqa: E731

    g = StateGraph(ReactState)
    g.add_node("bootstrap_agent", n(bootstrap_agent_node))
    g.add_node("react_agent", _make_node(_build_agent_node(ctx, toolbox), ctx))
    g.add_node("nudge", n(nudge_node))
    g.add_node("react_tools", _make_node(_build_tools_node(ctx, toolbox), ctx))
    g.add_node("exit_gate", n(exit_gate_node))
    # ---- 安检区：复用固定图确定性节点 ----
    g.add_node("grading", n(grading_node))
    g.add_node("human_review", n(human_review_node))
    g.add_node("generate_report", n(generate_report_node))
    g.add_node("__fail__", lambda s, _c=None: {"_": None})
    g.add_node("explain_error", n(explain_error_node))
    g.add_edge("__fail__", "explain_error")
    if joint:
        g.add_node("await_children", n(await_children_node))

    g.add_edge(START, "bootstrap_agent")
    g.add_conditional_edges(
        "bootstrap_agent", _route_error_or("react_agent"), {"error": "__fail__", "ok": "react_agent"}
    )
    g.add_conditional_edges(
        "react_agent",
        route_after_agent,
        {"error": "__fail__", "tools": "react_tools", "nudge": "nudge"},
    )
    g.add_edge("nudge", "react_agent")
    if joint:
        g.add_conditional_edges(
            "react_tools",
            route_after_tools_joint,
            {
                "error": "__fail__",
                "exit": "exit_gate",
                "agent": "react_agent",
                "await": "await_children",
            },
        )
        g.add_edge("await_children", "react_agent")
    else:
        g.add_conditional_edges(
            "react_tools",
            route_after_tools,
            {"error": "__fail__", "exit": "exit_gate", "agent": "react_agent"},
        )
    g.add_conditional_edges(
        "exit_gate", _route_error_or("grading"), {"error": "__fail__", "ok": "grading"}
    )
    # 判级终裁后路由与固定图同表：review/fail 进复核门，其余直出报告
    g.add_conditional_edges(
        "grading",
        route_after_grading,
        {"error": "__fail__", "review": "human_review", "report": "generate_report"},
    )
    g.add_conditional_edges(
        "human_review", _route_error_or("generate_report"), {"error": "__fail__", "ok": "generate_report"}
    )
    g.add_conditional_edges(
        "generate_report", _route_error_or(END), {"error": "__fail__", "ok": END}
    )
    g.add_edge("explain_error", END)

    return g.compile(checkpointer=checkpointer)
