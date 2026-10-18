"""SSE 消息流（流式协议：类型分派 + 逐字增量 + 卡片只推不改）。

对齐 design-demos/思维链_玻璃疏行演绎.html：
- 首帧全量历史回放（整帧，不重打字）；
- 助手文本/思考逐字流式（start→delta×N→end）；
- 卡片只推不改：每条消息只在首次出现推一次，任务快照翻转不重推；
- 唯一原地更新 = chain 工具行 tool_result 内容回写（按内容指纹检测）。

会话列表流（GET /agent/sessions/stream）：首帧全量，任何变化整表重推。

生成器测试直接驱动模块级 _sessions_stream / _message_stream——TestClient
不送达 http.disconnect，经 HTTP 测无限流会挂死套件。
"""
from __future__ import annotations

import asyncio
import json

from app.api.v1 import agent_session as agent_session_module
from app.api.v1.agent_session import (
    _digest_sessions,
    _message_stream,
    _sessions_stream,
)
from app.services.session_stream import SessionEventBus, build_frames, chunk_text


class _StubSessionService:
    """list_sessions 按调用次序返回预设列表（模拟列表变化）。"""

    def __init__(self, lists: list[list[dict]]) -> None:
        self._lists = lists
        self._calls = 0

    async def list_sessions(self) -> list[dict]:
        idx = min(self._calls, len(self._lists) - 1)
        self._calls += 1
        return self._lists[idx]


class _DisconnectAfter:
    """前 n 次返回 False，之后恒 True（模拟客户端在 n 轮后断开）。"""

    def __init__(self, n: int) -> None:
        self._n = n
        self._calls = 0

    async def __call__(self) -> bool:
        self._calls += 1
        return self._calls > self._n


def _collect_frames(gen, max_frames: int | None = None) -> list[dict]:
    """驱动生成器收集 data: 帧（跳过心跳）；max_frames=None 时跑到生成器自然结束。"""

    async def run() -> list[dict]:
        frames: list[dict] = []
        async for raw in gen:
            if raw.startswith("data: "):
                frames.append(json.loads(raw[6:]))
                if max_frames is not None and len(frames) >= max_frames:
                    break
        await gen.aclose()
        return frames

    return asyncio.run(run())


class _MsgStreamService:
    """消息流测试桩：list_messages 返回最新追加的快照（模拟仓储时序）。"""

    def __init__(self, history: list[dict]) -> None:
        self._current = list(history)
        self.event_bus = None

    def set_snapshot(self, snapshot: list[dict]) -> None:
        self._current = list(snapshot)

    async def list_messages(self, session_id: str) -> list[dict]:
        return list(self._current)


class _BoomMessageService:
    event_bus = None

    async def list_messages(self, session_id: str) -> list[dict]:
        raise RuntimeError("db down")


_STEP_WAIT_S = 0.35  # 每步等待：覆盖逐字流式全程（测试内 delta 间隔已被压到毫秒级）


def _drive_message_stream(svc: _MsgStreamService, steps: list[list[dict]]) -> list[dict]:
    """驱动消息流：历史回放 → 逐步替换仓储快照并唤醒总线 → 断开收尾。"""

    async def run() -> list[dict]:
        bus = SessionEventBus()
        svc.event_bus = bus
        frames: list[dict] = []
        disconnected = asyncio.Event()

        async def is_disconnected() -> bool:
            return disconnected.is_set()

        gen = _message_stream(svc, bus, "S1", is_disconnected)

        async def consume() -> None:
            async for raw in gen:
                if raw.startswith("data: "):
                    frames.append(json.loads(raw[6:]))

        task = asyncio.create_task(consume())
        await asyncio.sleep(_STEP_WAIT_S)  # 历史回放
        for snap in steps:
            svc.set_snapshot(snap)
            bus.notify("S1")
            await asyncio.sleep(_STEP_WAIT_S)
        disconnected.set()
        try:
            await asyncio.wait_for(task, timeout=3.0)
        except asyncio.TimeoutError:
            task.cancel()
        return frames

    return asyncio.run(run())


# ---------- 历史回放 ----------


