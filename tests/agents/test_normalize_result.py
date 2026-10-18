"""normalize_result 节点回归：class_definitions 类别定义表通道。

类别定义表（工具自报，适配器清洗后随 result 透传）必须在标准化节点进 state，
报告「二 · 检测结果」类别图例与「三 · 缺陷逐项深度分析」的代码→中文名转译、
以及前端详情页图例都以此为数据源；缺失时给空列表而非 None（前端免判空）。
"""
from __future__ import annotations

from app.agents.nodes.normalize_result import normalize_result_node
from app.services.deps import AgentContext

DEFS = [
    {"class_id": 0, "class_name": "BP", "description": "模糊的真缺陷", "is_true_defect": True},
    {"class_id": 2, "class_name": "FI", "description": "虚假荧光显示", "is_true_defect": False},
]


def _state(result: dict) -> dict:
    return {
        "task_id": "T1",
        "raw_result": {"result": result},
        "selected_capability": {"tool_name": "blade_defect_detector",
                                "capability_type": "defect_detection"},
    }


async def test_class_definitions_flow_into_state():
    out = await normalize_result_node(
        _state({
            "status": "abnormal",
            "summary": "检出 1 处真缺陷",
            "defects": [{"defect_type": "BP", "defect_name_cn": "模糊的真缺陷",
                         "confidence": 0.83}],
            "class_definitions": DEFS,
        }),
        AgentContext(),
    )

    assert out["class_definitions"] == DEFS
    assert [d["defect_type"] for d in out["standardized"]] == ["BP"]


async def test_class_definitions_defaults_to_empty_list():
    """工具未提供定义表（mock 服务/测量类能力）→ 空列表，前端与报告免判空。"""
    out = await normalize_result_node(
        _state({"status": "normal", "summary": "未发现明显异常", "defects": []}),
        AgentContext(),
    )

    assert out["class_definitions"] == []


async def test_malformed_class_definitions_degrades_to_empty_list():
    """定义表形态非法（非列表）→ 降级空列表，不阻断标准化。"""
    out = await normalize_result_node(
        _state({"status": "normal", "summary": "s", "defects": [],
                "class_definitions": {"class_name": "BP"}}),
        AgentContext(),
    )

    assert out["class_definitions"] == []
