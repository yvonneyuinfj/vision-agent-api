"""任务服务：编排层对外门面（提交 / 后台执行 / 状态 / 人工复核 / 追溯）。

- run_task：以 LangGraph astream 执行单场景图；节点内 interrupt() 暂停时，
  流事件携带 __interrupt__ 键，据此把任务置为 awaiting_review（不重新调度模型）。
- review：以 Command(resume=...) 恢复被 interrupt 暂停的图（HITL 续跑）。
- persist：节点链结束后把 state 快照写回任务表，供查询与报告引用。
"""
from __future__ import annotations

import asyncio
import json
from app.core.logger import get_logger
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable

from langgraph.errors import GraphInterrupt
from langgraph.types import Command

from app.agents.graph import build_single_graph, solve_review_command, solve_scene_confirm_command
from app.agents.llm.service import _text_of
from app.agents.react.graph import build_react_graph
from app.agents.react.joint_tools import JointToolbox
from app.agents.state import AgentState
from app.core.context import new_trace_id
from app.core.errors import BizCode, BusinessError
from app.repositories.task_repo import (
    STATUS_AWAITING_REVIEW,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_PENDING,
    STATUS_RUNNING,
    TaskRepo,
)
from app.services.deps import AgentContext

logger = get_logger("app.services.task")

_THREAD_KEY = "configurable"

# 联合轨父任务等待子任务的中断载荷标识（决策 10）
_CHILD_WAIT_KIND = "await_children"
# 子任务"已落定"状态集合：完成/失败/待复核都算落定（待复核子任务不阻塞父图，
# 父智能体据此在结论中标注该场景仍在人工复核）
_CHILD_SETTLED = {STATUS_COMPLETED, STATUS_FAILED, STATUS_AWAITING_REVIEW}


def _is_child_wait(value: Any) -> bool:
    """判定中断载荷是否为"等子任务"（区别于人工复核门中断）。"""
    return isinstance(value, dict) and value.get("kind") == _CHILD_WAIT_KIND


