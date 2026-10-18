"""会话工具箱：launch_inspection / get_task_detail。

与任务轨工具箱同构（两层分离）：
- impl：真实执行函数 `(args, state) -> content`（会话轨无共享副作用通道，
  side_state 恒为空）；执行中自行落库任务卡片消息；
- lc_tools：仅供 `model.bind_tools` 的 schema 包装。

会话轨工具永不抛异常——任何失败都转为可读的 ToolMessage 内容，
保证会话回合总能给用户一个答复。
"""
from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any, Awaitable, Callable

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from app.core.context import new_trace_id
from app.core.logger import get_logger
from app.models.session import KIND_DOC_CARD, KIND_TASK_CARD, ROLE_ASSISTANT
from app.services.deps import AgentContext

logger = get_logger("app.agents.session.tools")

SessionToolImpl = Callable[[dict[str, Any], dict[str, Any]], Awaitable[str]]


class _LaunchInspectionArgs(BaseModel):
    scene: str = Field(
        description="检测场景英文编码：borescope/infrared/dr/fpi/fracture/metallography"
    )
    note: str = Field(default="", description="送检备注（可选）：用户补充的检测要求或关注点")


class _GetTaskDetailArgs(BaseModel):
    task_id: str = Field(description="质检任务 ID（T 前缀）")


class _GeneratePptArgs(BaseModel):
    topic: str = Field(description="演示主题（一句话概括要做的 PPT 内容，必填）")
    audience: str = Field(default="", description="目标听众（可选），如：部门领导 / 客户 / 技术评审")
    pages: int = Field(default=0, description="期望页数（可选，0 表示自动，约 8 页）")
    style: str = Field(default="", description="呈现风格（可选），如：简洁商务 / 学术 / 汇报")
    request: str = Field(default="", description="补充要求（可选）：需特别覆盖的要点或结构说明")


