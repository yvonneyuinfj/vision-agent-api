"""ReAct 智能体轨（双轨并存 mode=agent）回归。

覆盖 18 项决策中落地的关键不变量：
- 路由函数：错误收敛优先、工具调用/纯文本分流、提交后进入出口安检；
- invoke_capability：自动标准化（焊进工具内部）+ 自救次数上限；
- submit_final：拒检分支、默认采用最近检测结果、空提交退回；
- exit_gate：拒检 -> failed 解释路径、终态完备性、判级线索终裁准备；
- bootstrap：无图拒绝启动、重试残留消息清空（RemoveMessage）；
- task_service._graph_for：双轨按 mode 选图。
"""
from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from app.agents.react.graph import route_after_agent, route_after_tools
from app.agents.react.nodes import bootstrap_agent_node, exit_gate_node
from app.agents.react.tools import (
    _invoke_capability_impl,
    _retrieve_knowledge_impl,
    _submit_final_impl,
)
from app.agents.tools.registry import ToolResult
from app.core.errors import BizCode
from app.models.capability import CapabilityManifest
from app.services.deps import AgentContext


# --------------------------------------------------------------------------
# 桩件
# --------------------------------------------------------------------------
class _Registry:
    def __init__(self, scenes: list[str] | None = None):
        self._scenes = scenes if scenes is not None else ["fracture", "borescope"]

    def list(self):
        return [
            CapabilityManifest(
                capability_id=f"{s}_v1", tool_name=f"{s}_tool", scene=s,
                capability_type="defect_detection",
            )
            for s in self._scenes
        ]

    def lookup(self, scene: str):
        for m in self.list():
            if m.scene == scene:
                return m
        return None


class _InferTool:
    """detect/* 工具桩：可注入成功载荷或失败。"""

    def __init__(self, defects: list[dict] | None = None, fail: tuple[int, str] | None = None):
        self.defects = defects if defects is not None else [
            {"defect_id": "d1", "defect_type": "cleavage", "severity": "high", "confidence": 0.9},
            {"defect_id": "d2", "defect_type": "dimple", "severity": "low", "confidence": 0.6},
        ]
        self.fail = fail
        self.calls = 0

    async def execute(self, payload: dict) -> ToolResult:
        self.calls += 1
        if self.fail:
            return ToolResult.failure(self.fail[0], self.fail[1])
        # 与真实 infer_tool 对齐：ToolResult.success({"raw_result": raw})，
        # raw.get("result") 才是算法结果（status/summary/defects）
        return ToolResult.success(
            {
                "raw_result": {
                    "result": {"defects": self.defects, "status": "abnormal", "summary": "检出 2 项"},
                    "task_id": payload.get("task_id"),
                    "artifacts": [],
                }
            }
        )


class _ToolRegistry:
    def __init__(self, infer: _InferTool | None = None, rag=None):
        self._infer = infer or _InferTool()
        self._rag = rag

    @property
    def infer(self) -> _InferTool:
        return self._infer

    def get(self, name: str):
        if name.startswith("detect/"):
            return self._infer
        if name == "knowledge/rag":
            return self._rag
        return None


class _FileRepo:
    """uri -> 本地路径；可注入一个真实可读文件供图片加载路径使用。"""

    def __init__(self, real_path: str | None = None):
        self._real = real_path

    def uri_to_path(self, uri: str) -> Path:
        if uri.startswith("bad://"):
            raise ValueError("outside data root")
        if self._real is not None:
            return Path(self._real)
        name = uri.split("/")[-1].split("?")[0] or "img.png"
        return Path(f"/tmp/{name}")


class _Settings:
    react_max_turns = 10
    react_max_capability_calls = 3


def _ctx(registry=None, tool_registry=None) -> AgentContext:
    return AgentContext(
        registry=registry if registry is not None else _Registry(),
        tool_registry=tool_registry if tool_registry is not None else _ToolRegistry(),
        file_repo=_FileRepo(),
        settings=_Settings(),
    )


def _base_state(**over: Any) -> dict[str, Any]:
    state: dict[str, Any] = {
        "task_id": "T1",
        "trace_id": "tr1",
        "input_uris": ["/api/v1/tasks/T1/files/a.png?kind=inputs"],
        "parameters": {},
        "capability_calls": 0,
    }
    state.update(over)
    return state


# --------------------------------------------------------------------------
# 路由函数
# --------------------------------------------------------------------------
def test_route_error_or_returns_mapping_keys_not_node_names():
    """条件边路由函数必须返回映射键（error/ok），不能返回目标节点名——
    否则 LangGraph 拿节点名查映射表抛 KeyError（线上实测踩坑）。"""
    from app.agents.react.graph import _route_error_or

    route = _route_error_or("react_agent")
    assert route({}) == "ok"
    assert route({"error": {"node": "x", "code": 1, "message": "m"}}) == "error"


def test_route_after_agent_error_converges():
    assert route_after_agent({"error": {"node": "x", "code": 1, "message": "m"}}) == "error"


def test_route_after_agent_tool_calls_go_tools():
    msg = AIMessage(content="", tool_calls=[{"name": "invoke_capability", "args": {}, "id": "c1"}])
    assert route_after_agent({"messages": [msg]}) == "tools"


def test_route_after_agent_plain_text_goes_nudge():
    assert route_after_agent({"messages": [AIMessage(content="我看看")]}) == "nudge"
    assert route_after_agent({"messages": []}) == "nudge"


def test_route_after_tools_submitted_exits():
    assert route_after_tools({"submitted": True}) == "exit"


def test_route_after_tools_error_converges():
    assert route_after_tools({"error": {"node": "t", "code": 1, "message": "m"}, "submitted": True}) == "error"


def test_route_after_tools_loops_back_to_agent():
    assert route_after_tools({"submitted": False}) == "agent"