def test_history_replayed_as_complete_frames(monkeypatch):
    """首帧全量历史：助手文本也整帧落地（不重打字）。"""
    monkeypatch.setattr(agent_session_module, "_POLL_INTERVAL_S", 0.01)
    monkeypatch.setattr(agent_session_module, "_DELTA_INTERVAL_S", 0.001)
    history = [
        {"message_id": "M1", "role": "user", "kind": "text", "content": "你好"},
        {"message_id": "M2", "role": "assistant", "kind": "text", "content": "你好！"},
    ]
    svc = _MsgStreamService(history)
    frames = _drive_message_stream(svc, [])
    assert [f["type"] for f in frames] == ["message", "message"]
    assert all(f["phase"] == "end" for f in frames)
    assert frames[1]["message"]["content"] == "你好！"


# ---------- 逐字流式 ----------


def test_new_assistant_text_streams_start_delta_end(monkeypatch):
    """新增助手文本：start → delta×N → end，delta 拼接 = 全文。"""
    monkeypatch.setattr(agent_session_module, "_POLL_INTERVAL_S", 0.01)
    monkeypatch.setattr(agent_session_module, "_DELTA_INTERVAL_S", 0.001)
    history = [{"message_id": "M1", "role": "user", "kind": "text", "content": "hi"}]
    new_msg = {"message_id": "M2", "role": "assistant", "kind": "text", "content": "你好，世界！"}
    svc = _MsgStreamService(history)
    frames = _drive_message_stream(svc, [history + [new_msg]])
    m2 = [f for f in frames if f.get("message_id") == "M2"]
    assert m2, frames
    assert m2[0]["type"] == "message" and m2[0]["phase"] == "start"
    assert m2[-1]["phase"] == "end"
    deltas = [f["delta"] for f in m2 if f["phase"] == "delta"]
    assert len(deltas) >= 2 and "".join(deltas) == "你好，世界！"
    assert m2[-1]["message"]["content"] == "你好，世界！"


def test_thinking_streams_deltas(monkeypatch):
    """新增思考行（chain thought）：逐字流式。"""
    monkeypatch.setattr(agent_session_module, "_POLL_INTERVAL_S", 0.01)
    monkeypatch.setattr(agent_session_module, "_DELTA_INTERVAL_S", 0.001)
    thought = json.dumps({"type": "thought", "turn": 1, "text": "先拆解场景。"})
    new_msg = {"message_id": "C1", "role": "event", "kind": "chain", "content": thought, "task_id": "T1"}
    svc = _MsgStreamService([])
    frames = _drive_message_stream(svc, [[new_msg]])
    th = [f for f in frames if f.get("type") == "thinking"]
    assert th, frames
    assert th[0]["phase"] == "start" and th[-1]["phase"] == "end"
    assert "".join(f["delta"] for f in th if f["phase"] == "delta") == "先拆解场景。"


# ---------- 只推不改 ----------


def test_card_pushed_once_even_if_task_snapshot_changes(monkeypatch):
    """卡片只推一次：任务快照翻转不再重推（对齐演示"卡片只推不改"）。"""
    monkeypatch.setattr(agent_session_module, "_POLL_INTERVAL_S", 0.01)
    monkeypatch.setattr(agent_session_module, "_DELTA_INTERVAL_S", 0.001)
    card = {
        "message_id": "K1", "role": "assistant", "kind": "task_card", "task_id": "T1",
        "content": json.dumps({"task_id": "T1", "status": "running", "scene": "borescope"}),
    }
    svc = _MsgStreamService([])
    # 第二步：同一卡片消息的任务快照翻转为 completed（旧协议会重推，新协议不推）
    card_flipped = {**card, "task": {"task_id": "T1", "status": "completed"}}
    frames = _drive_message_stream(svc, [[card], [card_flipped]])
    k1 = [f for f in frames if f.get("message_id") == "K1"]
    assert len(k1) == 1  # 只推一次，快照翻转不重推
    assert k1[0]["type"] == "card" and k1[0]["card_type"] == "task_card"
    assert k1[0]["payload"]["status"] == "running"  # 快照固化在落卡时刻


# ---------- chain 原地更新（唯一例外） ----------


