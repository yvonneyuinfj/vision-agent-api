"""ReAct 轨节点：bootstrap_agent / react_agent / nudge / react_tools / exit_gate。

分工（决策 9：自主区与安检区在图上泾渭分明）：
- bootstrap_agent ~ react_tools：自主区——智能体看图、选场景、调能力、
  自救重试、检索知识、决定提交时机；
- exit_gate 之后：安检区——判级终裁（grading 节点）→ 复核门（human_review
  节点）→ 报告（generate_report 节点），全部复用固定图的确定性节点。

联合轨（mode=joint，决策 10）另含 await_children 节点：父图以 interrupt
暂停等待子任务，子任务全部落定后由 task_service 唤醒，节点续跑并把各子
任务结论聚合回状态。
"""
from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path
from typing import Any, Iterator

from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    RemoveMessage,
    SystemMessage,
    ToolMessage,
)
from langgraph.types import interrupt

from app.agents.llm.prompts import REACT_AGENT_TEMPLATE_ID, load_system_prompt
from app.agents.llm.service import _build_image_blocks, _text_of, _uses_anthropic_content
from app.agents.nodes.node_utils import error_state, node
from app.agents.nodes.select_capability import _load_input_images
from app.agents.react.tools import ReactToolbox
from app.capabilities.normalizers import max_confidence, max_severity, min_confidence
from app.core.errors import BizCode
from app.core.logger import get_logger
from app.services.deps import AgentContext

logger = get_logger("app.agents.react.nodes")

_NUDGE_CAP = 200  # agent 纯文本输出的留痕截断长度
_CHILD_DEFECTS_MSG_CAP = 15  # 唤醒消息中每个子任务的缺陷条目展示上限


async def _emit_chain(ctx: AgentContext, task_id: str, payload: dict[str, Any]) -> None:
    """ReAct 思考链事件落任务步骤流（node="chain"），经步骤监听旁路转发到
    会话服务落/更新思考链行（共识轮：对话内显示模型 ReAct 思考链）。

    全量内容落库不截断（共识 Q7）；留痕失败不阻断主流程。
    """
    if ctx.task_repo is None or not task_id:
        return
    try:
        await ctx.task_repo.append_step(task_id, "chain", json.dumps(payload, ensure_ascii=False))
    except Exception:  # noqa: BLE001
        logger.exception("react chain emit failed task=%s type=%s", task_id, payload.get("type"))


# --------------------------------------------------------------------------
# bootstrap：初始化对话（重试同 thread 时清空上一轮残留消息）
# --------------------------------------------------------------------------
# Studio chat 上传图片兜底：消息内嵌 base64 块的白名单与限额（与
# _load_input_images 的格式白名单对齐，仅 jpg/png）
_STUDIO_MIME_EXT = {"image/jpeg": ".jpg", "image/png": ".png"}
_STUDIO_IMAGE_CAP = 8
_STUDIO_IMAGE_MAX_BYTES = 25 * 1024 * 1024


def _b64_decode_loose(value: Any) -> bytes | None:
    """宽容 base64 解码：容忍空白、缺填充、URL 安全字母表；非法载荷返回 None。"""
    if isinstance(value, (bytes, bytearray)):
        return bytes(value)
    if not isinstance(value, str) or not value.strip():
        return None
    compact = "".join(value.split()).replace("-", "+").replace("_", "/")
    compact += "=" * (-len(compact) % 4)
    try:
        return base64.b64decode(compact, validate=True)
    except (ValueError, TypeError):
        return None


def _sniff_media(data: bytes) -> str | None:
    """魔数嗅探兜底（Studio 块可能不带 mime）。"""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8"):
        return "image/jpeg"
    return None


def _normalize_media(raw: str | None) -> str | None:
    if not raw:
        return None
    media = raw.strip().lower()
    if media == "image/jpg":
        media = "image/jpeg"
    return media


def _media_from_data_url(url: str) -> tuple[str | None, bytes | None]:
    header, _, payload = url.partition(",")
    media = header[len("data:"):].partition(";")[0].strip().lower() or None
    return media, _b64_decode_loose(payload)


