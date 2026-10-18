"""会话轨（质检助手）回归：路由契约 / 工具 / 节点 / 服务编排。

覆盖决策 14/15/16 的关键不变量：
- 路由函数返回映射键（tools/end），不返回节点名；
- launch_inspection：场景白名单、无图拦截、任务卡片落库、非阻塞执行（mode=agent）；
- get_task_detail：任务快照序列化与未知任务可读错误；
- 会话节点：无模型/预算耗尽兜底文本（永远有回复）；
- SessionService：用户消息落库、路由低置信/场景外→场景确认卡片（优化轮 D4/D5）、
  任务落定事件回注唤醒。
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.agents.session.graph import _route_after_agent
from app.agents.session.nodes import build_session_agent_node, build_session_tools_node
from app.agents.session.tools import build_session_lc_tools, build_session_tool_impls
from app.models.capability import CapabilityManifest
from app.models.session import (
    KIND_CHAIN,
    KIND_GUIDANCE,
    KIND_ROUTING,
    KIND_REVIEW_CARD,
    ROLE_ASSISTANT,
    ROLE_EVENT,
    ROLE_USER,
)
from app.repositories.session_repo import SessionRepo
from app.services.deps import AgentContext
from app.services.session_service import SessionService


# --------------------------------------------------------------------------
# 桩件
# --------------------------------------------------------------------------
class _Registry:
    def list(self):
        return [
            CapabilityManifest(
                capability_id=f"{s}_v1", tool_name=f"{s}_tool", scene=s,
                capability_type="defect_detection",
            )
            for s in ("fracture", "borescope", "infrared")
        ]


class _FileRepo:
    def save_bytes(self, bucket: str, name: str, content: bytes, kind: str = "inputs") -> str:
        return f"/{kind}/{bucket}/{name}"

    def uri_to_path(self, uri: str) -> Path:
        return Path("/tmp") / uri.lstrip("/")


class _TaskRecord:
    def __init__(self, task_id: str, status="completed", grade=None, error=None):
        self.id = task_id
        self.status = status
        self.scene = "fracture"
        self.mode = "agent"
        self.grade_json = json.dumps(grade) if grade else None
        self.error_json = json.dumps(error) if error else None
        self.standardized_json = json.dumps(
            [{"defect_type": "cleavage", "severity": "high", "confidence": 0.8}]
        )
        self.created_at = None


class _TaskRepo:
    def __init__(self):
        self.records: dict[str, _TaskRecord] = {}

    async def get(self, task_id: str):
        return self.records.get(task_id)

    async def get_by_ids(self, task_ids: list[str]):
        return [self.records[t] for t in task_ids if t in self.records]

    async def list_tasks(self, *, scene=None, status=None, limit=20):
        rows = [r for r in self.records.values() if status is None or r.status == status]
        return rows[:limit]


class _TaskService:
    def __init__(self):
        self.prepared: list[dict] = []
        self.run_calls: list[str] = []

    async def prepare(self, **kwargs):
        self.prepared.append(kwargs)
        return {"task_id": f"TFAKE{len(self.prepared):04d}", "state": {"mode": kwargs.get("mode")}}

    async def run_task(self, task_id: str, state: dict) -> None:
        self.run_calls.append(task_id)


class _LLMStub:
    def __init__(self, routing: dict | None = None):
        self.react_model = None
        self._routing = routing

    async def route_session_image(self, images, scenes):
        return self._routing or {"category": "fracture", "confidence": 0.9, "evidence": [], "reasoning": "r"}


class _RecordingLLM(_LLMStub):
    """记录 VLM 路由调用次数：验证显式指定/关键词命中时确实跳过。"""

    def __init__(self, routing: dict | None = None):
        super().__init__(routing)
        self.route_calls = 0

    async def route_session_image(self, images, scenes):
        self.route_calls += 1
        return await super().route_session_image(images, scenes)


class _Settings:
    session_max_turns = 3


def _ctx(**overrides) -> AgentContext:
    base = dict(
        registry=_Registry(),
        file_repo=_FileRepo(),
        task_repo=_TaskRepo(),
        llm=_LLMStub(),
        settings=_Settings(),
    )
    base.update(overrides)
    return AgentContext(**base)


class _FakeGraph:
    """伪会话图：记录输入，返回预设的最终回复。"""

    def __init__(self, reply: str = "已为您处理。"):
        self.reply = reply
        self.inputs: list[dict] = []

    async def astream(self, input_state, config=None, stream_mode=None):
        self.inputs.append(input_state)
        yield {"session_agent": {"messages": [AIMessage(content=self.reply)]}}

    async def aget_state(self, config):
        class _Snap:
            pass

        snap = _Snap()
        snap.values = {"messages": [AIMessage(content=self.reply)]}
        return snap


@pytest_asyncio.fixture
async def repo(session_factory) -> SessionRepo:
    return SessionRepo(session_factory)


# --------------------------------------------------------------------------
# 路由契约
# --------------------------------------------------------------------------
def test_route_after_agent_returns_mapping_keys():
    assert _route_after_agent({"messages": []}) == "end"
    assert (
        _route_after_agent({"messages": [AIMessage(content="", tool_calls=[{"name": "x", "args": {}, "id": "1"}])]})
        == "tools"
    )
    assert _route_after_agent({"messages": [AIMessage(content="纯文本")]}) == "end"


# --------------------------------------------------------------------------
# 工具 impl
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_launch_inspection_requires_image(repo, session_factory):
    ctx = _ctx()
    impls = build_session_tool_impls(ctx, repo, _TaskService())
    await repo.create("S1", None)
    content = await impls["launch_inspection"]({"scene": "fracture"}, {"session_id": "S1"})
    assert "没有可送检的图片" in content


@pytest.mark.asyncio
async def test_launch_inspection_rejects_unknown_scene(repo):
    ctx = _ctx()
    impls = build_session_tool_impls(ctx, repo, _TaskService())
    await repo.create("S1", None)
    content = await impls["launch_inspection"]({"scene": "not_a_scene"}, {"session_id": "S1"})
    assert "不受支持" in content


@pytest.mark.asyncio
async def test_launch_inspection_happy_path(repo):
    ctx = _ctx()
    task_svc = _TaskService()
    impls = build_session_tool_impls(ctx, repo, task_svc)
    await repo.create("S1", None)
    await repo.add_message(
        "S1", role=ROLE_USER, kind="image", content="检测",
        images=[{"uri": "/inputs/_upload/a.jpg", "name": "a.jpg"}],
    )

    content = await impls["launch_inspection"]({"scene": "fracture", "note": "重点看裂纹"}, {"session_id": "S1"})
    payload = json.loads(content)
    assert payload["task_id"] == "TFAKE0001"
    assert payload["status"] == "pending"

    prepared = task_svc.prepared[0]
    assert prepared["mode"] == "agent"          # 会话派发恒走智能体轨（决策 16）
    assert prepared["scenes"] == ["fracture"]   # 场景判定一次并传递
    assert prepared["input_uris"] == ["/inputs/_upload/a.jpg"]

    # 任务卡片消息落库（先于助手最终回复）
    msgs = await repo.list_messages("S1")
    cards = [m for m in msgs if m.kind == "task_card"]
    assert len(cards) == 1 and cards[0].task_id == "TFAKE0001"

    # 非阻塞执行被调度（create_task 需让出控制权）
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert task_svc.run_calls == ["TFAKE0001"]


@pytest.mark.asyncio
async def test_get_task_detail_formats_record(repo):
    ctx = _ctx()
    ctx.task_repo.records["T1"] = _TaskRecord(
        "T1", status="completed", grade={"level": "fail", "summary": "高危", "triggered_rules": ["max_severity >= 3"]}
    )
    impls = build_session_tool_impls(ctx, repo, _TaskService())
    content = await impls["get_task_detail"]({"task_id": "T1"}, {})
    payload = json.loads(content)
    assert payload["status"] == "completed"
    assert payload["grade_level"] == "fail"
    assert payload["defects"][0]["defect_type"] == "cleavage"

    missing = await impls["get_task_detail"]({"task_id": "T404"}, {})
    assert "未找到" in missing


# --------------------------------------------------------------------------
# generate_ppt 会话工具（文档生成：主题→卡片）
# --------------------------------------------------------------------------
class _DocTaskRecord:
    def __init__(self, task_id: str) -> None:
        self.id = task_id
        self.status = "pending"
        self.scene = None
        self.mode = "document"
        self.report_json = None
        self.error_json = None
        self.created_at = None


class _DocTaskRepo:
    """仅支持 document 任务的建/读桩件（generate_ppt 依赖 create/get）。"""

    def __init__(self) -> None:
        self.records: dict[str, _DocTaskRecord] = {}

    async def create(self, task_id, *, scene, mode, inputs=None, trace_id=None):
        self.records[task_id] = _DocTaskRecord(task_id)

    async def get(self, task_id):
        return self.records.get(task_id)


class _FakePptService:
    def __init__(self, task_repo, *, engine="template", slide_count=5, fail=False):
        self._repo = task_repo
        self.engine = engine
        self.slide_count = slide_count
        self.fail = fail
        self.calls: list[tuple] = []

    async def run(self, task_id, params, materials, template_bytes=None):
        self.calls.append((task_id, params, materials, template_bytes))
        rec = self._repo.records[task_id]
        if self.fail:
            rec.status = "failed"
            rec.error_json = json.dumps({"message": "渲染失败"})
        else:
            rec.status = "completed"
            rec.report_json = json.dumps({
                "uri": f"/api/v1/tasks/{task_id}/files/presentation.pptx?kind=artifacts",
                "filename": "presentation.pptx",
                "engine": self.engine,
                "slide_count": self.slide_count,
            })


class _FakeSessionRepo:
    """内存会话仓桩件：仅记录 add_message，供 generate_ppt 测试验卡（零 DB）。"""

    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []

    async def add_message(self, session_id, *, role, kind="text", content="", images=None,
                          task_id=None, message_id=None):
        self.messages.append(
            {"session_id": session_id, "role": role, "kind": kind, "content": content, "task_id": task_id}
        )
        return "MCFAKE"


@pytest.mark.asyncio
async def test_generate_ppt_requires_topic():
    doc_repo = _DocTaskRepo()
    ctx = _ctx(task_repo=doc_repo)
    ctx.ppt_service = _FakePptService(doc_repo)
    impls = build_session_tool_impls(ctx, _FakeSessionRepo(), _TaskService())
    content = await impls["generate_ppt"]({"topic": "  "}, {"session_id": "S1"})
    assert "必须提供演示主题" in content


@pytest.mark.asyncio
async def test_generate_ppt_service_unavailable():
    ctx = _ctx()  # ppt_service 默认 None
    impls = build_session_tool_impls(ctx, _FakeSessionRepo(), _TaskService())
    content = await impls["generate_ppt"]({"topic": "季度总结"}, {"session_id": "S1"})
    assert "尚未装配" in content


@pytest.mark.asyncio
async def test_generate_ppt_happy_path_persists_doc_card():
    doc_repo = _DocTaskRepo()
    srepo = _FakeSessionRepo()
    ctx = _ctx(task_repo=doc_repo)
    svc = _FakePptService(doc_repo, engine="llm", slide_count=6)
    ctx.ppt_service = svc
    impls = build_session_tool_impls(ctx, srepo, _TaskService())

    content = await impls["generate_ppt"](
        {"topic": "季度质检总结", "audience": "部门领导"}, {"session_id": "S1"},
    )
    payload = json.loads(content)
    assert payload["status"] == "completed"
    assert payload["slide_count"] == 6

    # 工具入参转为 document 任务 params，且无资料
    task_id, params, materials, template_bytes = svc.calls[0]
    assert params["topic"] == "季度质检总结"
    assert params["audience"] == "部门领导"
    assert materials == [] and template_bytes is None

    # 文档卡落库（与任务号关联）
    cards = [m for m in srepo.messages if m["kind"] == "doc_card"]
    assert len(cards) == 1 and cards[0]["task_id"] == task_id
    snap = json.loads(cards[0]["content"])
    assert snap["status"] == "completed"
    assert snap["engine"] == "llm"
    assert snap["artifact_uri"].endswith("presentation.pptx?kind=artifacts")


@pytest.mark.asyncio
async def test_generate_ppt_failure_pushes_failed_card():
    doc_repo = _DocTaskRepo()
    srepo = _FakeSessionRepo()
    ctx = _ctx(task_repo=doc_repo)
    ctx.ppt_service = _FakePptService(doc_repo, fail=True)
    impls = build_session_tool_impls(ctx, srepo, _TaskService())

    payload = json.loads(await impls["generate_ppt"]({"topic": "崩溃主题"}, {"session_id": "S1"}))
    assert payload["status"] == "failed"
    cards = [m for m in srepo.messages if m["kind"] == "doc_card"]
    snap = json.loads(cards[0]["content"])
    assert snap["status"] == "failed" and snap["error_message"] == "渲染失败"


# --------------------------------------------------------------------------
# 节点
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_agent_node_no_model_fallback():
    ctx = _ctx(llm=None)
    node = build_session_agent_node(ctx, build_session_lc_tools())
    out = await node({"session_id": "S1", "loop_count": 0, "messages": []})
    assert "模型服务暂不可用" in out["messages"][0].content


@pytest.mark.asyncio
async def test_agent_node_budget_exhausted_fallback():
    class _L:
        react_model = object()  # 预算分支在调用模型前短路，只需非 None

    ctx = _ctx(llm=_L())
    node = build_session_agent_node(ctx, build_session_lc_tools())
    out = await node({"session_id": "S1", "loop_count": 3, "messages": []})  # == max_turns
    assert "本轮处理步骤较多" in out["messages"][0].content


@pytest.mark.asyncio
async def test_tools_node_unknown_tool_and_exception_readable():
    async def _boom(args, state):
        raise RuntimeError("boom")

    node = build_session_tools_node({"get_task_detail": _boom})
    state = {
        "messages": [
            AIMessage(
                content="",
                tool_calls=[
                    {"name": "nope", "args": {}, "id": "c1"},
                    {"name": "get_task_detail", "args": {"task_id": "T1"}, "id": "c2"},
                ],
            )
        ]
    }
    out = await node(state)
    msgs = out["messages"]
    assert isinstance(msgs[0], ToolMessage) and "未知工具" in msgs[0].content
    assert "工具执行异常：boom" in msgs[1].content


# --------------------------------------------------------------------------
# 会话过程可视化（共识轮）：思考/工具调用落思考链行（kind=chain，原地更新）
# --------------------------------------------------------------------------
class _SpyRepo:
    def __init__(self):
        self.added: list[dict[str, Any]] = []
        self.updated: list[tuple[str, str, str]] = []

    async def add_message(self, session_id, *, role, kind, content, **kw):
        self.added.append({
            "session_id": session_id, "role": role, "kind": kind,
            "content": content, "task_id": kw.get("task_id"),
            "message_id": kw.get("message_id"),
        })
        return kw.get("message_id") or f"M{len(self.added)}"

    async def update_message(self, session_id, message_id, content, **kw):
        self.updated.append((session_id, message_id, content))
        return True


@pytest.mark.asyncio
async def test_tools_node_emits_chain_line_with_in_place_update():
    """会话工具调用落思考链行：先落 running 行，执行完原地更新为完成行（共识 Q3-B）。"""
    repo = _SpyRepo()

    async def _ok(args, state):
        return "已派发质检任务 T123（场景：fracture，图片 1 张）"

    node = build_session_tools_node({"launch_inspection": _ok}, repo=repo)
    state = {
        "session_id": "S1",
        "messages": [AIMessage(content="", tool_calls=[
            {"name": "launch_inspection", "args": {"scene": "fracture"}, "id": "c1"},
        ])],
    }
    await node(state)
    chains = [a for a in repo.added if a["kind"] == KIND_CHAIN]
    assert len(chains) == 1
    first = json.loads(chains[0]["content"])
    assert first["type"] == "tool_call" and first["name"] == "launch_inspection"
    assert first["done"] is False and first["args"] == {"scene": "fracture"}
    assert chains[0]["message_id"]  # 确定性 id：结果事件可定位
    # 原地更新为完成行：含结果、done=True
    assert len(repo.updated) == 1
    sid, mid, content = repo.updated[0]
    assert sid == "S1" and mid == chains[0]["message_id"]
    done = json.loads(content)
    assert done["done"] is True and "T123" in done["content"] and done["is_error"] is False


@pytest.mark.asyncio
async def test_tools_node_launch_chain_row_has_no_task_binding():
    """launch_inspection 行不挂任务归属：它先于任务卡落库，挂上任务 id 会被
    任务卡截断成冗余碎组；派发与任务的关联由紧随的任务卡表达（共识轮）。"""
    repo = _SpyRepo()

    async def _ok(args, state):
        return "已派发质检任务 T777（场景：fpi，图片 1 张）"

    node = build_session_tools_node({"launch_inspection": _ok}, repo=repo)
    state = {
        "session_id": "S1",
        "messages": [AIMessage(content="", tool_calls=[
            {"name": "launch_inspection", "args": {"scene": "fpi"}, "id": "c1"},
        ])],
    }
    await node(state)
    chains = [a for a in repo.added if a["kind"] == KIND_CHAIN]
    assert chains[0]["task_id"] is None
    # 完成行：含派发结果文本（原地更新）
    assert len(repo.updated) == 1
    done = json.loads(repo.updated[0][2])
    assert done["done"] is True and "T777" in done["content"]


@pytest.mark.asyncio
async def test_tools_node_get_task_detail_chain_row_is_session_scoped():
    repo = _SpyRepo()

    async def _ok(args, state):
        return "ok"

    node = build_session_tools_node({"get_task_detail": _ok}, repo=repo)
    state = {
        "session_id": "S1",
        "messages": [AIMessage(content="", tool_calls=[
            {"name": "get_task_detail", "args": {"task_id": "T888"}, "id": "c1"},
        ])],
    }
    await node(state)
    chains = [a for a in repo.added if a["kind"] == KIND_CHAIN]
    # 助手行动行：会话级轻渲染，不并入任务思考链组
    assert chains[0]["task_id"] is None


@pytest.mark.asyncio
async def test_message_ordering_preserves_subsecond(repo, session_factory):
    """微秒级时序回归（用户反馈：轨迹组碎裂）——同秒消息必须按真实插入次序返回。

    MySQL 曾把 datetime 落到秒级导致同秒次序不定；现列类型 DATETIME(6)，
    仓储排序 (created_at, id) 双键兜底。基座已是真 MySQL（决策 50 的 R2），这条直接在
    会出问题的方言上验证——DDL 里的精度退回秒级时它会红。
    """
    from datetime import datetime, timedelta, timezone

    from app.repositories.session_repo import SessionMessageRecord

    await repo.create("SORD", None)
    base = datetime.now(timezone.utc)
    async with session_factory() as session:
        for i in range(5):
            session.add(SessionMessageRecord(
                id=f"MORD{i:02d}",
                session_id="SORD",
                role="event",
                kind="activity",
                content=json.dumps({"node": "n", "message": f"step{i}"}),
                created_at=base + timedelta(microseconds=i * 137),
            ))
        await session.commit()
    rows = await repo.list_messages("SORD")
    assert [r.id for r in rows] == [f"MORD{i:02d}" for i in range(5)]


@pytest.mark.asyncio
async def test_tools_node_without_repo_still_works():
    async def _ok(args, state):
        return "ok"

    node = build_session_tools_node({"get_task_detail": _ok})  # repo=None 不崩
    state = {
        "session_id": "S1",
        "messages": [AIMessage(content="", tool_calls=[
            {"name": "get_task_detail", "args": {"task_id": "T1"}, "id": "c1"},
        ])],
    }
    out = await node(state)
    assert out["messages"][0].content == "ok"


@pytest.mark.asyncio
async def test_agent_node_emits_thought_chain_row():
    """模型一边调工具一边输出的思考文本落 Think 行（共识 Q2）。"""
    repo = _SpyRepo()
    ctx = _ctx()

    class _ThoughtModel:
        def bind_tools(self, tools):
            return self

        async def ainvoke(self, messages):
            return AIMessage(
                content="置信度足够，直接按荧光场景派发。",
                tool_calls=[{"name": "launch_inspection", "args": {"scene": "fpi"}, "id": "c1"}],
            )

    ctx.llm = type("L", (), {"react_model": _ThoughtModel()})()
    node = build_session_agent_node(ctx, build_session_lc_tools(), repo=repo)
    await node({"session_id": "S1", "messages": [HumanMessage(content="送检")], "loop_count": 0})
    chains = [a for a in repo.added if a["kind"] == KIND_CHAIN]
    assert len(chains) == 1
    payload = json.loads(chains[0]["content"])
    assert payload["type"] == "thought" and "荧光" in payload["text"]


@pytest.mark.asyncio
async def test_agent_node_no_thought_for_tool_use_only_content():
    """Anthropic 形态：content 只有 tool_use 块（无文本思考）时不得外显原始调用块。"""
    repo = _SpyRepo()
    ctx = _ctx()

    class _ToolUseOnlyModel:
        def bind_tools(self, tools):
            return self

        async def ainvoke(self, messages):
            return AIMessage(
                content=[{"type": "tool_use", "id": "toolu_1", "name": "launch_inspection",
                          "input": {"scene": "fpi"}}],
                tool_calls=[{"name": "launch_inspection", "args": {"scene": "fpi"}, "id": "c1"}],
            )

    ctx.llm = type("L", (), {"react_model": _ToolUseOnlyModel()})()
    node = build_session_agent_node(ctx, build_session_lc_tools(), repo=repo)
    await node({"session_id": "S1", "messages": [HumanMessage(content="送检")], "loop_count": 0})
    assert [a for a in repo.added if a["kind"] == KIND_CHAIN] == []


# --------------------------------------------------------------------------
# 会话图端到端（伪模型：派发 -> 文本收尾）
# --------------------------------------------------------------------------
class _ScriptedModel:
    def __init__(self, script: list[AIMessage]):
        self._script = list(script)

    def bind_tools(self, tools):
        return self

    async def ainvoke(self, messages):
        return self._script.pop(0)


@pytest.mark.asyncio
async def test_session_graph_end_to_end(repo):
    from langgraph.checkpoint.memory import MemorySaver
    from app.agents.session.graph import build_session_graph

    ctx = _ctx()
    task_svc = _TaskService()
    script = [
        AIMessage(content="", tool_calls=[{"name": "launch_inspection", "args": {"scene": "fracture"}, "id": "c1"}]),
        AIMessage(content="已为您派发质检任务。"),
    ]
    ctx.llm = type("L", (), {"react_model": _ScriptedModel(script), "route_session_image": None})()

    graph = build_session_graph(ctx, repo, task_svc, checkpointer=MemorySaver())
    await repo.create("SE2E", None)
    await repo.add_message(
        "SE2E", role=ROLE_USER, kind="image", content="",
        images=[{"uri": "/inputs/_upload/a.jpg", "name": "a.jpg"}],
    )

    async for _ in graph.astream(
        {"messages": [HumanMessage(content="帮我检测")], "session_id": "SE2E", "loop_count": 0},
        config={"configurable": {"thread_id": "SE2E"}},
        stream_mode="updates",
    ):
        pass

    msgs = await repo.list_messages("SE2E")
    assert any(m.kind == "task_card" for m in msgs)
    assert task_svc.run_calls == ["TFAKE0001"]
    # 会话智能体的工具调用落思考链行（共识轮）
    chains = [json.loads(m.content) for m in msgs if m.kind == KIND_CHAIN]
    assert any(c.get("name") == "launch_inspection" and c.get("done") for c in chains)
    # 派发行动行属会话级（不挂任务）：任务关联由任务卡表达，思考链组不被截断
    launch = [m for m in msgs if m.kind == KIND_CHAIN
              and json.loads(m.content).get("name") == "launch_inspection"]
    assert launch and launch[0].task_id is None


# --------------------------------------------------------------------------
# SessionService
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_send_message_persists_and_replies(repo):
    ctx = _ctx()
    svc = SessionService(ctx, repo, _TaskService(), checkpointer=None)
    svc._graph = _FakeGraph(reply="您好，已收到图片。")

    sid = (await svc.create_session("测试会话"))["session_id"]
    await svc.send_message(sid, "帮我看看这张图", [("a.jpg", b"\x89PNG fake")])
    for _ in range(20):
        await asyncio.sleep(0.01)
        msgs = await repo.list_messages(sid)
        if any(m.role == ROLE_ASSISTANT and m.kind != "task_card" for m in msgs):
            break
    msgs = await repo.list_messages(sid)
    user_msgs = [m for m in msgs if m.role == ROLE_USER]
    assert len(user_msgs) == 1 and user_msgs[0].images_json  # 图片引用落库
    reply = [m for m in msgs if m.role == ROLE_ASSISTANT][0]
    assert reply.content == "您好，已收到图片。"
    # 路由命中场景：回复形态为普通文本（非引导）
    assert reply.kind == "text"


@pytest.mark.asyncio
async def test_send_message_null_routing_emits_scene_card(repo):
    """优化轮 D4/D5：场景外（category=null）→ 确定性指纹卡片，不再走模型引导回合。"""
    ctx = _ctx(llm=_LLMStub(routing={"category": None, "confidence": 0.1, "evidence": [], "alternative": None, "reasoning": "噪点"}))
    svc = SessionService(ctx, repo, _TaskService(), checkpointer=None)
    svc._graph = _FakeGraph(reply="不应被调用")

    sid = (await svc.create_session(None))["session_id"]
    await svc.send_message(sid, "这是啥", [("n.jpg", b"\x89PNG fake")])
    for _ in range(20):
        await asyncio.sleep(0.01)
        msgs = await repo.list_messages(sid)
        if any(m.kind == "scene_card" for m in msgs):
            break

    msgs = await repo.list_messages(sid)
    cards = [m for m in msgs if m.kind == "scene_card"]
    assert len(cards) == 1 and cards[0].role == ROLE_ASSISTANT
    payload = json.loads(cards[0].content)
    assert payload["mode"] == "fingerprint"
    # 指纹卡给出全部候选场景供选择
    assert {c["scene"] for c in payload["candidates"]} == {"fracture", "borescope", "infrared"}
    assert payload["reasoning"] == "噪点"
    assert payload["image"] and payload["image"]["name"] == "n.jpg"
    # 卡片分支不进入智能体回合（图未被驱动）
    assert svc._graph.inputs == []
    # 不再产生 guidance 文本回合
    assert not any(m.kind == KIND_GUIDANCE for m in msgs)


@pytest.mark.asyncio
async def test_send_message_low_confidence_emits_candidates_card(repo):
    """优化轮 D5：有主候选但置信度<0.7 → 候选排序卡（主候选+次候选）。"""
    ctx = _ctx(llm=_LLMStub(routing={
        "category": "fracture", "confidence": 0.45, "evidence": [],
        "alternative": "metallography", "reasoning": "浮雕与晶界难辨",
    }))
    svc = SessionService(ctx, repo, _TaskService(), checkpointer=None)
    svc._graph = _FakeGraph(reply="不应被调用")

    sid = (await svc.create_session(None))["session_id"]
    await svc.send_message(sid, "帮我检测", [("x.jpg", b"\x89PNG fake")])
    for _ in range(20):
        await asyncio.sleep(0.01)
        msgs = await repo.list_messages(sid)
        if any(m.kind == "scene_card" for m in msgs):
            break

    cards = [m for m in await repo.list_messages(sid) if m.kind == "scene_card"]
    assert len(cards) == 1
    payload = json.loads(cards[0].content)
    assert payload["mode"] == "candidates"
    assert payload["candidates"][0] == {"scene": "fracture", "confidence": 0.45}
    assert payload["candidates"][1] == {"scene": "metallography", "confidence": None}
    assert svc._graph.inputs == []


@pytest.mark.asyncio
async def test_send_message_scene_field_skips_vlm(repo):
    """请求 scene 字段显式指定场景：直接采用并跳过 VLM 路由（灰度热部图误判修复）。"""
    llm = _RecordingLLM()
    ctx = _ctx(llm=llm)
    svc = SessionService(ctx, repo, _TaskService(), checkpointer=None)
    svc._graph = _FakeGraph(reply="已按热部场景派发。")

    sid = (await svc.create_session(None))["session_id"]
    await svc.send_message(sid, "分析异常情况", [("h.jpg", b"\x89PNG fake")], scene="infrared")
    for _ in range(20):
        await asyncio.sleep(0.01)
        msgs = await repo.list_messages(sid)
        if any(m.role == ROLE_ASSISTANT for m in msgs):
            break

    assert llm.route_calls == 0  # 未烧 VLM
    msgs = await repo.list_messages(sid)
    routing = [m for m in msgs if m.kind == KIND_ROUTING]
    assert len(routing) == 1 and "infrared" in routing[0].content and "用户指定" in routing[0].content
    trigger = svc._graph.inputs[0]["messages"][0].content
    assert "[场景路由结果]" in trigger and '"category": "infrared"' in trigger


@pytest.mark.asyncio
async def test_send_message_direction_prefix_skips_vlm(repo):
    """旧契约兼容：「（指定方向：热部图像）」文本标注同样解析为显式指定。"""
    llm = _RecordingLLM()
    ctx = _ctx(llm=llm)
    svc = SessionService(ctx, repo, _TaskService(), checkpointer=None)
    svc._graph = _FakeGraph(reply="已按热部场景派发。")

    sid = (await svc.create_session(None))["session_id"]
    await svc.send_message(sid, "（指定方向：热部图像）分析试验图像中的异常情况", [("g.jpg", b"\x89PNG fake")])
    for _ in range(20):
        await asyncio.sleep(0.01)
        msgs = await repo.list_messages(sid)
        if any(m.role == ROLE_ASSISTANT for m in msgs):
            break

    assert llm.route_calls == 0
    trigger = svc._graph.inputs[0]["messages"][0].content
    assert '"category": "infrared"' in trigger and '"source": "explicit"' in trigger


@pytest.mark.asyncio
async def test_send_message_keyword_rule_skips_vlm(repo):
    """会话轨关键词规则层（与任务轨同源）：无字段/标注时正文命中也直采。"""
    llm = _RecordingLLM()
    ctx = _ctx(llm=llm)
    svc = SessionService(ctx, repo, _TaskService(), checkpointer=None)
    svc._graph = _FakeGraph(reply="已按热部场景派发。")

    sid = (await svc.create_session(None))["session_id"]
    await svc.send_message(sid, "分析热部试验图像中的异常情况", [("a.jpg", b"\x89PNG fake")])
    for _ in range(20):
        await asyncio.sleep(0.01)
        msgs = await repo.list_messages(sid)
        if any(m.role == ROLE_ASSISTANT for m in msgs):
            break

    assert llm.route_calls == 0
    msgs = await repo.list_messages(sid)
    routing = [m for m in msgs if m.kind == KIND_ROUTING]
    assert "关键词规则" in routing[0].content
    trigger = svc._graph.inputs[0]["messages"][0].content
    assert '"category": "infrared"' in trigger and '"source": "keyword"' in trigger


@pytest.mark.asyncio
async def test_send_message_scene_outside_pool_falls_back_to_vlm(repo):
    """候选池外的显式指定不可信：回退 VLM 路由与确认卡兜底。"""
    llm = _RecordingLLM()
    ctx = _ctx(llm=llm)
    svc = SessionService(ctx, repo, _TaskService(), checkpointer=None)
    svc._graph = _FakeGraph(reply="已派发。")

    sid = (await svc.create_session(None))["session_id"]
    await svc.send_message(sid, "帮我看看这张图", [("a.jpg", b"\x89PNG fake")], scene="fpi")
    for _ in range(20):
        await asyncio.sleep(0.01)
        msgs = await repo.list_messages(sid)
        if any(m.role == ROLE_ASSISTANT for m in msgs):
            break

    assert llm.route_calls == 1  # fpi 不在测试注册表池（fracture/borescope/infrared）→ 回退 VLM


@pytest.mark.asyncio
async def test_on_task_settled_wakes_session(repo):
    ctx = _ctx()
    ctx.task_repo.records["T777"] = _TaskRecord(
        "T777", status="completed", grade={"level": "pass", "summary": "合格"}
    )
    task_svc = _TaskService()
    svc = SessionService(ctx, repo, task_svc, checkpointer=None)
    svc._graph = _FakeGraph(reply="任务 T777 已完成，判级合格。")

    sid = (await svc.create_session(None))["session_id"]
    await repo.add_message(
        sid, role=ROLE_ASSISTANT, kind="task_card", content="已派发", task_id="T777"
    )

    await svc.on_task_settled("T777")
    for _ in range(20):
        await asyncio.sleep(0.01)
        msgs = await repo.list_messages(sid)
        if any(m.role == ROLE_ASSISTANT and m.content.startswith("任务 T777") for m in msgs):
            break

    msgs = await repo.list_messages(sid)
    events = [m for m in msgs if m.role == ROLE_EVENT]
    assert len(events) == 1 and "T777" in events[0].content and events[0].task_id == "T777"
    assert any(m.role == ROLE_ASSISTANT and m.content.startswith("任务 T777") for m in msgs)


@pytest.mark.asyncio
async def test_on_task_settled_unknown_task_noop(repo):
    svc = SessionService(_ctx(), repo, _TaskService(), checkpointer=None)
    svc._graph = _FakeGraph()
    await svc.on_task_settled("T_UNKNOWN")  # 无会话关联：静默返回
    await asyncio.sleep(0.02)
    assert svc._graph.inputs == []


@pytest.mark.asyncio
async def test_on_task_settled_awaiting_review_emits_review_card(repo):
    """任务暂停进入人工复核门 → 对话内弹出终裁复核交互卡片（就地复核，免离开对话）。"""
    ctx = _ctx()
    ctx.task_repo.records["T888"] = _TaskRecord(
        "T888", status="awaiting_review", grade={"level": "review", "summary": "置信度不足"}
    )
    svc = SessionService(ctx, repo, _TaskService(), checkpointer=None)
    svc._graph = _FakeGraph(reply="任务 T888 已暂停，请在复核卡片中完成终裁复核。")

    sid = (await svc.create_session(None))["session_id"]
    await repo.add_message(
        sid, role=ROLE_ASSISTANT, kind="task_card", content="已派发", task_id="T888"
    )

    await svc.on_task_settled("T888")
    for _ in range(20):
        await asyncio.sleep(0.01)
        msgs = await repo.list_messages(sid)
        if any(m.kind == KIND_REVIEW_CARD for m in msgs):
            break

    msgs = await repo.list_messages(sid)
    cards = [m for m in msgs if m.kind == KIND_REVIEW_CARD]
    assert len(cards) == 1
    card = cards[0]
    assert card.role == ROLE_ASSISTANT and card.task_id == "T888"
    payload = json.loads(card.content)
    assert payload["grade_level"] == "review"
    assert payload["grade_summary"] == "置信度不足"
    assert payload["defect_count"] == 1  # _TaskRecord 固定一条检出
    # 事件消息同步说明已弹出复核卡（会话智能体回报口径一致）
    events = [m for m in msgs if m.role == ROLE_EVENT]
    assert len(events) == 1 and "终裁复核" in events[0].content


@pytest.mark.asyncio
async def test_on_task_settled_completed_emits_no_review_card(repo):
    """非暂停落定（已完成）不弹复核卡：复核门仅在 awaiting_review 触发。"""
    ctx = _ctx()
    ctx.task_repo.records["T999"] = _TaskRecord(
        "T999", status="completed", grade={"level": "pass", "summary": "合格"}
    )
    svc = SessionService(ctx, repo, _TaskService(), checkpointer=None)
    svc._graph = _FakeGraph(reply="任务 T999 已完成。")

    sid = (await svc.create_session(None))["session_id"]
    await repo.add_message(
        sid, role=ROLE_ASSISTANT, kind="task_card", content="已派发", task_id="T999"
    )

    await svc.on_task_settled("T999")
    for _ in range(20):
        await asyncio.sleep(0.01)
        msgs = await repo.list_messages(sid)
        if any(m.role == ROLE_ASSISTANT and m.content.startswith("任务 T999") for m in msgs):
            break

    msgs = await repo.list_messages(sid)
    assert not any(m.kind == KIND_REVIEW_CARD for m in msgs)


@pytest.mark.asyncio
async def test_reconcile_review_cards_backfills_paused_task(repo):
    """启动补卡对账：功能上线前已暂停的任务，会话缺卡 → 补发终裁复核卡。"""
    ctx = _ctx()
    ctx.task_repo.records["T555"] = _TaskRecord(
        "T555", status="awaiting_review", grade={"level": "review", "summary": "低置信"}
    )
    svc = SessionService(ctx, repo, _TaskService(), checkpointer=None)
    svc._graph = _FakeGraph(reply="不应被调用")  # 补卡不触发智能体回合

    sid = (await svc.create_session(None))["session_id"]
    await repo.add_message(
        sid, role=ROLE_ASSISTANT, kind="task_card", content="已派发", task_id="T555"
    )

    await svc.reconcile_review_cards()

    cards = [m for m in await repo.list_messages(sid) if m.kind == KIND_REVIEW_CARD]
    assert len(cards) == 1
    assert cards[0].task_id == "T555"
    payload = json.loads(cards[0].content)
    assert payload["grade_level"] == "review"
    assert svc._graph.inputs == []  # 补卡只落卡、不唤醒回合


@pytest.mark.asyncio
async def test_reconcile_review_cards_idempotent(repo):
    """幂等：已落卡的任务重复对账不重复落卡；无会话归属的任务跳过。"""
    ctx = _ctx()
    ctx.task_repo.records["T666"] = _TaskRecord(
        "T666", status="awaiting_review", grade={"level": "review", "summary": "s"}
    )
    ctx.task_repo.records["T_ORPHAN"] = _TaskRecord(
        "T_ORPHAN", status="awaiting_review", grade={"level": "review", "summary": "s"}
    )
    svc = SessionService(ctx, repo, _TaskService(), checkpointer=None)
    svc._graph = _FakeGraph()

    sid = (await svc.create_session(None))["session_id"]
    await repo.add_message(
        sid, role=ROLE_ASSISTANT, kind="task_card", content="已派发", task_id="T666"
    )

    await svc.reconcile_review_cards()
    await svc.reconcile_review_cards()  # 二次对账不重复

    msgs = await repo.list_messages(sid)
    assert len([m for m in msgs if m.kind == KIND_REVIEW_CARD]) == 1
    # 无会话归属任务：无任何落库
    assert not any(m.task_id == "T_ORPHAN" for m in msgs)


# --------------------------------------------------------------------------
# 直播流式（首字延迟 = 模型 TTFB：astream 逐 token 推帧，落库复用同 id）
# --------------------------------------------------------------------------
from langchain_core.messages import AIMessageChunk  # noqa: E402


class _SpyBus:
    """直播帧总线桩：记录 publish 的 (session_id, frame)。"""

    def __init__(self) -> None:
        self.frames: list[tuple[str, dict]] = []

    def publish(self, session_id: str, frame: dict) -> None:
        self.frames.append((session_id, frame))


class _StreamingModel:
    """伪流式模型：逐片 yield AIMessageChunk，可选尾部附工具调用块。"""

    def __init__(self, pieces: list[str], tool_call: dict | None = None):
        self._pieces = pieces
        self._tool_call = tool_call

    def bind_tools(self, tools):
        return self

    async def ainvoke(self, messages):
        """无直播总线时节点回退 ainvoke：聚合全部分片（工具调用转 AIMessage 形态）。"""
        content = "".join(self._pieces)
        if self._tool_call is not None:
            tc = self._tool_call
            return AIMessage(
                content=content,
                tool_calls=[{"name": tc["name"], "args": tc["args"], "id": tc.get("id", "c1")}],
            )
        return AIMessage(content=content)

    async def astream(self, messages):
        for p in self._pieces:
            yield AIMessageChunk(content=p)
        if self._tool_call is not None:
            tc = self._tool_call
            yield AIMessageChunk(
                content="",
                tool_call_chunks=[{
                    "name": tc["name"], "args": json.dumps(tc["args"], ensure_ascii=False),
                    "id": tc.get("id", "c1"), "index": 0, "type": "tool_call_chunk",
                }],
            )


@pytest.mark.asyncio
async def test_agent_node_streams_final_text_reply_live():
    """文本收尾回合：逐字直播 message start→delta→end，且 streamed_reply_id 复用直播 id。"""
    bus = _SpyBus()
    ctx = _ctx()
    ctx.llm = type("L", (), {"react_model": _StreamingModel(["你好！", "我是质检", "助手。"])})()
    node = build_session_agent_node(ctx, build_session_lc_tools(), repo=_SpyRepo(), live=bus)

    out = await node({"session_id": "S1", "messages": [HumanMessage(content="你好")], "loop_count": 0})

    frames = [f for sid, f in bus.frames if sid == "S1"]
    assert frames[0]["phase"] == "start" and frames[0]["type"] == "message"
    deltas = [f["delta"] for f in frames if f.get("phase") == "delta"]
    assert "".join(deltas) == "你好！我是质检助手。"
    assert frames[-1]["phase"] == "end"
    assert frames[-1]["message"]["content"] == "你好！我是质检助手。"
    # 全程同一 message_id（落库复用，SSE 不重推）
    assert len({f["message_id"] for f in frames}) == 1
    assert out["streamed_reply_id"] == frames[0]["message_id"]


@pytest.mark.asyncio
async def test_agent_node_tool_turn_morphs_bubble_into_thought_row():
    """工具轮：直播气泡收尾变形为思考行（同 id thinking end），思考行以直播 id 落库。"""
    bus = _SpyBus()
    repo = _SpyRepo()
    ctx = _ctx()
    ctx.llm = type("L", (), {"react_model": _StreamingModel(
        ["置信度足够，", "直接派发。"],
        tool_call={"name": "launch_inspection", "args": {"scene": "fpi"}, "id": "c1"},
    )})()
    node = build_session_agent_node(ctx, build_session_lc_tools(), repo=repo, live=bus)

    out = await node({"session_id": "S1", "messages": [HumanMessage(content="送检")], "loop_count": 0})

    frames = [f for sid, f in bus.frames if sid == "S1"]
    live_id = frames[0]["message_id"]
    assert frames[0]["type"] == "message" and frames[0]["phase"] == "start"
    # 收尾：同 id thinking end 帧（前端按 id 替换 → 气泡变思考行）
    end = frames[-1]
    assert end["type"] == "thinking" and end["phase"] == "end"
    assert end["message_id"] == live_id and end["text"] == "置信度足够，直接派发。"
    # 思考行落库复用直播 id（SSE 落库路径跳过重推）
    thoughts = [a for a in repo.added if a["kind"] == KIND_CHAIN]
    assert len(thoughts) == 1 and thoughts[0]["message_id"] == live_id
    # 工具轮不设最终回复直播 id
    assert "streamed_reply_id" not in out


@pytest.mark.asyncio
async def test_agent_node_no_live_frames_without_bus():
    """无直播总线（旧装配/测试桩）：行为回退整回合生成，不推帧、不落直播 id。"""
    ctx = _ctx()
    ctx.llm = type("L", (), {"react_model": _StreamingModel(["整段回复"])})()
    node = build_session_agent_node(ctx, build_session_lc_tools(), repo=_SpyRepo(), live=None)
    out = await node({"session_id": "S1", "messages": [HumanMessage(content="hi")], "loop_count": 0})
    assert out["messages"][0].content == "整段回复"
    assert "streamed_reply_id" not in out
