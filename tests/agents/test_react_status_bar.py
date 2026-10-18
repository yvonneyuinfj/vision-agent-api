"""ReAct 状态栏（ch02 Agent Status Bar）测试：代码维护、尾部注入、确定性建议。"""
from __future__ import annotations

from app.agents.react.nodes import _render_status_bar


def test_status_bar_empty_state_suggests_detection():
    """初始状态：无检测记录 -> 下一步建议 invoke_capability。"""
    bar = _render_status_bar({}, loop=0, max_turns=10, max_calls=3)

    assert bar.startswith("<agent_status>")
    assert bar.endswith("</agent_status>")
    assert "第 1/10 轮" in bar
    assert "能力调用已用 0/3 次" in bar
    assert "尚未检测" in bar
    assert "已检索知识：0 条" in bar
    assert "下一步：判断场景后调用 invoke_capability" in bar


def test_status_bar_after_detection_summarizes_scenes_and_defects():
    state = {
        "capability_calls": 2,
        "scenes_tried": [
            {"scene": "borescope", "ok": True, "defect_count": 2, "summary": "检出 2 项"},
            {"scene": "fracture", "ok": False, "defect_count": None, "summary": "检测失败：超时"},
        ],
        "standardized": [
            {"defect_type": "crack", "severity": "high"},
            {"defect_type": "dent", "severity": "low"},
            {"defect_type": "crack", "severity": "low"},
        ],
        "knowledge": [],
    }

    bar = _render_status_bar(state, loop=3, max_turns=10, max_calls=3)

    assert "第 4/10 轮" in bar
    assert "能力调用已用 2/3 次" in bar
    assert "borescope（检出 2 项）" in bar
    assert "fracture（检测失败：超时）" in bar
    assert "最近检出（3 项）" in bar
    assert "crack×2、dent×1" in bar
    # 有缺陷未检索 -> 建议先检索再提交
    assert "retrieve_knowledge" in bar


def test_status_bar_with_knowledge_suggests_submit():
    state = {
        "capability_calls": 1,
        "scenes_tried": [{"scene": "borescope", "ok": True, "defect_count": 1, "summary": "检出 1 项"}],
        "standardized": [{"defect_type": "crack"}],
        "knowledge": [{"chunk_id": "c1"}],
    }

    bar = _render_status_bar(state, loop=2, max_turns=10, max_calls=3)

    assert "已检索知识：1 条" in bar
    assert "下一步：核对结果后调用 submit_final" in bar
    assert "review_required" in bar


def test_status_bar_no_defects_no_detection_suggestion_order():
    """零缺陷但已检测：直接建议提交（判级由系统终裁）。"""
    state = {
        "scenes_tried": [{"scene": "fracture", "ok": True, "defect_count": 0, "summary": "未发现异常"}],
        "standardized": [],
    }

    bar = _render_status_bar(state, loop=1, max_turns=10, max_calls=3)

    assert "fracture（检出 0 项）" in bar
    assert "下一步：核对结果后调用 submit_final" in bar
