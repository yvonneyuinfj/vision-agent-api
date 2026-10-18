"""会话流式协议（对齐 design-demos/思维链_玻璃疏行演绎.html）。

把"整条消息一次性推送"重构为与演示一致的**类型分派 + 逐字增量**协议：

顶层 ``type`` 决定前端渲染器：

- ``message``  文本消息（用户/助手/事件/路由）。助手文本与场景引导逐字流式
  （``phase: start → delta×N → end``）；其余整帧落地（单个 ``phase: end``）。
- ``thinking`` 思考行（ReAct 思维链 thought），逐字流式。
- ``chain``    工具调用行（``event: tool_call`` running → ``tool_result`` 原地更新，
  这是协议中唯一允许的"原地更新"，与演示一致）。
- ``card``     生命周期卡片（``card_type: task/review/done/report/scene``），
  只推不改——创建、复核、完成、报告就绪各落一张新卡，绝不在原卡上改写。

卡片快照语义：卡片消息的 ``content`` 在**落卡那一刻**即固化任务状态快照
（JSON），后续任务状态翻转不会改写卡片；完成卡的报告状态、复核卡的 AI 预判
都取自快照，从而保证"只推不改"。

事件总线 ``SessionEventBus``：进程内按会话扇出的唤醒信号。消息落库/更新时
由仓储层旁路触发 ``notify``，SSE 生成器订阅后被唤醒、即时重读并推帧，
把既往 2s 轮询的到达延迟压到近零；轮询仍作为兜底保留（断线自愈不依赖总线）。
"""
from __future__ import annotations

import asyncio
import json
import random
from typing import Any

# ---------- 顶层 type 常量 ----------
TYPE_MESSAGE = "message"
TYPE_THINKING = "thinking"
TYPE_CHAIN = "chain"
TYPE_CARD = "card"

# ---------- 卡片 card_type 常量 ----------
CARD_TASK = "task_card"
CARD_REVIEW = "review_card"
CARD_DONE = "done_card"
CARD_REPORT = "report_card"
CARD_SCENE = "scene_card"
CARD_DOC = "doc_card"

# kind → card_type（card 家族）
_CARD_KIND_MAP = {
    "task_card": CARD_TASK,
    "review_card": CARD_REVIEW,
    "done_card": CARD_DONE,
    "report_card": CARD_REPORT,
    "scene_card": CARD_SCENE,
    "doc_card": CARD_DOC,
}

# 逐字流式的分片节奏（演示：2~4 字一片）
_CHUNK_MIN, _CHUNK_MAX = 2, 4
# 超长文本的分片上限：避免一条长消息炸出上千帧
_MAX_CHUNKS = 140


class SessionEventBus:
    """进程内会话事件总线：按 session_id 扇出唤醒信号与**直播帧**。

    队列承载两类条目：
    - 电平唤醒信号（``True``）：消息落库/更新时由仓储层旁路触发 ``notify``，
      订阅方（SSE 生成器）被唤醒后重读仓储拿权威数据，天然去重且不丢帧；
    - 直播帧（``dict``）：LLM 逐 token 生成期间的实时帧（``publish``），
      SSE 生成器**原样透传**给客户端（首字延迟 = 模型 TTFB，不再等全文落库），
      帧内 ``message_id`` 与后续落库消息同 id，SSE 侧重放/轮询路径据此去重。
    多个订阅者（多标签页）各自独立队列；队列满（消费过慢）丢弃本次条目，
    由下一轮轮询/历史回放兜底，绝不阻塞消息落库主流程。
    """

    def __init__(self) -> None:
        self._queues: dict[str, set[asyncio.Queue]] = {}

    def subscribe(self, session_id: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=64)
        self._queues.setdefault(session_id, set()).add(q)
        return q

    def unsubscribe(self, session_id: str, q: asyncio.Queue) -> None:
        subs = self._queues.get(session_id)
        if not subs:
            return
        subs.discard(q)
        if not subs:
            self._queues.pop(session_id, None)

    def notify(self, session_id: str) -> None:
        """唤醒该会话的全部订阅者；队满即丢（电平语义，重读自愈）。"""
        for q in list(self._queues.get(session_id, ())):
            try:
                q.put_nowait(True)
            except asyncio.QueueFull:
                pass

    def publish(self, session_id: str, frame: dict[str, Any]) -> None:
        """向该会话全部订阅者推送一条直播帧（LLM 逐 token 实时帧）。

        SSE 生成器原样透传；帧必须自带 ``message_id``，落库复用同 id 后
        各连接按 id 幂等去重（本连接已透传过的不再经落库路径重推）。
        队满丢弃由历史回放兜底（直播窗口外的订阅者重连即见全量）。
        """
        for q in list(self._queues.get(session_id, ())):
            try:
                q.put_nowait(frame)
            except asyncio.QueueFull:
                pass

    def subscriber_count(self, session_id: str) -> int:
        return len(self._queues.get(session_id, ()))


