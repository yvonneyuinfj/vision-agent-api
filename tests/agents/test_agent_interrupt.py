"""核心编排语义测试：interrupt 人机复核 + Command(resume) 恢复（LangGraph 1.2+ 真实行为）。"""
from __future__ import annotations

import asyncio
from typing import TypedDict

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt


class ReviewState(TypedDict, total=False):
    x: int
    review: dict
    report: str


def _grade(s: ReviewState) -> dict:
    return {"x": s.get("x", 0) + 1}


def _gate(s: ReviewState) -> dict:
    decision = interrupt({"question": "人工复核"})
    return {"review": decision}


def _report(s: ReviewState) -> dict:
    return {"report": f"R-{s.get('x')}"}


def _build_graph():
    g = StateGraph(ReviewState)
    g.add_node("grade", _grade)
    g.add_node("gate", _gate)
    g.add_node("report", _report)
    g.add_edge(START, "grade")
    g.add_edge("grade", "gate")
    g.add_edge("gate", "report")
    g.add_edge("report", END)
    return g.compile(checkpointer=MemorySaver())


def _cfg() -> dict:
    return {"configurable": {"thread_id": "it-1"}}


async def _run_interrupted(graph, state: dict) -> dict | None:
    """模拟 task_service.run_task 对 interrupt 的识别（__interrupt__ 键而非异常）。"""
    async for event in graph.astream(state, config=_cfg(), stream_mode="values"):
        if isinstance(event, dict) and "__interrupt__" in event:
            return event["__interrupt__"][0].value
    return None


def test_interrupt_halts_and_resume_continues():
    # Arrange
    graph = _build_graph()

    # Act 1: 首次执行应暂停（返回 interrupt 载荷，而非报告）
    paused = asyncio.run(_run_interrupted(graph, {"x": 0}))

    # Assert 1
    assert paused == {"question": "人工复核"}
    assert asyncio.run(graph.aget_state(_cfg())).values["x"] == 1  # grade 节点已提交

    # Act 2: 人工复核 confirm 后 resume
    async def _resume():
        async for event in graph.astream(
            Command(resume={"action": "confirm", "opinion": "ok"}), config=_cfg(), stream_mode="values"
        ):
            if isinstance(event, dict) and event.get("report"):
                return event
        return None

    final = asyncio.run(_resume())

    # Assert 2：报告产出且复核意见入 state
    assert final is not None
    assert final["report"] == "R-1"
    assert final["review"]["action"] == "confirm"