# --------------------------------------------------------------------------
# invoke_capability
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_invoke_capability_normalizes_and_updates_side_state():
    ctx = _ctx()
    content, side = await _invoke_capability_impl({"scene": "fracture"}, _base_state(), ctx)
    assert side["scene"] == "fracture"
    assert side["capability_calls"] == 1
    assert len(side["standardized"]) == 2
    assert side["grade_hint"]["family"] == "defect"
    assert side["grade_hint"]["max_severity"] == "high"
    assert side["grade_hint"]["min_confidence"] == 0.6
    assert "fracture" in content and '"defect_count": 2' in content
    # 状态栏数据源：尝试即记录 scenes_tried
    assert side["scenes_tried"] == [
        {"scene": "fracture", "ok": True, "defect_count": 2, "summary": "检出 2 项"}
    ]


@pytest.mark.asyncio
async def test_invoke_capability_scenes_tried_accumulates_across_calls():
    """自救重试时 scenes_tried 追加（跨调用经 state 通道累积）。"""
    ctx = _ctx()
    _content1, side1 = await _invoke_capability_impl({"scene": "fracture"}, _base_state(), ctx)
    state2 = {**_base_state(), "scenes_tried": side1["scenes_tried"], "capability_calls": 1}
    _content2, side2 = await _invoke_capability_impl({"scene": "borescope"}, state2, ctx)

    assert len(side2["scenes_tried"]) == 2
    assert [t["scene"] for t in side2["scenes_tried"]] == ["fracture", "borescope"]


@pytest.mark.asyncio
async def test_invoke_capability_failure_records_scene_attempt():
    infer = _InferTool(fail=(5001, "model timeout"))
    ctx = _ctx(tool_registry=_ToolRegistry(infer=infer))
    _content, side = await _invoke_capability_impl({"scene": "fracture"}, _base_state(), ctx)

    assert side["scenes_tried"][0]["ok"] is False
    assert "model timeout" in side["scenes_tried"][0]["summary"]


@pytest.mark.asyncio
async def test_invoke_capability_unknown_scene_lists_registered():
    ctx = _ctx()
    content, side = await _invoke_capability_impl({"scene": "catscan"}, _base_state(), ctx)
    assert "无可用能力" in content and "borescope" in content
    assert side["capability_calls"] == 1  # 错误判断也计入次数（防无限试探）
    assert side["scenes_tried"][0]["scene"] == "catscan"
    assert side["scenes_tried"][0]["ok"] is False


@pytest.mark.asyncio
async def test_invoke_capability_budget_cap():
    ctx = _ctx()
    state = _base_state(capability_calls=3)
    content, side = await _invoke_capability_impl({"scene": "fracture"}, state, ctx)
    assert "上限" in content
    assert side == {}


@pytest.mark.asyncio
async def test_invoke_capability_failure_returns_readable_error():
    infer = _InferTool(fail=(5001, "model timeout"))
    ctx = _ctx(tool_registry=_ToolRegistry(infer=infer))
    content, side = await _invoke_capability_impl({"scene": "fracture"}, _base_state(), ctx)
    assert "检测失败" in content and "model timeout" in content
    assert side["capability_calls"] == 1
    assert "standardized" not in side  # 失败不污染结果通道


# --------------------------------------------------------------------------
# retrieve_knowledge
# --------------------------------------------------------------------------
class _RagTool:
    def __init__(self, knowledge: list[dict] | None = None, ok: bool = True):
        self.knowledge = knowledge if knowledge is not None else [
            {"chunk_id": "c1", "content": "解理断裂判据……", "score": 0.9, "doc_name": "ndt.pdf"},
        ]
        self.ok = ok

    async def execute(self, payload: dict) -> ToolResult:
        if not self.ok:
            return ToolResult.failure(5002, "ragflow down")
        return ToolResult.success({"knowledge": self.knowledge})


@pytest.mark.asyncio
async def test_retrieve_knowledge_merges_and_traces():
    ctx = _ctx(tool_registry=_ToolRegistry(rag=_RagTool()))
    content, side = await _retrieve_knowledge_impl({"query": "解理断裂如何判级"}, _base_state(), ctx)
    assert "解理断裂判据" in content
    assert len(side["knowledge"]) == 1
    assert side["retrieval_trace"][0]["hits"] == 1


@pytest.mark.asyncio
async def test_retrieve_knowledge_missing_tool_degrades():
    ctx = _ctx(tool_registry=_ToolRegistry(rag=None))
    content, side = await _retrieve_knowledge_impl({"query": "x"}, _base_state(), ctx)
    assert "未装配" in content and side == {}


# --------------------------------------------------------------------------
# submit_final
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_submit_final_refusal():
    content, side = await _submit_final_impl(
        {"refusal_reason": "图片是一只猫"}, _base_state(), _ctx()
    )
    assert side["submitted"] is True
    assert side["submit_payload"] == {"refused": True, "reason": "图片是一只猫"}


@pytest.mark.asyncio
async def test_submit_final_defaults_to_last_standardized():
    state = _base_state(standardized=[{"defect_type": "crack", "severity": "high"}], capability_calls=1)
    content, side = await _submit_final_impl({"summary": "检出裂纹"}, state, _ctx())
    assert side["submitted"] is True
    assert side["submit_payload"]["defects"] == [{"defect_type": "crack", "severity": "high"}]


@pytest.mark.asyncio
async def test_submit_final_rejects_empty_submission_without_detection():
    content, side = await _submit_final_impl({}, _base_state(capability_calls=0), _ctx())
    assert "退回" in content and side == {}


@pytest.mark.asyncio
async def test_submit_final_agent_override_defects():
    state = _base_state(standardized=[{"defect_type": "old"}], capability_calls=1)
    _, side = await _submit_final_impl(
        {"defects": [{"defect_type": "new", "severity": "low"}]}, state, _ctx()
    )
    assert side["submit_payload"]["defects"] == [{"defect_type": "new", "severity": "low"}]


