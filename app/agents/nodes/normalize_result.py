"""节点：结果标准化处理（需求 §2.2 第四步、§1.2"不强行套用同一结构"）。

按 capability.capability_type 分流（对齐 docs/a.md §6.3 三分类标准化）：
- defect_detection：统一 severity 枚举（low/medium/high/null），未提供严重度不伪造
- measurement：保留原始指标与单位，附加计量说明
- classification：保留 label/score 语义，不做严重度推断

标准化逻辑拆  app/capabilities/normalizers/ 独立模块；本节点只做分发与
grade_hint 派生，工具自身 result 结构不同也无需改节点。
"""
from __future__ import annotations

from typing import Any

from app.agents.nodes.node_utils import error_state, node
from app.capabilities.normalizers import (
    max_confidence,
    max_severity,
    min_confidence,
    normalize_result,
)
from app.core.errors import BizCode
from app.services.deps import AgentContext


@node("normalize_result")
async def normalize_result_node(state: dict[str, Any], ctx: AgentContext) -> dict[str, Any]:
    raw = state.get("raw_result")
    if not raw:
        return error_state("normalize_result", BizCode.INFERENCE_FAILED, "no raw result to normalize")

    capability = state.get("selected_capability") or {}
    capability_type = capability.get("capability_type") or "defect_detection"

    tool_result = raw.get("result") or {}
    entries, family = normalize_result(capability_type, tool_result)

    summary = tool_result.get("summary") or (
        "未发现异常" if not entries else f"检出 {len(entries)} 项"
    )

    # 类别定义表（工具自报 class_definitions，适配器已清洗）：报告/前端图例消费
    definitions = tool_result.get("class_definitions")

    return {
        "standardized": entries,
        "class_definitions": definitions if isinstance(definitions, list) else [],
        "grade_hint": {
            "family": family,
            "status": tool_result.get("status", "unknown"),
            "summary": summary,
            "max_severity": max_severity(entries) if family == "defect" else None,
            "min_confidence": min_confidence(entries) if family == "defect" else None,
            "max_confidence": max_confidence(entries) if family == "defect" else None,
        },
    }