def _block_image_payload(block: dict[str, Any]) -> tuple[str | None, bytes | None]:
    """解析单个多模态块 → (media_type, bytes)；不适用返回 (None, None)。

    覆盖已知全部形态（Studio 各版本/各 SDK 实测与 langgraph-api 规范块）：
    1. OpenAI：{"type":"image_url","image_url":{"url":"data:..."}}（或字符串简写）
    2. Anthropic：{"type":"image","source":{"type":"base64","media_type","data"}}
    3. LangChain 规范媒体块：{"type":"image","base64"|"data"|"url","mime_type"}
    """
    btype = block.get("type")
    if btype == "image_url":
        url = block.get("image_url")
        if isinstance(url, dict):
            url = url.get("url")
        if isinstance(url, str) and url.startswith("data:"):
            return _media_from_data_url(url)
        return None, None  # http(s) URL：不服务端抓取（SSRF 防御）
    if btype == "image":
        declared = _normalize_media(block.get("mime_type") or block.get("media_type"))
        source = block.get("source")
        if isinstance(source, dict) and source.get("type") == "base64":
            media = _normalize_media(source.get("media_type")) or declared
            return media, _b64_decode_loose(source.get("data"))
        for key in ("base64", "data"):
            if block.get(key) is not None:
                return declared, _b64_decode_loose(block.get(key))
        url = block.get("url")
        if isinstance(url, str) and url.startswith("data:"):
            media, data = _media_from_data_url(url)
            return media or declared, data
    return None, None


def _iter_message_image_blocks(messages: list[Any]) -> Iterator[tuple[str, bytes]]:
    """提取消息内嵌图片块，yield (media_type, bytes)。

    media_type 缺失时按魔数嗅探；白名单（仅 jpg/png）与大小限额在
    _extract_message_images 统一把关。
    """
    for msg in messages or []:
        content = getattr(msg, "content", None)
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            media, data = _block_image_payload(block)
            if not data:
                continue
            media = media or _sniff_media(data)
            if media:
                yield media, data


def _describe_message_blocks(messages: list[Any], cap: int = 300) -> str:
    """消息块特征摘要（诊断用：无图可提取时随错误信息回显，便于定位形态）。"""
    parts: list[str] = []
    for msg in messages or []:
        role = getattr(msg, "type", "?")
        content = getattr(msg, "content", None)
        if isinstance(content, str):
            parts.append(f"{role}[text]")
        elif isinstance(content, list):
            blocks: list[str] = []
            for b in content:
                if isinstance(b, dict):
                    keys = sorted(k for k in b if k != "type" and b.get(k) is not None)
                    blocks.append(f"{b.get('type', '?')}({','.join(keys)})")
                else:
                    blocks.append(type(b).__name__)
            parts.append(f"{role}[{';'.join(blocks)}]")
    summary = " ".join(parts) if parts else "（无消息）"
    return summary[:cap]


def _extract_message_images(
    messages: list[Any], ctx: AgentContext,
) -> tuple[list[tuple[str, bytes]], list[str]]:
    """Studio chat 上传图片兜底：消息 base64 块 → 落盘 → 业务 URI。

    主应用链路图片一律经接口上传为落盘 URI；本分支仅在落盘侧无可读图片
    时触发（LangGraph Studio chat tab 直跑图等场景）。返回
    （图片载荷列表, 落盘后的 input_uris）。
    """
    file_repo = ctx.file_repo
    if file_repo is None:
        return [], []
    images: list[tuple[str, bytes]] = []
    uris: list[str] = []
    for media_type, data in _iter_message_image_blocks(messages):
        if media_type not in _STUDIO_MIME_EXT:
            continue
        if len(images) >= _STUDIO_IMAGE_CAP:
            logger.warning("studio image extract: cap=%d reached, rest skipped", _STUDIO_IMAGE_CAP)
            break
        if len(data) > _STUDIO_IMAGE_MAX_BYTES:
            logger.warning("studio image extract: oversized (%dB) skipped", len(data))
            continue
        name = f"studio_{len(images) + 1}{_STUDIO_MIME_EXT[media_type]}"
        try:
            uri = file_repo.save_bytes("_studio", name, data, kind="inputs")
        except Exception:  # noqa: BLE001
            logger.exception("studio image extract: save failed")
            continue
        images.append((media_type, data))
        uris.append(uri)
    return images, uris


