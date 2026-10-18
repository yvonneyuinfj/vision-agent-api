"""节点：指标解释（专业指标逐项解读，LLM 可选增强）。

位于判级之后、报告之前：解释不参与判级（只消费 grade 之前的标准化结果与
引用知识），LLM 不可用/失败时降级为空串，报告省略该章节，任务不受影响。
"""
from __future__ import annotations

from typing import Any

from app.agents.llm.prompts import DEFAULT_LANGUAGE
from app.agents.nodes.node_utils import node
from app.services.deps import AgentContext


@node("explain_metrics")
async def explain_metrics_node(state: dict[str, Any], ctx: AgentContext) -> dict[str, Any]:
    standardized = state.get("standardized") or []
    llm = ctx.llm
    if not standardized or llm is None or not llm.available:
        return {"metric_explanations": ""}

    language = (state.get("parameters") or {}).get("language") or DEFAULT_LANGUAGE
    try:
        text = await llm.explain_metrics(
            standardized=standardized,
            knowledge=state.get("knowledge") or [],
            language=language,
        )
    except Exception:  # noqa: BLE001 - 可选增强：任何异常只降级，不产生 error
        return {"metric_explanations": ""}
    return {"metric_explanations": (text or "").strip()}