def build_session_tool_impls(
    ctx: AgentContext,
    session_repo: Any,  # SessionRepo（避免循环导入：运行期注入）
    task_service: Any,  # TaskService
) -> dict[str, SessionToolImpl]:
    """装配 impl 分发表（依赖闭包注入）。"""

    async def _launch_inspection_impl(args: dict[str, Any], state: dict[str, Any]) -> str:
        scene = str(args.get("scene") or "").strip().lower()
        note = str(args.get("note") or "").strip()
        session_id = str(state.get("session_id") or "")

        if not session_id:
            return "派发失败：当前会话上下文缺失。"
        if not scene:
            return "派发失败：必须提供 scene 参数。"

        scenes = sorted({m.scene for m in (ctx.registry.list() if ctx.registry else [])})
        if scene not in scenes:
            return (
                f"派发失败：场景 {scene} 不受支持。已注册场景：{', '.join(scenes) or '无'}。"
                "请重新确认用户图像所属场景。"
            )

        # 待送检图片聚合：上次派发之后所有用户上传（多条消息分批上传全部纳入）
        images = await session_repo.pending_user_images(session_id)
        input_uris = [str(img.get("uri") or "") for img in images if img.get("uri")]
        if not input_uris:
            return "派发失败：当前会话没有可送检的图片，请先引导用户上传图片。"

        try:
            prepared = await task_service.prepare(
                scene=scene,
                mode="agent",  # 会话派发的任务恒走智能体轨（决策 16）
                scenes=[scene],  # 场景已判定一次并传递，不再二次路由
                parameters={},
                input_uris=input_uris,
                auxiliary={},
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("launch_inspection prepare failed, session=%s", session_id)
            return f"派发失败：任务创建异常（{exc}）。请稍后重试。"

        task_id = prepared["task_id"]

        # 任务创建卡（只推不改）：落卡时刻的 JSON 快照（场景/模式/图片数/备注），
        # 生命周期后续阶段（复核/完成/报告）各推独立新卡，绝不原地改写本卡
        try:
            card_payload = {
                "task_id": task_id,
                "status": "running",
                "scene": scene,
                "mode": "agent",
                "note": note,
                "image_count": len(input_uris),
            }
            await session_repo.add_message(
                session_id,
                role=ROLE_ASSISTANT,
                kind=KIND_TASK_CARD,
                content=json.dumps(card_payload, ensure_ascii=False),
                task_id=task_id,
            )
        except Exception:  # noqa: BLE001
            logger.exception("launch_inspection card message persist failed")

        # 非阻塞执行（决策 16）：任务在后台跑，落定后由事件回注唤醒会话
        asyncio.create_task(task_service.run_task(task_id, prepared["state"]))
        logger.info(
            "session %s launched inspection task %s (scene=%s, images=%d)",
            session_id, task_id, scene, len(input_uris),
        )
        return json.dumps(
            {
                "task_id": task_id,
                "scene": scene,
                "status": "pending",
                "message": "任务已派发，正在后台执行；任务落定后会自动回报。",
            },
            ensure_ascii=False,
        )

    async def _get_task_detail_impl(args: dict[str, Any], state: dict[str, Any]) -> str:
        task_id = str(args.get("task_id") or "").strip()
        if not task_id:
            return "查询失败：必须提供 task_id 参数。"
        record = await ctx.task_repo.get(task_id) if ctx.task_repo else None
        if record is None:
            return f"未找到任务 {task_id}。请确认任务 ID（T 前缀）。"

        def _loads(raw: str | None) -> Any:
            if not raw:
                return None
            try:
                return json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                return None

        grade = _loads(record.grade_json) or {}
        error = _loads(record.error_json) or {}
        standardized = _loads(record.standardized_json) or []
        defects = [
            {
                "defect_type": d.get("defect_type"),
                "severity": d.get("severity"),
                "confidence": d.get("confidence"),
                "description": d.get("description") or "",
            }
            for d in standardized
            if isinstance(d, dict)
        ][:10]
        return json.dumps(
            {
                "task_id": task_id,
                "status": record.status,
                "scene": record.scene,
                "mode": record.mode,
                "grade_level": grade.get("level"),
                "grade_summary": grade.get("summary"),
                "triggered_rules": grade.get("triggered_rules") or [],
                "defects": defects,
                "error_message": error.get("message"),
            },
            ensure_ascii=False,
        )

    async def _generate_ppt_impl(args: dict[str, Any], state: dict[str, Any]) -> str:
        """会话内生成 PPT：主题→AI/模板大纲→渲染 .pptx→落盘→推文档卡。

        秒级同步生成（与 launch_inspection 的非阻塞派发不同）：直接 await
        ppt_service.run 并在本工具内落文档卡，完成/失败一次到位。
        模板套用、自定义大纲仍留在能力中心表单（待接入聊天，标注建设中）。
        """
        topic = str(args.get("topic") or "").strip()
        session_id = str(state.get("session_id") or "")
        if not session_id:
            return "生成失败：当前会话上下文缺失。"
        if not topic:
            return "生成失败：必须提供演示主题 topic。"
        if ctx.ppt_service is None or ctx.task_repo is None:
            return "生成失败：文档生成服务尚未装配，暂不可用。"

        params: dict[str, Any] = {
            "topic": topic,
            "audience": str(args.get("audience") or "").strip(),
            "pages": str(args.get("pages") or "").strip(),
            "style": str(args.get("style") or "简洁商务").strip(),
            "request": str(args.get("request") or "").strip(),
        }
        task_id = f"T{int(uuid.uuid4().hex[:14], 16)}"[:18]
        try:
            await ctx.task_repo.create(
                task_id,
                scene=None,
                mode="document",
                inputs={"params": params, "materials": [], "template": None, "source": "session"},
                trace_id=new_trace_id(),
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("generate_ppt create task failed, session=%s", session_id)
            return f"生成失败：任务创建异常（{exc}）。请稍后重试。"

        try:
            await ctx.ppt_service.run(task_id, params, [])
        except Exception as exc:  # noqa: BLE001 - run 内部已收敛，此处仅防未预期
            logger.exception("generate_ppt run failed, task=%s", task_id)

        record = await ctx.task_repo.get(task_id)
        status = getattr(record, "status", "failed") if record else "failed"
        report: dict[str, Any] = {}
        error: dict[str, Any] = {}
        if record is not None:
            for raw, target in ((record.report_json, "r"), (record.error_json, "e")):
                try:
                    parsed = json.loads(raw) if raw else {}
                except (json.JSONDecodeError, TypeError):
                    parsed = {}
                if target == "r" and isinstance(parsed, dict):
                    report = parsed
                elif target == "e" and isinstance(parsed, dict):
                    error = parsed

        payload = {
            "task_id": task_id,
            "status": status,
            "doc_type": "pptx",
            "title": topic,
            "filename": report.get("filename") or "presentation.pptx",
            "artifact_uri": report.get("uri"),
            "slide_count": report.get("slide_count"),
            "engine": report.get("engine"),
            "error_message": error.get("message") if status != "completed" else None,
        }
        try:
            await session_repo.add_message(
                session_id,
                role=ROLE_ASSISTANT,
                kind=KIND_DOC_CARD,
                content=json.dumps(payload, ensure_ascii=False),
                task_id=task_id,
            )
        except Exception:  # noqa: BLE001
            logger.exception("generate_ppt doc card persist failed")

        if status == "completed":
            logger.info("session %s generated ppt task %s (slides=%s)", session_id, task_id, payload["slide_count"])
            return json.dumps(
                {
                    "task_id": task_id,
                    "status": "completed",
                    "slide_count": payload["slide_count"],
                    "filename": payload["filename"],
                    "message": "已生成 PPT，下载卡片已推送给用户，无需再重复描述内容。",
                },
                ensure_ascii=False,
            )
        return json.dumps(
            {
                "task_id": task_id,
                "status": status,
                "error_message": payload["error_message"] or "生成失败",
                "message": "PPT 生成失败，已向用户推送失败卡片。",
            },
            ensure_ascii=False,
        )

    return {
        "launch_inspection": _launch_inspection_impl,
        "get_task_detail": _get_task_detail_impl,
        "generate_ppt": _generate_ppt_impl,
    }


async def _dispatch_guard(**_kwargs: Any) -> str:  # pragma: no cover - 仅满足 schema 包装
    """lc_tools 仅供 bind_tools 取 schema；真实执行经 session_tools 节点按 impl 分发。"""
    raise NotImplementedError("会话工具必须经 session_tools 节点分发执行")


def build_session_lc_tools() -> list[StructuredTool]:
    """bind_tools schema 列表（与 ctx 无关）。"""
    return [
        StructuredTool.from_function(
            coroutine=_dispatch_guard,
            name="launch_inspection",
            description=(
                "按指定场景派发一个质检任务（使用用户最近上传的图像，智能体轨执行）。"
                "仅当你确信应该送检时调用；场景由用户确认或用户指定。"
            ),
            args_schema=_LaunchInspectionArgs,
        ),
        StructuredTool.from_function(
            coroutine=_dispatch_guard,
            name="get_task_detail",
            description="查询质检任务的状态、判级结论与缺陷明细，用于回答用户追问。",
            args_schema=_GetTaskDetailArgs,
        ),
        StructuredTool.from_function(
            coroutine=_dispatch_guard,
            name="generate_ppt",
            description=(
                "根据用户描述的主题生成一份演示文稿（PPT）：自动拟定大纲并渲染为 .pptx，"
                "完成后以可下载卡片回复用户。当用户表达“做个 PPT/演示文稿/汇报材料”等意图时调用；"
                "topic 必填，其余听众/页数/风格/补充要求按用户描述尽量提取。"
            ),
            args_schema=_GeneratePptArgs,
        ),
    ]