def _materialize_local_paths(
    uris: list[str], ctx: AgentContext,
) -> tuple[list[tuple[str, bytes]], list[str]]:
    """Studio graph 视图兜底：input_uris 里的本地绝对路径/file:// URL → 拷贝落盘。

    主应用提交任务一律服务端上传落盘，从不接受客户端 URI，此分支只对
    LangGraph Studio 手动填值场景生效。仅接受白名单扩展名（与
    _load_input_images 对齐：仅 jpg/png）；路径不存在/非文件逐条跳过。
    """
    file_repo = ctx.file_repo
    if file_repo is None:
        return [], []
    images: list[tuple[str, bytes]] = []
    new_uris: list[str] = []
    for raw in uris or []:
        candidate = raw[7:] if raw.startswith("file://") else raw
        if not candidate.startswith("/"):
            continue  # 业务 URI/相对路径不在此分支处理
        path = Path(candidate)
        if not path.is_file():
            logger.warning("studio local path skipped (not a file): %s", candidate)
            continue
        media_type = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png"}.get(
            path.suffix.lower()
        )
        if media_type is None:
            logger.warning("studio local path skipped (ext not jpg/png): %s", candidate)
            continue
        try:
            data = path.read_bytes()
        except OSError as exc:
            logger.warning("studio local path unreadable %s: %s", candidate, exc)
            continue
        if not data or len(data) > _STUDIO_IMAGE_MAX_BYTES:
            continue
        try:
            uri = file_repo.save_bytes("_studio", path.name, data, kind="inputs")
        except Exception:  # noqa: BLE001
            logger.exception("studio local path persist failed: %s", candidate)
            continue
        images.append((media_type, data))
        new_uris.append(uri)
    return images, new_uris


def _prepare_images_sync(
    raw_uris: list[Any], stale_msgs: list[Any], ctx: AgentContext,
) -> tuple[list[tuple[str, bytes]], list[str]]:
    """三层取图链（同步阻塞 IO 合集，整体在工作线程执行，不占事件循环）：
    落盘业务 URI → chat 消息 base64 块落盘 → 本地路径拷贝落盘。
    """
    input_uris: list[str] = [str(u) for u in (raw_uris or []) if u]
    images = _load_input_images(input_uris, ctx)
    if not images:
        # LangGraph Studio chat tab 上传：图片以 base64 块内嵌在消息里——
        # 落盘转 URI 后按正常链路继续（主应用路径不经过此分支）
        images, studio_uris = _extract_message_images(stale_msgs, ctx)
        if studio_uris:
            input_uris = studio_uris
            logger.info(
                "bootstrap_agent: recovered %d image(s) from chat message blocks", len(images)
            )
    if not images:
        # LangGraph Studio graph 视图：input_uris 填的本地绝对路径/file:// URL
        # ——拷贝落盘转业务 URI 后继续（主应用提交任务不接受客户端 URI）
        images, local_uris = _materialize_local_paths(input_uris, ctx)
        if local_uris:
            input_uris = local_uris
            logger.info(
                "bootstrap_agent: materialized %d local image path(s)", len(images)
            )
    return images, input_uris


