"""LangGraph Studio 接入入口：独立装配 + 导出四轨图（供 `langgraph dev` 加载）。

LangGraph Studio（浏览器版 smith.langchain.com/studio，免费账号）可视化调试：
看拓扑、任意输入运行、逐节点看状态、HITL interrupt/resume、time travel。

本模块执行与 FastAPI lifespan 相同的全量依赖装配（AppContainer.startup），
再以 checkpointer=None 编译导出四张图——Studio dev server 自行管理线程与检查点
（interrupt 人工复核照常可用）。

装配在独立守护线程的事件循环里完成：启动期遗留的后台任务（能力轮询已显式
stop；联合任务启动对账为 fire-and-forget）不随主导入循环收尾，避免
asyncio.run teardown 挂死；DB 连接池在装配期末 dispose，Studio 运行时在
dev server 自己的事件循环按需重建连接。

注意事项：
- Studio 运行与 :8000 的 FastAPI 主服务相互独立，但共用同一份 .env/DB：
  从 Studio 发起的运行会写入真实任务记录（便于端到端验证，也请留意数据）。
- 能力清单为装配期快照（轮询任务不跨事件循环存活），能力表变更后
  重启 `langgraph dev` 即可。
"""
from __future__ import annotations

import asyncio
import threading
from typing import Any

from app.agents.graph import build_single_graph
from app.agents.react.graph import build_react_graph
from app.agents.react.joint_tools import JointToolbox
from app.agents.session.graph import build_session_graph
from app.services.container import AppContainer

_BOOT_TIMEOUT_S = 120.0


async def _boot() -> AppContainer:
    c = AppContainer()
    await c.startup()
    # 能力轮询任务不跨事件循环存活：显式停干净（Studio 用装配期快照）
    if c.registry is not None:
        await c.registry.stop()
    # 释放启动期创建的 DB 连接池：Studio dev server 之后在另一事件循环跑图，
    # 跨循环复用旧连接会报错；dispose 后连接在运行循环按需重建。
    if c.engine is not None:
        await c.engine.dispose()
    return c


def _boot_sync() -> AppContainer:
    holder: dict[str, Any] = {}

    def _runner() -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            holder["container"] = loop.run_until_complete(_boot())
        except BaseException as exc:  # noqa: BLE001
            holder["error"] = exc
        # 有意不 close 事件循环：启动期遗留后台任务（联合任务启动对账，
        # 可能正在补跑父图）不保证响应取消，强制收尾会挂死；守护线程退出
        # 后残留任务休眠（不再被调度），连接池已 dispose，无跨循环复用风险。

    thread = threading.Thread(target=_runner, daemon=True)
    thread.start()
    thread.join(timeout=_BOOT_TIMEOUT_S)
    if thread.is_alive():
        raise RuntimeError(f"studio container startup timed out ({_BOOT_TIMEOUT_S:.0f}s)")
    if "error" in holder:
        raise holder["error"]
    return holder["container"]


_container = _boot_sync()
_ctx = _container.agent_context
assert _ctx is not None, "container startup failed: agent_context missing"
assert _container.task_service is not None
assert _container.session_repo is not None

# 1) 固定流水线轨（单场景质检全流程）
single_graph = build_single_graph(_ctx)

# 2) ReAct 轨（单场景：标准三工具 + 出口安检）
react_graph = build_react_graph(_ctx)

# 3) ReAct 联合轨（父子任务链；工具箱绑定 task_service 以支持子任务派发）
_joint_toolbox = JointToolbox()
joint_graph = build_react_graph(_ctx, joint=True, toolbox=_joint_toolbox)
_joint_toolbox.bind(_container.task_service)

# 4) 会话智能体轨（对话内思考链/工具调用）
session_graph = build_session_graph(_ctx, _container.session_repo, _container.task_service)

__all__ = ["single_graph", "react_graph", "joint_graph", "session_graph"]
