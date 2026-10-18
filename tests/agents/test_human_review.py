"""human_review 节点：复核动作语义（confirm/revise/requeue/reject）与判级派生字段一致性。

M3 回归：requeue 不再静默降级为 confirm（显式错误收敛）；
revise 覆盖 level/summary 时同步清理自动规则派生的 triggered_rules，
避免报告出现「结论=pass 但命中高风险规则」的自相矛盾。

复核门收窄回归：仅 review 级暂停等人工（需求「仅检测结果低于阈值等规则命中
才转人工」）；pass/defect/fail 为可信自动判级，直接放行到报告。
"""
from __future__ import annotations

import asyncio

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from app.agents.graph import solve_review_command
from app.agents.nodes.human_review import human_review_node
from app.core.errors import BizCode
from app.services.deps import AgentContext


def _ctx() -> AgentContext:
    return AgentContext()


def _build():
    async def _review(state, _config=None):
        return await human_review_node(state, _ctx())

    g = StateGraph(dict)
    g.add_node("review", _review)
    g.add_edge(START, "review")
    g.add_edge("review", END)
    return g.compile(checkpointer=MemorySaver())


def _grade(level: str = "review") -> dict:
    return {
        "level": level,
        "summary": "规则命中需要人工复核：max_severity >= 3",
        "triggered_rules": ["max_severity >= 3"],
        "max_severity": "high",
        "auto": True,
    }


def _run(graph, resume: Command, thread: str) -> dict:
    async def _go() -> dict:
        final = None
        async for event in graph.astream(
            {"task_id": "T1", "grade": _grade()},
            config={"configurable": {"thread_id": thread}},
            stream_mode="values",
        ):
            if isinstance(event, dict) and "__interrupt__" in event:
                break
            final = event
        async for event in graph.astream(
            resume, config={"configurable": {"thread_id": thread}}, stream_mode="values"
        ):
            final = event
        return dict(final or {})

    return asyncio.run(_go())


def test_confirm_marks_grade_confirmed():
    final = _run(_build(), solve_review_command("confirm", "符合预期", None), "hr-1")
    assert final["review"]["grading_result"] == "confirmed"
    assert final["grade"]["confirmed"] is True
    assert final["grade"]["auto"] is False


def test_revise_overrides_level_and_clears_triggered_rules():
    final = _run(
        _build(),
        solve_review_command("revise", "误判，实为合格", {"level": "pass", "summary": "人工复核判定合格"}),
        "hr-2",
    )
    assert final["review"]["grading_result"] == "revised"
    assert final["grade"]["level"] == "pass"
    assert final["grade"]["summary"] == "人工复核判定合格"
    # 派生字段同步清理：不再残留自动规则命中列表
    assert final["grade"]["triggered_rules"] == []
    assert final["grade"]["auto"] is False


def test_requeue_converges_unsupported_error_instead_of_silent_confirm():
    final = _run(_build(), solve_review_command("requeue", "换模型再检", None), "hr-3")
    assert final["review"]["grading_result"] == "requeue_unsupported"
    assert final["error"]["code"] == int(BizCode.INVALID_REQUEST)
    assert "requeue" in final["error"]["message"]


def test_reject_converges_error():
    final = _run(_build(), solve_review_command("reject", "图像不可用", None), "hr-4")
    assert final["review"]["grading_result"] == "rejected"
    assert final["error"]["code"] == int(BizCode.INVALID_REQUEST)
    assert "图像不可用" in final["error"]["message"]


async def test_auto_confirm_when_level_not_reviewable():
    result = await human_review_node({"task_id": "T1", "grade": _grade("pass")}, _ctx())
    assert result["review"]["action"] == "auto_confirm"
    assert "error" not in result


async def test_fail_level_auto_confirms_without_interrupt():
    """需求回归：fail（可信中高严重度）是规则自动判级结论，不再全量卡人工复核。

    只有 review 级暂停等人工；高严重度须人工确认的场景由 need_review 规则
    （max_severity >= 3）改判 review 承接，而非靠复核门拦 fail。
    """
    result = await human_review_node(
        {"task_id": "T1", "grade": {"level": "fail", "summary": "存在中/高严重度缺陷，判为不合格", "triggered_rules": [], "auto": True}},
        _ctx(),
    )
    assert result["review"]["action"] == "auto_confirm"
    assert "error" not in result
    # 判级结论不被复核门改写（fail 原样进报告）
    assert "grade" not in result


async def test_defect_level_auto_confirms():
    result = await human_review_node(
        {"task_id": "T1", "grade": {"level": "defect", "summary": "存在低严重度缺陷，建议回修", "triggered_rules": [], "auto": True}},
        _ctx(),
    )
    assert result["review"]["action"] == "auto_confirm"