@node("bootstrap_agent")
async def bootstrap_agent_node(state: dict[str, Any], ctx: AgentContext) -> dict[str, Any]:
    stale = state.get("messages") or []
    removals = [RemoveMessage(id=m.id) for m in stale if getattr(m, "id", None)]

    scenes: list[str] = []
    if ctx.registry is not None:
        scenes = sorted({m.scene for m in ctx.registry.list()})
    max_turns = int(getattr(ctx.settings, "react_max_turns", 10))
    # 模板 YAML 读取为同步文件 IO：移入工作线程（Studio 阻塞调用检测友好）
    system_prompt = await asyncio.to_thread(
        load_system_prompt,
        template_id=REACT_AGENT_TEMPLATE_ID,
        scenes_list=", ".join(scenes) if scenes else "（注册中心为空）",
        max_turns=str(max_turns),
    )

    images, input_uris = await asyncio.to_thread(
        _prepare_images_sync, state.get("input_uris") or [], stale, ctx
    )
    if not images:
        # 诊断：把收到的 input_uris 与消息块特征随错误回显（Studio 里直接可见），
        # 便于定位是"没传图"还是"传了但不可读"（具体跳过原因同时在
        # langgraph dev 终端以 warning 打印：invalid file uri / not a file 等）
        seen = _describe_message_blocks(stale)
        uris_desc = ", ".join(input_uris[:3]) if input_uris else "（空）"
        hint = (
            "提示：input_uris 必须是 JSON 数组；可填业务 URI（文件须真实存在于 "
            "data/ 根内）或本地绝对路径（仅 jpg/png），如 "
            "[\"/path/to/img.jpg\"]"
            if input_uris
            else "提示：请在 input_uris 填 JSON 数组，如 [\"/path/to/img.jpg\"]（本地绝对路径，仅 jpg/png）"
        )
        logger.warning(
            "bootstrap_agent: no readable images; input_uris=%s; message blocks: %s",
            uris_desc, seen,
        )
        return error_state(
            "bootstrap_agent", BizCode.INVALID_REQUEST,
            f"无可读检测图片，无法启动智能体检测（input_uris={uris_desc}；"
            f"消息块特征：{seen}）。{hint}",
        )

    hints: list[str] = []
    if state.get("scene"):
        hints.append(f"用户声明场景：{state['scene']}（强参考，但你仍需自行核实图片内容）")
    declared = [s for s in (state.get("scenes") or []) if isinstance(s, str) and s.strip()]
    if declared:
        hints.append(f"用户声明的候选场景列表：{', '.join(declared)}")
    if state.get("parameters"):
        hints.append(f"用户透传参数：{state['parameters']}")
    hint_text = ("\n".join(hints) + "\n") if hints else ""

    brief = (
        f"任务 {state.get('task_id', '-')}：请对附带的图片执行质量检测。"
        f"{hint_text}按系统提示词的标准流程自主执行，完成后必须调用 submit_final。"
    )
    if state.get("mode") == "joint":
        brief += (
            "\n\n【联合质检协议】这是多场景联合质检任务，请按以下协议执行：\n"
            "1) 为每个候选场景分别调用 launch_child_task 派发子任务"
            "（每个子任务是独立的单场景智能体，继承本任务图片）；\n"
            "2) 全部派发完毕后调用 await_children 等待——所有子任务落定后系统会自动唤醒你，"
            "等待期间不要轮询、不要反复调用 get_child_status；\n"
            "3) 被唤醒后，综合各子任务结论：汇总缺陷条目、对比各场景结果、"
            "必要时调用 retrieve_knowledge 查询综合判级依据，"
            "最后调用 submit_final 提交联合结论（在 analysis 中说明各场景检测情况与汇总理由）。\n"
            "不要在父任务内直接调用 invoke_capability 替代子任务。"
        )

    llm = ctx.llm
    model = llm.react_model if llm is not None else None
    if model is None:
        return error_state(
            "bootstrap_agent", BizCode.LLM_UNAVAILABLE, "未配置可用模型，无法启动智能体检测"
        )
    content = _build_image_blocks(
        anthropic=_uses_anthropic_content(model), prompt=brief, images=images
    )

    return {
        "messages": removals
        + [SystemMessage(content=system_prompt), HumanMessage(content=content)],
        # Studio chat 兜底落盘的新 URI 回写（下游工具按 input_uris 重读图片）
        "input_uris": input_uris,
        # 运行期通道归零（重试同 thread 时检查点可能残留上一轮取值）
        "loop_count": 0,
        "capability_calls": 0,
        "scenes_tried": [],
        "submitted": False,
        "submit_payload": None,
        "error": None,
        # 联合轨通道同样归零（同 thread 重试残留会使派发幂等误判）
        "children_launched": {},
        "await_children_requested": False,
        "children_settled": 0,
    }


# --------------------------------------------------------------------------
# react_agent：循环心脏（预算检查 + 工具绑定调用 + 每轮留痕）
# --------------------------------------------------------------------------
def _render_status_bar(
    state: dict[str, Any], loop: int, max_turns: int, max_calls: int
) -> str:
    """代码维护的 Agent 状态栏（ch02：轨迹尾部显式状态投影，替代模型自行数数）。

    数据全部来自 state 通道（scenes_tried/capability_calls/knowledge/standardized），
    不含任何模型生成内容；每轮以 transient 消息追加到上下文末尾（不入检查点）。
    """
    calls = int(state.get("capability_calls") or 0)
    tried = state.get("scenes_tried") or []
    knowledge = state.get("knowledge") or []
    defects = state.get("standardized") or []

    lines = [
        "<agent_status>",
        f"任务进度：第 {loop + 1}/{max_turns} 轮；能力调用已用 {calls}/{max_calls} 次",
    ]
    if tried:
        tried_desc = "；".join(
            f"{t.get('scene')}（{'检出 {} 项'.format(t['defect_count']) if t.get('ok') else t.get('summary') or '失败'}）"
            for t in tried
            if isinstance(t, dict)
        )
        lines.append(f"已检测场景：{tried_desc}")
    else:
        lines.append("已检测场景：尚未检测")
    if defects:
        types: dict[str, int] = {}
        for d in defects:
            if isinstance(d, dict):
                key = str(d.get("defect_type") or d.get("label") or "未知")
                types[key] = types.get(key, 0) + 1
        defect_desc = "、".join(f"{k}×{v}" for k, v in types.items())
        lines.append(f"最近检出（{len(defects)} 项）：{defect_desc}")
    lines.append(f"已检索知识：{len(knowledge)} 条")

    # 下一步建议（确定性规则，非 LLM 生成）
    if not tried:
        lines.append("下一步：判断场景后调用 invoke_capability")
    elif defects and not knowledge:
        lines.append("下一步：如需判级依据/成因，可调用 retrieve_knowledge，然后 submit_final")
    else:
        lines.append("下一步：核对结果后调用 submit_final 提交（有漏检/误报矛盾时置 review_required）")
    lines.append("</agent_status>")
    return "\n".join(lines)


