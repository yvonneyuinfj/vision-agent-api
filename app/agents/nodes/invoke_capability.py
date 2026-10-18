"""节点：算法调用（经 ToolRegistry 动态解析工具）。

规则：节点不直接依赖任何具体算法/适配器 —— 从 ctx.tool_registry 按
capability.tool_name（detect/<tool_name>）解析并执行 InferTool；
新增算法（红外/断口/组织）只需扩充 manifest + 装配工具，本节点零改动。
"""
from __future__ import annotations

from typing import Any

from app.agents.nodes.node_utils import error_state, node
from app.core.errors import BizCode
from app.services.deps import AgentContext


@node("invoke_capability")
async def invoke_capability_node(state: dict[str, Any], ctx: AgentContext) -> dict[str, Any]:
    capability = state.get("selected_capability")
    if capability is None:
        return error_state("invoke_capability", BizCode.SCENARIO_NOT_SUPPORTED, "no capability to invoke")

    tool_registry = ctx.tool_registry
    if tool_registry is None:
        return error_state("invoke_capability", BizCode.INTERNAL_ERROR, "tool registry unavailable")

    tool_name = f"detect/{capability.get('tool_name')}"
    tool = tool_registry.get(tool_name)
    if tool is None:
        return error_state("invoke_capability", BizCode.SCENARIO_NOT_SUPPORTED, f"tool not registered: {tool_name}")

    file_repo = ctx.file_repo
    if file_repo is None:
        return error_state("invoke_capability", BizCode.INTERNAL_ERROR, "file repo unavailable")

    # URI -> 本地路径（严格限制在 data 根内，见 file_repo.uri_to_path）
    try:
        image_paths = [str(file_repo.uri_to_path(uri)) for uri in state.get("input_uris", [])]
        auxiliary_paths = {
            name: str(file_repo.uri_to_path(uri)) for name, uri in state.get("auxiliary", {}).items()
        }
    except ValueError as exc:
        return error_state("invoke_capability", BizCode.INVALID_REQUEST, f"invalid input uri: {exc}")

    result = await tool.execute(
        {
            "task_id": state["task_id"],
            "trace_id": state.get("trace_id", ""),
            "image_paths": image_paths,
            "auxiliary_paths": auxiliary_paths,
            "parameters": state.get("parameters") or {},
            "file_repo": file_repo,
        }
    )
    if not result.ok:
        return error_state("invoke_capability", BizCode(result.code), result.message)
    return result.result or {}