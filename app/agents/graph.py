"""LangGraph 状态图构建：单场景质检流程（对齐 a.md §9）。

单场景：select_capability →（歧义且开关开启时经 scene_confirm HITL 确认）→
    resolve_capability → validate → invoke_capability → normalize_result
    → rag_retrieval → grading；判级后路由：review/fail 人工复核门（HITL）
    → generate_report；pass/defect 直接 generate_report。

节点内以 state['error'] 收敛失败，保持图结构稳定。HITL 幂等性约束：
interrupt 节点（scene_confirm / human_review）内不做任何昂贵或非幂等副作用，
候选/判级数据一律由上游节点一次性写入 state，resume 重跑仅重放 interrupt 本身。
"""
from __future__ import annotations

import hashlib
import json
from app.core.logger import get_logger
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from app.agents.nodes.explain_error import explain_error_node
from app.agents.nodes.explain_metrics import explain_metrics_node
from app.agents.nodes.generate_report import generate_report_node
from app.agents.nodes.grading import grading_node
from app.agents.nodes.human_review import human_review_node
from app.agents.nodes.invoke_capability import invoke_capability_node
from app.agents.nodes.normalize_result import normalize_result_node
from app.agents.nodes.rag_retrieval import rag_retrieval_node
from app.agents.nodes.resolve_capability import resolve_capability_node
from app.agents.nodes.scene_confirm import scene_confirm_node
from app.agents.nodes.select_capability import select_capability_node
from app.agents.nodes.validate import validate_node
from app.agents.state import AgentState
from app.services.deps import AgentContext

logger = get_logger("app.agents.graph")

# 判级结论需人工复核的级别
_REVIEWABLE = {"review", "fail"}


def route_after_grading(state: AgentState) -> str:
    """判级后路由：错误收敛优先；结论无需复核 -> 自动报告；需复核 -> 人工复核门。

    路由表 {error: __fail__, review: human_review, report: generate_report}：
    state['error'] 存在（判级节点失败）→ "error"；
    level ∈ {review, fail} → "review"；否则（pass/defect）→ "report"。
    """
    if state.get("error"):
        return "error"
    grade = state.get("grade") or {}
    level = grade.get("level", "pass")
    return "review" if level in _REVIEWABLE else "report"


def _route_error_or_next(state: AgentState) -> str:
    """任一步骤发生业务/内部错误 -> 收敛到 __fail__（保留上下文供 report 阶段可写 error）。"""
    return "error" if state.get("error") else "next"


def route_after_select(state: AgentState) -> str:
    """能力选择后路由：错误 -> __fail__；识别出场景 -> 直接命中能力；
    多候选歧义（scene 未定且候选 >= 2）-> 人工场景确认门（HITL）。"""
    if state.get("error"):
        return "error"
    if state.get("scene"):
        return "resolve"
    if len(state.get("candidate_scenes") or []) > 1:
        return "confirm"
    # 识别无结论且候选不足（开关关闭时 select 已收敛 error，此处为防御兜底）
    return "error"


def _make_node(node_fn, ctx: AgentContext):
    """适配 LangGraph 节点签名 (state[, config])：注入 AgentContext 闭包。

    节点均为 async：wrapper 保持 async 返回 await 结果，LangGraph 按 async 节点执行。
    """
    async def _wrapper(state, _config=None):
        return await node_fn(state, ctx)
    return _wrapper


