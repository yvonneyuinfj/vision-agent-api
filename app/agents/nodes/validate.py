"""节点：输入校验（需求 §2.2 第二步 与 §3.2 接入约束）。

红外需辐射值 CSV、X 射线需参考图与标定信息——不符合即 INVALID_REQUEST 快速失败。
"""
from __future__ import annotations

from typing import Any

from app.agents.nodes.node_utils import error_state, node
from app.core.errors import BizCode
from app.services.deps import AgentContext


@node("validate")
async def validate_node(state: dict[str, Any], ctx: AgentContext) -> dict[str, Any]:
    capability = state.get("selected_capability")
    if capability is None:
        return error_state("validate", BizCode.SCENARIO_NOT_SUPPORTED, "no capability routed")

    input_required: dict[str, Any] = capability.get("input_required", {})
    if state.get("mode") == "joint":
        # 联合质检按场景在子任务节点校验，这里只保证主输入存在；
        # input_required 的逐项校验由 M2 联合质检下沉到各子任务节点完成。
        pass

    if not state.get("input_uris"):
        return error_state("validate", BizCode.INVALID_REQUEST, "no image uploaded")

    # 必填配套输入（如 radiance_csv / reference_image）
    for key, required in input_required.items():
        if key in ("images", "image_field"):
            continue
        if required is True and key not in state.get("auxiliary", {}):
            return error_state(
                "validate",
                BizCode.INVALID_REQUEST,
                f"missing required auxiliary input: {key}",
            )

    return {}