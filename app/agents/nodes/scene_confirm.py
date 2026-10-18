"""节点：场景人工确认门（Human-in-the-loop，LangGraph interrupt）。

仅当「识别无结论 + scene_confirm_enabled 开关开启 + 候选 >= 2」时由图路由进入：
暂停图执行等待人工确认候选场景，前端通过 POST /api/v1/tasks/{id}/scene-confirm
提交选择，由 task_service 以 Command(resume=...) 恢复执行。

幂等性设计（HITL 关键约束：resume 时节点从头重跑）：
- 候选列表从 state['candidate_scenes'] 读取（select_capability 一次性计算写入），
  本节点不做图片读取 / VLM 识别等任何昂贵且非幂等的副作用；
- interrupt() 是节点内第一个动作，resume 重跑时仅重放 interrupt 本身，
  候选稳定来自 checkpoint，杜绝"展示给用户的候选 ≠ 恢复后校验的候选"。
"""
from __future__ import annotations

from typing import Any

from langgraph.types import interrupt

from app.agents.nodes.node_utils import error_state, node
from app.core.errors import BizCode
from app.services.deps import AgentContext


@node("scene_confirm")
async def scene_confirm_node(state: dict[str, Any], ctx: AgentContext) -> dict[str, Any]:
    candidates = state.get("candidate_scenes") or []
    if len(candidates) < 2:
        # 防御：路由保证只在多候选时进入，此处兜底直接收敛
        return error_state("scene_confirm", BizCode.SCENARIO_NOT_SUPPORTED, "无法识别检测场景")

    chosen = interrupt({
        "confirm_type": "scene",
        "task_id": state.get("task_id"),
        "candidates": candidates,
        "question": "检测场景存在歧义，请人工确认",
    })
    if not (
        isinstance(chosen, dict)
        and chosen.get("confirm_type") == "scene"
        and chosen.get("scene") in {c.get("scene") for c in candidates}
    ):
        return error_state("scene_confirm", BizCode.INVALID_REQUEST, "人工确认的场景无效")

    # resume 返回的 dict 经节点 wrapper 重新过 state 通道；task_id 以 resume
    # 载荷为兜底（StateGraph(dict) 测试图未声明 task_id 通道时不会透传）
    update: dict[str, Any] = {"scene": chosen["scene"]}
    if chosen.get("task_id"):
        update["task_id"] = chosen["task_id"]
    return update