def chunk_text(text: str, *, max_chunks: int = _MAX_CHUNKS) -> list[str]:
    """把文本切成逐字流式分片（2~4 字一片；超长文本均分压帧数）。

    确定性分片（不引入随机源），保证可复现、可断言。
    """
    n = len(text)
    if n == 0:
        return []
    if n <= max_chunks * _CHUNK_MIN:
        chunks: list[str] = []
        i = 0
        k = 0
        span = _CHUNK_MAX - _CHUNK_MIN + 1
        while i < n:
            step = _CHUNK_MIN + (k % span)  # 2,3,4 循环，确定性
            k += 1
            chunks.append(text[i:i + step])
            i += step
        return chunks
    size = (n + max_chunks - 1) // max_chunks
    return [text[i:i + size] for i in range(0, n, size)]


def chunk_text_random(text: str, *, max_chunks: int = _MAX_CHUNKS) -> list[str]:
    """随机节奏分片（2~4 字），用于真实播放观感；测试请用 chunk_text。"""
    n = len(text)
    if n == 0:
        return []
    chunks: list[str] = []
    i = 0
    while i < n and len(chunks) < max_chunks:
        step = random.randint(_CHUNK_MIN, _CHUNK_MAX)
        chunks.append(text[i:i + step])
        i += step
    if i < n:  # 超出帧数上限后整段并入末片
        chunks[-1] = chunks[-1] + text[i:] if chunks else text[i:]
    return chunks


def _parse_json(raw: str | None) -> Any:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None


def _message_frame(msg: dict[str, Any], *, phase: str, delta: str | None = None) -> dict[str, Any]:
    m = msg.get("message") or {}
    frame: dict[str, Any] = {
        "type": TYPE_MESSAGE,
        "message_id": msg.get("message_id"),
        "phase": phase,
        "message": {k: v for k, v in m.items() if k != "content" or phase == "end"},
    }
    if delta is not None:
        frame["delta"] = delta
    return frame


def _is_streamable(msg: dict[str, Any]) -> bool:
    """助手文本与场景引导走逐字流式；用户/事件/路由整帧。"""
    if msg.get("role") == "assistant" and msg.get("kind") in ("text", "guidance"):
        return True
    return False


def _thinking_frames(msg: dict[str, Any], payload: dict[str, Any], *, live: bool) -> list[dict[str, Any]]:
    text = payload.get("text") or ""
    base = {
        "type": TYPE_THINKING,
        "message_id": msg.get("message_id"),
        "task_id": msg.get("task_id") or payload.get("task_id"),
        "turn": payload.get("turn"),
    }
    if not live:
        return [{**base, "phase": "end", "text": text}]
    frames: list[dict[str, Any]] = [{**base, "phase": "start"}]
    for piece in chunk_text(text):
        frames.append({**base, "phase": "delta", "delta": piece})
    frames.append({**base, "phase": "end", "text": text})
    return frames


def _chain_frames(msg: dict[str, Any], payload: dict[str, Any]) -> list[dict[str, Any]]:
    done = bool(payload.get("done"))
    frame = {
        "type": TYPE_CHAIN,
        "event": "tool_result" if done else "tool_call",
        "message_id": msg.get("message_id"),
        "task_id": msg.get("task_id") or payload.get("task_id"),
        "turn": payload.get("turn"),
        "call_id": payload.get("call_id"),
        "name": payload.get("name"),
        "args": payload.get("args") or {},
        "done": done,
    }
    if done:
        frame["content"] = payload.get("content")
        frame["is_error"] = bool(payload.get("is_error"))
    return [frame]


def _card_frame(msg: dict[str, Any], card_type: str) -> dict[str, Any]:
    payload = _parse_json(msg.get("content")) or {}
    if not isinstance(payload, dict):
        payload = {"raw": msg.get("content")}
    return {
        "type": TYPE_CARD,
        "card_type": card_type,
        "message_id": msg.get("message_id"),
        "task_id": msg.get("task_id") or payload.get("task_id"),
        "payload": payload,
    }


def build_frames(msg: dict[str, Any], *, live: bool) -> list[dict[str, Any]]:
    """把一条消息视图转成协议帧序列。

    ``live=True``：可流式消息展开为 start/delta×N/end（逐字推送）；
    ``live=False``（历史回放）：一律整帧，避免刷新后旧消息重新"打字"。
    """
    kind = msg.get("kind")
    role = msg.get("role")

    if kind in _CARD_KIND_MAP:
        return [_card_frame(msg, _CARD_KIND_MAP[kind])]

    if kind == "chain":
        payload = _parse_json(msg.get("content")) or {}
        if payload.get("type") == "thought":
            return _thinking_frames(msg, payload, live=live)
        return _chain_frames(msg, payload)

    if kind == "activity":  # 旧机械步骤行：历史数据干净切割，不再渲染
        return []

    # 普通消息：message 族
    content = msg.get("content") or ""
    message = {
        "role": role,
        "kind": kind,
        "content": content,
    }
    images = msg.get("images") or []
    if images:
        message["images"] = images
    wrapped = {"message_id": msg.get("message_id"), "message": message, "task_id": msg.get("task_id")}

    if live and _is_streamable(msg):
        frames = [_message_frame(wrapped, phase="start")]
        for piece in chunk_text(content):
            frames.append(_message_frame(wrapped, phase="delta", delta=piece))
        frames.append(_message_frame(wrapped, phase="end"))
        return frames
    return [_message_frame(wrapped, phase="end")]


def chain_content_digest(msg: dict[str, Any]) -> str:
    """chain 行的内容指纹（仅 content，不含任务快照）：用于 tool_result 原地更新检测。"""
    return str(msg.get("content") or "")