@pytest.mark.asyncio
async def test_submit_final_lenient_parses_stringified_defects():
    """共识 Q5：模型把数组序列化成字符串（如 '[]'）是高频错误，宽容接住。"""
    state = _base_state(capability_calls=1)
    content, side = await _submit_final_impl({"defects": "[]"}, state, _ctx())
    assert side["submitted"] is True and side["submit_payload"]["defects"] == []


@pytest.mark.asyncio
async def test_submit_final_lenient_parses_stringified_defect_entries():
    state = _base_state(capability_calls=1)
    _, side = await _submit_final_impl(
        {"defects": '[{"defect_type": "crack", "severity": "low"}]'}, state, _ctx()
    )
    assert side["submit_payload"]["defects"] == [{"defect_type": "crack", "severity": "low"}]


@pytest.mark.asyncio
async def test_submit_final_bad_defects_error_teaches_format():
    """教得会的错误：给出条目示例与字符串化提示，而不是冷拒。"""
    state = _base_state(capability_calls=1)
    content, side = await _submit_final_impl({"defects": "not-json"}, state, _ctx())
    assert side == {}
    assert "defect_type" in content and "字符串化的数组" in content


@pytest.mark.asyncio
async def test_submit_final_review_request_requires_reason():
    state = _base_state(capability_calls=1)
    content, side = await _submit_final_impl({"review_required": True}, state, _ctx())
    assert side == {} and "review_reason" in content


@pytest.mark.asyncio
async def test_submit_final_review_request_passes_through_payload():
    state = _base_state(capability_calls=1, standardized=[])
    content, side = await _submit_final_impl(
        {"review_required": True, "review_reason": "能力报无异常，但图像可见网状荧光显示", "summary": "疑似漏检"},
        state,
        _ctx(),
    )
    assert side["submitted"] is True
    payload = side["submit_payload"]
    assert payload["review_required"] is True
    assert payload["review_reason"] == "能力报无异常，但图像可见网状荧光显示"
    assert "复核请求" in content  # 回执明确告知将以待复核终态收场


# --------------------------------------------------------------------------
# exit_gate
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_exit_gate_refusal_converges_to_fail_path():
    state = _base_state(submit_payload={"refused": True, "reason": "非检测图像"})
    out = await exit_gate_node(state, _ctx())
    assert out["error"]["code"] == int(BizCode.SCENARIO_NOT_SUPPORTED)
    assert "拒检" in out["error"]["message"] and "质检助手" in out["error"]["message"]


@pytest.mark.asyncio
async def test_exit_gate_requires_complete_terminal():
    state = _base_state(submit_payload={"refused": False, "defects": []}, capability_calls=0)
    out = await exit_gate_node(state, _ctx())
    assert "不完备" in out["error"]["message"]


@pytest.mark.asyncio
async def test_exit_gate_prepares_grade_hint_and_analysis():
    defects = [
        {"defect_type": "crack", "severity": "high", "confidence": 0.9},
        {"defect_type": "pore", "severity": "low", "confidence": 0.55},
    ]
    state = _base_state(
        submit_payload={"refused": False, "defects": defects, "summary": "两处缺陷", "analysis": "### 分析\n高风险"},
        grade_hint={"family": "defect", "status": "abnormal"},
        capability_calls=1,
    )
    out = await exit_gate_node(state, _ctx())
    assert out["standardized"] == defects
    assert out["grade_hint"]["max_severity"] == "high"
    assert out["grade_hint"]["min_confidence"] == 0.55
    assert out["metric_explanations"].startswith("### 分析")


@pytest.mark.asyncio
async def test_exit_gate_passes_through_agent_review_request():
    """共识：异议信号从提交载荷透传进 grade_hint，供判级终裁消费。"""
    state = _base_state(
        submit_payload={
            "refused": False,
            "defects": [],
            "summary": "疑似漏检",
            "review_required": True,
            "review_reason": "能力报无异常，但图像可见缺陷显示",
        },
        grade_hint={"family": "defect", "status": "normal"},
        capability_calls=1,
    )
    out = await exit_gate_node(state, _ctx())
    assert out["grade_hint"]["review_required"] is True
    assert out["grade_hint"]["review_reason"] == "能力报无异常，但图像可见缺陷显示"


@pytest.mark.asyncio
async def test_exit_gate_defaults_review_required_false():
    state = _base_state(
        submit_payload={"refused": False, "defects": []},
        grade_hint={"family": "defect", "status": "normal"},
        capability_calls=1,
    )
    out = await exit_gate_node(state, _ctx())
    assert out["grade_hint"]["review_required"] is False
    assert out["grade_hint"]["review_reason"] == ""


@pytest.mark.asyncio
async def test_agent_request_security_chain_interrupts_for_review():
    """图级 E2E：异议提交 → exit_gate 透传 → grading 恒判 review → human_review 暂停。

    共识（异议机制）的端到端不变量：智能体请求复核时，安检区必须把任务
    停在人工复核门（interrupt），而不是沿用能力的 normal 结论自动放行。
    """
    from typing import TypedDict

    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.graph import END, START, StateGraph

    from app.agents.graph import route_after_grading
    from app.agents.nodes.grading import grading_node
    from app.agents.nodes.human_review import human_review_node

    class _SecurityChainState(TypedDict, total=False):
        task_id: str
        submit_payload: dict[str, Any]
        grade_hint: dict[str, Any]
        capability_calls: int
        standardized: list
        grade: dict[str, Any]
        review: dict[str, Any]
        error: dict[str, Any]

    ctx = AgentContext()  # settings=None → 判级用内置默认规则

    async def _eg(state, _config=None):
        return await exit_gate_node(state, ctx)

    async def _gr(state, _config=None):
        return await grading_node(state, ctx)

    async def _hr(state, _config=None):
        return await human_review_node(state, ctx)

    g = StateGraph(_SecurityChainState)
    g.add_node("exit_gate", _eg)
    g.add_node("grading", _gr)
    g.add_node("human_review", _hr)
    g.add_edge(START, "exit_gate")
    g.add_edge("exit_gate", "grading")
    g.add_conditional_edges(
        "grading",
        route_after_grading,
        {"error": END, "review": "human_review", "report": END},
    )
    g.add_edge("human_review", END)
    graph = g.compile(checkpointer=MemorySaver())

    state = {
        "task_id": "T-objection",
        "submit_payload": {
            "refused": False,
            "defects": [],
            "summary": "疑似漏检",
            "review_required": True,
            "review_reason": "能力报无异常，但图像可见网状荧光显示",
        },
        "grade_hint": {"family": "defect", "status": "normal"},
        "capability_calls": 1,
    }
    final = None
    interrupted = False
    async for event in graph.astream(
        state, config={"configurable": {"thread_id": "obj-1"}}, stream_mode="values"
    ):
        if isinstance(event, dict) and "__interrupt__" in event:
            interrupted = True
            break
        final = event

    assert interrupted, "异议提交应在人工复核门暂停图执行"
    assert final["grade"]["level"] == "review"
    assert "agent_request" in final["grade"]["triggered_rules"]
    assert "网状荧光显示" in final["grade"]["summary"]