def test_interrupt_question_shows_agent_objection_reason():
    """共识（异议机制）：智能体异议触发的复核，中断文案直接呈现异议理由，
    而不是写死的"高风险或低置信度项"。"""
    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.graph import END, START, StateGraph

    from app.agents.nodes.human_review import human_review_node

    async def _review(state, _config=None):
        return await human_review_node(state, _ctx())

    g = StateGraph(dict)
    g.add_node("review", _review)
    g.add_edge(START, "review")
    g.add_edge("review", END)
    graph = g.compile(checkpointer=MemorySaver())

    objection_grade = {
        "level": "review",
        "summary": "智能体异议，请求人工复核：能力报无异常，但图像可见网状荧光显示",
        "triggered_rules": ["agent_request"],
        "auto": True,
    }
    cfg = {"configurable": {"thread_id": "hr-objection"}}

    async def _go():
        async for _ in graph.astream(
            {"task_id": "T1", "grade": objection_grade}, config=cfg, stream_mode="values"
        ):
            pass

    asyncio.run(_go())
    snapshot = graph.get_state(cfg)
    assert snapshot.interrupts, "复核门应暂停图执行"
    question = snapshot.interrupts[0].value["question"]
    assert "智能体异议" in question and "网状荧光显示" in question


def test_interrupt_question_rule_review_keeps_original_wording():
    """规则触发的复核（非异议）：中断文案保持原有表述。"""
    graph = _build()
    cfg = {"configurable": {"thread_id": "hr-rule"}}

    async def _go():
        async for _ in graph.astream({"task_id": "T1", "grade": _grade()}, config=cfg, stream_mode="values"):
            pass

    asyncio.run(_go())
    snapshot = graph.get_state(cfg)
    question = snapshot.interrupts[0].value["question"]
    assert question == "检测结果存在高风险或低置信度项，请人工复核"


# ---------- 缺陷修订（标注即改判依据：全量列表契约 + diff 留痕 + artifacts 载荷） ----------

def _defect(defect_id: str, dtype: str = "BP", sev: str = "high", bbox=None) -> dict:
    return {
        "defect_id": defect_id,
        "defect_type": dtype,
        "defect_name_cn": "模糊的真缺陷",
        "confidence": 0.76,
        "severity": sev,
        "location": {"type": "bbox", "bbox": bbox or [10.0, 10.0, 50.0, 50.0]},
    }


def _run_with_state(graph, state: dict, resume: Command, thread: str):
    """跑中断→恢复全程，返回 (中断载荷, 终态)。"""
    async def _go():
        payload = None
        final = None
        async for event in graph.astream(
            state, config={"configurable": {"thread_id": thread}}, stream_mode="values",
        ):
            if isinstance(event, dict) and "__interrupt__" in event:
                payload = event["__interrupt__"][0].value
                break
            final = event
        async for event in graph.astream(
            resume, config={"configurable": {"thread_id": thread}}, stream_mode="values",
        ):
            final = event
        return payload, dict(final or {})

    return asyncio.run(_go())


def test_interrupt_payload_carries_tool_artifacts():
    """复核期中断载荷带 raw_result.artifacts（随 review_json 落库）：
    复核人无需等终局报告即可看工具渲染的算法标注图。"""
    arts = [{"uri": "/api/v1/tasks/T1/files/r.jpg?kind=artifacts", "name": "r.jpg", "type": "result_image"}]
    state = {"task_id": "T1", "grade": _grade(), "raw_result": {"artifacts": arts}}
    payload, _ = _run_with_state(
        _build(), state, solve_review_command("confirm", "ok", None), "hr-art-1",
    )
    assert payload["artifacts"] == arts


def test_interrupt_payload_artifacts_default_empty():
    state = {"task_id": "T1", "grade": _grade()}
    payload, _ = _run_with_state(
        _build(), state, solve_review_command("confirm", "ok", None), "hr-art-2",
    )
    assert payload["artifacts"] == []