def test_chain_tool_result_updates_in_place(monkeypatch):
    """tool_call 落行后 tool_result 内容回写 → 推 tool_result 更新帧（同 message_id）。"""
    monkeypatch.setattr(agent_session_module, "_POLL_INTERVAL_S", 0.01)
    monkeypatch.setattr(agent_session_module, "_DELTA_INTERVAL_S", 0.001)
    call = json.dumps({"type": "tool_call", "turn": 1, "call_id": "c1", "name": "launch_child_task",
                       "args": {"scene": "borescope"}, "done": False})
    result = json.dumps({"type": "tool_call", "turn": 1, "call_id": "c1", "name": "launch_child_task",
                         "args": {"scene": "borescope"}, "content": "已派发", "is_error": False, "done": True})
    m_call = {"message_id": "MC1", "role": "event", "kind": "chain", "content": call, "task_id": "T1"}
    m_done = {**m_call, "content": result}
    svc = _MsgStreamService([])
    frames = _drive_message_stream(svc, [[m_call], [m_done]])
    chain = [f for f in frames if f.get("type") == "chain"]
    assert [f["event"] for f in chain] == ["tool_call", "tool_result"]
    assert chain[1]["content"] == "已派发" and chain[1]["is_error"] is False
    assert chain[0]["message_id"] == chain[1]["message_id"] == "MC1"


# ---------- 卡片帧 ----------


def test_card_frames_for_all_card_kinds():
    kinds = {
        "task_card": {"task_id": "T1", "status": "running"},
        "review_card": {"task_id": "T1", "grade_level": "B"},
        "done_card": {"task_id": "T1", "status": "completed", "report": {"status": "ready"}},
        "report_card": {"task_id": "T1", "report": {"status": "ready", "uri": "/r/T1"}},
        "scene_card": {"mode": "candidates", "candidates": []},
    }
    for kind, payload in kinds.items():
        msg = {"message_id": "K", "role": "assistant", "kind": kind,
               "content": json.dumps(payload), "task_id": "T1"}
        frames = build_frames(msg, live=True)
        assert len(frames) == 1
        assert frames[0] == {"type": "card", "card_type": kind, "message_id": "K",
                             "task_id": "T1", "payload": payload}


# ---------- 分片 ----------


def test_chunk_text_short_text_small_pieces():
    chunks = chunk_text("你好，世界！")
    assert "".join(chunks) == "你好，世界！"
    assert all(2 <= len(c) <= 4 for c in chunks[:-1])  # 末片可短于 2（余数）
    assert 1 <= len(chunks[-1]) <= 4


def test_chunk_text_long_text_capped():
    text = "字" * 5000
    chunks = chunk_text(text, max_chunks=140)
    assert "".join(chunks) == text
    assert len(chunks) <= 140


def test_chunk_text_empty():
    assert chunk_text("") == []


# ---------- 会话列表流（协议未变） ----------


def test_sessions_digest_changes_with_list():
    a = [{"session_id": "S1", "title": "t1"}]
    b = [{"session_id": "S1", "title": "t1"}, {"session_id": "S2", "title": "t2"}]
    assert _digest_sessions(a) != _digest_sessions(b)
    assert _digest_sessions(a) == _digest_sessions(list(a))


def test_sessions_stream_first_frame_full_then_repush_on_change(monkeypatch):
    """首帧全量；列表变化（活跃排序/新建等）整表重推——替代列表轮询。"""
    monkeypatch.setattr(agent_session_module, "_POLL_INTERVAL_S", 0.005)
    before = [{"session_id": "S1", "title": "旧排序"}]
    after = [{"session_id": "S2", "title": "新活跃"}, {"session_id": "S1", "title": "旧排序"}]
    svc = _StubSessionService([before, after])
    gen = _sessions_stream(svc, _DisconnectAfter(n=10**9))

    frames = _collect_frames(gen, max_frames=2)

    assert frames[0] == {"type": "sessions", "sessions": before}
    assert frames[1] == {"type": "sessions", "sessions": after}


def test_sessions_stream_no_repush_when_unchanged(monkeypatch):
    """列表无变化不重推（只发心跳），避免前端无谓渲染。"""
    monkeypatch.setattr(agent_session_module, "_POLL_INTERVAL_S", 0.001)
    same = [{"session_id": "S1", "title": "t"}]
    svc = _StubSessionService([same, same, same, same])
    gen = _sessions_stream(svc, _DisconnectAfter(n=3))  # 3 轮后断开 → 生成器自然结束

    frames = _collect_frames(gen)

    assert frames == [{"type": "sessions", "sessions": same}]