# --------------------------------------------------------------------------
# bootstrap
# --------------------------------------------------------------------------
def _write_png(tmp_path) -> str:
    """最小可读取的 .png（_build_image_blocks 只做 base64，不校验魔数）。"""
    p = tmp_path / "input.png"
    p.write_bytes(b"\x89PNG\r\n\x1a\n" + b"fake-payload")
    return str(p)


@pytest.mark.asyncio
async def test_bootstrap_requires_readable_images(tmp_path):
    ctx = _ctx()
    ctx.file_repo = _FileRepo(real_path=_write_png(tmp_path))
    state = _base_state(input_uris=[])
    out = await bootstrap_agent_node(state, ctx)
    assert out["error"]["code"] == int(BizCode.INVALID_REQUEST)


@pytest.mark.asyncio
async def test_bootstrap_requires_model(tmp_path):
    ctx = _ctx()
    ctx.file_repo = _FileRepo(real_path=_write_png(tmp_path))
    ctx.llm = None
    out = await bootstrap_agent_node(_base_state(), ctx)
    assert out["error"]["code"] == int(BizCode.LLM_UNAVAILABLE)


class _ReactLLMStub:
    """仅提供 react_model：bootstrap 只需要它判定模型可用性并选内容块格式。"""

    class _Model:  # 非 ChatAnthropic -> 走 OpenAI image_url 块
        pass

    react_model = _Model()


@pytest.mark.asyncio
async def test_bootstrap_clears_stale_messages_and_resets_budget(tmp_path):
    stale = [HumanMessage(content="上一轮残留", id="m-old-1")]
    ctx = _ctx()
    ctx.file_repo = _FileRepo(real_path=_write_png(tmp_path))
    ctx.llm = _ReactLLMStub()
    state = _base_state(messages=stale, loop_count=7, capability_calls=2, submitted=True)
    out = await bootstrap_agent_node(state, ctx)

    assert out["loop_count"] == 0
    assert out["capability_calls"] == 0
    assert out["submitted"] is False
    # 残留消息以 RemoveMessage 清除，随后是 system + human
    msgs = out["messages"]
    assert msgs[0].id == "m-old-1" and msgs[0].type == "remove"
    assert msgs[1].type == "system" and msgs[2].type == "human"
    # 系统提示词渲染了场景清单与预算
    assert "fracture" in msgs[1].content and "10" in msgs[1].content


# --------------------------------------------------------------------------
# bootstrap：Studio chat 上传图片兜底（消息 base64 块 → 落盘 → input_uris）
# --------------------------------------------------------------------------
_PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"fake-payload"


class _StudioFileRepo:
    """捕获 save_bytes（Studio 图片落盘）；可注入真实路径供落盘侧读取。"""

    def __init__(self, real_path: str | None = None):
        self._real = real_path
        self.saved: list[tuple[str, str, bytes, str]] = []

    def save_bytes(self, task_id: str, name: str, content: bytes, kind: str = "inputs") -> str:
        self.saved.append((task_id, name, content, kind))
        return f"file:///data/{task_id}/{kind}/{name}"

    def uri_to_path(self, uri: str) -> Path:
        if self._real is not None:
            return Path(self._real)
        raise ValueError("no real file")


def _png_data_url() -> str:
    return "data:image/png;base64," + base64.b64encode(_PNG_BYTES).decode()


@pytest.mark.asyncio
async def test_bootstrap_recovers_openai_style_chat_image():
    """Studio chat 上传（image_url data URL）：落盘转 URI 后正常启动。"""
    msg = HumanMessage(content=[
        {"type": "text", "text": "检测这张图"},
        {"type": "image_url", "image_url": {"url": _png_data_url()}},
    ])
    ctx = _ctx()
    ctx.file_repo = _StudioFileRepo()
    ctx.llm = _ReactLLMStub()
    state = _base_state(input_uris=[], messages=[msg])

    out = await bootstrap_agent_node(state, ctx)

    assert out.get("error") is None
    assert out["input_uris"] == ["file:///data/_studio/inputs/studio_1.png"]
    assert ctx.file_repo.saved == [("_studio", "studio_1.png", _PNG_BYTES, "inputs")]


@pytest.mark.asyncio
async def test_bootstrap_recovers_anthropic_style_chat_image():
    """anthropic base64 source 块形态同样兜底。"""
    msg = HumanMessage(content=[
        {"type": "image", "source": {
            "type": "base64", "media_type": "image/png",
            "data": base64.b64encode(_PNG_BYTES).decode(),
        }},
    ])
    ctx = _ctx()
    ctx.file_repo = _StudioFileRepo()
    ctx.llm = _ReactLLMStub()
    state = _base_state(input_uris=[], messages=[msg])

    out = await bootstrap_agent_node(state, ctx)

    assert out.get("error") is None
    assert len(ctx.file_repo.saved) == 1


