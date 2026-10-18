"""会话服务：会话 CRUD + 消息发送（路由→智能体回合）+ 任务落定事件回注。

会话轨主流程（决策 14/16）：
1. 用户消息（可带图）落库；带图先跑 session_routing（含 null 分支）；
2. 路由结果作为观察拼入用户消息，交给会话智能体回合自主决策
   （确认派发 / 引导 / 追问）；
3. 回合非阻塞执行（asyncio.create_task），前端轮询消息列表取回复；
4. 会话派发的任务落定后由 task_service.on_settled 回注事件消息，
   唤醒会话线程主动回报（决策 10 的事件注入模式）。
"""
from __future__ import annotations

import asyncio
import json
import mimetypes
import uuid
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage

from app.agents.llm.service import _build_image_blocks, _text_of, _uses_anthropic_content
from app.agents.routers.scene_router import _match_by_keywords, parse_explicit_direction
from app.agents.session.graph import build_session_graph
from app.core.errors import BizCode, BusinessError
from app.core.logger import get_logger
from app.models.session import (
    KIND_CHAIN,
    KIND_DONE_CARD,
    KIND_EVENT,
    KIND_GUIDANCE,
    KIND_IMAGE,
    KIND_REPORT_CARD,
    KIND_REVIEW_CARD,
    KIND_ROUTING,
    KIND_SCENE_CARD,
    KIND_TASK_CARD,
    KIND_TEXT,
    ROLE_ASSISTANT,
    ROLE_EVENT,
    ROLE_USER,
    SESSION_ARCHIVED,
)
from app.models.task import STATUS_AWAITING_REVIEW
from app.repositories.session_repo import SessionRepo
from app.services.deps import AgentContext

logger = get_logger("app.services.session_service")

_THREAD_KEY = "configurable"

# 场景确认卡片阈值（优化轮 D5）：置信度 ≥0.7 静默直发（交给会话智能体派发），
# <0.7 或场景外（category=null）落确定性交互卡片，不再依赖模型即兴追问
_SCENE_CARD_THRESHOLD = 0.7


def new_session_id() -> str:
    return f"S{int(uuid.uuid4().hex[:14], 16)}"[:18]


def _media_type(filename: str) -> str:
    mt, _ = mimetypes.guess_type(filename or "")
    return mt or "image/jpeg"