def test_sessions_stream_exits_on_disconnect(monkeypatch):
    """客户端断开 → 生成器立即退出（资源不泄漏）。"""
    monkeypatch.setattr(agent_session_module, "_POLL_INTERVAL_S", 0.005)
    svc = _StubSessionService([[{"session_id": "S1", "title": "t"}]])
    gen = _sessions_stream(svc, _DisconnectAfter(n=0))  # 立即断开

    frames = _collect_frames(gen)

    assert len(frames) == 1 and frames[0]["type"] == "sessions"


def test_sessions_stream_error_frame_on_first_failure(monkeypatch):
    """首帧拉取失败 → 错误帧后结束（不无限重试刷屏）。"""
    monkeypatch.setattr(agent_session_module, "_POLL_INTERVAL_S", 0.005)

    class _BoomService:
        async def list_sessions(self) -> list[dict]:
            raise RuntimeError("db down")

    gen = _sessions_stream(_BoomService(), _DisconnectAfter(n=10**9))

    frames = _collect_frames(gen)

    assert frames == [{"type": "error", "message": "db down"}]


def test_sessions_stream_route_defined_before_session_id():
    """/stream 必须先于 /{session_id} 定义，否则 'stream' 被当成 session_id 吞掉。"""
    paths = [getattr(r, "path", None) for r in agent_session_module.router.routes]
    stream = "/api/v1/agent/sessions/stream"
    by_id = "/api/v1/agent/sessions/{session_id}"
    assert stream in paths and by_id in paths
    assert paths.index(stream) < paths.index(by_id)


# ---------- 消息流错误与断开 ----------


def test_message_stream_error_frame_on_first_failure():
    gen = _message_stream(_BoomMessageService(), None, "S1", _DisconnectAfter(10**9))
    frames = _collect_frames(gen, max_frames=1)
    assert frames == [{"type": "error", "message": "db down"}]


def test_message_stream_exits_on_disconnect():
    """客户端断开 → 生成器退出（历史回放后即走）。"""
    svc = _MsgStreamService([{"message_id": "M1", "role": "user", "kind": "text", "content": "hi"}])
    gen = _message_stream(svc, None, "S1", _DisconnectAfter(n=0))
    frames = _collect_frames(gen)
    assert len(frames) == 1 and frames[0]["message_id"] == "M1"


# ---------- 仓储 → 事件总线 唤醒链路（集成） ----------


async def test_repo_message_listener_wakes_bus(db_engine):
    """add_message / update_message 落库 → 旁路唤醒总线订阅者（近零延迟推帧的根基）。"""
    from app.repositories.session_repo import SessionRepo
    from app.services.session_stream import SessionEventBus

    _, sf = db_engine
    repo = SessionRepo(sf)
    bus = SessionEventBus()
    repo.set_message_listener(bus.notify)
    await repo.create("S1", "t")

    q = bus.subscribe("S1")
    assert q.empty()
    mid = await repo.add_message("S1", role="user", kind="text", content="你好")
    # 落库后订阅者被唤醒（电平信号入队）
    assert not q.empty()
    q.get_nowait()  # 消费掉

    # 原地更新同样唤醒（工具行结果回写场景）
    assert await repo.update_message("S1", mid, "你好（更新）")
    assert not q.empty()
    bus.unsubscribe("S1", q)


