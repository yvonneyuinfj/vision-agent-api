"""场景人工确认：识别（select_capability）与确认（scene_confirm）分离的 HITL 拓扑。

关键不变量（H1 修复）：
- interrupt 只发生在 scene_confirm 节点，其内部除 interrupt 外零副作用；
- resume 重跑 scene_confirm 时，候选稳定来自 state['candidate_scenes']（checkpoint），
  不会重复执行 select_capability 的图片读取 / VLM 识别。
"""
from __future__ import annotations

import asyncio

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from app.agents.graph import solve_scene_confirm_command
from app.agents.nodes.scene_confirm import scene_confirm_node
from app.agents.nodes.select_capability import select_capability_node
from app.core.errors import BizCode
from app.models.capability import CapabilityManifest
from app.services.deps import AgentContext


class _ManifestRegistry:
    def __init__(self, scenes: list[str]):
        self._scenes = scenes

    def list(self) -> list[CapabilityManifest]:
        return [CapabilityManifest(capability_id=f"{s}_v1", tool_name=f"{s}_v1", scene=s)
                for s in self._scenes]

    def lookup(self, scene: str) -> CapabilityManifest | None:
        for m in self.list():
            if m.scene == scene:
                return m
        return None


class _AmbiguousLLM:
    available = True

    def __init__(self) -> None:
        self.calls = 0

    async def extract_scene(self, text: str, scenes: list[str], *, images=None) -> list[dict]:
        self.calls += 1
        return [
            {"scene": "borescope", "source": "llm"},
            {"scene": "infrared", "source": "llm", "role": "alternative"},
        ]


class _SettingsStub:
    def __init__(self, enabled: bool):
        self.scene_confirm_enabled = enabled


def _ctx(enabled: bool = True, llm: _AmbiguousLLM | None = None) -> AgentContext:
    return AgentContext(registry=_ManifestRegistry(["borescope", "infrared"]),
                        llm=llm or _AmbiguousLLM(), settings=_SettingsStub(enabled))


def _route_after_select(state: dict) -> str:
    if state.get("error"):
        return "error"
    if state.get("scene"):
        return "end"
    if len(state.get("candidate_scenes") or []) > 1:
        return "confirm"
    return "error"


def _build(ctx: AgentContext):
    async def _sel(state, _config=None):
        return await select_capability_node(state, ctx)

    async def _confirm(state, _config=None):
        return await scene_confirm_node(state, ctx)

    g = StateGraph(dict)
    g.add_node("select", _sel)
    g.add_node("confirm", _confirm)
    g.add_node("__fail__", lambda s, _c=None: {"_": None})
    g.add_edge(START, "select")
    g.add_conditional_edges("select", _route_after_select,
                            {"error": "__fail__", "end": END, "confirm": "confirm"})
    g.add_edge("confirm", END)
    g.add_edge("__fail__", END)
    return g.compile(checkpointer=MemorySaver())


def test_ambiguous_scene_interrupts_with_candidates_when_enabled():
    graph = _build(_ctx(True))

    async def _first():
        async for event in graph.astream({"task_id": "T1"}, config={"configurable": {"thread_id": "tr-1"}},
                                         stream_mode="values"):
            if isinstance(event, dict) and "__interrupt__" in event:
                return event["__interrupt__"][0].value
        return None

    payload = asyncio.run(_first())
    assert payload is not None
    assert payload["confirm_type"] == "scene"
    assert {c["scene"] for c in payload["candidates"]} == {"borescope", "infrared"}


def test_resume_with_chosen_scene_sets_scene():
    graph = _build(_ctx(True))

    async def _run():
        final = None
        async for event in graph.astream({"task_id": "T1"}, config={"configurable": {"thread_id": "tr-2"}},
                                         stream_mode="values"):
            if isinstance(event, dict) and "__interrupt__" in event:
                break
            final = event
        async for event in graph.astream(
            solve_scene_confirm_command("infrared"),
            config={"configurable": {"thread_id": "tr-2"}}, stream_mode="values",
        ):
            final = event
        return dict(final or {})

    final = asyncio.run(_run())
    assert final["scene"] == "infrared"


async def test_disabled_flag_keeps_4004_convergence():
    result = await select_capability_node({"task_id": "T1"}, _ctx(False))
    assert result["error"]["code"] == int(BizCode.SCENARIO_NOT_SUPPORTED)


def test_confirm_with_invalid_scene_converges_error():
    graph = _build(_ctx(True))

    async def _run():
        final = None
        async for event in graph.astream({"task_id": "T1"}, config={"configurable": {"thread_id": "tr-3"}},
                                         stream_mode="values"):
            if isinstance(event, dict) and "__interrupt__" in event:
                break
        async for event in graph.astream(
            solve_scene_confirm_command("dr"),
            config={"configurable": {"thread_id": "tr-3"}}, stream_mode="values",
        ):
            final = event
        return dict(final or {})

    final = asyncio.run(_run())
    assert final["error"]["code"] == int(BizCode.INVALID_REQUEST)


def test_resume_does_not_rerun_recognition():
    """H1 核心：resume 只重放 scene_confirm，VLM/LLM 识别不重复计费。"""
    llm = _AmbiguousLLM()
    graph = _build(_ctx(True, llm))

    async def _run():
        async for event in graph.astream({"task_id": "T1"}, config={"configurable": {"thread_id": "tr-4"}},
                                         stream_mode="values"):
            if isinstance(event, dict) and "__interrupt__" in event:
                break
        async for _ in graph.astream(
            solve_scene_confirm_command("borescope"),
            config={"configurable": {"thread_id": "tr-4"}}, stream_mode="values",
        ):
            pass

    asyncio.run(_run())
    assert llm.calls == 1  # 识别只在首次执行 select_capability 时发生一次