@pytest.mark.asyncio
async def test_bootstrap_recovers_canonical_media_block():
    """LangChain 规范媒体块（langgraph-api 同款）：type=image + base64 + mime_type。"""
    msg = HumanMessage(content=[
        {"type": "image", "base64": base64.b64encode(_PNG_BYTES).decode(),
         "mime_type": "image/png"},
    ])
    ctx = _ctx()
    ctx.file_repo = _StudioFileRepo()
    ctx.llm = _ReactLLMStub()
    state = _base_state(input_uris=[], messages=[msg])

    out = await bootstrap_agent_node(state, ctx)

    assert out.get("error") is None
    assert ctx.file_repo.saved[0][2] == _PNG_BYTES


@pytest.mark.asyncio
async def test_bootstrap_recovers_media_block_without_mime_by_sniffing():
    """规范块缺 mime 时按魔数嗅探（PNG 头）。"""
    msg = HumanMessage(content=[
        {"type": "image", "data": base64.b64encode(_PNG_BYTES).decode()},
    ])
    ctx = _ctx()
    ctx.file_repo = _StudioFileRepo()
    ctx.llm = _ReactLLMStub()
    state = _base_state(input_uris=[], messages=[msg])

    out = await bootstrap_agent_node(state, ctx)

    assert out.get("error") is None
    assert ctx.file_repo.saved[0][1] == "studio_1.png"  # 嗅探出 png 扩展名


@pytest.mark.asyncio
async def test_bootstrap_recovers_image_url_string_shorthand_and_url_key():
    """image_url 字符串简写 / image 块 url=data URL 两种变体。"""
    data_url = _png_data_url()
    msg = HumanMessage(content=[
        {"type": "image_url", "image_url": data_url},
        {"type": "image", "url": data_url},
    ])
    ctx = _ctx()
    ctx.file_repo = _StudioFileRepo()
    ctx.llm = _ReactLLMStub()
    state = _base_state(input_uris=[], messages=[msg])

    out = await bootstrap_agent_node(state, ctx)

    assert out.get("error") is None
    assert len(ctx.file_repo.saved) == 2
    assert out["input_uris"] == [
        "file:///data/_studio/inputs/studio_1.png",
        "file:///data/_studio/inputs/studio_2.png",
    ]


@pytest.mark.asyncio
async def test_bootstrap_rejects_non_whitelisted_chat_image():
    """白名单外格式（与 _load_input_images 对齐：仅 jpg/png）仍拒绝。"""
    msg = HumanMessage(content=[
        {"type": "image_url", "image_url": {"url": "data:image/webp;base64,QUJD"}},
    ])
    ctx = _ctx()
    ctx.file_repo = _StudioFileRepo()
    state = _base_state(input_uris=[], messages=[msg])

    out = await bootstrap_agent_node(state, ctx)

    assert out["error"]["code"] == int(BizCode.INVALID_REQUEST)
    assert ctx.file_repo.saved == []


@pytest.mark.asyncio
async def test_bootstrap_prefers_disk_uris_over_message_blocks(tmp_path):
    """落盘侧有可读图片时不走兜底（主应用链路不受影响、不重复落盘）。"""
    msg = HumanMessage(content=[
        {"type": "image_url", "image_url": {"url": _png_data_url()}},
    ])
    ctx = _ctx()
    ctx.file_repo = _StudioFileRepo(real_path=_write_png(tmp_path))
    ctx.llm = _ReactLLMStub()
    state = _base_state(messages=[msg])  # 默认带 input_uris

    out = await bootstrap_agent_node(state, ctx)

    assert out.get("error") is None
    assert out["input_uris"] == state["input_uris"]  # 原样保留
    assert ctx.file_repo.saved == []  # 未触发兜底落盘


@pytest.mark.asyncio
async def test_bootstrap_studio_image_cap(monkeypatch):
    """数量上限：超出 cap 的图片不落盘。"""
    from app.agents.react import nodes as react_nodes

    monkeypatch.setattr(react_nodes, "_STUDIO_IMAGE_CAP", 1)
    blocks = [{"type": "image_url", "image_url": {"url": _png_data_url()}} for _ in range(3)]
    ctx = _ctx()
    ctx.file_repo = _StudioFileRepo()
    ctx.llm = _ReactLLMStub()
    state = _base_state(input_uris=[], messages=[HumanMessage(content=blocks)])

    out = await bootstrap_agent_node(state, ctx)

    assert out.get("error") is None
    assert len(ctx.file_repo.saved) == 1


def test_iter_message_image_blocks_skips_malformed():
    """畸形/不支持的块静默跳过，合法 jpeg 块正常提取。"""
    from app.agents.react.nodes import _iter_message_image_blocks

    jpeg_url = "data:image/jpeg;base64," + base64.b64encode(b"\xff\xd8fake").decode()
    messages = [
        HumanMessage(content="纯文本消息"),  # 非 list 内容
        HumanMessage(content=[
            {"type": "image_url", "image_url": {"url": "https://x/img.png"}},  # 非 data URL
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,!!bad!!"}},
            {"type": "image", "source": {"type": "http"}},  # 非 base64 source
            {"type": "text", "text": "无关块"},
            {"type": "image_url", "image_url": {"url": jpeg_url}},
        ]),
    ]

    found = list(_iter_message_image_blocks(messages))

    assert found == [("image/jpeg", b"\xff\xd8fake")]