def build_single_graph(
    ctx: AgentContext,
    checkpointer: BaseCheckpointSaver | None = None,
):
    """单场景质检图（含人工复核门）。"""
    n = lambda fn: _make_node(fn, ctx)  # noqa: E731 - 图内节点注册速记
    g = StateGraph(AgentState)
    g.add_node("select_capability", n(select_capability_node))
    g.add_node("scene_confirm", n(scene_confirm_node))
    g.add_node("resolve_capability", n(resolve_capability_node))
    g.add_node("validate", n(validate_node))
    g.add_node("invoke_capability", n(invoke_capability_node))
    g.add_node("normalize_result", n(normalize_result_node))
    g.add_node("rag_retrieval", n(rag_retrieval_node))
    g.add_node("grading", n(grading_node))
    g.add_node("explain_metrics", n(explain_metrics_node))
    g.add_node("human_review", n(human_review_node))
    g.add_node("generate_report", n(generate_report_node))
    g.add_node("__fail__", lambda s, _c=None: {"_": None})
    g.add_node("explain_error", n(explain_error_node))
    # 无条件边：explain_error 内部失败也不回路由（防 __fail__↔explain_error 死循环）
    g.add_edge("__fail__", "explain_error")

    # 能力选择（合并原 intent + route：场景识别 + 候选收窄一体）；
    # 歧义多候选 -> scene_confirm 人工确认门（HITL），确认后回能力命中
    g.add_edge(START, "select_capability")
    g.add_conditional_edges(
        "select_capability",
        route_after_select,
        {"error": "__fail__", "resolve": "resolve_capability", "confirm": "scene_confirm"},
    )
    g.add_conditional_edges(
        "scene_confirm",
        _route_error_or_next,
        {"error": "__fail__", "next": "resolve_capability"},
    )
    g.add_conditional_edges(
        "resolve_capability",
        _route_error_or_next,
        {"error": "__fail__", "next": "validate"},
    )
    # 按能力 input_required 校验输入 -> 执行
    g.add_conditional_edges(
        "validate",
        _route_error_or_next,
        {"error": "__fail__", "next": "invoke_capability"},
    )
    g.add_conditional_edges(
        "invoke_capability",
        _route_error_or_next,
        {"error": "__fail__", "next": "normalize_result"},
    )
    g.add_conditional_edges(
        "normalize_result",
        _route_error_or_next,
        {"error": "__fail__", "next": "rag_retrieval"},
    )
    g.add_conditional_edges(
        "rag_retrieval",
        _route_error_or_next,
        {"error": "__fail__", "next": "grading"},
    )
    g.add_conditional_edges(
        "grading",
        route_after_grading,
        {"error": "__fail__", "review": "human_review", "report": "explain_metrics"},
    )
    g.add_conditional_edges(
        "explain_metrics",
        _route_error_or_next,
        {"error": "__fail__", "next": "generate_report"},
    )
    g.add_conditional_edges(
        "human_review",
        _route_error_or_next,
        {"error": "__fail__", "next": "generate_report"},
    )
    g.add_conditional_edges(
        "generate_report",
        _route_error_or_next,
        {"error": "__fail__", "next": END},
    )
    g.add_edge("explain_error", END)

    return g.compile(checkpointer=checkpointer)


def solve_review_command(
    action: str,
    opinion: str,
    override_grade: dict[str, Any] | None,
    defects: list[dict[str, Any]] | None = None,
) -> Command:
    """构造人工复核恢复指令：Command(resume=...) 恢复被 interrupt 暂停的图。

    defects 为 revise 携带的全量缺陷修订列表（含 source/human_rejected/revised
    溯源标记）；human_review 节点整表替换 state["standardized"] 并留 diff 痕。
    """
    resume: dict[str, Any] = {"action": action, "opinion": opinion or ""}
    if override_grade:
        resume["override_grade"] = override_grade
    if defects is not None:
        resume["defects"] = defects
    return Command(resume=resume)


def solve_scene_confirm_command(scene: str, task_id: str | None = None) -> Command:
    """构造场景人工确认恢复指令：Command(resume={"confirm_type": "scene", ...})。"""
    resume: dict[str, Any] = {"confirm_type": "scene", "scene": scene}
    if task_id:
        resume["task_id"] = task_id
    return Command(resume=resume)


def graph_fingerprint(ctx: AgentContext) -> str:
    """程序结构 + 能力清单签名，用于断点续跑判重（需求 §5.1 状态持久化与恢复）。

    节点名从图 builder 动态派生（而非硬编码清单），保证图结构演进时
    指纹如实反映真实拓扑。
    """
    probe = build_single_graph(ctx)
    node_names = sorted(name for name in probe.builder.nodes if name != "__start__")
    structure: dict[str, Any] = {"nodes": node_names}
    if ctx.registry is not None:
        structure["capabilities"] = [
            {"tool": m.tool_name, "ver": m.tool_version, "model": m.model_version}
            for m in ctx.registry.list()
        ]
    return hashlib.sha1(json.dumps(structure, sort_keys=True).encode()).hexdigest()[:12]


# 联合质检（需求 §2.3 多场景联合：按业务规则逐级下钻，M2 起实现完整汇总）
def build_joint_graph(
    ctx: AgentContext,
    checkpointer: BaseCheckpointSaver | None = None,
):
    """多场景联合质检图。

    骨架阶段：以单场景图为原子单元，由 task_service 按 config/rules/joint.yaml 的
    业务规则（调用顺序 + 下钻条件）循环调度子图；上一场景的输出作为下一场景的
    auxiliary 输入（下钻），各场景结果写入 state['joint_results'] 后统一汇总报告。

    完整实现（嵌套 compile 子图 + 部件级结论聚合）在 M2-multi-scene 落地，
    本函数当前退化为单场景图，保证接口形态与调用方对齐。
    """
    return build_single_graph(ctx, checkpointer)