def _build_agent_node(ctx: AgentContext, toolbox: ReactToolbox):
    max_turns = int(getattr(ctx.settings, "react_max_turns", 10))
    max_calls = int(getattr(ctx.settings, "react_max_capability_calls", 3))
    image_policy = str(getattr(ctx.settings, "react_image_context", "always")).strip().lower()

    @node("react_agent")
    async def react_agent_node(state: dict[str, Any], _ctx: AgentContext) -> dict[str, Any]:
        loop = int(state.get("loop_count") or 0)
        if loop >= max_turns:
            return error_state(
                "react_agent",
                BizCode.INTERNAL_ERROR,
                f"ReAct 循环预算耗尽（{max_turns} 轮）仍未提交，任务终止",
            )

        llm = _ctx.llm
        model = llm.react_model if llm is not None else None
        if model is None:
            return error_state(
                "react_agent", BizCode.LLM_UNAVAILABLE, "未配置可用模型，智能体无法继续"
            )

        bound = model.bind_tools(toolbox.lc_tools)
        remaining = max_turns - loop
        # 状态栏（含剩余预算）仅本次调用注入，不写入 messages 通道（避免检查点累积冗余）
        hint = HumanMessage(
            content=(
                f"{_render_status_bar(state, loop, max_turns, max_calls)}\n"
                f"[系统] 剩余循环预算 {remaining} 轮。完成检测后必须调用 submit_final 提交；"
                "若图片不属于任何支持场景，调用 submit_final 并填写 refusal_reason。"
            )
        )
        # 图片折叠策略（react_image_context=fold）：首轮场景分析（已产生 AI 回复）
        # 之后，历史图片块折叠为占位文本压缩多模态 token；默认 always 保持视觉证据
        history = list(state.get("messages") or [])
        if image_policy == "fold" and any(isinstance(m, AIMessage) for m in history):
            from app.agents.llm.service import placeholder_image_blocks

            history = placeholder_image_blocks(history, keep_last_k=0)
        # hint 仅本次调用注入，不写入 messages 通道（避免检查点累积冗余）
        reply = await bound.ainvoke(history + [hint])

        calls = getattr(reply, "tool_calls", None) or []
        text = _text_of(reply.content).strip()
        if calls:
            detail = "calls=" + ",".join(str(c.get("name")) for c in calls)
        else:
            detail = f"text={text[:_NUDGE_CAP]!r}"
        task_id = str(state.get("task_id") or "")
        if _ctx.task_repo is not None and task_id:
            try:
                await _ctx.task_repo.append_step(
                    task_id, "react_agent", f"turn {loop + 1}/{max_turns} {detail}"
                )
            except Exception:  # noqa: BLE001 - 留痕失败不阻断
                logger.exception("react_agent append_step failed")
            # ReAct 思考链：先思考行、后工具调用行（running 态，结果由
            # react_tools 的 tool_result 事件原地更新），经步骤监听进会话流
            turn = loop + 1
            if text:
                await _emit_chain(_ctx, task_id, {"type": "thought", "turn": turn, "text": text})
            for idx, call in enumerate(calls):
                await _emit_chain(_ctx, task_id, {
                    "type": "tool_call",
                    "turn": turn,
                    "call_id": str(call.get("id") or f"call_{turn}_{idx}"),
                    "name": str(call.get("name") or ""),
                    "args": call.get("args") or {},
                })

        return {"messages": [reply], "loop_count": loop + 1}

    return react_agent_node


# --------------------------------------------------------------------------
# nudge：智能体只说话不行动时的提醒（预算由 loop_count 兜底）
# --------------------------------------------------------------------------
@node("nudge")
async def nudge_node(state: dict[str, Any], ctx: AgentContext) -> dict[str, Any]:
    return {
        "messages": [
            HumanMessage(
                content=(
                    "请继续调用工具推进检测。完成检测后必须调用 submit_final 提交结论；"
                    "若图片不属于任何支持场景，调用 submit_final 并填写 refusal_reason。"
                    "不要只输出文字。"
                )
            )
        ]
    }


