"""节点：人工复核门（Human-in-the-loop，LangGraph interrupt）。

仅当判级结论为 review（低置信度检测结果 / 高严重度 / 规则无法覆盖 / 智能体异议）
时在此暂停图执行；pass/defect/fail 为可信自动判级，直接放行到报告
（需求：仅当检测模型结果低于设定阈值等规则命中时才转人工，非全量复核）。
前端通过 POST /api/v1/tasks/{id}/review 提交决定（confirm/revise/requeue/reject），
由 task_service 以 Command(resume=...) 恢复执行。复核过程全程留痕（state['review']）。
"""
from __future__ import annotations

from typing import Any

from langgraph.types import Command, interrupt  # noqa: F401 - 图恢复处使用 Command

from app.agents.nodes.node_utils import error_state, node
from app.core.errors import BizCode
from app.services.deps import AgentContext

# fail（中高严重度且置信度达标）是规则自动判级结论，不再卡人工；
# 高严重度须人工确认的场景由 need_review 规则（max_severity >= 3）改判 review 承接。
REVIEW_REQUIRED_LEVELS = {"review"}

# 修订条目允许携带的溯源标记字段（决策：全量列表 + 条目自带标记，
# 后端整表替换 standardized，审计 diff 由 _defect_revision_diff 对齐生成）
_MANUAL_ID_PREFIX = "M"


def _validate_revised_defects(entries: Any) -> list[dict[str, Any]] | None:
    """校验全量修订列表：必须为 dict 且 defect_type 为 str；非法返回 None。

    人工新增条目缺 defect_id 时按 M001… 补号（模型条目为 D001…，天然不冲突）。
    """
    if not isinstance(entries, list):
        return None
    cleaned: list[dict[str, Any]] = []
    manual_seq = 0
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("defect_type"), str):
            return None
        item = dict(entry)
        if item.get("source") == "manual" and not item.get("defect_id"):
            manual_seq += 1
            item["defect_id"] = f"{_MANUAL_ID_PREFIX}{manual_seq:03d}"
        cleaned.append(item)
    return cleaned


def _defect_revision_diff(
    original: list[Any], revised: list[dict[str, Any]]
) -> dict[str, int]:
    """按 defect_id 对齐新旧表生成审计 diff 摘要（评估矩阵的数据源：
    added=人工补漏检数、excluded=人工判误检数）。一条只计一次，排除优先。"""
    orig_by_id: dict[Any, dict[str, Any]] = {}
    for i, entry in enumerate(original):
        if isinstance(entry, dict):
            orig_by_id[entry.get("defect_id") or f"__idx_{i}"] = entry
    added = excluded = reclassified = severity_changed = moved = 0
    seen: set[Any] = set()
    for i, entry in enumerate(revised):
        key = entry.get("defect_id") or f"__idx_{i}"
        seen.add(key)
        old = orig_by_id.get(key)
        if entry.get("source") == "manual":
            added += 1
            continue
        if old is None:
            # 智能体视觉判断条目已在 react 轨裁决时打标（非人工新增）；
            # react 轨条目常无 defect_id（索引兜底对齐），插入/删除造成的
            # 错位不得把它误计成人工补漏检（污染 review-matrix 漏检指标）
            if entry.get("source") == "agent_visual":
                continue
            added += 1
            continue
        if entry.get("human_rejected") and not old.get("human_rejected"):
            excluded += 1
            continue
        if entry.get("defect_type") != old.get("defect_type"):
            reclassified += 1
        if entry.get("severity") != old.get("severity"):
            severity_changed += 1
        new_bbox = (entry.get("location") or {}).get("bbox")
        old_bbox = (old.get("location") or {}).get("bbox")
        if new_bbox != old_bbox:
            moved += 1
    removed = len(set(orig_by_id) - seen)  # 契约要求保留条目标记排除；硬删兜底审计
    return {
        "added": added, "excluded": excluded, "reclassified": reclassified,
        "severity_changed": severity_changed, "moved": moved, "removed": removed,
    }


