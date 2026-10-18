"""复核数据闭环（评估矩阵）聚合测试：漏检/误检统计 + 阈值对照口径。"""
from __future__ import annotations

import json

import pytest

from app.repositories.task_repo import TaskRepo
from app.services.review_matrix import aggregate_review_matrix, collect_review_matrix


def _task(
    task_id: str,
    *,
    scene: str = "borescope",
    grade: dict | None = None,
    review: dict | None = None,
) -> dict:
    return {
        "task_id": task_id,
        "scene": scene,
        "capability_used": None,
        "status": "completed",
        "grade_json": json.dumps(grade or {}, ensure_ascii=False) if grade else None,
        "review_json": json.dumps(review or {}, ensure_ascii=False) if review else None,
    }


def test_aggregate_counts_actions_and_defect_diffs():
    tasks = [
        _task(
            "T1",
            grade={"level": "review", "triggered_rules": ["max_confidence < 0.5"], "max_confidence": 0.42},
            review={"action": "revise", "grading_result": "revised",
                    "defects_diff": {"added": 2, "excluded": 1}},
        ),
        _task(
            "T2",
            grade={"level": "review", "triggered_rules": ["agent_request"], "max_confidence": 0.9},
            review={"action": "confirm", "grading_result": "confirmed"},
        ),
        _task(
            "T3",
            scene="fpi",
            grade={"level": "review", "triggered_rules": ["max_severity >= 3"], "max_confidence": 0.8},
            review={"action": "reject", "grading_result": "rejected"},
        ),
    ]

    matrix = aggregate_review_matrix(tasks, confidence_threshold=0.5)

    assert matrix["sample_count"] == 3
    total = matrix["total"]
    assert total["tasks"] == 3
    assert total["actions"] == {"confirmed": 1, "revised": 1, "rejected": 1}
    assert total["defects"]["added"] == 2
    assert total["defects"]["excluded"] == 1
    assert total["agent_requests"] == 1  # T2 触发智能体异议
    assert total["revised_rate"] == round(1 / 3, 4)
    # 分场景
    assert matrix["by_scene"]["borescope"]["tasks"] == 2
    assert matrix["by_scene"]["fpi"]["actions"]["rejected"] == 1
    # 阈值对照：低置信人群只有 T1（0.42 < 0.5）
    assert matrix["low_confidence_band"]["tasks"] == 1
    assert matrix["low_confidence_band"]["actions"]["revised"] == 1


def test_aggregate_skips_interrupt_payloads_without_action():
    """复核门中断快照（question/grade 载荷）无 action 字段：不计入统计。"""
    tasks = [
        _task("T1", grade={"level": "review"}, review={"question": "请人工复核", "grade": {"level": "review"}}),
        _task("T2", review=None),
    ]

    matrix = aggregate_review_matrix(tasks)

    assert matrix["sample_count"] == 0
    assert matrix["total"]["tasks"] == 0


def test_aggregate_empty_inputs():
    matrix = aggregate_review_matrix([])

    assert matrix["sample_count"] == 0
    assert matrix["total"]["actions"]["confirmed"] == 0
    assert matrix["total"]["revised_rate"] is None
    assert matrix["low_confidence_band"]["tasks"] == 0


def test_aggregate_tolerates_dirty_json():
    tasks = [
        _task("T1", grade="not-json{", review={"action": "confirm", "grading_result": "confirmed"}),
    ]

    matrix = aggregate_review_matrix(tasks)

    assert matrix["sample_count"] == 1
    assert matrix["total"]["actions"]["confirmed"] == 1


@pytest.mark.asyncio
async def test_repo_list_reviewed_and_collect(session_factory):
    """仓储链路：list_reviewed 拉取带复核留痕任务，投影含聚合所需字段。"""
    repo = TaskRepo(session_factory)
    await repo.create("T001", scene="borescope", mode="single")
    await repo.create("T002", scene="fpi", mode="single")
    await repo.patch_json(
        "T001",
        grade_json={"level": "review", "max_confidence": 0.4},
        review_json={"action": "revise", "grading_result": "revised", "defects_diff": {"added": 1}},
    )

    rows = await collect_review_matrix(repo)
    assert len(rows) == 1
    assert rows[0]["task_id"] == "T001"
    assert rows[0]["scene"] == "borescope"

    matrix = aggregate_review_matrix(rows, confidence_threshold=0.5)
    assert matrix["sample_count"] == 1
    assert matrix["total"]["defects"]["added"] == 1
    assert matrix["low_confidence_band"]["tasks"] == 1