class SessionService:
    def __init__(
        self,
        ctx: AgentContext,
        session_repo: SessionRepo,
        task_service: Any,  # TaskService（运行期注入，避免循环导入）
        checkpointer: Any | None = None,
        event_bus: Any | None = None,  # SessionEventBus（流式协议唤醒总线）
    ) -> None:
        self._ctx = ctx
        self._repo = session_repo
        self._task_service = task_service
        self._graph = build_session_graph(
            ctx, session_repo, task_service, checkpointer, live=event_bus
        )
        self._locks: dict[str, asyncio.Lock] = {}
        # 流式协议事件总线：消息落库旁路唤醒 → SSE 近零延迟推帧（agent_session）
        self.event_bus = event_bus
        # 任务 → 来源会话解析缓存（活动流旁路每步一次，避免重复查库；
        # 值可为 None 表示"确认无会话"，联合子任务经父链解析后同样缓存）
        self._task_session_cache: dict[str, str | None] = {}

    def _lock(self, session_id: str) -> asyncio.Lock:
        lock = self._locks.get(session_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[session_id] = lock
        return lock

    def _scenes(self) -> list[str]:
        if self._ctx.registry is None:
            return []
        return sorted({m.scene for m in self._ctx.registry.list()})

    # ---------- 会话 CRUD ----------
    async def create_session(self, title: str | None = None) -> dict[str, Any]:
        session_id = new_session_id()
        await self._repo.create(session_id, title or None)
        return {"session_id": session_id, "title": title or None, "status": "active"}

    async def list_sessions(self) -> list[dict[str, Any]]:
        rows = await self._repo.list_recent()
        return [
            {
                "session_id": s.id,
                "title": s.title,
                "status": s.status,
                "created_at": s.created_at.isoformat() if s.created_at else None,
                "updated_at": s.updated_at.isoformat() if s.updated_at else None,
                "last_message": (last or "")[:80] or None,
            }
            for s, last in rows
        ]

    async def get_session(self, session_id: str, *, with_messages: bool = True) -> dict[str, Any]:
        record = await self._repo.get(session_id)
        if record is None:
            raise BusinessError(BizCode.INVALID_REQUEST, f"session {session_id} not found", http_status=404)
        view: dict[str, Any] = {
            "session_id": record.id,
            "title": record.title,
            "status": record.status,
            "created_at": record.created_at.isoformat() if record.created_at else None,
            "updated_at": record.updated_at.isoformat() if record.updated_at else None,
        }
        if with_messages:
            view["messages"] = await self._message_views(session_id)
        return view

    async def rename_session(self, session_id: str, title: str) -> None:
        record = await self._repo.get(session_id)
        if record is None:
            raise BusinessError(BizCode.INVALID_REQUEST, f"session {session_id} not found", http_status=404)
        await self._repo.rename(session_id, title.strip()[:256])

    async def delete_session(self, session_id: str) -> None:
        record = await self._repo.get(session_id)
        if record is None:
            raise BusinessError(BizCode.INVALID_REQUEST, f"session {session_id} not found", http_status=404)
        await self._repo.delete(session_id)
        self._locks.pop(session_id, None)

    # ---------- 消息视图（任务卡片附带任务快照） ----------
    async def _message_views(self, session_id: str) -> list[dict[str, Any]]:
        rows = await self._repo.list_messages(session_id)
        task_ids = sorted({r.task_id for r in rows if r.task_id})
        snapshots: dict[str, dict[str, Any]] = {}
        if task_ids and self._ctx.task_repo is not None:
            records = await self._ctx.task_repo.get_by_ids(task_ids)
            for rec in records:
                grade = {}
                report = {}
                defects: list[Any] = []
                try:
                    grade = json.loads(rec.grade_json) if rec.grade_json else {}
                except (json.JSONDecodeError, TypeError):
                    pass
                try:
                    report_raw = getattr(rec, "report_json", None)
                    report = json.loads(report_raw) if report_raw else {}
                except (json.JSONDecodeError, TypeError):
                    pass
                try:
                    parsed_std = json.loads(rec.standardized_json) if rec.standardized_json else []
                    defects = parsed_std if isinstance(parsed_std, list) else []
                except (json.JSONDecodeError, TypeError):
                    pass
                snapshots[rec.id] = {
                    "task_id": rec.id,
                    "status": rec.status,
                    "scene": rec.scene,
                    "mode": rec.mode,
                    "grade_level": grade.get("level"),
                    "grade_summary": grade.get("summary"),
                    "created_at": rec.created_at.isoformat() if rec.created_at else None,
                    # 报告摘要卡（优化轮 D7）：判级徽章 + 缺陷计数 + AI 分析首段
                    "defect_count": len(defects),
                    "report_uri": report.get("uri"),
                    "analysis_lead": report.get("analysis_lead"),
                }
        views: list[dict[str, Any]] = []
        for r in rows:
            images: list[dict[str, Any]] = []
            if r.images_json:
                try:
                    parsed = json.loads(r.images_json)
                    images = parsed if isinstance(parsed, list) else []
                except (json.JSONDecodeError, TypeError):
                    images = []
            views.append(
                {
                    "message_id": r.id,
                    "session_id": r.session_id,
                    "role": r.role,
                    "kind": r.kind,
                    "content": r.content,
                    "images": images,
                    "task_id": r.task_id,
                    "task": snapshots.get(r.task_id) if r.task_id else None,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                }
            )
        return views

    async def list_messages(self, session_id: str) -> list[dict[str, Any]]:
        record = await self._repo.get(session_id)
        if record is None:
            raise BusinessError(BizCode.INVALID_REQUEST, f"session {session_id} not found", http_status=404)
        return await self._message_views(session_id)

    # ---------- 发送消息（路由 → 智能体回合，非阻塞） ----------
    async def send_message(
        self,
        session_id: str,
        content: str,
        files: list[tuple[str, bytes]] | None = None,
        scene: str = "",
    ) -> dict[str, Any]:
        """落库用户消息并触发智能体回合；回合异步执行，回复经消息列表取回。

        files 元素为 (filename, content)；仅接受图片扩展名。
        scene 为用户显式指定的场景码（可选）：命中注册表候选池时直接采用，跳过 VLM 路由。
        """
        record = await self._repo.get(session_id)
        if record is None:
            raise BusinessError(BizCode.INVALID_REQUEST, f"session {session_id} not found", http_status=404)

        text = (content or "").strip()
        images: list[dict[str, Any]] = []
        image_payloads: list[tuple[str, bytes]] = []  # (media_type, bytes)
        for name, data in files or []:
            ext = (name.rsplit(".", 1)[-1].lower() if "." in name else "")
            if ext not in {"jpg", "jpeg", "png", "tif", "tiff"} or not data:
                continue
            uri = self._ctx.file_repo.save_bytes("_upload", name, data, kind="inputs")
            images.append({"uri": uri, "name": name, "media_type": _media_type(name)})
            image_payloads.append((_media_type(name), data))

        if not text and not images:
            raise BusinessError(BizCode.INVALID_REQUEST, "message content or image required", http_status=422)

        await self._repo.add_message(
            session_id,
            role=ROLE_USER,
            kind=KIND_IMAGE if images else KIND_TEXT,
            content=text,
            images=images or None,
        )

        # 非阻塞：回合在后台执行（决策 16），前端轮询消息列表
        asyncio.create_task(self._run_user_turn(session_id, text, images, image_payloads, scene))
        return {"session_id": session_id, "accepted": True, "images": len(images)}

    async def _run_user_turn(
        self,
        session_id: str,
        text: str,
        images: list[dict[str, Any]],
        image_payloads: list[tuple[str, bytes]],
        scene_hint: str = "",
    ) -> None:
        routing: dict[str, Any] | None = None
        if image_payloads:
            pool = self._scenes()
            # 显式指定优先（与任务轨规则层对齐）：命中即免烧 VLM，
            # 灰度热部图会被 VLM 决策树误判为 dr（见 scene_router 模块注释）
            routing = self._resolve_forced_routing(scene_hint, text, images, pool)
            if routing is None and self._ctx.llm is not None:
                try:
                    routing = await self._ctx.llm.route_session_image(image_payloads, pool)
                except Exception:  # noqa: BLE001
                    logger.exception("session %s routing failed; continue without routing", session_id)

        # 会话级活动行（优化轮 D3）：路由判断过程即时可见，不再只是最终文本
        if image_payloads:
            if routing is None:
                route_text = "路由服务不可用，跳过场景识别"
            elif routing.get("category"):
                source = routing.get("source")
                if source == "explicit":
                    route_text = f"场景识别：{routing['category']}（用户指定）"
                elif source == "keyword":
                    route_text = f"场景识别：{routing['category']}（关键词规则）"
                else:
                    route_text = f"场景识别：{routing['category']}（置信度 {routing.get('confidence')}）"
            else:
                route_text = "场景识别：未匹配已知检测场景，进入场景确认"
            try:
                await self._repo.add_message(
                    session_id,
                    role=ROLE_EVENT,
                    kind=KIND_ROUTING,
                    content=json.dumps({"node": "session_routing", "message": route_text}, ensure_ascii=False),
                )
            except Exception:  # noqa: BLE001
                logger.exception("append routing activity failed session=%s", session_id)

        # 场景确认卡片（优化轮 D4/D5）：低置信或场景外 → 确定性交互卡片，
        # 不进入智能体回合（不再依赖模型即兴追问）；高置信维持静默直发
        if routing is not None:
            confidence = routing.get("confidence") or 0.0
            if not routing.get("category") or confidence < _SCENE_CARD_THRESHOLD:
                try:
                    await self._emit_scene_card(session_id, routing, images)
                except Exception:  # noqa: BLE001
                    logger.exception("emit scene card failed session=%s", session_id)
                return

        prompt_text = text or "请查看我上传的图片。"
        if routing is not None:
            prompt_text += "\n\n[场景路由结果]\n" + json.dumps(routing, ensure_ascii=False)

        model = self._ctx.llm.react_model if self._ctx.llm else None
        if model is not None and image_payloads:
            content: Any = _build_image_blocks(
                anthropic=_uses_anthropic_content(model), prompt=prompt_text, images=image_payloads
            )
        else:
            content = prompt_text

        await self._run_turn(session_id, HumanMessage(content=content), routing=routing)

    def _resolve_forced_routing(
        self,
        scene_hint: str,
        text: str,
        images: list[dict[str, Any]],
        pool: list[str],
    ) -> dict[str, Any] | None:
        """显式场景优先（跳过 VLM 路由），信号由强到弱三级：

        1) 请求携带的 scene 字段（前端能力下拉的结构化指定）；
        2) 消息文本中的「（指定方向：xxx）」能力标注（兼容旧契约）；
        3) SCENE_KEYWORDS 关键词规则（正文 + 文件名，与任务轨规则层同源）。

        仅接受注册表候选池内场景，池外/未命中返回 None 交回 VLM 路由与场景确认卡兜底。
        """
        hint = (scene_hint or "").strip().lower()
        if hint and hint in pool:
            return self._forced_route(hint, "explicit", "用户显式指定场景（请求 scene 字段）")
        marked = parse_explicit_direction(text)
        if marked and marked in pool:
            return self._forced_route(marked, "explicit", "用户显式指定场景（文本指定方向标注）")
        keyword = _match_by_keywords([text] + [str(img.get("name") or "") for img in images])
        if keyword and keyword in pool:
            return self._forced_route(keyword, "keyword", "关键词规则命中（与任务轨规则层同源）")
        return None

    @staticmethod
    def _forced_route(scene: str, source: str, reasoning: str) -> dict[str, Any]:
        """构造与 route_session_image 同形的路由结果（显式指定视为满置信）。"""
        return {
            "category": scene,
            "confidence": 1.0,
            "evidence": [],
            "alternative": None,
            "reasoning": reasoning,
            "source": source,
        }

    async def _emit_scene_card(
        self, session_id: str, routing: dict[str, Any], images: list[dict[str, Any]]
    ) -> None:
        """落场景确认交互卡片（优化轮 D4）：

        - candidates 模式：有主候选但置信度不足 → 主候选（带置信度）+ 次候选排序；
        - fingerprint 模式：场景外（category=null）→ 全场景指纹选择 + 采集指引入口。
        前端渲染卡片与按钮；用户选定后以强制指定消息触发派发回合。
        """
        category = routing.get("category")
        confidence = routing.get("confidence") or 0.0
        alternative = routing.get("alternative")
        if category:
            mode = "candidates"
            candidates: list[dict[str, Any]] = [{"scene": category, "confidence": confidence}]
            if alternative and alternative != category:
                candidates.append({"scene": alternative, "confidence": None})
        else:
            mode = "fingerprint"
            candidates = [{"scene": s, "confidence": None} for s in self._scenes()]
        payload = {
            "mode": mode,
            "candidates": candidates,
            "reasoning": routing.get("reasoning") or "",
            "image": images[0] if images else None,
        }
        await self._repo.add_message(
            session_id,
            role=ROLE_ASSISTANT,
            kind=KIND_SCENE_CARD,
            content=json.dumps(payload, ensure_ascii=False),
        )

    async def _run_turn(
        self,
        session_id: str,
        trigger: Any,
        *,
        routing: dict[str, Any] | None = None,
    ) -> None:
        """执行一个会话回合：触发消息入图 -> 循环到文本回复 -> 落库回复。"""
        async with self._lock(session_id):
            config = {_THREAD_KEY: {"thread_id": session_id}}
            # loop_count 每回合归零（LastValue 通道，输入即覆盖）；
            # streamed_reply_id 同样按回合重置（防上一回合直播 id 残留误复用）
            input_state = {
                "messages": [trigger],
                "session_id": session_id,
                "loop_count": 0,
                "streamed_reply_id": "",
            }
            reply_text = ""
            streamed_id: str | None = None
            try:
                async for _event in self._graph.astream(input_state, config=config, stream_mode="updates"):
                    pass
                snapshot = await self._graph.aget_state(config)
                values = (snapshot.values if snapshot else {}) or {}
                for m in reversed(values.get("messages") or []):
                    if isinstance(m, AIMessage) and not m.tool_calls:
                        reply_text = _text_of(m.content).strip()
                        break
                streamed_id = values.get("streamed_reply_id") or None
            except Exception as exc:  # noqa: BLE001
                logger.exception("session %s turn failed", session_id)
                reply_text = f"抱歉，本轮处理出现异常（{exc}），请稍后重试。"
                streamed_id = None

            if not reply_text:
                reply_text = "（本轮未生成回复，请重试）"
            kind = KIND_GUIDANCE if (routing is not None and not routing.get("category")) else KIND_TEXT
            # streamed_id 非空：最终回复已逐字直播，落库复用同 id → SSE 落库路径跳过重推
            await self._repo.add_message(
                session_id, role=ROLE_ASSISTANT, kind=kind, content=reply_text,
                message_id=streamed_id,
            )

    # ---------- 任务落定事件回注（决策 10） ----------
    _REVIEW_ACTION_LABEL = {
        "confirm": "同意 AI 判级",
        "revise": "改判 / 修正",
        "requeue": "退回重检",
        "reject": "驳回",
    }

    async def on_task_review_accepted(self, task_id: str, action: str) -> None:
        """复核受理即时回注：用户点击提交后第一时间在对话内看到受理回执。

        复核提交改为「即时受理 + 后台恢复」后，图恢复（含报告生成）在后台执行，
        本事件承担"提交成功"的即时反馈；任务落定后另有完成/报告卡推送。
        """
        try:
            session_id = await self._repo.find_session_by_task(task_id)
        except Exception:  # noqa: BLE001
            logger.exception("find_session_by_task failed for %s", task_id)
            return
        if not session_id:
            return
        label = self._REVIEW_ACTION_LABEL.get(action, action)
        text = f"[任务事件] 任务 {task_id} 的终裁复核决定已受理：{label}。任务已恢复执行，落定后将推送完成卡与报告。"
        await self._repo.add_message(
            session_id, role=ROLE_EVENT, kind=KIND_EVENT, content=text, task_id=task_id
        )

    async def on_task_settled(self, task_id: str) -> None:
        """task_service 落定钩子：快速返回，唤醒回合后台执行。"""
        try:
            session_id = await self._repo.find_session_by_task(task_id)
        except Exception:  # noqa: BLE001
            logger.exception("find_session_by_task failed for %s", task_id)
            return
        if not session_id:
            return
        asyncio.create_task(self._wake_with_task_event(session_id, task_id))

    async def _wake_with_task_event(self, session_id: str, task_id: str) -> None:
        record = await self._ctx.task_repo.get(task_id) if self._ctx.task_repo else None
        if record is None:
            return
        grade: dict[str, Any] = {}
        error: dict[str, Any] = {}
        try:
            grade = json.loads(record.grade_json) if record.grade_json else {}
            error = json.loads(record.error_json) if record.error_json else {}
        except (json.JSONDecodeError, TypeError):
            pass

        parts = [
            f"[任务事件] 任务 {task_id} 状态更新：{record.status}",
            f"场景：{record.scene or '未判定'}",
        ]
        if grade.get("level"):
            parts.append(f"判级：{grade.get('level')}（{grade.get('summary') or ''}）")
        if error.get("message"):
            parts.append(f"错误信息：{error.get('message')}")
        if record.status == "awaiting_review":
            parts.append(
                "该任务已暂停等待人工终裁复核；系统已在对话内向用户弹出「终裁复核」卡片，"
                "用户可在卡片中就地提交复核。"
            )
        elif record.status == "completed":
            parts.append("该任务已完成。")
        elif record.status == "failed":
            parts.append("该任务已失败。")
        event_text = "；".join(parts) + " 请按系统提示词的任务事件回报规则主动向用户回报。"

        await self._repo.add_message(
            session_id, role=ROLE_EVENT, kind=KIND_EVENT, content=event_text, task_id=task_id
        )
        # 生命周期卡片只推不改（对齐玻璃疏行演绎演示）：
        # - 暂停进入人工复核门 → 弹终裁复核卡（就地四动作复核，免离开对话）；
        # - 完成 / 失败落定 → 推完成卡（含报告状态），报告就绪再单独推报告卡，
        #   绝不回头改写完成卡。
        if record.status == "awaiting_review":
            try:
                await self._emit_review_card(session_id, task_id, record, grade)
            except Exception:  # noqa: BLE001
                logger.exception("emit review card failed session=%s task=%s", session_id, task_id)
        elif record.status in ("completed", "failed"):
            try:
                await self._emit_done_card(session_id, task_id, record, grade, error)
            except Exception:  # noqa: BLE001
                logger.exception("emit done card failed session=%s task=%s", session_id, task_id)
        await self._run_turn(session_id, HumanMessage(content=event_text))

    async def _emit_review_card(
        self, session_id: str, task_id: str, record: Any, grade: dict[str, Any]
    ) -> None:
        """落终裁复核交互卡片：任务暂停（awaiting_review）时由落定回注弹出。

        卡片本体（四动作 + 意见表单）由前端复用 ReviewPanel 就地渲染并提交，
        表单内容与复核页（ReviewPage + ReviewPanel）完全一致；缺陷明细随快照
        下发（AI 预判行），改判候选仍可按需拉任务详情。content 为落卡时刻的
        JSON 快照——此后任务状态翻转不改写本卡（只推不改）。
        """
        defects: list[Any] = []
        try:
            parsed_std = json.loads(record.standardized_json) if record.standardized_json else []
            defects = parsed_std if isinstance(parsed_std, list) else []
        except (json.JSONDecodeError, TypeError):
            defects = []
        # 缺陷摘要行（对齐演示复核卡 rc-drow：名称 + 严重度 + 置信度）
        defect_rows = [
            {
                "name": (d.get("defect_name_cn") or d.get("defect_type") or "未命名缺陷")
                if isinstance(d, dict) else str(d),
                "severity": d.get("severity") if isinstance(d, dict) else None,
                "confidence": d.get("confidence") if isinstance(d, dict) else None,
            }
            for d in defects[:10]
        ]
        payload = {
            "task_id": task_id,
            "status": record.status,
            "scene": record.scene,
            "grade_level": grade.get("level"),
            "grade_summary": grade.get("summary") or "",
            "defect_count": len(defects),
            "defects": defect_rows,
        }
        await self._repo.add_message(
            session_id,
            role=ROLE_ASSISTANT,
            kind=KIND_REVIEW_CARD,
            content=json.dumps(payload, ensure_ascii=False),
            task_id=task_id,
        )

    async def _emit_done_card(
        self,
        session_id: str,
        task_id: str,
        record: Any,
        grade: dict[str, Any],
        error: dict[str, Any],
    ) -> None:
        """落完成卡（只推不改）：结论 + 报告状态快照；报告就绪另推报告卡。

        报告在图内随任务收敛生成，落定时 report_json 已就绪 → report.status=ready；
        异常兜底（无报告）记 none，前端据此呈现"无报告"而非假"生成中"。
        """
        report: dict[str, Any] = {}
        try:
            report = json.loads(record.report_json) if getattr(record, "report_json", None) else {}
        except (json.JSONDecodeError, TypeError):
            report = {}
        defects: list[Any] = []
        try:
            parsed_std = json.loads(record.standardized_json) if record.standardized_json else []
            defects = parsed_std if isinstance(parsed_std, list) else []
        except (json.JSONDecodeError, TypeError):
            defects = []
        failed = record.status == "failed"
        payload = {
            "task_id": task_id,
            "status": record.status,
            "scene": record.scene,
            "mode": record.mode,
            "grade_level": grade.get("level"),
            "grade_summary": grade.get("summary") or "",
            "defect_count": len(defects),
            "error_message": error.get("message") if failed else None,
            "report": {
                "status": "ready" if report.get("uri") else "none",
                "uri": report.get("uri"),
            },
        }
        await self._repo.add_message(
            session_id,
            role=ROLE_ASSISTANT,
            kind=KIND_DONE_CARD,
            content=json.dumps(payload, ensure_ascii=False),
            task_id=task_id,
        )
        # 报告就绪 → 紧接着推独立的报告卡（不改写完成卡；卡片只推不改）
        if report.get("uri") and not failed:
            await self._emit_report_card(session_id, task_id, record, grade, report, len(defects))

    async def _emit_report_card(
        self,
        session_id: str,
        task_id: str,
        record: Any,
        grade: dict[str, Any],
        report: dict[str, Any],
        defect_count: int,
    ) -> None:
        """落报告就绪卡：报告全文入口（前端点击展开，沿用对话内报告渲染）。"""
        payload = {
            "task_id": task_id,
            "scene": record.scene,
            "grade_level": grade.get("level"),
            "defect_count": defect_count,
            "report": {
                "status": "ready",
                "uri": report.get("uri"),
                "analysis_lead": report.get("analysis_lead") or "",
                "created_at": report.get("created_at"),
            },
        }
        await self._repo.add_message(
            session_id,
            role=ROLE_ASSISTANT,
            kind=KIND_REPORT_CARD,
            content=json.dumps(payload, ensure_ascii=False),
            task_id=task_id,
        )

    # ---------- 启动补卡对账 ----------
    async def reconcile_review_cards(self) -> None:
        """启动补卡对账：为缺卡的暂停任务补发终裁复核卡。

        两类窗口会造成会话内没有复核卡：
        1. 重启发生在「任务落定 awaiting_review → 卡片落库」窗口，卡片随进程丢失；
        2. 任务在本功能上线前就已暂停（落定事件走的是旧代码）。
        启动时扫描全部 awaiting_review 任务，对归属会话且尚未落卡者补发
        （has_review_card 幂等判定）。只补卡、不再触发会话智能体回合——
        暂停消息与回报已在历史中。
        """
        if self._ctx.task_repo is None:
            return
        try:
            records = await self._ctx.task_repo.list_tasks(status=STATUS_AWAITING_REVIEW, limit=200)
        except Exception:  # noqa: BLE001
            logger.exception("reconcile_review_cards: list awaiting_review tasks failed")
            return
        if not records:
            return
        logger.info("reconcile_review_cards: scanning %d awaiting_review task(s)", len(records))
        for rec in records:
            try:
                session_id = await self._resolve_session_by_task(rec.id)
                if not session_id:
                    continue  # 非会话派发的任务（任务提交页直提）：本就无对话可弹
                if await self._repo.has_review_card(session_id, rec.id):
                    continue
                grade: dict[str, Any] = {}
                try:
                    grade = json.loads(rec.grade_json) if rec.grade_json else {}
                except (json.JSONDecodeError, TypeError):
                    grade = {}
                await self._emit_review_card(session_id, rec.id, rec, grade)
                logger.info(
                    "reconcile_review_cards: emitted card for task %s in session %s", rec.id, session_id
                )
            except Exception:  # noqa: BLE001 单个任务补卡失败不阻断其余对账
                logger.exception("reconcile_review_cards: task %s failed", rec.id)

    # ---------- 任务执行活动流（优化轮 D2：append_step 旁路 → 会话实时展示） ----------
    async def on_task_step(self, task_id: str, node: str, message: str) -> None:
        """任务步骤事件入口（经 TaskRepo.append_step 旁路触发，晚绑定注入）。

        共识轮（对话内显示模型思考链）：只转发 node="chain" 的思考链事件；
        机械步骤行（react_agent/react_tools/工具留痕/里程碑节点）不再进对话流，
        只留在任务自身 steps_json 审计里。

        chain 事件语义：
        - thought / tool_call：新增 kind=chain 行（tool_call 行为 running 态，
          消息 id 由任务+call_id 确定性推导，供结果回写定位）；
        - tool_result：按同 id 原地更新该工具行内容（含结果、done）。
        """
        if node != "chain":
            return
        try:
            payload = json.loads(message)
        except (TypeError, ValueError):
            return
        try:
            session_id = await self._resolve_session_by_task(task_id)
        except Exception:  # noqa: BLE001
            logger.exception("resolve session for task %s failed", task_id)
            return
        if not session_id:
            return
        try:
            await self._apply_chain_event(session_id, task_id, payload)
        except Exception:  # noqa: BLE001 - 思考链落库失败不影响任务主流程
            logger.exception("apply chain event failed session=%s task=%s", session_id, task_id)

    async def _apply_chain_event(self, session_id: str, task_id: str, payload: dict[str, Any]) -> None:
        """把单条思考链事件落成消息：新增（thought/工具调用）或原地更新（结果）。"""
        etype = payload.get("type")
        if etype == "tool_result":
            call_id = str(payload.get("call_id") or "")
            mid = self._chain_message_id(task_id, call_id)
            content = json.dumps({**payload, "type": "tool_call", "done": True}, ensure_ascii=False)
            if mid and await self._repo.update_message(session_id, mid, content):
                return
            # 未命中（异常路径）：降级为新增，行不丢
            await self._repo.add_message(
                session_id, role=ROLE_EVENT, kind=KIND_CHAIN, content=content,
                task_id=task_id, message_id=mid or None,
            )
            return
        if etype == "tool_call":
            mid = self._chain_message_id(task_id, str(payload.get("call_id") or ""))
            await self._repo.add_message(
                session_id, role=ROLE_EVENT, kind=KIND_CHAIN,
                content=json.dumps({**payload, "done": False}, ensure_ascii=False),
                task_id=task_id, message_id=mid or None,
            )
            return
        if etype == "thought":
            await self._repo.add_message(
                session_id, role=ROLE_EVENT, kind=KIND_CHAIN,
                content=json.dumps(payload, ensure_ascii=False), task_id=task_id,
            )

    @staticmethod
    def _chain_message_id(task_id: str, call_id: str) -> str | None:
        """工具调用行的确定性消息 id：任务+call_id → 结果事件可原位更新同一行。"""
        if not call_id:
            return None
        return f"MC{task_id}{call_id}"[:64]

    async def _resolve_session_by_task(self, task_id: str) -> str | None:
        cached = self._task_session_cache.get(task_id, "__miss__")
        if cached != "__miss__":
            return cached
        session_id = await self._repo.find_session_by_task(task_id)
        if not session_id and self._ctx.task_repo is not None:
            # 联合子任务：沿父链向上找会话归属
            current = task_id
            for _ in range(4):
                record = await self._ctx.task_repo.get(current)
                if record is None:
                    break
                auxiliary: dict[str, Any] = {}
                try:
                    inputs = json.loads(record.inputs_json) if record.inputs_json else {}
                    auxiliary = inputs.get("auxiliary") or {}
                except (json.JSONDecodeError, TypeError, AttributeError):
                    auxiliary = {}
                parent_id = auxiliary.get("parent_task_id")
                if not parent_id:
                    break
                session_id = await self._repo.find_session_by_task(parent_id)
                if session_id:
                    break
                current = parent_id
        self._task_session_cache[task_id] = session_id
        return session_id
