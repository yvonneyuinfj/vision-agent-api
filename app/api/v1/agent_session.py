"""质检助手会话路由：会话 CRUD / 消息收发 / 消息流（SSE）。

严格分层：路由层只做参数校验与外壳封装，编排全部交给
services.session_service。智能体回合非阻塞执行（决策 16），
回复与任务卡片经消息列表/消息流取回。

消息流协议（对齐 design-demos/思维链_玻璃疏行演绎.html）：
顶层 ``type`` 决定前端渲染器——``message``（气泡，助手文本逐字
start→delta→end）、``thinking``（思考行逐字）、``chain``（工具调用行，
唯一允许的原地更新：tool_result 回写同行）、``card``（task/review/done/
report/scene，只推不改）。首帧全量历史回放（整帧不重打字），之后经
事件总线近零延迟推新增，轮询兜底。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
from typing import Any, AsyncIterator, Awaitable, Callable

from fastapi import APIRouter, Form, Request, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.core.errors import BizCode, BusinessError
from app.core.logger import get_logger
from app.core.response import StandardEnvelope
from app.services.session_stream import build_frames, chain_content_digest

logger = get_logger("app.api.agent_session")

router = APIRouter(prefix="/api/v1/agent/sessions", tags=["agent-sessions"])

# 流端点服务端轮询间隔（秒）：事件总线唤醒为主，本值仅作兜底；测试可 monkeypatch 调小
_POLL_INTERVAL_S = 2.0
# 逐字流式帧间节奏（秒）：对齐演示的"文字一点一点推送"观感
_DELTA_INTERVAL_S = 0.03


def _svc(request: Request):
    c = request.app.state.container
    if c is None or getattr(c, "session_service", None) is None:
        raise BusinessError(BizCode.INTERNAL_ERROR, "container not ready")
    return c.session_service


class CreateSessionRequest(BaseModel):
    title: str | None = Field(default=None, max_length=256)


class RenameSessionRequest(BaseModel):
    title: str = Field(min_length=1, max_length=256)


@router.post("", response_model=StandardEnvelope[dict])
async def create_session(request: Request, body: CreateSessionRequest) -> StandardEnvelope[dict]:
    return StandardEnvelope.ok(await _svc(request).create_session(body.title))


@router.get("", response_model=StandardEnvelope[list[dict]])
async def list_sessions(request: Request) -> StandardEnvelope[list[dict]]:
    return StandardEnvelope.ok(await _svc(request).list_sessions())


# 注意：必须定义在 /{session_id} 之前，否则 "stream" 会被当成 session_id 吞掉
@router.get("/stream")
async def stream_sessions(request: Request) -> StreamingResponse:
    """会话列表流（SSE）：首帧全量，此后任何变化（新建/重命名/删除/活跃排序）整表重推。

    前端以本流替代 GET /sessions 轮询；客户端断开（request.is_disconnected）即退出。
    事件格式：data: {"type":"sessions","sessions":[...]}；心跳 `: ping`。
    """
    svc = _svc(request)
    return StreamingResponse(
        _sessions_stream(svc, request.is_disconnected),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _sessions_stream(
    svc: Any, is_disconnected: Callable[[], Awaitable[bool]],
) -> AsyncIterator[str]:
    """列表流生成器：首帧全量，之后整表摘要比对，变化重推；断开即退出。

    抽为模块级纯函数以便单测直接驱动（TestClient 不送达 http.disconnect，
    经 HTTP 测无限流会挂死）。
    """
    last_digest = ""
    try:
        sessions = await svc.list_sessions()
        last_digest = _digest_sessions(sessions)
        yield _sse({"type": "sessions", "sessions": sessions})
    except Exception as exc:  # noqa: BLE001
        yield _sse({"type": "error", "message": str(exc)})
        return
    idle = 0
    while True:
        if await is_disconnected():
            return
        await asyncio.sleep(_POLL_INTERVAL_S)
        try:
            sessions = await svc.list_sessions()
        except Exception:  # noqa: BLE001 - 瞬时故障跳过本轮，下轮重试
            continue
        digest = _digest_sessions(sessions)
        if digest != last_digest:
            last_digest = digest
            idle = 0
            yield _sse({"type": "sessions", "sessions": sessions})
        else:
            idle += 1
            if idle >= 8:  # 约 16s 无变化发一次心跳，防中间层掐空闲连接
                idle = 0
                yield ": ping\n\n"


@router.get("/{session_id}", response_model=StandardEnvelope[dict])
async def get_session(request: Request, session_id: str) -> StandardEnvelope[dict]:
    return StandardEnvelope.ok(await _svc(request).get_session(session_id))


@router.patch("/{session_id}", response_model=StandardEnvelope[dict])
async def rename_session(
    request: Request, session_id: str, body: RenameSessionRequest
) -> StandardEnvelope[dict]:
    await _svc(request).rename_session(session_id, body.title)
    return StandardEnvelope.ok({"session_id": session_id, "title": body.title.strip()})


@router.delete("/{session_id}", response_model=StandardEnvelope[dict])
async def delete_session(request: Request, session_id: str) -> StandardEnvelope[dict]:
    await _svc(request).delete_session(session_id)
    return StandardEnvelope.ok({"session_id": session_id, "deleted": True})


@router.post("/{session_id}/messages", response_model=StandardEnvelope[dict])
async def send_message(
    request: Request,
    session_id: str,
    content: str = Form(default=""),
    scene: str = Form(default=""),
    files: list[UploadFile] = [],
) -> StandardEnvelope[dict]:
    """scene 为可选的显式场景码（前端能力下拉结构化指定）：命中候选池时跳过 VLM 路由。"""
    payloads: list[tuple[str, bytes]] = []
    for f in files:
        data = await f.read()
        if data:
            payloads.append((f.filename or "image.jpg", data))
    result = await _svc(request).send_message(session_id, content, payloads, scene=scene)
    return StandardEnvelope.ok(result)


@router.get("/{session_id}/messages", response_model=StandardEnvelope[list[dict]])
async def list_messages(request: Request, session_id: str) -> StandardEnvelope[list[dict]]:
    return StandardEnvelope.ok(await _svc(request).list_messages(session_id))


@router.get("/{session_id}/stream")
async def stream_messages(request: Request, session_id: str) -> StreamingResponse:
    """消息流（SSE）：类型分派 + 逐字增量协议（对齐玻璃疏行演绎演示）。

    - 首帧全量历史回放（整帧，不重新打字）；
    - 之后事件总线唤醒即时推新增：助手文本/思考逐字（start→delta×N→end），
      卡片只推不改（创建/复核/完成/报告各一张新卡），工具行 tool_call→tool_result
      原地更新（协议唯一允许的原地更新）；
    - 客户端断开（await request.is_disconnected）即退出；心跳防中间层掐连接。
    """
    svc = _svc(request)
    # 会话存在性先校验（404 走统一异常外壳；响应开始后再无法改状态码）
    await svc.get_session(session_id, with_messages=False)

    return StreamingResponse(
        _message_stream(svc, getattr(svc, "event_bus", None), session_id, request.is_disconnected),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _message_stream(
    svc: Any,
    bus: Any,
    session_id: str,
    is_disconnected: Callable[[], Awaitable[bool]],
) -> AsyncIterator[str]:
    """消息流生成器（模块级纯函数以便单测直接驱动；经 HTTP 测无限流会挂死）。

    只推不改语义：每条消息只在**首次出现**时推一次（卡片绝不原地改写）；
    唯一例外是 chain 工具行的 tool_result 内容回写（按内容指纹检测）。
    队列承载两类条目：电平唤醒（重读仓储）与直播帧（LLM 逐 token 实时帧，
    原样透传，首字延迟 = 模型 TTFB）；直播帧的 message_id 与落库消息同 id，
    已透传的 id 不再经落库路径重推。事件总线提供近零延迟唤醒；
    拿不到总线（老装配/测试桩）时退化为纯轮询。
    """
    emitted: set[str] = set()            # 已推消息 id（只推不改；含直播帧）
    chain_digests: dict[str, str] = {}   # chain 行内容指纹（检测工具行结果回写）
    queue = bus.subscribe(session_id) if bus is not None else None
    try:
        # ---- 首帧：全量历史回放（整帧落地，不重打字） ----
        try:
            history = await svc.list_messages(session_id)
        except Exception as exc:  # noqa: BLE001
            yield _sse({"type": "error", "message": str(exc)})
            return
        for msg in history:
            mid = msg["message_id"]
            emitted.add(mid)
            if msg.get("kind") == "chain":
                chain_digests[mid] = chain_content_digest(msg)
            for fr in build_frames(msg, live=False):
                yield _sse(fr)

        # ---- 增量：事件总线唤醒 + 直播帧为主，轮询兜底 ----
        idle = 0
        while True:
            if await is_disconnected():
                return
            wake = False
            pushed = False
            if queue is not None:
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=_POLL_INTERVAL_S)
                except asyncio.TimeoutError:
                    wake = True  # 无信号：走轮询兜底
                else:
                    batch = [item]
                    while not queue.empty():  # 一次性取空队列（逐字帧批量透传）
                        try:
                            batch.append(queue.get_nowait())
                        except asyncio.QueueEmpty:
                            break
                    for it in batch:
                        if isinstance(it, dict):
                            mid = it.get("message_id")
                            if mid:
                                emitted.add(mid)  # 落库复用同 id，避免重推
                            if await is_disconnected():
                                return
                            yield _sse(it)
                            pushed = True
                        else:
                            wake = True
            else:
                await asyncio.sleep(_POLL_INTERVAL_S)
                wake = True
            if not wake:
                idle = 0 if pushed else idle + 1
                if idle >= 8:
                    idle = 0
                    yield ": ping\n\n"
                continue
            try:
                msgs = await svc.list_messages(session_id)
            except Exception:  # noqa: BLE001 - 瞬时故障跳过本轮，下轮重试
                msgs = []
            for msg in msgs:
                mid = msg["message_id"]
                kind = msg.get("kind")
                if mid not in emitted:
                    emitted.add(mid)
                    if kind == "chain":
                        chain_digests[mid] = chain_content_digest(msg)
                    for fr in build_frames(msg, live=True):
                        if await is_disconnected():
                            return
                        yield _sse(fr)
                        if fr.get("phase") == "delta":
                            await asyncio.sleep(_DELTA_INTERVAL_S)
                    pushed = True
                elif kind == "chain":
                    digest = chain_content_digest(msg)
                    if chain_digests.get(mid) != digest:
                        chain_digests[mid] = digest
                        for fr in build_frames(msg, live=False):
                            yield _sse(fr)
                        pushed = True
            if pushed:
                idle = 0
            else:
                idle += 1
                if idle >= 8:  # 约 16s 无增量发一次心跳，防中间层掐空闲连接
                    idle = 0
                    yield ": ping\n\n"
    finally:
        if queue is not None and bus is not None:
            bus.unsubscribe(session_id, queue)


def _sse(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _digest_sessions(sessions: list[dict[str, Any]]) -> str:
    """整份会话列表的摘要，供列表流变更检测（标题/排序/增删任何变化都重推）。"""
    raw = json.dumps(sessions, ensure_ascii=False, sort_keys=True)
    return hashlib.md5(raw.encode("utf-8")).hexdigest()