def _iso_utc(dt: datetime | None) -> str | None:
    """把库里的裸 UTC 墙钟序列化为带 +00:00 的 ISO 串（前端据此换算本地时区）。

    MySQL 的 DATETIME 列不存时区，读回一律 naive（`DateTime(timezone=True)` 也不保留），
    故在 API 边界统一补 offset——不补的话前端会按本地时间解析，"今日任务数"这类筛选会错位。
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def _safe_json(raw: str | None) -> Any:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:  # noqa: BLE001
        return None


class TaskService:
    """任务门面：submit / run / get / review / cancel。"""

    def __init__(self, ctx: AgentContext, repo: TaskRepo, checkpointer: Any = None) -> None:
        self._ctx = ctx
        self._repo = repo
        self._checkpointer = checkpointer
        # 双轨并存（决策 13）：single=固定流水线，agent=ReAct 智能体轨。
        # 两图共用同一 checkpointer（thread_id=task_id），人工复核 resume 依赖同一 saver。
        self._graph = build_single_graph(ctx, checkpointer=checkpointer)
        self._react_graph = build_react_graph(ctx, checkpointer=checkpointer)
        # 联合轨（决策 10）：父子任务 + 子任务落定事件注入。工具箱需要
        # task_service 派发子任务，图先建、服务后立，经 bind() 延迟注入解循环。
        self._joint_toolbox = JointToolbox()
        self._joint_graph = build_react_graph(
            ctx, checkpointer=checkpointer, joint=True, toolbox=self._joint_toolbox
        )
        self._joint_toolbox.bind(self)
        # 联合父图唤醒锁：多个子任务近乎同时落定时串行化唤醒判定
        self._parent_locks: dict[str, asyncio.Lock] = {}
        # 任务落定回调（会话轨事件回注入口，决策 10）：容器装配期注入。
        # 回调方自行决定同步/异步语义；此处保证异常不外泄到任务主流程。
        self.on_settled: Callable[[str], Awaitable[None]] | None = None
        # 复核受理回调（即时反馈入口）：认领成功后向来源会话回注受理事件，
        # 用户点击提交后第一时间看到"已受理"，无需等待报告生成（后台恢复）。
        self.on_review_accepted: Callable[[str, str], Awaitable[None]] | None = None
        # 任务步骤回调（会话实时活动流入口，优化轮 D2）：容器装配期注入。
        # 签名 (task_id, node, message)；由 TaskRepo.append_step 旁路触发。
        self.on_step: Callable[[str, str, str], Awaitable[None]] | None = None
        repo.set_step_listener(self._emit_step)

    async def _emit_step(self, task_id: str, node: str, message: str) -> None:
        """TaskRepo 步骤旁路 → 晚绑定 on_step 回调（异常兜底在仓储层）。"""
        callback = self.on_step
        if callback is None:
            return
        await callback(task_id, node, message)

    async def _notify_settled(self, task_id: str) -> None:
        # 联合轨：子任务落定先尝试唤醒等待中的联合父任务（决策 10 事件注入）
        try:
            await self._maybe_resume_parent(task_id)
        except Exception:  # noqa: BLE001
            logger.exception("joint parent resume failed for child %s", task_id)
        callback = self.on_settled
        if callback is None:
            return
        try:
            await callback(task_id)
        except Exception:  # noqa: BLE001
            logger.exception("on_settled callback failed for task %s", task_id)

    def _graph_for(self, mode: str | None):
        """按任务模式选图：agent=单场景 ReAct，joint=联合 ReAct，其余走固定流水线。"""
        if mode == "agent":
            return self._react_graph
        if mode == "joint":
            return self._joint_graph
        return self._graph

    # ---------- 提交准备（路由层上传文件后调用） ----------
    async def prepare(
        self,
        *,
        scene: str | None,
        mode: str,
        scenes: list[str] | None,
        parameters: dict[str, Any] | None,
        input_uris: list[str],
        auxiliary: dict[str, str],
    ) -> dict[str, Any]:
        task_id = f"T{int(uuid.uuid4().hex[:14], 16)}"[:18]
        trace_id = new_trace_id()

        await self._repo.create(
            task_id,
            scene=scene,
            mode=mode,
            inputs={
                "scenes": scenes,
                "input_uris": input_uris,
                "auxiliary": auxiliary,
                # parameters 一并落库：重试需按原参数重建 state（老记录无此键时重试取 {}）
                "parameters": parameters or {},
            },
            trace_id=trace_id,
        )

        state: AgentState = {
            "task_id": task_id,
            "trace_id": trace_id,
            "mode": mode,
            "scene": scene,
            "scenes": scenes or [],
            "input_uris": input_uris,
            "auxiliary": auxiliary,
            "parameters": parameters or {},
        }
        return {"task_id": task_id, "state": state}

    # ---------- 后台执行（FastAPI BackgroundTasks 或独立 worker 调用） ----------
    async def run_task(self, task_id: str, state: AgentState) -> None:
        config = {_THREAD_KEY: {"thread_id": task_id}}
        graph = self._graph_for(state.get("mode"))
        await self._repo.set_status(task_id, STATUS_RUNNING)
        interrupted_value: dict[str, Any] | None = None
        try:
            async for event in graph.astream(state, config=config, stream_mode="values"):
                # LangGraph 1.2+: interrupt() 不抛异常，通过 __interrupt__ 状态键表达暂停
                if isinstance(event, dict) and "__interrupt__" in event:
                    interrupted_value = event["__interrupt__"][0].value
                    break
        except GraphInterrupt:
            # 防御路径（无 checkpointer 的底层实现会以异常表达暂停）
            snapshot = await graph.aget_state(config)
            interrupted_value = self._extract_interruption(snapshot)
        except Exception as exc:  # noqa: BLE001
            await self._fail(task_id, {"code": int(BizCode.INTERNAL_ERROR), "message": f"{exc}"})
            await self._notify_settled(task_id)
            return

        if interrupted_value is not None:
            # 中断期派生通道同步落库（standardized/class_definitions/knowledge/
            # capability_used）：复核工作台的「标注图」层按 standardized 绘制
            # Konva 缺陷框、AI 预判列与类别图例也消费它们——不落库则复核页
            # 只见原图、无任何标注（终局 _persist 要等复核后才执行，太晚）。
            # 复用 _persist：patch_json 跳过 None，不会覆盖下面写入的 review_json。
            try:
                snapshot = await graph.aget_state(config)
                await self._persist(task_id, snapshot.values)
            except Exception:  # noqa: BLE001 - 快照落库失败不阻断复核门（日志留痕）
                logger.exception("persist paused snapshot failed, task=%s", task_id)
            if _is_child_wait(interrupted_value):
                # 联合父任务等待子任务：保持 running、落中断上下文，不算落定、
                # 不进入人工复核语义（决策 10）。子任务落定时 _maybe_resume_parent 唤醒。
                await self._repo.patch_json(task_id, review_json=interrupted_value)
                logger.info("joint task %s awaiting children: %s", task_id, interrupted_value)
                return
            # 人工复核门 Goal：暂停而非失败
            await self._repo.patch_json(task_id, review_json=interrupted_value)
            # 复核暂停期间判级结果同步落库：任务卡/事件回报/评估矩阵在
            # awaiting_review 阶段就能看到判级与异议理由（中断载荷自带 grade）
            paused_grade = interrupted_value.get("grade") if isinstance(interrupted_value, dict) else None
            if isinstance(paused_grade, dict):
                await self._repo.patch_json(task_id, grade_json=paused_grade)
            paused_scene = interrupted_value.get("scene") if isinstance(interrupted_value, dict) else None
            if isinstance(paused_scene, str) and paused_scene:
                await self._repo.patch_json(task_id, scene=paused_scene)
            await self._repo.set_status(task_id, STATUS_AWAITING_REVIEW)
            logger.info("task %s paused for human review: %s", task_id, interrupted_value)
            await self._notify_settled(task_id)
            return

        # 图收敛后的统一终态（含 error -> 失败落库：异常总结 + 报告引用）
        await self._finish(task_id)

    # ---------- 人工复核恢复 ----------
    async def review_task(
        self,
        task_id: str,
        *,
        action: str,
        opinion: str,
        override_grade: dict[str, Any] | None = None,
        defects: list[dict[str, Any]] | None = None,
    ) -> None:
        record = await self._repo.get(task_id)
        if record is None:
            raise BusinessError(BizCode.TASK_NOT_FOUND, f"task {task_id} not found", http_status=404)
        if record.status != STATUS_AWAITING_REVIEW:
            raise BusinessError(
                BizCode.TASK_STATE_CONFLICT,
                f"task {task_id} not awaiting review (status={record.status})",
                http_status=409,
            )

        pending = _safe_json(record.review_json)
        if isinstance(pending, dict) and pending.get("confirm_type") == "scene":
            raise BusinessError(
                BizCode.TASK_STATE_CONFLICT,
                f"task {task_id} awaits scene confirm, use /scene-confirm",
                http_status=409,
            )

        command = solve_review_command(action, opinion, override_grade, defects=defects)
        # 即时受理 + 后台恢复：原实现同步等待图跑完（含报告生成，可达分钟级），
        # 前端提交按钮长时间无响应、甚至触发 60s 请求超时，用户无法确认是否提交成功。
        # 现以条件 UPDATE 原子认领（awaiting_review→running，双击只有一个成功），
        # 认领成功立即返回；图恢复后台执行，落定后经 on_settled 推完成/报告卡。
        if not await self._repo.claim_review(task_id):
            raise BusinessError(
                BizCode.TASK_STATE_CONFLICT,
                f"task {task_id} not awaiting review (already resumed?)",
                http_status=409,
            )
        asyncio.create_task(self._resume(task_id, command, mode=record.mode))
        callback = self.on_review_accepted
        if callback is not None:
            try:
                await callback(task_id, action)
            except Exception:  # noqa: BLE001 - 反馈事件失败不阻断受理结果
                logger.exception("on_review_accepted callback failed for task %s", task_id)

    # ---------- 场景人工确认恢复 ----------
    async def confirm_scene(self, task_id: str, scene: str) -> None:
        record = await self._repo.get(task_id)
        if record is None:
            raise BusinessError(BizCode.TASK_NOT_FOUND, f"task {task_id} not found", http_status=404)
        if record.status != STATUS_AWAITING_REVIEW:
            raise BusinessError(
                BizCode.TASK_STATE_CONFLICT,
                f"task {task_id} not awaiting confirm (status={record.status})",
                http_status=409,
            )
        pending = _safe_json(record.review_json)
        valid = isinstance(pending, dict) and pending.get("confirm_type") == "scene"
        if not valid:
            raise BusinessError(BizCode.TASK_STATE_CONFLICT, "task not awaiting scene confirm", http_status=409)
        candidates = [c.get("scene") for c in (pending.get("candidates") or [])]
        if scene not in candidates:
            raise BusinessError(
                BizCode.INVALID_REQUEST,
                f"scene {scene} not in candidates {candidates}",
                http_status=400,
            )
        # 即时受理 + 后台恢复（与复核提交同构）：场景确认后流水线全程可达分钟级，
        # 同步等待会让前端「按此送检」按钮长时间无响应；原子认领后转后台执行。
        if not await self._repo.claim_review(task_id):
            raise BusinessError(
                BizCode.TASK_STATE_CONFLICT,
                f"task {task_id} not awaiting confirm (already resumed?)",
                http_status=409,
            )
        asyncio.create_task(
            self._resume(task_id, solve_scene_confirm_command(scene, task_id=task_id), mode=record.mode)
        )

    # ---------- 失败重试 ----------
    async def retry_task(self, task_id: str) -> dict[str, Any]:
        """失败任务原地重试：原子重置为 pending 并重建初始 state（同 thread_id 重跑）。

        与 run_task 衔接：路由层拿到返回的 state 后走 background.add_task(svc.run_task, ...)，
        与提交路径完全一致。同 thread_id 重跑时旧通道值残留，故所有运行期派生键显式置空
        （尤其 error——否则每一跳的 _route_error_or_next 读到残留值立即路由回 __fail__）。
        """
        record = await self._repo.get(task_id)
        if record is None:
            raise BusinessError(BizCode.TASK_NOT_FOUND, f"task {task_id} not found", http_status=404)
        if not await self._repo.begin_retry(task_id):
            raise BusinessError(
                BizCode.TASK_STATE_CONFLICT,
                f"task {task_id} not retryable (status={record.status})",
                http_status=409,
            )

        try:
            inputs = json.loads(record.inputs_json or "{}")
        except Exception:  # noqa: BLE001
            inputs = {}

        state: AgentState = {
            "task_id": task_id,
            "trace_id": record.trace_id,
            "mode": record.mode,
            "scene": record.scene,
            "scenes": inputs.get("scenes") or [],
            "input_uris": inputs.get("input_uris") or [],
            "auxiliary": inputs.get("auxiliary") or {},
            "parameters": inputs.get("parameters") or {},
            # 清空上一轮残留的运行期派生通道
            "candidate_scenes": [],
            "selected_capability": None,
            "raw_result": None,
            "standardized": [],
            "class_definitions": [],
            "grade_hint": None,
            "knowledge": [],
            "knowledge_prompt": "",
            "grade": None,
            "review": None,
            "retrieval_trace": [],
            "metric_explanations": "",
            "error_report": None,
            "report": None,
            "error": None,
            # ReAct 轨运行期通道（agent 模式重试时归零；messages 残留由
            # bootstrap_agent 节点以 RemoveMessage 清空）
            "messages": [],
            "loop_count": 0,
            "capability_calls": 0,
            "submitted": False,
            "submit_payload": None,
        }
        await self._repo.append_step(task_id, "retry", "task retried by user")
        logger.info("task %s retried by user", task_id)
        return {"task_id": task_id, "state": state}

    # ---------- 查询 ----------
    async def get(self, task_id: str) -> dict[str, Any] | None:
        record = await self._repo.get(task_id)
        if record is None:
            return None

        def _load(attr: str) -> Any:
            raw = getattr(record, attr, None)
            if not raw:
                return None
            try:
                return json.loads(raw)
            except Exception:  # noqa: BLE001
                return None

        # input_uris 供前端回显原图（URI 本身即下载端点，前端不得再臆造文件名）
        inputs = _load("inputs_json") or {}
        parent_task_id = str((inputs.get("auxiliary") or {}).get("parent_task_id") or "") or None

        # 联合父任务暴露子任务摘要（优化轮 E2 父子可视化）
        children: list[dict[str, Any]] = []
        if record.mode == "joint":
            for child in await self._repo.find_children(record.id):
                child_grade = {}
                try:
                    child_grade = json.loads(child.grade_json) if child.grade_json else {}
                except (json.JSONDecodeError, TypeError):
                    pass
                children.append({
                    "task_id": child.id,
                    "scene": child.scene,
                    "mode": child.mode,
                    "status": child.status,
                    "grade_level": child_grade.get("level"),
                    "created_at": _iso_utc(child.created_at),
                })

        return {
            "task_id": record.id,
            "trace_id": record.trace_id,
            "scene": record.scene,
            "mode": record.mode,
            "status": record.status,
            "capability_used": record.capability_used,
            "created_at": _iso_utc(record.created_at),
            "updated_at": _iso_utc(record.updated_at),
            "finished_at": _iso_utc(record.finished_at),
            "input_uris": inputs.get("input_uris") or [],
            "steps": _load("steps_json"),
            "standardized": _load("standardized_json") or [],
            "class_definitions": _load("class_definitions_json") or [],
            "grade": _load("grade_json"),
            "knowledge": _load("knowledge_json") or [],
            "review": _load("review_json"),
            "report": _load("report_json"),
            "error": _load("error_json"),
            "parent_task_id": parent_task_id,
            "children": children,
        }

    async def list_tasks(
        self, *, scene: str | None = None, status: str | None = None, limit: int = 20
    ) -> list[dict[str, Any]]:
        records = await self._repo.list_tasks(scene=scene, status=status, limit=limit)
        out: list[dict[str, Any]] = []
        for r in records:
            # 子任务标记（优化轮 E2）：列表「子」标签数据源
            parent_task_id = None
            inputs: dict[str, Any] = {}
            try:
                inputs = json.loads(r.inputs_json) if r.inputs_json else {}
                pid = str((inputs.get("auxiliary") or {}).get("parent_task_id") or "")
                parent_task_id = pid or None
            except (json.JSONDecodeError, TypeError):
                pass
            out.append({
                "task_id": r.id,
                "scene": r.scene,
                "mode": r.mode,
                "status": r.status,
                "created_at": _iso_utc(r.created_at),
                "updated_at": _iso_utc(r.updated_at),
                "parent_task_id": parent_task_id,
                # 真实图像数：列表页「图像数量」列数据源（源自落盘 input_uris）
                "image_count": len(inputs.get("input_uris") or []),
            })
        return out

    async def get_stats(self) -> dict[str, int]:
        """权威全量统计（列表接口受 limit 截断，统计以全表聚合为准）。"""
        return await self._repo.get_stats()

    # ---------- ReAct 思考链（详情页面板数据源，仅 agent 轨有内容） ----------
    async def trace(self, task_id: str) -> dict[str, Any]:
        """读检查点中的完整对话轨迹，归一为思考链条目列表。

        条目类型：
        - briefing：任务简报（HumanMessage，bootstrap 注入）；
        - thought：智能体推理文本（AIMessage 文本部分，可空）；
        - tool_call：智能体发起的工具调用（含参数摘要）；
        - tool_result：工具返回内容（截断防膨胀）。
        """
        record = await self._repo.get(task_id)
        if record is None:
            raise BusinessError(BizCode.TASK_NOT_FOUND, f"task {task_id} not found", http_status=404)
        config = {_THREAD_KEY: {"thread_id": task_id}}
        graph = self._graph_for(record.mode)
        snapshot = await graph.aget_state(config)
        values = snapshot.values if snapshot and snapshot.values else {}
        messages = values.get("messages") or []

        from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

        entries: list[dict[str, Any]] = []
        turn = 0
        for msg in messages:
            if isinstance(msg, SystemMessage):
                continue  # 系统提示词不展示（长且与思考链无关）
            if isinstance(msg, HumanMessage):
                entries.append({"type": "briefing", "text": _text_of(msg.content)[:800]})
                continue
            if isinstance(msg, AIMessage):
                turn += 1
                text = _text_of(msg.content).strip()
                if text:
                    entries.append({"type": "thought", "turn": turn, "text": text[:2000]})
                for call in getattr(msg, "tool_calls", None) or []:
                    args = call.get("args") or {}
                    entries.append(
                        {
                            "type": "tool_call",
                            "turn": turn,
                            "name": call.get("name"),
                            "args": {k: str(v)[:200] for k, v in args.items()},
                        }
                    )
                continue
            if isinstance(msg, ToolMessage):
                entries.append(
                    {
                        "type": "tool_result",
                        "turn": turn,
                        "name": getattr(msg, "name", None),
                        "content": str(msg.content)[:1200],
                    }
                )
        return {
            "task_id": task_id,
            "mode": record.mode,
            "status": record.status,
            "loop_count": values.get("loop_count"),
            "capability_calls": values.get("capability_calls"),
            "submitted": values.get("submitted"),
            "entries": entries,
        }

    # ---------- 内部 ----------
    # ---- 联合轨：子任务落定 → 唤醒等待中的父图（决策 10 事件注入） ----
    async def _maybe_resume_parent(self, child_id: str) -> None:
        """子任务落定钩子：解析其联合父任务并尝试唤醒（委托 _resume_joint_if_ready）。"""
        record = await self._repo.get(child_id)
        if record is None:
            return
        inputs = _safe_json(getattr(record, "inputs_json", None)) or {}
        parent_id = str((inputs.get("auxiliary") or {}).get("parent_task_id") or "")
        if not parent_id:
            return
        await self._resume_joint_if_ready(parent_id)

    async def _resume_joint_if_ready(self, parent_id: str) -> None:
        """若联合父任务正停在 await_children 且全部子任务均已落定，组装各子任务
        结论并唤醒父图继续 ReAct 循环。

        判定全程持父级锁并在锁内复查检查点，防多个子任务同时落定导致重复唤醒；
        启动对账（优化轮 E5）与落定钩子共用本路径。
        """
        lock = self._parent_locks.setdefault(parent_id, asyncio.Lock())
        async with lock:
            parent = await self._repo.get(parent_id)
            if parent is None or parent.mode != "joint":
                return
            config = {_THREAD_KEY: {"thread_id": parent_id}}
            snapshot = await self._joint_graph.aget_state(config)
            pending = self._extract_interruption(snapshot)
            if not _is_child_wait(pending):
                return  # 父图不在等待（已唤醒/已终态/复核中）：无操作

            child_ids = list((pending.get("children") or {}).values())
            if not child_ids:
                return
            for cid in child_ids:
                crec = await self._repo.get(cid)
                if crec is None or crec.status not in _CHILD_SETTLED:
                    return  # 仍有兄弟未落定：继续等待
            outcomes = [await self._child_outcome(cid) for cid in child_ids]
            logger.info(
                "joint parent %s: all %d children settled, resuming", parent_id, len(child_ids)
            )
            await self._resume(parent_id, Command(resume={"children": outcomes}), mode="joint")

    async def reconcile_joint_parents(self) -> None:
        """启动唤醒对账（优化轮 E5）。

        重启若发生在「子任务已落定、父图停在 await_children」窗口，唤醒事件
        随进程丢失，父任务永久挂起。启动时扫描所有 running 联合任务，对仍停在
        子任务等待且兄弟全落定者补发唤醒（与正常唤醒同锁同复查，幂等安全）。
        """
        try:
            records = await self._repo.list_tasks(status=STATUS_RUNNING, limit=200)
        except Exception:  # noqa: BLE001
            logger.exception("reconcile_joint_parents: list running tasks failed")
            return
        joints = [r for r in records if r.mode == "joint"]
        if not joints:
            return
        logger.info("reconcile_joint_parents: scanning %d running joint task(s)", len(joints))
        for rec in joints:
            try:
                await self._resume_joint_if_ready(rec.id)
            except Exception:  # noqa: BLE001 单个父任务唤醒失败不阻断其余对账
                logger.exception("reconcile_joint_parents: resume %s failed", rec.id)

    async def _child_outcome(self, child_id: str) -> dict[str, Any]:
        """子任务结论投影（唤醒父图的载荷单元）。"""
        detail = await self.get(child_id) or {}
        grade = detail.get("grade") or {}
        return {
            "task_id": child_id,
            "scene": detail.get("scene"),
            "status": detail.get("status"),
            "grade_level": grade.get("level"),
            "grade_summary": grade.get("summary"),
            "defects": detail.get("standardized") or [],
        }

    async def sweep_stale_reviews(self, *, timeout_s: int | None = None) -> list[dict[str, Any]]:
        """HITL 复核超时扫描：滞留复核门过久的任务标记升级（绝不自动裁决）。

        业务红线：智能体异议 / 低置信度 / 高严重度任务必须人工终裁，
        超时只做两件事——审计留痕（review_timeout 步骤行，含滞留时长，
        幂等：已有同类行不重复追加）+ 告警日志（供运维/值班跟进）。
        绝不自动 confirm 放行，也不改任务状态。
        """
        if timeout_s is None:
            timeout_s = int(getattr(self._ctx.settings, "review_timeout_s", 0) or 0)
        if timeout_s <= 0:
            return []
        before = datetime.now(timezone.utc) - timedelta(seconds=timeout_s)
        try:
            records = await self._repo.list_stale_awaiting_review(before=before)
        except Exception:  # noqa: BLE001
            logger.exception("sweep_stale_reviews: scan failed")
            return []

        stale: list[dict[str, Any]] = []
        for rec in records:
            try:
                steps = json.loads(rec.steps_json or "[]")
            except json.JSONDecodeError:
                steps = []
            if any(s.get("node") == "review_timeout" for s in steps if isinstance(s, dict)):
                continue  # 已升级标记：幂等跳过
            hours = timeout_s / 3600
            try:
                await self._repo.append_step(
                    rec.id,
                    "review_timeout",
                    f"人工复核已滞留超过 {hours:g} 小时未处理，升级提醒（不自动裁决）",
                )
            except Exception:  # noqa: BLE001
                logger.exception("sweep_stale_reviews: mark %s failed", rec.id)
                continue
            logger.warning(
                "review timeout: task %s awaiting review over %ss (scene=%s) — escalated, never auto-decided",
                rec.id, timeout_s, rec.scene,
            )
            stale.append({"task_id": rec.id, "scene": rec.scene, "hours": hours})
        return stale

    async def _persist(self, task_id: str, values: AgentState) -> None:
        """完成后把派生可追溯结果写入任务表（审计 + 报告引用）。

        steps（节点执行审计链）唯一权威来源是 TaskRepo.append_step（@node
        装饰器逐节点追加，跨重试保留完整历史），此处不再从 state 回写——
        state schema 从未携带 steps 通道，原 steps_json 写入为死代码。
        """
        try:
            await self._repo.patch_json(
                task_id,
                scene=values.get("scene"),
                capability_used=(values.get("selected_capability") or {}).get("tool_name"),
                standardized_json=values.get("standardized") or [],
                class_definitions_json=values.get("class_definitions") or [],
                grade_json=values.get("grade"),
                knowledge_json=values.get("knowledge") or [],
                review_json=values.get("review"),
                report_json=values.get("report"),
            )
        except Exception:  # noqa: BLE001
            logger.exception("persist failed, task=%s", task_id)

    async def _resume(self, task_id: str, command: Command, mode: str | None = None) -> None:
        """以 Command 恢复被 interrupt 暂停的图（复核/场景确认共用）。

        mode 决定恢复哪条轨（agent 走 ReAct 图）；缺省时回查任务记录。
        再暂停、异常与终态持久化统一在此处理。
        """
        if mode is None:
            record = await self._repo.get(task_id)
            mode = record.mode if record is not None else None
        graph = self._graph_for(mode)
        config = {_THREAD_KEY: {"thread_id": task_id}}
        await self._repo.set_status(task_id, STATUS_RUNNING)
        try:
            async for event in graph.astream(command, config=config, stream_mode="values"):
                if isinstance(event, dict) and "__interrupt__" in event:
                    value = event["__interrupt__"][0].value
                    if _is_child_wait(value):
                        # 唤醒后再次进入等待（补派子任务后二次 await）：保持 running
                        await self._repo.patch_json(task_id, review_json=value)
                        logger.info("joint task %s awaiting children again: %s", task_id, value)
                        return
                    await self._repo.patch_json(task_id, review_json=value)
                    await self._repo.set_status(task_id, STATUS_AWAITING_REVIEW)
                    await self._notify_settled(task_id)
                    return
        except GraphInterrupt:  # 防御路径
            snapshot = await graph.aget_state(config)
            value = self._extract_interruption(snapshot)
            if _is_child_wait(value):
                await self._repo.patch_json(task_id, review_json=value)
                return
            await self._repo.patch_json(task_id, review_json=value)
            await self._repo.set_status(task_id, STATUS_AWAITING_REVIEW)
            await self._notify_settled(task_id)
            return
        except Exception as exc:  # noqa: BLE001
            await self._fail(task_id, {"code": int(BizCode.INTERNAL_ERROR), "message": f"{exc}"})
            await self._notify_settled(task_id)
            return
        await self._finish(task_id)

    async def _finish(self, task_id: str) -> None:
        """图正常收敛后的统一终态：含 error -> 失败落库（error 报告一并持久化）；否则 completed。"""
        config = {_THREAD_KEY: {"thread_id": task_id}}
        final = await self._graph.aget_state(config)
        if final and final.values:
            await self._persist(task_id, final.values)
            if final.values.get("error"):
                await self._finalize_error(task_id, final.values)
            else:
                await self._repo.set_status(task_id, STATUS_COMPLETED)
        else:
            await self._repo.set_status(task_id, STATUS_FAILED)
        # 级联终止兜底：父任务终态后仍在跑的子任务（如末轮派发后立即提交）
        # failed/completed 都扫一遍（幂等：已落定子任务不动）
        await self._cascade_orphan_children(task_id, parent_terminal="terminal")
        await self._notify_settled(task_id)

    async def _finalize_error(self, task_id: str, values: AgentState) -> None:
        """失败终态落库：错误码/消息 + 节点上下文与 summary + 异常报告引用。"""
        error = values.get("error") or {}
        await self._repo.set_error(task_id, int(error.get("code", 4001)), error.get("message") or "failed")
        await self._repo.patch_json(
            task_id,
            error_json=dict(error),
            report_json=values.get("error_report"),
        )

    async def _fail(self, task_id: str, error: dict[str, Any]) -> None:
        logger.error("task %s failed: %s", task_id, error)
        await self._repo.set_error(task_id, int(error.get("code", 4001)), error.get("message", "failed"))
        await self._cascade_orphan_children(task_id, parent_terminal="failed")

    async def _cascade_orphan_children(self, parent_id: str, *, parent_terminal: str) -> None:
        """级联终止（多 Agent 孤儿清理）：联合父任务终态后仍在跑的子任务收尾。

        父任务 failed/completed 而子任务仍 running/pending：其结果已无处可用
        （父图不会再恢复等待）。孤儿子任务一律 failed + 明确原因（不是留下
        永久 running 的僵尸行）；已落定（含 awaiting_review）的子任务不受
        影响——它们的结论仍可追溯查询。
        """
        try:
            children = await self._repo.find_children(parent_id)
        except Exception:  # noqa: BLE001
            logger.exception("cascade_orphan_children: find_children %s failed", parent_id)
            return
        orphans = [c for c in children if c.status in (STATUS_PENDING, STATUS_RUNNING)]
        if not orphans:
            return
        for child in orphans:
            try:
                await self._repo.set_error(
                    child.id,
                    int(BizCode.TASK_STATE_CONFLICT),
                    f"父任务 {parent_id} 已终态（{parent_terminal}），子任务级联终止",
                )
                # 审计行直接入库（on_step 回调仅服务会话活动流，测试/离线场景为空）
                await self._repo.append_step(
                    child.id, "cascade_terminate",
                    f"父任务 {parent_terminal}，级联终止（结果已无处可用）",
                )
                logger.info(
                    "cascade: child %s terminated (parent %s %s)", child.id, parent_id, parent_terminal
                )
            except Exception:  # noqa: BLE001 单个子任务收尾失败不阻断其余
                logger.exception("cascade_orphan_children: terminate %s failed", child.id)

    @staticmethod
    def _extract_interruption(snapshot: Any) -> dict[str, Any] | None:
        if snapshot is None or not snapshot.tasks:
            return None
        try:
            return snapshot.tasks[0].interrupts[0].value if snapshot.tasks[0].interrupts else None
        except (IndexError, AttributeError):
            return None