# --------------------------------------------------------------------------
# react_tools：工具分发（side_state 顺序合并 + 逐调用留痕）
# --------------------------------------------------------------------------
def _build_tools_node(ctx: AgentContext, toolbox: ReactToolbox):
    @node("react_tools")
    async def react_tools_node(state: dict[str, Any], _ctx: AgentContext) -> dict[str, Any]:
        messages = state.get("messages") or []
        last_ai = next(
            (m for m in reversed(messages) if isinstance(m, AIMessage) and getattr(m, "tool_calls", None)),
            None,
        )
        if last_ai is None:
            return error_state("react_tools", BizCode.INTERNAL_ERROR, "无可执行的工具调用")

        tool_msgs: list[Any] = []
        side: dict[str, Any] = {}
        working: dict[str, Any] = dict(state)

        for call in last_ai.tool_calls:
            name = str(call.get("name") or "")
            args = call.get("args") or {}
            call_id = call.get("id") or f"call_{name}_{len(tool_msgs)}"
            impl = toolbox.impls.get(name)
            is_error = False
            if impl is None:
                content = f"未知工具：{name}（可用：{', '.join(toolbox.impls)}）"
                is_error = True
            else:
                try:
                    content, impl_side = await impl(args, working, _ctx)
                except Exception as exc:  # noqa: BLE001 - 工具异常转为可读结果交回智能体
                    logger.exception("react tool %s failed", name)
                    content, impl_side = f"工具执行异常：{exc}", {}
                    is_error = True
                side.update(impl_side)
                working.update(impl_side)
            tool_msgs.append(ToolMessage(content=content, tool_call_id=call_id, name=name))

            task_id = str(state.get("task_id") or "")
            if _ctx.task_repo is not None and task_id:
                try:
                    await _ctx.task_repo.append_step(
                        task_id,
                        f"tool:{name}",
                        f"args={args} -> {str(content)[:_NUDGE_CAP]}",
                    )
                except Exception:  # noqa: BLE001
                    logger.exception("react_tools append_step failed")
                # 思考链：观察结果按 call_id 原地更新对应工具行（共识 Q3）；
                # args 随结果重发——更新是整行覆盖，不带 args 会丢 IN 段
                await _emit_chain(_ctx, task_id, {
                    "type": "tool_result",
                    "turn": int(state.get("loop_count") or 1),
                    "call_id": str(call_id),
                    "name": name,
                    "args": args,
                    "content": str(content),
                    "is_error": is_error,
                })

        return {"messages": tool_msgs, **side}

    return react_tools_node


# --------------------------------------------------------------------------
# await_children：联合轨等待节点（决策 10：子任务落定事件注入的暂停/唤醒点）
# --------------------------------------------------------------------------
@node("await_children")
async def await_children_node(state: dict[str, Any], ctx: AgentContext) -> dict[str, Any]:
    """interrupt 暂停等待子任务；唤醒后聚合各子任务结论回状态。

    暂停载荷：{"kind": "await_children", "children": {scene: task_id}}——
    task_service 据此识别"等子任务"与"人工复核"两类中断。
    唤醒载荷：{"children": [{task_id, scene, status, grade_level,
    grade_summary, defects}, ...]}——由 task_service._maybe_resume_parent 组装。
    """
    children = state.get("children_launched") or {}
    if not children:
        return error_state(
            "await_children", BizCode.INVALID_REQUEST, "尚未派发任何子任务，无法进入等待"
        )

    payload = {"kind": "await_children", "children": dict(children)}
    resume_value = interrupt(payload)

    # ---- 被唤醒：聚合子任务结论 ----
    outcomes = resume_value.get("children") if isinstance(resume_value, dict) else None
    if not isinstance(outcomes, list):
        outcomes = []

    # 确定性聚合：子任务标准化缺陷并入提交基线（标注来源场景）；
    # 智能体若不手写 defects，submit_final 默认采用该聚合结果（产物同构，决策 3）
    merged: list[Any] = list(state.get("standardized") or [])
    for outcome in outcomes:
        scene = outcome.get("scene")
        for defect in outcome.get("defects") or []:
            if not isinstance(defect, dict):
                continue
            entry = dict(defect)
            entry.setdefault("scene", scene)
            merged.append(entry)

    # 配对 await_children 工具调用的 tool_call_id（ToolMessage 必须配对）
    call_id: str | None = None
    for message in reversed(state.get("messages") or []):
        for call in getattr(message, "tool_calls", None) or []:
            if call.get("name") == "await_children":
                call_id = call.get("id")
        if call_id:
            break

    digest = [
        {
            "task_id": outcome.get("task_id"),
            "scene": outcome.get("scene"),
            "status": outcome.get("status"),
            "grade_level": outcome.get("grade_level"),
            "grade_summary": outcome.get("grade_summary"),
            "defects": (outcome.get("defects") or [])[:_CHILD_DEFECTS_MSG_CAP],
        }
        for outcome in outcomes
    ]
    content = json.dumps(
        {
            "message": "所有子任务已落定，各子任务结论如下。请综合结果并调用 submit_final 提交联合结论。",
            "children": digest,
        },
        ensure_ascii=False,
    )
    tool_msg = ToolMessage(
        content=content, tool_call_id=call_id or "await_children", name="await_children"
    )

    if ctx.task_repo is not None and state.get("task_id"):
        try:
            brief = "、".join(
                f"{o.get('scene')}:{o.get('status')}/{o.get('grade_level')}" for o in digest
            )
            await ctx.task_repo.append_step(
                state["task_id"], "await_children", f"唤醒：{len(digest)} 个子任务落定（{brief}）"
            )
        except Exception:  # noqa: BLE001 - 留痕失败不阻断
            logger.exception("await_children append_step failed")

    return {
        "messages": [tool_msg],
        "standardized": merged,
        "children_settled": len(outcomes),
        "await_children_requested": False,
    }