# --------------------------------------------------------------------------
# bootstrap：Studio graph 视图兜底（input_uris 填本地路径 → 拷贝落盘）
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_bootstrap_materializes_local_path(tmp_path):
    """graph 视图填本地绝对路径：拷贝落盘转业务 URI 后正常启动。"""
    img = tmp_path / "sample.jpg"
    img.write_bytes(b"\xff\xd8fake-jpeg")
    ctx = _ctx()
    ctx.file_repo = _StudioFileRepo()
    ctx.llm = _ReactLLMStub()
    state = _base_state(input_uris=[str(img)])

    out = await bootstrap_agent_node(state, ctx)

    assert out.get("error") is None
    assert out["input_uris"] == ["file:///data/_studio/inputs/sample.jpg"]
    assert ctx.file_repo.saved == [("_studio", "sample.jpg", b"\xff\xd8fake-jpeg", "inputs")]


@pytest.mark.asyncio
async def test_bootstrap_materializes_file_url(tmp_path):
    """file:// URL 形态同样接受。"""
    img = tmp_path / "shot.png"
    img.write_bytes(_PNG_BYTES)
    ctx = _ctx()
    ctx.file_repo = _StudioFileRepo()
    ctx.llm = _ReactLLMStub()
    state = _base_state(input_uris=[f"file://{img}"])

    out = await bootstrap_agent_node(state, ctx)

    assert out.get("error") is None
    assert len(ctx.file_repo.saved) == 1


@pytest.mark.asyncio
async def test_bootstrap_local_path_missing_or_bad_ext_rejected(tmp_path):
    """路径不存在/扩展名不在白名单：仍拒绝启动。"""
    txt = tmp_path / "note.txt"
    txt.write_text("not an image")
    ctx = _ctx()
    ctx.file_repo = _StudioFileRepo()
    state = _base_state(input_uris=[str(tmp_path / "nope.jpg"), str(txt)])

    out = await bootstrap_agent_node(state, ctx)

    assert out["error"]["code"] == int(BizCode.INVALID_REQUEST)
    assert ctx.file_repo.saved == []


# --------------------------------------------------------------------------
# task_service 三轨选图（single=固定流水线 / agent=单场景 ReAct / joint=联合 ReAct）
# --------------------------------------------------------------------------
def test_graph_for_selects_track_by_mode():
    from app.services.task_service import TaskService

    class _Repo:  # __init__ 只需要 repo 占位
        def set_step_listener(self, listener):  # 优化轮 D2：步骤事件旁路挂载点
            self._step_listener = listener

    ctx = AgentContext(settings=_Settings())
    svc = TaskService(ctx, _Repo(), checkpointer=None)
    assert svc._graph_for("agent") is svc._react_graph
    assert svc._graph_for("single") is svc._graph
    assert svc._graph_for(None) is svc._graph
    # 决策 8/10：联合层全自主——joint 走专属联合 ReAct 图，不再回落固定流水线
    assert svc._graph_for("joint") is svc._joint_graph
    assert svc._joint_graph is not svc._react_graph


# --------------------------------------------------------------------------
# ReAct 思考链发射（共识轮：对话内显示模型思考链）
# --------------------------------------------------------------------------
class _ChainSpyRepo:
    def __init__(self):
        self.steps: list[tuple[str, str, str]] = []

    async def append_step(self, task_id, node, message):
        self.steps.append((task_id, node, message))

    def chain(self) -> list[dict]:
        import json as _json
        return [_json.loads(m) for (t, n, m) in self.steps if n == "chain"]


class _ScriptedReplyModel:
    def __init__(self, reply: AIMessage):
        self._reply = reply

    def bind_tools(self, tools):
        return self

    async def ainvoke(self, messages):
        return self._reply


@pytest.mark.asyncio
async def test_react_agent_emits_thought_and_tool_call_chain():
    """react_agent 轮：先发思考行、再发工具调用行（running 态），全量不截断。"""
    from app.agents.react.nodes import _build_agent_node
    from app.agents.react.tools import ReactToolbox

    repo = _ChainSpyRepo()
    ctx = _ctx()
    ctx.task_repo = repo
    ctx.llm = type("L", (), {"react_model": _ScriptedReplyModel(AIMessage(
        content=[
            {"type": "text", "text": "图像呈荧光网状裂纹，先调用检测能力核实。"},
            {"type": "tool_use", "id": "toolu_x", "name": "invoke_capability",
             "input": {"scene": "fpi"}},
        ],
        tool_calls=[{"name": "invoke_capability", "args": {"scene": "fpi"}, "id": "toolu_x"}],
    ))})()

    node = _build_agent_node(ctx, ReactToolbox())
    state = _base_state(messages=[], loop_count=0)
    await node(state, ctx)

    chain = repo.chain()
    assert len(chain) == 2  # thought 先于 tool_call
    assert chain[0]["type"] == "thought" and "荧光网状裂纹" in chain[0]["text"]
    assert chain[1]["type"] == "tool_call" and chain[1]["name"] == "invoke_capability"
    assert chain[1]["call_id"] == "toolu_x" and chain[1]["args"] == {"scene": "fpi"}
    # tool_use 块不得混入思考文本（_text_of 污染回归）
    assert "toolu_x" not in chain[0]["text"] and "{" not in chain[0]["text"]


@pytest.mark.asyncio
async def test_react_agent_thought_only_turn_emits_thought_row():
    """无工具调用的纯文本轮：只发思考行。"""
    from app.agents.react.nodes import _build_agent_node
    from app.agents.react.tools import ReactToolbox

    repo = _ChainSpyRepo()
    ctx = _ctx()
    ctx.task_repo = repo
    ctx.llm = type("L", (), {"react_model": _ScriptedReplyModel(
        AIMessage(content="先说明观察结论。"))})()

    node = _build_agent_node(ctx, ReactToolbox())
    await node(_base_state(messages=[], loop_count=0), ctx)

    chain = repo.chain()
    assert len(chain) == 1 and chain[0]["type"] == "thought"


