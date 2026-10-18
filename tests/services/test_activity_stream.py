"""会话实时思考链流（共识轮：对话内显示模型思考链）：append_step 旁路 → 会话 kind=chain 落库。

链路契约：
- TaskRepo.append_step 落库后触发监听器；监听器异常不破坏审计主流程；
- TaskService 装配即挂监听，经晚绑定 on_step 转发（容器注入会话侧处理器）；
- SessionService.on_task_step 只转发 node="chain" 的思考链事件：
  thought/tool_call 新增行，tool_result 按确定性 id 原地更新为完成行；
  机械步骤（非 chain 节点）不再进对话流，仅留任务 steps_json 审计；
  按 task_id 定位会话（联合子任务沿父链向上），无归属的任务静默忽略；
- 会话列表预览（最后一条消息）排除 activity/chain/routing（其 content 是 JSON）。
"""
from __future__ import annotations

import json

import pytest
import pytest_asyncio
from langgraph.checkpoint.memory import MemorySaver

from app.models.session import KIND_CHAIN, KIND_TEXT, ROLE_EVENT, ROLE_USER
from app.repositories.session_repo import SessionRepo
from app.repositories.task_repo import TaskRepo
from app.services.deps import AgentContext
from app.services.session_service import SessionService
from app.services.task_service import TaskService


@pytest_asyncio.fixture
async def repos(session_factory):
    return TaskRepo(session_factory), SessionRepo(session_factory)


@pytest_asyncio.fixture
async def svc(repos):
    task_repo, session_repo = repos
    ctx = AgentContext(task_repo=task_repo)
    task_service = TaskService(ctx, task_repo, checkpointer=MemorySaver())
    session_service = SessionService(ctx, session_repo, task_service, checkpointer=MemorySaver())
    # 模拟容器装配：晚绑定步骤回调
    task_service.on_step = session_service.on_task_step
    return task_service, session_service, task_repo, session_repo


async def _new_task(task_repo: TaskRepo, task_id: str, *, auxiliary: dict | None = None) -> None:
    await task_repo.create(
        task_id,
        scene=None,
        mode="agent",
        inputs={"scenes": None, "input_uris": [], "auxiliary": auxiliary or {}},
    )


# --------------------------------------------------------------------------
# TaskRepo 旁路
# --------------------------------------------------------------------------
async def test_append_step_fires_listener(repos):
    task_repo, _ = repos
    await _new_task(task_repo, "T1")
    seen: list[tuple[str, str, str]] = []

    async def listener(task_id, node, message):
        seen.append((task_id, node, message))

    task_repo.set_step_listener(listener)
    await task_repo.append_step("T1", "react_agent", "turn 1/10 calls=invoke_capability")
    assert seen == [("T1", "react_agent", "turn 1/10 calls=invoke_capability")]


async def test_append_step_listener_failure_does_not_break_audit(repos):
    task_repo, _ = repos
    await _new_task(task_repo, "T1")

    async def boom(*_args):
        raise RuntimeError("listener crash")

    task_repo.set_step_listener(boom)
    await task_repo.append_step("T1", "grading", "ok")  # 不得抛出
    rec = await task_repo.get("T1")
    steps = json.loads(rec.steps_json)
    assert len(steps) == 1 and steps[0]["node"] == "grading"


async def test_task_service_wires_listener_and_emits(repos):
    task_repo, _ = repos
    ctx = AgentContext(task_repo=task_repo)
    service = TaskService(ctx, task_repo, checkpointer=MemorySaver())
    assert task_repo._step_listener is not None  # 装配即挂

    await _new_task(task_repo, "T1")
    seen: list[str] = []

    async def on_step(task_id, node, message):
        seen.append(node)

    service.on_step = on_step
    await task_repo.append_step("T1", "exit_gate", "ok")
    assert seen == ["exit_gate"]


async def test_emit_step_without_callback_is_noop(repos):
    task_repo, _ = repos
    ctx = AgentContext(task_repo=task_repo)
    service = TaskService(ctx, task_repo, checkpointer=MemorySaver())
    await _new_task(task_repo, "T1")
    await service._emit_step("T1", "grading", "ok")  # on_step=None 不得抛出


# --------------------------------------------------------------------------
# SessionService.on_task_step：会话定位 + chain 思考链落库/原地更新
# --------------------------------------------------------------------------
def _chain(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False)


async def test_step_of_session_task_lands_as_chain(svc):
    """node=chain 的 thought/tool_call 落 kind=chain 行并挂任务 id。"""
    _, session_service, task_repo, session_repo = svc
    await session_repo.create("S1", None)
    await session_repo.add_message("S1", role=ROLE_USER, kind="text", content="查一下", task_id="T1")
    await _new_task(task_repo, "T1")

    await session_service.on_task_step(
        "T1", "chain", _chain({"type": "thought", "turn": 1, "text": "先看图"}),
    )

    msgs = await session_repo.list_messages("S1")
    chains = [m for m in msgs if m.kind == KIND_CHAIN]
    assert len(chains) == 1
    assert chains[0].task_id == "T1" and chains[0].role == ROLE_EVENT
    assert json.loads(chains[0].content)["text"] == "先看图"