def _entry_match_keys(entry: dict[str, Any]) -> set[tuple[Any, ...]]:
    """条目身份键：defect_id、location.bbox、(类别键, 置信度) 三层。

    类别键取 defect_type/type/label/name（digest 与宽解析四种历史形态）；
    (类别, 标量) 组合键仅在两者齐备时生成——纯类别匹配会把手写同类
    条目误判为模型条目，宁缺匹配也不误标。
    """
    keys: set[tuple[Any, ...]] = set()
    if entry.get("defect_id"):
        keys.add(("id", entry["defect_id"]))
    bbox = (entry.get("location") or {}).get("bbox")
    if bbox:
        keys.add(("bbox", tuple(bbox)))
    type_key = entry.get("defect_type") or entry.get("type") or entry.get("label") or entry.get("name")
    scalar = entry.get("confidence", entry.get("score", entry.get("value")))
    if type_key is not None and scalar is not None:
        keys.add(("kv", type_key, scalar))
    return keys


def _bbox_center(bbox: list[Any]) -> tuple[float, float]:
    return ((bbox[0] + bbox[2]) / 2.0, (bbox[1] + bbox[3]) / 2.0)


def _bbox_size(bbox: list[Any]) -> float:
    return max(1.0, abs(bbox[2] - bbox[0]), abs(bbox[3] - bbox[1]))


def _backfill_image_name(model_entries: list[Any], submitted: list[Any]) -> list[Any]:
    """多图溯源回填：提交条目缺 image_name 时从模型原始条目回填（平台兜底）。

    智能体 submit_final 复制条目时常丢 image_name——多图 fan-out 任务里
    无图名的框会被前端全图叠加（bbox 混叠事故 2026-09-17 T11415637844088391）。
    匹配两级（不依赖提示词，纯平台侧保证）：
    1. 身份键精确匹配：defect_id / bbox / (类别, 置信度)；
    2. 邻域回退：中心点落在模型框自身尺寸内的最近条目（智能体微调
       坐标/改写小数位时仍可归属），继承其 image_name。
    视觉手写条目（远离任何模型框）保持无图名。
    """
    by_key: dict[tuple[Any, ...], str] = {}
    tagged: list[tuple[list[Any], str]] = []  # (bbox, image_name) 邻域回退池
    for e in model_entries:
        if not isinstance(e, dict):
            continue
        name = str(e.get("image_name") or "")
        if not name:
            continue
        for key in _entry_match_keys(e):
            by_key.setdefault(key, name)
        bbox = (e.get("location") or {}).get("bbox")
        if isinstance(bbox, (list, tuple)) and len(bbox) == 4:
            tagged.append((list(bbox), name))
    if not by_key and not tagged:
        return list(submitted)
    out: list[Any] = []
    for entry in submitted:
        if isinstance(entry, dict) and not entry.get("image_name"):
            hit = next((by_key[k] for k in _entry_match_keys(entry) if k in by_key), None)
            if hit is None and tagged:
                bbox = (entry.get("location") or {}).get("bbox")
                if isinstance(bbox, (list, tuple)) and len(bbox) == 4:
                    cx, cy = _bbox_center(list(bbox))
                    reach = _bbox_size(list(bbox))
                    near = [
                        (abs(_bbox_center(mb)[0] - cx) + abs(_bbox_center(mb)[1] - cy), name)
                        for mb, name in tagged
                        if abs(_bbox_center(mb)[0] - cx) <= reach and abs(_bbox_center(mb)[1] - cy) <= reach
                    ]
                    if near:
                        hit = min(near)[1]
            if hit:
                entry = {**entry, "image_name": hit}
        out.append(entry)
    return out


