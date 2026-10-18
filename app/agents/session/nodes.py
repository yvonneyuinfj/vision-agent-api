"""会话轨节点：session_agent（决策）与 session_tools（分发）。

会话回合语义：智能体产出无工具调用的文本回复即本回合结束（与任务轨
"必须以 submit_final 收尾"不同）；循环预算耗尽时强制输出文本兜底回复，
保证用户永远拿得到答复。

过程可视化（共识轮：对话内显示模型 ReAct 思考链）：会话智能体自身
的思考与工具调用同样以 kind=chain 行落库——思考落 {"type":"thought"}，
工具调用先落 {"type":"tool_call", "done": false}（running 态），执行完
按确定性消息 id 原地更新为含结果的完成行（共识 Q3-B / Q6-A）。
"""
from __future__ import annotations

import json
from typing import Any

from langchain_core.messages import AIMessage, AnyMessage, SystemMessage, ToolMessage
from langchain_core.tools import StructuredTool

from app.agents.llm.prompts import SESSION_AGENT_TEMPLATE_ID, load_system_prompt
from app.agents.llm.service import placeholder_image_blocks
from app.agents.session.tools import SessionToolImpl
from app.core.logger import get_logger
from app.models.session import KIND_CHAIN, ROLE_EVENT
from app.repositories.session_repo import new_message_id
from app.services.deps import AgentContext
from app.services.session_stream import TYPE_MESSAGE, TYPE_THINKING

logger = get_logger("app.agents.session.nodes")

# 直播 delta 帧攒批阈值：模型 token 粒度太细（1~3 字/片），攒到 N 字再推，
# 帧量与"一点一点出字"观感两全（不落库、不影响持久化）
_LIVE_FLUSH_CHARS = 6