async def test_tool_call_then_result_updates_in_place(svc):
    """tool_call 先落 running 行，tool_result 按 call_id 原地更新为完成行（Q3-B）。"""
    _, session_service, task_repo, session_repo = svc
    await session_repo.create("S1", None)
    await session_repo.add_message("S1", role=ROLE_USER, kind="text", content="查一下", task_id="T1")
    await _new_task(task_repo, "T1")

    await session_service.on_task_step(
        "T1", "chain", _chain({"type": "tool_call", "turn": 1, "call_id": "call_a",
                                "name": "invoke_capability", "args": {"scene": "fpi"}}),
    )
    await session_service.on_task_step(
        "T1", "chain", _chain({"type": "tool_result", "turn": 1, "call_id": "call_a",
                                "name": "invoke_capability", "content": "ok"}),
    )

    msgs = await session_repo.list_messages("S1")
    chains = [m for m in msgs if m.kind == KIND_CHAIN]
    assert len(chains) == 1  # 同一行原地更新，不是两条
    payload = json.loads(chains[0].content)
    assert payload["done"] is True and payload["content"] == "ok"


async def test_mechanical_step_is_not_forwarded(svc):
    """共识轮：机械步骤（非 chain 节点）不再进对话流。"""
    _, session_service, task_repo, session_repo = svc
    await session_repo.create("S1", None)
    await session_repo.add_message("S1", role=ROLE_USER, kind="text", content="查一下", task_id="T1")
    await _new_task(task_repo, "T1")

    await session_service.on_task_step("T1", "tool:invoke_capability", "args -> ok")
    await session_service.on_task_step("T1", "grading", "ok")

    msgs = await session_repo.list_messages("S1")
    assert all(m.kind != KIND_CHAIN for m in msgs)


async def test_child_task_step_routes_via_parent_chain(svc):
    """联合子任务无会话归属：沿 auxiliary.parent_task_id 向上解析到发起会话。"""
    _, session_service, task_repo, session_repo = svc
    await session_repo.create("S1", None)
    await session_repo.add_message("S1", role=ROLE_USER, kind="task_card", content="", task_id="TP")
    await _new_task(task_repo, "TP")
    await _new_task(task_repo, "TC", auxiliary={"parent_task_id": "TP"})

    await session_service.on_task_step(
        "TC", "chain", _chain({"type": "thought", "turn": 1, "text": "子任务思考"}),
    )

    msgs = await session_repo.list_messages("S1")
    chains = [m for m in msgs if m.kind == KIND_CHAIN]
    assert len(chains) == 1 and chains[0].task_id == "TC"


async def test_step_of_orphan_task_is_ignored(svc):
    _, session_service, task_repo, session_repo = svc
    await session_repo.create("S1", None)
    await _new_task(task_repo, "T_ORPHAN")

    await session_service.on_task_step(
        "T_ORPHAN", "chain", _chain({"type": "thought", "turn": 1, "text": "无归属"}),
    )

    msgs = await session_repo.list_messages("S1")
    assert all(m.kind != KIND_CHAIN for m in msgs)


async def test_session_resolution_is_cached(svc):
    _, session_service, task_repo, session_repo = svc
    await session_repo.create("S1", None)
    await session_repo.add_message("S1", role=ROLE_USER, kind="text", content="", task_id="T1")
    await _new_task(task_repo, "T1")

    await session_service.on_task_step(
        "T1", "chain", _chain({"type": "thought", "turn": 1, "text": "一"}),
    )

    # 第二步：若解析未缓存，find_session_by_task 会再被查库
    calls: list[str] = []
    original = session_service._repo.find_session_by_task

    async def spying(task_id):
        calls.append(task_id)
        return await original(task_id)

    session_service._repo.find_session_by_task = spying
    await session_service.on_task_step(
        "T1", "chain", _chain({"type": "thought", "turn": 2, "text": "二"}),
    )

    assert calls == []
    msgs = await session_repo.list_messages("S1")
    assert len([m for m in msgs if m.kind == KIND_CHAIN]) == 2


# --------------------------------------------------------------------------
# 列表预览排除 activity/chain/routing
# --------------------------------------------------------------------------
async def test_list_recent_preview_skips_chain(svc):
    _, session_service, _, session_repo = svc
    await session_repo.create("S1", None)
    await session_repo.add_message("S1", role=ROLE_USER, kind=KIND_TEXT, content="用户原话")
    await session_repo.add_message(
        "S1", role=ROLE_EVENT, kind=KIND_CHAIN,
        content=_chain({"type": "thought", "turn": 1, "text": "思考"}),
    )

    rows = await session_repo.list_recent()
    assert rows[0][1] == "用户原话"  # 不是 chain 的 JSON 串