@node("human_review")
async def human_review_node(state: dict[str, Any], ctx: AgentContext) -> dict[str, Any]:
    grade = state.get("grade") or {}
    level = grade.get("level", "pass")

    if level not in REVIEW_REQUIRED_LEVELS:
        return {"review": {"action": "auto_confirm", "opinion": "规则自动通过，无需人工复核"}}

    # ---- 触发人工复核（暂停图执行直到外部 resume）----
    # 载荷瘦身：standardized/knowledge 全量列表已由上游节点写入 state 并可经
    # GET /tasks/{id} 回源，interrupt 载荷只带计数摘要，避免 checkpoint 每步
    # 序列化大列表造成的写放大。
    triggered = grade.get("triggered_rules") or []
    if "agent_request" in triggered:
        # 智能体异议触发的复核：问题文案直接呈现异议理由（判级摘要已含）
        question = str(grade.get("summary") or "智能体请求人工复核")
    else:
        question = "检测结果存在高风险或低置信度项，请人工复核"
    # 工具渲染的标注图（result_image 等）随中断载荷落库：复核期即可三方对照
    # （模型自渲染图 / 结构化框叠加 / 智能体异议描述），无需等终局 report。
    raw = state.get("raw_result") or {}
    artifacts = raw.get("artifacts") if isinstance(raw, dict) else None
    interruption = interrupt(
        {
            "question": question,
            "task_id": state["task_id"],
            "scene": state.get("scene"),
            "grade": grade,
            "standardized_count": len(state.get("standardized") or []),
            "knowledge_count": len(state.get("knowledge") or []),
            "artifacts": artifacts if isinstance(artifacts, list) else [],
            "decision_options": ["confirm", "revise", "requeue", "reject"],
        }
    )

    decision: dict[str, Any] = interruption if isinstance(interruption, dict) else {}
    # defects 大字段先剥离：修订后全量列表的权威落点是 standardized 通道
    # （随终局 _persist 落库），review 留痕只带 diff 摘要，避免 checkpoint/
    # review_json 双份写放大；非 revise 动作携带 defects 时按契约忽略。
    revised_defects = decision.pop("defects", None) if isinstance(decision, dict) else None
    action = decision.get("action", "confirm")

    if action == "reject":
        return {
            "review": {**decision, "grading_result": "rejected"},
            "error": {
                "node": "human_review",
                "code": int(BizCode.INVALID_REQUEST),
                "message": decision.get("opinion") or "人工复核驳回",
            },
        }

    if action == "requeue":
        # 重检需把流程重新路由回执行段（改图拓扑），属于 M2 联合质检配套能力；
        # 当前不支持时显式报错，绝不静默降级为 confirm 造成“以为重检实际通过”的误判。
        return {
            "review": {**decision, "grading_result": "requeue_unsupported"},
            "error": {
                "node": "human_review",
                "code": int(BizCode.INVALID_REQUEST),
                "message": "requeue（重新排队再检）尚未支持，请使用 retry 接口重跑失败任务，或选择 confirm/revise/reject",
            },
        }

    if action == "revise" and (decision.get("override_grade") or revised_defects is not None):
        # 复核人员修正判级结论：override 覆盖 level/summary 的同时，清理由自动
        # 规则派生的 triggered_rules（否则报告会出现“结论=pass 但命中高风险规则”的自相矛盾）。
        # 缺陷修订（Q4 契约）：全量列表整表替换 standardized，diff 摘要留痕；
        # 判级不重算（人工终裁），一致性由前端提交前强提示兜底。
        review_record: dict[str, Any] = {**decision, "grading_result": "revised"}
        update: dict[str, Any] = {}
        revised = revised_defects
        if revised is not None:
            cleaned = _validate_revised_defects(revised)
            if cleaned is None:
                return error_state(
                    "human_review",
                    BizCode.INVALID_REQUEST,
                    "defects 修订列表非法：须为对象数组且每项含字符串 defect_type",
                )
            update["standardized"] = cleaned
            review_record["defects_diff"] = _defect_revision_diff(
                state.get("standardized") or [], cleaned
            )
        override = decision.get("override_grade")
        if isinstance(override, dict) and override:
            update["grade"] = {
                **grade,
                **override,
                "auto": False,
                "triggered_rules": override.get("triggered_rules") or [],
            }
        update["review"] = review_record
        return update

    # confirm 走报告
    return {
        "review": {**decision, "grading_result": "confirmed"},
        "grade": {**grade, "confirmed": True, "auto": False},
    }