def test_revise_with_defects_replaces_standardized_and_records_diff():
    original = [_defect("D001"), _defect("D002", dtype="SP", sev="medium")]
    revised = [
        {**_defect("D001"), "human_rejected": True},                        # 排除
        {**_defect("D002", dtype="SP", sev="medium"),                        # 改类+改严重度
         "defect_type": "BP", "severity": "high",
         "revised": {"defect_type": "SP", "severity": "medium"}},
        {"defect_type": "crack", "severity": "high", "source": "manual",     # 人工新增（无 id）
         "location": {"type": "bbox", "bbox": [100.0, 100.0, 150.0, 150.0]}},
    ]
    state = {"task_id": "T1", "grade": _grade(), "standardized": original}
    _, final = _run_with_state(
        _build(), state,
        solve_review_command("revise", "补漏检+排误检", None, defects=revised),
        "hr-def-1",
    )
    std = final["standardized"]
    assert len(std) == 3
    assert std[0]["human_rejected"] is True
    assert std[1]["defect_type"] == "BP"
    # 人工新增条目自动补号（模型条目 D 前缀，天然不冲突）
    assert std[2]["defect_id"] == "M001"
    diff = final["review"]["defects_diff"]
    assert diff["added"] == 1 and diff["excluded"] == 1
    assert diff["reclassified"] == 1 and diff["severity_changed"] == 1
    assert diff["moved"] == 0 and diff["removed"] == 0
    # review 留痕不带 defects 大字段（权威落点是 standardized 通道）
    assert "defects" not in final["review"]


def test_revise_defects_counts_moved_bbox_and_hard_removal():
    original = [_defect("D001", bbox=[1.0, 2.0, 3.0, 4.0]), _defect("D002")]
    revised = [{**_defect("D001", bbox=[5.0, 6.0, 7.0, 8.0])}]  # 移框 + 硬删 D002
    state = {"task_id": "T1", "grade": _grade(), "standardized": original}
    _, final = _run_with_state(
        _build(), state,
        solve_review_command("revise", "框位修正", None, defects=revised),
        "hr-def-2",
    )
    diff = final["review"]["defects_diff"]
    assert diff["moved"] == 1 and diff["removed"] == 1 and diff["added"] == 0


def test_revise_defects_without_override_grade_keeps_grade():
    """只修缺陷不改判级：grade 原样（人工终裁，不自动重判）。"""
    state = {"task_id": "T1", "grade": _grade(), "standardized": [_defect("D001")]}
    _, final = _run_with_state(
        _build(), state,
        solve_review_command("revise", "仅修订缺陷", None,
                             defects=[{**_defect("D001"), "human_rejected": True}]),
        "hr-def-3",
    )
    # 节点不输出 grade 更新 = 判级原样保留（人工终裁，不自动重判）
    assert "grade" not in final
    assert final["standardized"][0]["human_rejected"] is True


def test_revise_with_malformed_defects_converges_error():
    state = {"task_id": "T1", "grade": _grade(), "standardized": [_defect("D001")]}
    _, final = _run_with_state(
        _build(), state,
        solve_review_command("revise", "x", None, defects=[{"no_type": 1}]),
        "hr-def-4",
    )
    assert final["error"]["code"] == int(BizCode.INVALID_REQUEST)
    assert "defect_type" in final["error"]["message"]


def test_confirm_ignores_defects_payload():
    """契约：仅 revise 携带修订；confirm 带 defects 时忽略（standardized 原样、无痕）。"""
    original = [_defect("D001")]
    state = {"task_id": "T1", "grade": _grade(), "standardized": original}
    _, final = _run_with_state(
        _build(), state,
        solve_review_command("confirm", "ok", None, defects=[{**_defect("D001"), "human_rejected": True}]),
        "hr-def-5",
    )
    assert final["review"]["grading_result"] == "confirmed"
    assert "defects" not in final["review"] and "defects_diff" not in final["review"]
    # confirm 分支不输出 standardized 更新 = 原表原样保留
    assert "standardized" not in final


# --------------------------------------------------------------------------
# 裁决溯源 × 复核 diff：agent_visual 条目不误计人工漏检（review-matrix 指标保真）
# --------------------------------------------------------------------------
def test_defect_revision_diff_ignores_agent_visual_on_index_shift():
    """react 轨条目常无 defect_id（索引兜底对齐）：复核全量表回传时
    手写 agent_visual 条目因错位对不上旧表，不得计入人工补漏检。"""
    from app.agents.nodes.human_review import _defect_revision_diff

    original = [
        {"defect_type": "BP", "confidence": 0.76, "location": {"bbox": [1, 2, 3, 4]}},
        {"defect_type": "crack", "confidence": 0.9, "source": "agent_visual"},
    ]
    # 复核者在最前插入一条人工新增，agent_visual 条目索引从 1 错位到 2
    revised = [
        {"defect_type": "pore", "confidence": 0.8, "source": "manual"},
        {"defect_type": "BP", "confidence": 0.76, "location": {"bbox": [1, 2, 3, 4]}},
        {"defect_type": "crack", "confidence": 0.9, "source": "agent_visual"},
    ]
    diff = _defect_revision_diff(original, revised)

    assert diff["added"] == 1  # 仅人工 manual 计漏检
    assert diff["removed"] == 0