@pytest.mark.asyncio
async def test_react_tools_emits_result_chain_keyed_by_call_id():
    """react_tools：结果事件带 call_id（供原地更新），全量内容不截断。"""
    from app.agents.react.nodes import _build_tools_node
    from app.agents.react.tools import ReactToolbox

    repo = _ChainSpyRepo()
    ctx = _ctx()
    ctx.task_repo = repo
    # retrieve_knowledge 无 rag 工具时降级返回可读错误——正好验证 is_error 语义不影响留痕
    last_ai = AIMessage(content="", tool_calls=[
        {"name": "retrieve_knowledge", "args": {"query": "渗透检测判级"}, "id": "call_k1"},
    ])
    node = _build_tools_node(ctx, ReactToolbox())
    out = await node(_base_state(messages=[last_ai], loop_count=2), ctx)

    assert len(out["messages"]) == 1
    chain = repo.chain()
    assert len(chain) == 1
    result = chain[0]
    assert result["type"] == "tool_result" and result["call_id"] == "call_k1"
    assert result["name"] == "retrieve_knowledge" and result["turn"] == 2
    assert result["args"] == {"query": "渗透检测判级"}  # args 随结果重发（整行覆盖不丢 IN 段）
    assert isinstance(result["content"], str) and result["content"]


# --------------------------------------------------------------------------
# exit_gate 裁决溯源：手写条目打标 + 口径对照计数（报告图注数据源）
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_exit_gate_tags_handwritten_entry_as_agent_visual():
    """事故现场（2026-09-16 T48086…）：模型检出 3 处（BP×2、SP×1，均带
    bbox），智能体裁决时原样保留 + 手写 1 条视觉判断 crack（无 location）。
    手写条目必须打 source=agent_visual，模型条目不得误标。"""
    model_entries = [
        {
            "defect_type": "BP", "confidence": 0.756847, "severity": None,
            "location": {"type": "bbox", "bbox": [292.23, 107.88, 324.91, 136.13]},
        },
        {
            "defect_type": "SP", "confidence": 0.515624, "severity": None,
            "location": {"type": "bbox", "bbox": [291.75, 108.21, 325.38, 136.60]},
        },
        {
            "defect_type": "BP", "confidence": 0.3838, "severity": None,
            "location": {"type": "bbox", "bbox": [400.10, 300.20, 450.30, 360.40]},
        },
    ]
    submitted = [
        # 模型条目（agent 从 digest 复制：type 键宽形态 + 原样 location）
        {"type": "BP", "confidence": 0.756847, "location": {"type": "bbox", "bbox": [292.23, 107.88, 324.91, 136.13]}},
        {"defect_type": "SP", "confidence": 0.515624, "location": {"type": "bbox", "bbox": [291.75, 108.21, 325.38, 136.60]}},
        {"defect_type": "BP", "confidence": 0.3838, "location": {"type": "bbox", "bbox": [400.10, 300.20, 450.30, 360.40]}},
        # 手写视觉判断：无 bbox、无 id、类型不在模型集合
        {"defect_type": "crack", "description": "视觉判断：被检面右下弧形线状荧光迹痕"},
    ]
    state = _base_state(
        standardized=model_entries,
        submit_payload={"refused": False, "defects": submitted, "summary": "检出 4 处"},
        grade_hint={"family": "defect", "status": "abnormal"},
        capability_calls=1,
    )
    out = await exit_gate_node(state, _ctx())

    out_defects = out["standardized"]
    assert len(out_defects) == 4
    tagged = [e for e in out_defects if e.get("source") == "agent_visual"]
    assert len(tagged) == 1 and tagged[0]["defect_type"] == "crack"
    # 模型三条（含 type 键宽形态）不误标
    assert sum(1 for e in out_defects if e.get("source")) == 1
    # type 宽键条目原样保留（不被改写为 defect_type）
    assert out_defects[0].get("type") == "BP"
    # 口径对照计数入 grade_hint（报告图注数据源）
    assert out["grade_hint"]["model_defect_count"] == 3


@pytest.mark.asyncio
async def test_exit_gate_no_tag_when_defaulting_to_model_entries():
    """省略 defects（默认采用能力结果）：条目即模型条目，不打标；
    model_defect_count 与终裁数一致（前端不显示口径差异行）。"""
    model_entries = [{"defect_type": "BP", "defect_id": "D001",
                      "location": {"type": "bbox", "bbox": [1, 2, 3, 4]}}]
    state = _base_state(
        standardized=model_entries,
        submit_payload={"refused": False, "defects": None},
        grade_hint={"family": "defect", "status": "abnormal"},
        capability_calls=1,
    )
    out = await exit_gate_node(state, _ctx())
    assert out["standardized"] == model_entries
    assert all(not e.get("source") for e in out["standardized"])
    assert out["grade_hint"]["model_defect_count"] == 1


@pytest.mark.asyncio
async def test_exit_gate_matching_by_defect_id_and_kv_key():
    """身份键分层匹配：defect_id 命中（改类条目）、(类别,置信度) 命中
    （无 location 的原样复制）不误标；仅全新手写条目打标。"""
    model_entries = [
        {"defect_id": "D001", "defect_type": "BP", "confidence": 0.7},          # id 匹配
        {"defect_type": "SP", "confidence": 0.5},                               # kv 匹配（无 location）
    ]
    submitted = [
        {"defect_id": "D001", "defect_type": "pore", "confidence": 0.7},        # D001 改类 -> 模型条目
        {"defect_type": "SP", "confidence": 0.5},                               # kv 命中 -> 模型条目
        {"defect_type": "crack", "confidence": 0.9},                            # 手写 -> 打标
    ]
    state = _base_state(
        standardized=model_entries,
        submit_payload={"refused": False, "defects": submitted},
        grade_hint={"family": "defect", "status": "abnormal"},
        capability_calls=1,
    )
    out = await exit_gate_node(state, _ctx())
    sources = [e.get("source") for e in out["standardized"]]
    assert sources == [None, None, "agent_visual"]


