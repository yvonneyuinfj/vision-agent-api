"""节点公共工具：安全执行（记录失败不炸图）+ 追溯 step + 业务抛出转换。

interrupt() 语义（LangGraph 1.2+ 实测）：
- 节点内首次调用会抛出 GraphInterrupt 异常（暂停图，HITL），这里必须放行；
- 图外 astream 观察到 __interrupt__ 状态键（task_service 据此暂停任务）。
"""
from __future__ import annotations

from app.core.logger import get_logger
from functools import wraps
from typing import Any, Awaitable, Callable, TypeVar

from langgraph.errors import GraphInterrupt

from app.core.errors import BizCode, BusinessError
from app.services.deps import AgentContext

logger = get_logger("app.agents.nodes")

NodeFn = Callable[[dict[str, Any], AgentContext], Awaitable[dict[str, Any]]]

StateT = TypeVar("StateT", bound=dict[str, Any])


def node(node_name: str) -> Callable[[NodeFn], NodeFn]:
    """节点装饰器：统一捕获业务错误映射为 state['error']，绝不抛穿出图导致任务悬挂。"""

    def decorator(fn: NodeFn) -> NodeFn:
        @wraps(fn)
        async def wrapper(state: StateT, ctx: AgentContext) -> dict[str, Any]:
            task_id = state.get("task_id") or "-"
            try:
                result = await fn(state, ctx)
                await _append_step(ctx, task_id, node_name, "ok")
                return result
            except GraphInterrupt:
                raise  # HITL：interrupt() 暂停必须原样上抛给图运行器
            except BusinessError as exc:
                await _append_step(ctx, task_id, node_name, f"business error {exc.code} {exc.message}")
                return {"error": {"node": node_name, "code": int(exc.code), "message": exc.message}}
            except Exception as exc:  # noqa: BLE001
                logger.exception("node %s failed, task=%s", node_name, task_id)
                await _append_step(ctx, task_id, node_name, f"error {exc!r}")
                return {
                    "error": {
                        "node": node_name,
                        "code": int(BizCode.INFERENCE_FAILED),
                        "message": f"{node_name} failed: {exc}",
                    }
                }

        return wrapper

    return decorator


async def _append_step(ctx: AgentContext, task_id: str, node_name: str, message: str) -> None:
    if ctx.task_repo is not None:
        try:
            await ctx.task_repo.append_step(task_id, node_name, message)
        except Exception:  # noqa: BLE001 - 追溯失败不阻断流程
            logger.exception("append step failed")


def error_state(node_name: str, code: BizCode, message: str) -> dict[str, Any]:
    return {"error": {"node": node_name, "code": int(code), "message": message}}