"""节点：能力命中（按已确定场景查询能力注册中心）。

从原 select_capability 节点拆出：识别（select_capability）与可能的
人工确认（scene_confirm）都只负责产出 state['scene']，本节点在 scene
确定后统一完成 capability 命中，命中即写入 selected_capability；
无可用能力 → 错误收敛。对「识别直连」与「确认续跑」两条路径同形复用。
"""
from __future__ import annotations

from typing import Any

from app.agents.nodes.node_utils import error_state, node
from app.core.errors import BizCode
from app.services.deps import AgentContext


@node("resolve_capability")
async def resolve_capability_node(state: dict[str, Any], ctx: AgentContext) -> dict[str, Any]:
    registry = ctx.registry
    if registry is None:
        return error_state("resolve_capability", BizCode.INTERNAL_ERROR, "registry unavailable")

    scene = state.get("scene")
    if not scene:
        return error_state("resolve_capability", BizCode.SCENARIO_NOT_SUPPORTED, "无法识别检测场景")

    capability = registry.lookup(scene)
    if capability is None:
        return error_state("resolve_capability", BizCode.SCENARIO_NOT_SUPPORTED, f"场景 {scene} 无可用能力")

    return {"selected_capability": capability.model_dump()}
