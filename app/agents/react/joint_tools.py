"""联合质检工具箱（mode=joint 的 ReAct 轨）：标准三件套 + 子任务编排。

决策对齐：
- 决策 8：联合层全自主——不存在硬编码联合业务规则，汇总时机/方式由智能体决定；
- 决策 10：父子任务 + 子任务落定事件注入——父任务派发子任务后调用
  await_children 登记等待，图以 interrupt 暂停；任一子任务落定时
  task_service._maybe_resume_parent 检查兄弟是否全部落定，齐了才唤醒父图；
- 决策 4：单一能力通道——每个子任务都是完整的单场景 agent 轨（能力调用
  仍走 invoke_capability 通道），父任务不绕过子任务直连能力。

task_service 通过 bind() 延迟注入（图先于服务实例装配，避免循环依赖）。
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from app.agents.react.tools import ReactToolbox, _dispatch_guard
from app.core.logger import get_logger

logger = get_logger("app.agents.react.joint_tools")

_CHILD_RELAUNCH_MARGIN = 2  # 允许的自救补派额度（超出候选场景数的部分）
_CHILD_NOTE_CAP = 200


class _LaunchChildArgs(BaseModel):
    scene: str = Field(description="子任务的检测场景编码（必须在本任务候选场景列表内）")
    note: str = Field(default="", description="给子任务智能体的备注（可选，如重点关注什么）")


class _GetChildStatusArgs(BaseModel):
    task_id: str = Field(description="子任务 ID（launch_child_task 返回）")


class _AwaitChildrenArgs(BaseModel):
    reason: str = Field(default="", description="本次进入等待的原因（可选，仅作留痕）")


class JointToolbox(ReactToolbox):
    """标准三件套 + launch_child_task / get_child_status / await_children。"""

    def __init__(self) -> None:
        super().__init__()
        self.task_service: Any = None  # bind() 注入
        self.impls.update(
            {
                "launch_child_task": self._launch_child_impl,
                "get_child_status": self._get_child_status_impl,
                "await_children": self._await_children_impl,
            }
        )
        self.lc_tools.extend(
            [
                StructuredTool.from_function(
                    coroutine=_dispatch_guard,
                    name="launch_child_task",
                    description=(
                        "为指定场景派发一个单场景质检子任务（独立智能体后台执行，继承本任务图片）。"
                        "同一场景重复调用返回已有子任务，不会重复派发。"
                        "全部候选场景派发完毕后调用 await_children 等待。"
                    ),
                    args_schema=_LaunchChildArgs,
                ),
                StructuredTool.from_function(
                    coroutine=_dispatch_guard,
                    name="get_child_status",
                    description="查询已派发子任务的当前状态与结论摘要（仅可查询本任务派发的子任务）。",
                    args_schema=_GetChildStatusArgs,
                ),
                StructuredTool.from_function(
                    coroutine=_dispatch_guard,
                    name="await_children",
                    description=(
                        "登记等待：所有已派发子任务落定后系统会自动唤醒你并回传各子任务结论。"
                        "等待期间无需轮询，不要反复调用 get_child_status。"
                    ),
                    args_schema=_AwaitChildrenArgs,
                ),
            ]
        )

    def bind(self, task_service: Any) -> None:
        """容器装配期注入 task_service（图先建、服务后立，延迟绑定解循环）。"""
        self.task_service = task_service

    # ------------------------------------------------------------------
    # launch_child_task：派发单场景子任务（agent 轨，后台执行）
    # ------------------------------------------------------------------
    async def _launch_child_impl(
        self, args: dict[str, Any], state: dict[str, Any], ctx: Any
    ) -> tuple[str, dict[str, Any]]:
        scene = str(args.get("scene") or "").strip().lower()
        note = str(args.get("note") or "").strip()[:_CHILD_NOTE_CAP]
        if not scene:
            return "调用失败：必须提供 scene 参数。", {}

        launched = dict(state.get("children_launched") or {})
        if scene in launched:
            return (
                json.dumps(
                    {
                        "task_id": launched[scene],
                        "scene": scene,
                        "message": "该场景的子任务已派发，无需重复；可用 get_child_status 查询。",
                    },
                    ensure_ascii=False,
                ),
                {},
            )

        declared = [str(s).strip().lower() for s in (state.get("scenes") or []) if str(s).strip()]
        if declared and scene not in declared:
            return (
                f"场景 {scene} 不在本任务候选列表（{', '.join(declared)}）内，"
                "请按候选场景派发子任务。",
                {},
            )

        if ctx.registry is None or ctx.registry.lookup(scene) is None:
            registered = sorted({m.scene for m in ctx.registry.list()}) if ctx.registry is not None else []
            return (
                f"场景 {scene} 无可用能力。已注册场景：{', '.join(registered) or '无'}。",
                {},
            )

        cap = max(len(declared), 1) + _CHILD_RELAUNCH_MARGIN
        if len(launched) >= cap:
            return (
                f"子任务派发已达上限（{cap} 个，含自救补派）。"
                "请调用 await_children 等待已有子任务落定，并基于现有结论提交。",
                {},
            )

        svc = self.task_service
        if svc is None:
            return "平台服务未就绪（任务服务未注入），无法派发子任务。", {}

        auxiliary: dict[str, str] = {"parent_task_id": str(state.get("task_id") or "")}
        if note:
            auxiliary["note"] = note
        # 备注经 parameters 透传：bootstrap 会以"用户透传参数"提示给子智能体
        # （不直接写 state 非 schema 通道，避免 LangGraph 更新校验问题）
        child_params = dict(state.get("parameters") or {})
        if note:
            child_params["note"] = note
        try:
            prepared = await svc.prepare(
                scene=scene,
                mode="agent",
                scenes=[scene],
                parameters=child_params,
                input_uris=list(state.get("input_uris") or []),
                auxiliary=auxiliary,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("launch_child_task prepare failed")
            return f"子任务派发失败：{exc}", {}

        child_id = prepared["task_id"]
        child_state = dict(prepared["state"])

        async def _run_child() -> None:
            try:
                await svc.run_task(child_id, child_state)
            except Exception as exc:  # noqa: BLE001 - 双保险：run_task 内部已有兜底
                logger.exception("child task %s crashed", child_id)
                try:
                    await svc._repo.set_error(child_id, 5001, f"子任务执行异常：{exc}")
                    await svc._notify_settled(child_id)
                except Exception:  # noqa: BLE001
                    logger.exception("child crash persist failed")

        asyncio.create_task(_run_child())

        launched[scene] = child_id
        return (
            json.dumps(
                {
                    "task_id": child_id,
                    "scene": scene,
                    "status": "pending",
                    "message": "子任务已派发（后台执行）。请继续派发其余候选场景；全部派发后调用 await_children。",
                },
                ensure_ascii=False,
            ),
            {"children_launched": launched},
        )

    # ------------------------------------------------------------------
    # get_child_status：查询已派发子任务的状态与结论
    # ------------------------------------------------------------------
    async def _get_child_status_impl(
        self, args: dict[str, Any], state: dict[str, Any], ctx: Any
    ) -> tuple[str, dict[str, Any]]:
        task_id = str(args.get("task_id") or "").strip()
        launched = state.get("children_launched") or {}
        if task_id not in set(launched.values()):
            return f"任务 {task_id} 不是本任务派发的子任务，无法查询。", {}
        svc = self.task_service
        if svc is None:
            return "平台服务未就绪，无法查询子任务。", {}
        detail = await svc.get(task_id)
        if detail is None:
            return f"子任务 {task_id} 不存在。", {}
        grade = detail.get("grade") or {}
        snapshot = {
            "task_id": task_id,
            "scene": detail.get("scene"),
            "status": detail.get("status"),
            "grade_level": grade.get("level"),
            "grade_summary": grade.get("summary"),
            "defects": (detail.get("standardized") or [])[:15],
        }
        return json.dumps(snapshot, ensure_ascii=False), {}

    # ------------------------------------------------------------------
    # await_children：登记等待（路由进 await 分支 → 节点 interrupt）
    # ------------------------------------------------------------------
    async def _await_children_impl(
        self, args: dict[str, Any], state: dict[str, Any], ctx: Any
    ) -> tuple[str, dict[str, Any]]:
        launched = state.get("children_launched") or {}
        if not launched:
            return "还不能等待：尚未派发任何子任务。请先用 launch_child_task 逐场景派发。", {}
        reason = str(args.get("reason") or "").strip()
        digest = "、".join(f"{scene}→{tid}" for scene, tid in launched.items())
        suffix = f"（原因：{reason}）" if reason else ""
        return (
            f"已登记等待{suffix}：子任务 {digest} 全部落定后系统会自动唤醒你并回传结论；"
            "等待期间无需轮询。",
            {"await_children_requested": True},
        )


def build_joint_tools(ctx: Any) -> JointToolbox:
    """装配联合工具箱；task_service 由调用方经 bind() 注入。"""
    _ = ctx
    return JointToolbox()


__all__ = ["JointToolbox", "build_joint_tools"]
