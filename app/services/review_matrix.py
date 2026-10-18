"""复核数据闭环（评估矩阵）：把人工复核留痕聚合为漏检/误检统计。

数据源（已存在的留痕，此前仅用于报告渲染）：
- tasks.review_json：action（confirm/revise/reject）+ defects_diff 审计摘要
  （added=人工补漏检数、excluded=人工判误检数、reclassified/severity_changed/moved/removed）
- tasks.grade_json：判级结论 + triggered_rules（含 agent_request 智能体异议）
  + max_confidence（低置信转人工的阈值依据）

产出（三层）：
- 总量与动作分布：复核单量、confirm/revise/reject 计数、改判率
- 缺陷级矩阵：added/excluded/reclassified/severity_changed/moved/removed 汇总
  （added↔能力漏检、excluded↔能力误报，是能力质量评估的核心指标）
- 阈值对照：低置信转人工人群（max_confidence < confidence_threshold）中
  人工确认率 vs 改判率——确认率越高说明阈值偏保守（本可自动放行），
  改判率越高说明阈值偏激进（漏放风险）
"""
from __future__ import annotations

import json
from typing import Any

from app.core.logger import get_logger

logger = get_logger("app.services.review_matrix")

# 复核动作计数键（grading_result 终态语义）
_ACTION_KEYS = ("confirmed", "revised", "rejected")

_DEFECT_DIFF_KEYS = (
    "added",
    "excluded",
    "reclassified",
    "severity_changed",
    "moved",
    "removed",
)


def _loads(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}


def _empty_bucket() -> dict[str, Any]:
    return {
        "tasks": 0,
        "actions": {key: 0 for key in _ACTION_KEYS},
        "agent_requests": 0,
        "defects": {key: 0 for key in _DEFECT_DIFF_KEYS},
    }


def _merge_bucket(bucket: dict[str, Any], grade: dict[str, Any], review: dict[str, Any]) -> None:
    bucket["tasks"] += 1
    result = str(review.get("grading_result") or "")
    if result in _ACTION_KEYS:
        bucket["actions"][result] += 1
    elif review.get("action") == "confirm":
        bucket["actions"]["confirmed"] += 1
    if "agent_request" in (grade.get("triggered_rules") or []):
        bucket["agent_requests"] += 1
    diff = review.get("defects_diff")
    if isinstance(diff, dict):
        for key in _DEFECT_DIFF_KEYS:
            try:
                bucket["defects"][key] += int(diff.get(key) or 0)
            except (TypeError, ValueError):
                pass


def aggregate_review_matrix(
    tasks: list[dict[str, Any]], *, confidence_threshold: float = 0.5
) -> dict[str, Any]:
    """纯函数聚合（可单测）：tasks 为任务投影 dict 列表。

    每条投影字段：{task_id, scene, capability_used, status, grade_json, review_json}。
    只统计带真实复核决定（review 含 action/grading_result）的任务。
    """
    total = _empty_bucket()
    by_scene: dict[str, dict[str, Any]] = {}
    low_conf = _empty_bucket()  # max_confidence < 阈值转人工人群
    sample_count = 0

    for task in tasks:
        review = _loads(task.get("review_json"))
        # 复核门中断载荷（question/grade）不是复核决定：必须有 action 才计入
        if not review.get("action"):
            continue
        sample_count += 1
        grade = _loads(task.get("grade_json"))

        _merge_bucket(total, grade, review)
        scene = str(task.get("scene") or "unknown")
        bucket = by_scene.setdefault(scene, _empty_bucket())
        _merge_bucket(bucket, grade, review)

        try:
            max_conf = float(grade.get("max_confidence"))
        except (TypeError, ValueError):
            max_conf = None
        if max_conf is not None and max_conf < confidence_threshold:
            _merge_bucket(low_conf, grade, review)

    def _finalize(bucket: dict[str, Any]) -> dict[str, Any]:
        revised = bucket["actions"]["revised"]
        decided = sum(bucket["actions"].values())
        bucket["revised_rate"] = round(revised / decided, 4) if decided else None
        return bucket

    return {
        "sample_count": sample_count,
        "confidence_threshold": confidence_threshold,
        "total": _finalize(total),
        "by_scene": {scene: _finalize(b) for scene, b in by_scene.items()},
        "low_confidence_band": _finalize(low_conf),
    }


async def collect_review_matrix(task_repo: Any, *, limit: int = 500) -> list[dict[str, Any]]:
    """拉取带复核留痕的任务投影（服务层入口，供 API/脚本共用）。"""
    rows = await task_repo.list_reviewed(limit=limit)
    return [
        {
            "task_id": r.id,
            "scene": r.scene,
            "capability_used": r.capability_used,
            "status": r.status,
            "grade_json": r.grade_json,
            "review_json": r.review_json,
        }
        for r in rows
    ]
