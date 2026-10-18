"""图路由函数回归：错误收敛契约不得在 grading 分支丢失。

grading 之后的条件边同样要检查 state['error']：判级节点失败（缺 grade_hint 等）
必须收敛到 __fail__，而不是按默认 level=pass 流入 generate_report。
"""
from __future__ import annotations

from app.agents.graph import route_after_grading


def test_route_after_grading_error_converges_to_fail():
    state = {
        "error": {"node": "grading", "code": 4001, "message": "missing grade_hint"},
    }
    assert route_after_grading(state) == "error"


def test_route_after_grading_error_wins_over_grade():
    """同时携带 error 与 grade 时，错误收敛优先。"""
    state = {
        "grade": {"level": "pass"},
        "error": {"node": "grading", "code": 4001, "message": "boom"},
    }
    assert route_after_grading(state) == "error"


def test_route_after_grading_review_levels():
    assert route_after_grading({"grade": {"level": "review"}}) == "review"
    assert route_after_grading({"grade": {"level": "fail"}}) == "review"


def test_route_after_grading_auto_report_levels():
    assert route_after_grading({"grade": {"level": "pass"}}) == "report"
    assert route_after_grading({"grade": {"level": "defect"}}) == "report"
    assert route_after_grading({}) == "report"  # 缺 grade 默认 pass -> report