def _tag_agent_visual(model_entries: list[Any], submitted: list[Any]) -> list[Any]:
    """裁决溯源：手写条目打 source=agent_visual 标记。

    提交条目与能力/子任务原始条目（覆盖前的 standardized）按身份键
    任一匹配即视为模型检出（原样复制/改类/改严重度）；对不上的条目
    是智能体凭视觉证据手写的补充——模型标注图上没有对应框，报告口径
    对照（模型标注 M 处 vs 终裁 N 处）需要这个标记把差异讲清楚。
    已有 source 标记（如人工 manual）不覆盖。
    """
    model_keys: set[tuple[Any, ...]] = set()
    for e in model_entries:
        if isinstance(e, dict):
            model_keys |= _entry_match_keys(e)
    if not model_keys:
        # 模型侧无任何条目（纯 classifications/指标或拒检旁路）：
        # 不可判定，不打标，避免误伤
        return list(submitted)

    tagged: list[Any] = []
    for entry in submitted:
        if (
            not isinstance(entry, dict)
            or entry.get("source")
            or (_entry_match_keys(entry) & model_keys)
        ):
            tagged.append(entry)
        else:
            tagged.append({**entry, "source": "agent_visual"})
    return tagged


# --------------------------------------------------------------------------
# exit_gate：出口安检第一道——拒检分流 + 终态完备性 + 判级线索终裁准备
# --------------------------------------------------------------------------
@node("exit_gate")
async def exit_gate_node(state: dict[str, Any], ctx: AgentContext) -> dict[str, Any]:
    payload = state.get("submit_payload") or {}

    if payload.get("refused"):
        reason = str(payload.get("reason") or "图像不属于任何支持的检测场景")
        return error_state(
            "exit_gate",
            BizCode.SCENARIO_NOT_SUPPORTED,
            f"智能体拒检：{reason}。如需场景识别帮助，请使用质检助手会话页获取引导。",
        )

    model_entries = state.get("standardized") or []
    defects = payload.get("defects")
    if defects is None:
        defects = model_entries
    if not isinstance(defects, list):
        defects = model_entries
    if defects is not model_entries:
        # 多图溯源回填：智能体复制模型条目时丢的 image_name 按身份键补齐
        # （前端按图过滤依赖它，缺了会全图叠加——bbox 混叠）
        defects = _backfill_image_name(model_entries, defects)
        # 智能体裁决溯源：提交列表与模型原始条目对不上的手写条目打标
        defects = _tag_agent_visual(model_entries, defects)
    if not defects and not int(state.get("capability_calls") or 0) and not int(
        state.get("children_settled") or 0
    ):
        # 联合轨父任务不直接调能力，以落定子任务数为完备性凭据（决策 10）
        return error_state(
            "exit_gate", BizCode.INTERNAL_ERROR, "未执行任何检测即提交，终态不完备，任务终止"
        )

    hint = state.get("grade_hint") or {}
    family = hint.get("family") or "defect"

    grade_hint = {
        "family": family,
        "status": hint.get("status", "unknown"),
        "summary": str(payload.get("summary") or "").strip()
        or hint.get("summary")
        or ("未发现异常" if not defects else f"检出 {len(defects)} 项"),
        "max_severity": max_severity(defects) if family == "defect" else None,
        "min_confidence": min_confidence(defects) if family == "defect" else None,
        # 判级复核门依据（需求：检测结果低于阈值才转人工）：取最高置信度
        "max_confidence": max_confidence(defects) if family == "defect" else None,
        # 智能体异议信号透传至判级终裁（共识：异议恒改判人工复核）
        "review_required": bool(payload.get("review_required") or False),
        "review_reason": str(payload.get("review_reason") or "").strip(),
        # 口径对照数据源：能力/子任务原始检出数（覆盖前 standardized）。
        # 报告图注用它解释「模型标注图框数 vs 终裁列表数」的差异来源
        "model_defect_count": len(model_entries),
    }

    out: dict[str, Any] = {"standardized": defects, "grade_hint": grade_hint}
    analysis = str(payload.get("analysis") or "").strip()
    if analysis:
        out["metric_explanations"] = analysis  # 报告"指标解释"章节采用智能体分析段
    return out