async def test_message_stream_end_to_end_with_real_repo(db_engine, monkeypatch):
    """真实仓储 + 总线驱动消息流：落库的消息经唤醒被逐字推出。"""
    from app.repositories.session_repo import SessionRepo
    from app.services.session_stream import SessionEventBus

    monkeypatch.setattr(agent_session_module, "_POLL_INTERVAL_S", 0.01)
    monkeypatch.setattr(agent_session_module, "_DELTA_INTERVAL_S", 0.001)

    _, sf = db_engine
    repo = SessionRepo(sf)
    bus = SessionEventBus()
    repo.set_message_listener(bus.notify)
    await repo.create("S1", "t")

    class _RepoSvc:
        event_bus = bus

        async def list_messages(self, session_id):
            rows = await repo.list_messages(session_id)
            return [
                {
                    "message_id": r.id, "session_id": r.session_id, "role": r.role,
                    "kind": r.kind, "content": r.content, "images": [],
                    "task_id": r.task_id, "created_at": None,
                }
                for r in rows
            ]

    svc = _RepoSvc()
    frames: list[dict] = []
    disconnected = asyncio.Event()

    async def is_disconnected() -> bool:
        return disconnected.is_set()

    gen = _message_stream(svc, bus, "S1", is_disconnected)

    async def consume():
        async for raw in gen:
            if raw.startswith("data: "):
                frames.append(json.loads(raw[6:]))

    task = asyncio.create_task(consume())
    await asyncio.sleep(0.05)  # 空历史回放
    await repo.add_message(
        "S1", role="assistant", kind="text", content="任务已完成，判级 B。"
    )
    await asyncio.sleep(0.4)  # 逐字流式全程
    disconnected.set()
    try:
        await asyncio.wait_for(task, timeout=2.0)
    except asyncio.TimeoutError:
        task.cancel()

    msgs = [f for f in frames if f.get("type") == "message"]
    assert msgs, frames
    assert msgs[0]["phase"] == "start" and msgs[-1]["phase"] == "end"
    assert msgs[-1]["message"]["content"] == "任务已完成，判级 B。"
    assert "".join(f.get("delta", "") for f in msgs if f["phase"] == "delta") == "任务已完成，判级 B。"


# ---------- 直播帧（LLM 逐 token 实时帧）透传与去重 ----------


async def test_message_stream_live_frame_passthrough_and_db_suppression(monkeypatch):
    """直播帧原样透传（首字延迟=模型TTFB）；落库复用同 message_id 不再经落库路径重推。"""
    monkeypatch.setattr(agent_session_module, "_POLL_INTERVAL_S", 0.01)
    bus = SessionEventBus()
    svc = _MsgStreamService([])
    disconnected = asyncio.Event()

    async def is_disconnected() -> bool:
        return disconnected.is_set()

    frames: list[dict] = []

    async def consume():
        async for raw in _message_stream(svc, bus, "S1", is_disconnected):
            if raw.startswith("data: "):
                frames.append(json.loads(raw[6:]))

    task = asyncio.create_task(consume())
    await asyncio.sleep(0.05)  # 空历史回放完成

    # LLM 逐 token 直播：start → delta → end（同 message_id）
    bus.publish("S1", {"type": "message", "message_id": "MLIVE", "phase": "start",
                       "message": {"role": "assistant", "kind": "text"}})
    bus.publish("S1", {"type": "message", "message_id": "MLIVE", "phase": "delta", "delta": "你好"})
    bus.publish("S1", {"type": "message", "message_id": "MLIVE", "phase": "end",
                       "message": {"role": "assistant", "kind": "text", "content": "你好"}})
    await asyncio.sleep(0.1)

    # 落库复用同 id + 唤醒：落库路径必须跳过重推
    svc.set_snapshot([{"message_id": "MLIVE", "role": "assistant", "kind": "text",
                       "content": "你好", "images": []}])
    bus.notify("S1")
    await asyncio.sleep(0.1)

    disconnected.set()
    try:
        await asyncio.wait_for(task, timeout=2.0)
    except asyncio.TimeoutError:
        task.cancel()

    live = [f for f in frames if f.get("message_id") == "MLIVE"]
    assert [f["phase"] for f in live] == ["start", "delta", "end"]
    assert live[1]["delta"] == "你好"
    assert live[2]["message"]["content"] == "你好"

    # 断线重连（新生成器）：历史回放仍能拿到该消息的整帧（直播窗口外自愈）
    replay: list[dict] = []
    async for raw in _message_stream(svc, bus, "S1", _DisconnectAfter(n=0)):
        if raw.startswith("data: "):
            replay.append(json.loads(raw[6:]))
    assert any(
        f.get("type") == "message" and f.get("message_id") == "MLIVE" and f.get("phase") == "end"
        for f in replay
    )