def _thought_of(content: Any) -> str:
    """仅提取真正的文本思考块（text/str），thinking、tool_use 等一律跳过。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text") or ""))
            elif isinstance(block, str):
                parts.append(block)
        return "".join(parts)
    return ""


def _chain_message_id(session_id: str, call_id: str) -> str | None:
    """工具调用行的确定性消息 id：会话+call_id → 结果可原位更新同一行。"""
    if not call_id:
        return None
    return f"MC{session_id}{call_id}"[:64]


async def _emit_chain(
    repo: Any,
    session_id: str,
    payload: dict[str, Any],
    *,
    message_id: str | None = None,
    task_id: str | None = None,
) -> None:
    """会话级思考链行落库；任何失败不得打断回合。

    task_id 非空时该行并入对应任务的思考链组（前端按 task_id 聚合）。
    """
    if repo is None or not session_id or session_id == "-":
        return
    try:
        await repo.add_message(
            session_id,
            role=ROLE_EVENT,
            kind=KIND_CHAIN,
            content=json.dumps(payload, ensure_ascii=False),
            task_id=task_id,
            message_id=message_id,
        )
    except Exception:  # noqa: BLE001
        logger.exception("session %s emit chain failed (%s)", session_id, payload.get("type"))


async def _update_chain(
    repo: Any, session_id: str, message_id: str, payload: dict[str, Any],
) -> None:
    """工具调用行原地更新为完成态（共识 Q3-B）；未命中则降级新增，行不丢。

    payload 中的 task_id 会回填消息归属列（派发类调用落行时任务尚未创建）。
    """
    if repo is None or not session_id or session_id == "-" or not message_id:
        return
    content = json.dumps(payload, ensure_ascii=False)
    task_id = payload.get("task_id")
    try:
        if await repo.update_message(session_id, message_id, content, task_id=task_id):
            return
        await repo.add_message(
            session_id, role=ROLE_EVENT, kind=KIND_CHAIN, content=content,
            task_id=task_id, message_id=message_id,
        )
    except Exception:  # noqa: BLE001
        logger.exception("session %s update chain failed (%s)", session_id, message_id)


def _build_system_prompt() -> str:
    return load_system_prompt(template_id=SESSION_AGENT_TEMPLATE_ID)


async def _stream_agent_reply(
    bound: Any,
    messages: list[AnyMessage],
    *,
    live: Any,
    session_id: str,
    turn: int,
) -> tuple[Any, str | None, bool]:
    """astream 逐 token 生成 + 直播帧推送（首字延迟 = 模型 TTFB，不再等全文）。

    文本增量按 ``message`` 帧推（助手气泡逐字生长，与演示一致）；回合若以
    工具调用收尾，结束时以**同 id** 的 ``thinking`` end 帧把气泡变形为思考行
    （前端渲染模型按 message_id 整体替换，协议天然支持）。

    返回 ``(reply, live_id, streamed)``：streamed=True 表示已推过 start 帧，
    落库必须复用 live_id 以免 SSE 落库路径重推。无直播总线 / 模型不支持流式
    （测试桩）/ 未推帧即失败 → 回退 ``ainvoke``（live_id=None, streamed=False）。
    """
    astream = getattr(bound, "astream", None)
    if live is None or astream is None:
        return await bound.ainvoke(messages), None, False

    live_id = new_message_id()
    started = False
    buf: list[str] = []
    buf_len = 0

    def flush() -> None:
        nonlocal buf, buf_len
        if not buf:
            return
        live.publish(
            session_id,
            {"type": TYPE_MESSAGE, "message_id": live_id, "phase": "delta", "delta": "".join(buf)},
        )
        buf, buf_len = [], 0

    full: Any = None
    try:
        async for chunk in astream(messages):
            full = chunk if full is None else full + chunk
            text = _thought_of(getattr(chunk, "content", ""))
            if not text:
                continue
            if not started:
                live.publish(
                    session_id,
                    {
                        "type": TYPE_MESSAGE,
                        "message_id": live_id,
                        "phase": "start",
                        "message": {"role": "assistant", "kind": "text"},
                    },
                )
                started = True
            buf.append(text)
            buf_len += len(text)
            if buf_len >= _LIVE_FLUSH_CHARS:
                flush()
    except Exception as exc:  # noqa: BLE001
        if not started:
            raise  # 尚未推帧：交给外层统一错误回复（与 ainvoke 失败同路径）
        # 已推部分文本：以残文收口，客户端不停留在流式态
        logger.exception("session %s live stream broke mid-way; finalize partial", session_id)
        partial = _thought_of(getattr(full, "content", "")) if full is not None else ""
        live.publish(
            session_id,
            {
                "type": TYPE_MESSAGE,
                "message_id": live_id,
                "phase": "end",
                "message": {"role": "assistant", "kind": "text", "content": partial},
            },
        )
        return AIMessage(content=partial or f"抱歉，模型调用失败（{exc}），请稍后重试。"), live_id, True

    if full is None or not started:
        # 空流 / 无文本输出（如纯工具调用轮无思考文本）：未推过帧
        reply = full if full is not None else AIMessage(content="")
        return reply, None, False

    flush()
    if getattr(full, "tool_calls", None):
        # 工具轮：气泡 → 思考行变形（同 id 替换；思考行随后以同 id 落库）
        live.publish(
            session_id,
            {
                "type": TYPE_THINKING,
                "message_id": live_id,
                "phase": "end",
                "text": _thought_of(full.content).strip(),
                "turn": turn,
            },
        )
    else:
        # 最终文本回复：message end 收口（落库复用 live_id，见 streamed_reply_id）
        live.publish(
            session_id,
            {
                "type": TYPE_MESSAGE,
                "message_id": live_id,
                "phase": "end",
                "message": {"role": "assistant", "kind": "text", "content": _thought_of(full.content)},
            },
        )
    return full, live_id, True


def build_session_agent_node(
    ctx: AgentContext, lc_tools: list[StructuredTool], repo: Any = None, live: Any = None,
):
    """会话智能体节点：系统提示 + 历史消息 -> 文本回复或工具调用。

    ``live`` 为 SessionEventBus（直播帧通道）：模型逐 token 生成期间实时推帧，
    首字延迟 = 模型 TTFB；None 时行为退化为整回合生成后一次性落库（旧路径）。
    """

    async def session_agent_node(state: dict[str, Any]) -> dict[str, Any]:
        session_id = str(state.get("session_id") or "-")
        loop_count = int(state.get("loop_count") or 0)
        max_turns = int(getattr(ctx.settings, "session_max_turns", 6))

        model = ctx.llm.react_model if ctx.llm else None
        if model is None:
            return {
                "messages": [AIMessage(content="抱歉，模型服务暂不可用，请稍后再试。")],
                "loop_count": loop_count + 1,
            }

        if loop_count >= max_turns:
            logger.warning("session %s turn budget exhausted (%d)", session_id, loop_count)
            return {
                "messages": [
                    AIMessage(
                        content="本轮处理步骤较多，我先给出当前进展：请查看上方任务卡片，"
                        "或重新发送您的问题，我会继续协助。"
                    )
                ],
                "loop_count": loop_count + 1,
            }

        bound = model.bind_tools(lc_tools) if lc_tools else model
        messages: list[AnyMessage] = [SystemMessage(content=_build_system_prompt())]
        # 图片折叠（请求时视图）：历史含图消息只保留最近一条的图片块，
        # 更早图片替换为占位文本——持久化通道不受影响，仅压缩本轮请求
        messages.extend(placeholder_image_blocks(state.get("messages") or [], keep_last_k=1))
        try:
            reply, live_id, streamed = await _stream_agent_reply(
                bound, messages, live=live, session_id=session_id, turn=loop_count + 1,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("session %s agent llm call failed", session_id)
            return {
                "messages": [AIMessage(content=f"抱歉，模型调用失败（{exc}），请稍后重试。")],
                "loop_count": loop_count + 1,
            }

        if not isinstance(reply, AIMessage):
            reply = AIMessage(content=str(reply))
        tool_names = [tc.get("name") for tc in (reply.tool_calls or [])]
        logger.info(
            "session %s agent turn %d/%d calls=%s",
            session_id, loop_count + 1, max_turns, ",".join(tool_names) or "(text)",
        )
        updates: dict[str, Any] = {"messages": [reply], "loop_count": loop_count + 1}
        if reply.tool_calls:
            # 思考链：模型一边调工具一边输出的思考文本落 Think 行（全量不截断，共识 Q7）
            thought = _thought_of(reply.content).strip()
            if thought:
                # 复用直播 id（已逐字推过 → SSE 落库路径跳过重推）；未直播时正常新 id
                await _emit_chain(
                    repo, session_id,
                    {"type": "thought", "turn": loop_count + 1, "text": thought},
                    message_id=live_id,
                )
        elif streamed and live_id:
            # 最终文本回复已直播：落库复用同 id（session_service 读此通道值）
            updates["streamed_reply_id"] = live_id
        return updates

    return session_agent_node


def build_session_tools_node(impls: dict[str, SessionToolImpl], repo: Any = None):
    """会话工具分发节点：impl 层异常一律转为可读 ToolMessage，不中断回合。"""

    async def session_tools_node(state: dict[str, Any]) -> dict[str, Any]:
        messages = state.get("messages") or []
        session_id = str(state.get("session_id") or "-")
        last_ai = next(
            (m for m in reversed(messages) if isinstance(m, AIMessage) and m.tool_calls), None
        )
        if last_ai is None:
            return {"messages": []}

        tool_msgs: list[AnyMessage] = []
        for call in last_ai.tool_calls:
            name = str(call.get("name") or "")
            args = call.get("args") or {}
            call_id = call.get("id") or f"call_{name}_{len(tool_msgs)}"
            row_id = _chain_message_id(session_id, str(call_id))
            turn = int(state.get("loop_count") or 1)
            # 会话助手的工具行属"助手行动"（无任务归属，轻渲染）：派发/查详情
            # 与任务的关联由紧随的任务卡表达；任务自身的 ReAct 链才进任务组。
            # 工具行先落（running 态），执行完原地更新为完成行（共识 Q3-B）。
            await _emit_chain(
                repo, session_id,
                {"type": "tool_call", "turn": turn, "call_id": str(call_id),
                 "name": name, "args": args, "done": False},
                message_id=row_id,
            )
            impl = impls.get(name)
            is_error = False
            if impl is None:
                content = f"未知工具 {name}；可用工具：{', '.join(sorted(impls))}。"
                is_error = True
            else:
                try:
                    content = await impl(args, state)
                except Exception as exc:  # noqa: BLE001
                    logger.exception("session tool %s failed", name)
                    content = f"工具执行异常：{exc}"
                    is_error = True
            await _update_chain(
                repo, session_id, row_id or "",
                {"type": "tool_call", "turn": turn, "call_id": str(call_id),
                 "name": name, "args": args, "content": str(content),
                 "is_error": is_error, "done": True},
            )
            tool_msgs.append(ToolMessage(content=content, tool_call_id=call_id, name=name))
        return {"messages": tool_msgs}

    return session_tools_node