@pytest.mark.asyncio
async def test_exit_gate_preserves_existing_source_marker():
    """已有 source 标记（人工 manual 等）不覆盖——溯源标记只增不改。"""
    submitted = [{"defect_type": "crack", "source": "manual"}]
    state = _base_state(
        standardized=[{"defect_type": "BP", "confidence": 0.9}],
        submit_payload={"refused": False, "defects": submitted},
        grade_hint={"family": "defect", "status": "abnormal"},
        capability_calls=1,
    )
    out = await exit_gate_node(state, _ctx())
    assert out["standardized"][0]["source"] == "manual"


@pytest.mark.asyncio
async def test_exit_gate_no_tag_when_model_side_has_no_entries():
    """模型侧无条目（纯 classifications/指标族）：身份键无处可对，
    不打标避免误伤整表。"""
    submitted = [{"label": "解理", "score": 0.9}]
    state = _base_state(
        standardized=[],
        submit_payload={"refused": False, "defects": submitted},
        grade_hint={"family": "classification", "status": "abnormal"},
        capability_calls=1,
    )
    out = await exit_gate_node(state, _ctx())
    assert out["standardized"][0].get("source") is None


@pytest.mark.asyncio
async def test_exit_gate_backfills_image_name_for_multiimage_entries():
    """事故现场（2026-09-17 T11415637844088391）：多图 fan-out 检出 7 条带
    image_name，智能体 submit_final 复制条目时丢掉 image_name → 前端按图
    过滤失效，所有 bbox 叠到每张图上。出口安检必须按身份键回填图名。"""
    model_entries = [
        {"defect_type": "BP", "confidence": 0.75, "image_name": "a.jpg",
         "location": {"type": "bbox", "bbox": [1, 2, 3, 4]}},
        {"defect_type": "BP", "confidence": 0.38, "image_name": "a.jpg",
         "location": {"type": "bbox", "bbox": [5, 6, 7, 8]}},
        {"defect_type": "BP", "confidence": 0.48, "image_name": "b.jpg",
         "location": {"type": "bbox", "bbox": [9, 10, 11, 12]}},
    ]
    submitted = [
        # agent 复制条目（bbox 原样），丢 image_name
        {"defect_type": "BP", "confidence": 0.75, "location": {"type": "bbox", "bbox": [1, 2, 3, 4]}},
        {"defect_type": "BP", "confidence": 0.38, "location": {"type": "bbox", "bbox": [5, 6, 7, 8]}},
        {"defect_type": "BP", "confidence": 0.48, "location": {"type": "bbox", "bbox": [9, 10, 11, 12]}},
        # 手写视觉条目：无匹配，保持无图名（前端全图可见是合理降级）
        {"defect_type": "crack", "description": "视觉判断", "location": {"type": "bbox", "bbox": [20, 20, 30, 30]}},
    ]
    state = _base_state(
        standardized=model_entries,
        submit_payload={"refused": False, "defects": submitted, "summary": "检出 4 处"},
        grade_hint={"family": "defect", "status": "abnormal"},
        capability_calls=1,
    )
    out = await exit_gate_node(state, _ctx())

    defects = out["standardized"]
    assert defects[0]["image_name"] == "a.jpg"
    assert defects[1]["image_name"] == "a.jpg"
    assert defects[2]["image_name"] == "b.jpg"
    # 手写条目不回填（且被 agent_visual 打标）
    assert "image_name" not in defects[3]
    assert defects[3]["source"] == "agent_visual"


@pytest.mark.asyncio
async def test_exit_gate_no_backfill_when_model_entries_untagged():
    """模型条目本身无 image_name（单图任务/存量）：回填为无操作，
    条目原样透传，不引入 image_name 空串等脏字段。"""
    model_entries = [
        {"defect_type": "BP", "confidence": 0.75,
         "location": {"type": "bbox", "bbox": [1, 2, 3, 4]}},
    ]
    submitted = [
        {"defect_type": "BP", "confidence": 0.75, "location": {"type": "bbox", "bbox": [1, 2, 3, 4]}},
    ]
    state = _base_state(
        standardized=model_entries,
        submit_payload={"refused": False, "defects": submitted, "summary": "检出 1 处"},
        grade_hint={"family": "defect", "status": "abnormal"},
        capability_calls=1,
    )
    out = await exit_gate_node(state, _ctx())
    assert "image_name" not in out["standardized"][0]


@pytest.mark.asyncio
async def test_exit_gate_backfill_neighborhood_for_adjusted_bbox():
    """智能体微调坐标（改写小数位）时身份键失配——邻域回退按中心点
    距离归属到最近的模型框，继承其 image_name。"""
    model_entries = [
        {"defect_type": "BP", "confidence": 0.75, "image_name": "a.jpg",
         "location": {"type": "bbox", "bbox": [100.0, 100.0, 200.0, 200.0]}},
        {"defect_type": "BP", "confidence": 0.5, "image_name": "b.jpg",
         "location": {"type": "bbox", "bbox": [500.0, 500.0, 600.0, 600.0]}},
    ]
    submitted = [
        # 智能体微调了坐标（±2px）：身份键失配
        {"defect_type": "BP", "confidence": 0.75,
         "location": {"type": "bbox", "bbox": [101.5, 98.0, 202.0, 201.0]}},
        # 远离任何模型框的手写条目：不回填
        {"defect_type": "crack", "description": "视觉判断",
         "location": {"type": "bbox", "bbox": [300.0, 300.0, 350.0, 350.0]}},
    ]
    state = _base_state(
        standardized=model_entries,
        submit_payload={"refused": False, "defects": submitted, "summary": "检出 2 处"},
        grade_hint={"family": "defect", "status": "abnormal"},
        capability_calls=1,
    )
    out = await exit_gate_node(state, _ctx())

    defects = out["standardized"]
    # 微调条目回填到 a.jpg（中心距离 ~3.25 < 框尺寸 100）
    assert defects[0]["image_name"] == "a.jpg"
    # 远端手写条目不回填（且打 agent_visual）
    assert "image_name" not in defects[1]
    assert defects[1]["source"] == "agent_visual"
