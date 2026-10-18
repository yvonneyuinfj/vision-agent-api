"""AI Agent 工具抽象与注册中心（对齐参考标杆 pi 的 tool/registry 模式）。

设计意图：
- 把「孔探检测」「红外检测」「RAG 检索」「判级引擎」等能力统一抽象为 `Tool`，
  LangGraph 状态图只依赖注册中心按**名称**取工具，不在图里硬编码任何具体算法；
- 新增算法（如红外、断口、组织）只要实现一个 Tool 并 register，图无需改动；
- 支持运行时动态挂载/卸载（与 capabilities 热重载解耦，M2 联合质检可复用同一注册表）。

分层定位：本模块属于**编排层**的工具域（app/agents/tools/），不直接发起 IO；
具体执行由注入的适配器 / 知识层承担。
"""
from __future__ import annotations

import inspect
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Callable, Generic, TypeVar

from app.core.logger import get_logger

logger = get_logger("app.agents.tools")

R = TypeVar("R")


class Tool(ABC, Generic[R]):
    """统一工具抽象：name + description + execute。

    execute(inputs) -> R；子类负责自身输入校验、调用底层能力并把异常收敛为
    ToolResult.failure(...)，不向外抛裸异常（与节点 node_utils 的错误收敛一致）。
    """

    #: 唯一标识，如 "detect/borescope"、"detect/infrared"、"knowledge/rag"
    name: str = ""
    #: 单行说明（注册中心 / 前端能力中心展示）
    description: str = ""
    #: 场景标签（空 = 全场景适用），用于按场景过滤
    scene: str = ""

    @abstractmethod
    async def execute(self, inputs: dict[str, Any]) -> R:
        """执行工具。输入输出均为普通 dict 以便序列化与状态写入。"""

    def meta(self) -> dict[str, Any]:
        return {"name": self.name, "description": self.description, "scene": self.scene}


@dataclass(frozen=True)
class ToolResult:
    """工具统一返回（对齐规范 §7 外壳的最小化投影）。"""

    ok: bool
    code: int = 4001  # 失败业务码（成功时忽略）
    message: str = ""
    result: Any = None

    @staticmethod
    def success(result: Any) -> "ToolResult":
        return ToolResult(ok=True, code=0, result=result)

    @staticmethod
    def failure(code: int, message: str) -> "ToolResult":
        return ToolResult(ok=False, code=code, message=message)


def _snake(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


class _FnTool(Tool[Any]):
    """普通函数包装为 Tool（装饰器/register_callable 统一路径）。"""

    def __init__(
        self,
        fn: Callable[[dict[str, Any]], Any],
        name: str,
        scene: str = "",
        description: str = "",
    ) -> None:
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "scene", scene)
        object.__setattr__(self, "description", description or name)
        self._fn = fn

    async def execute(self, inputs: dict[str, Any]) -> Any:
        return await self._fn(inputs) if inspect.iscoroutinefunction(self._fn) else self._fn(inputs)


class ToolRegistry:
    """工具注册中心：注册 / 解析 / 按名枚举 / 场景过滤。

    - register(tool)            显式注册实例
    - register_decorator()      类/函数装饰器
    - register_callable(fn)     普通 async 函数包装注册（轻量工具）
    - get(name) / has(name)     精确解析
    - for_scene(scene)           该场景可用的工具列表
    - execute(name, inputs)     便捷执行，异常统一包成 ToolResult.failure
    """

    def __init__(self) -> None:
        self._tools: dict[str, Tool[Any]] = {}

    # ---------- 注册 ----------
    def register(self, tool: Tool[Any]) -> None:
        if not isinstance(tool, Tool):
            raise TypeError(f"expected Tool, got {type(tool).__name__}")
        if not tool.name:
            raise ValueError("Tool.name is required")
        self._tools[tool.name] = tool
        logger.info("tool registered: %s", tool.name)

    def register_callable(
        self,
        fn: Callable[[dict[str, Any]], Any],
        name: str = "",
        description: str = "",
        scene: str = "",
    ) -> "ToolRegistry":
        """用 async/sync 函数注册轻量工具（隐式 Tool 包装）。"""
        self.register(
            _FnTool(fn=fn, name=name or _snake(fn.__name__), scene=scene, description=description)
        )
        return self

    def register_decorator(
        self, name: str = "", description: str = "", scene: str = ""
    ) -> Callable[[Any], Tool[Any]]:
        """装饰器：注册 Tool 子类（实例化）或普通函数（包装）。

        @registry.register_decorator()
        class MyTool(Tool): ...

        @registry.register_decorator(name="x", description="...")
        async def my_fn(inputs): ...
        """

        def _wrap(obj) -> Tool[Any]:  # noqa: ANN001
            if inspect.isclass(obj) and issubclass(obj, Tool):
                instance: Tool[Any] = obj()
                instance.name = name or _snake(obj.__name__)
                if description:
                    instance.description = description
                if scene:
                    instance.scene = scene
            elif callable(obj):
                instance = _FnTool(
                    fn=obj,
                    name=name or _snake(obj.__name__),
                    scene=scene,
                    description=description,
                )
            else:  # pragma: no cover
                raise TypeError(f"cannot register {obj!r}")
            self.register(instance)
            return instance

        return _wrap

    # ---------- 查询 ----------
    def get(self, name: str) -> Tool[Any] | None:
        return self._tools.get(name)

    def has(self, name: str) -> bool:
        return name in self._tools

    def for_scene(self, scene: str) -> list[Tool[Any]]:
        """返回指定场景（或全局标签）可用的工具，保持注册顺序。"""
        return [t for t in self._tools.values() if t.scene in ("", scene)]

    def list(self) -> list[Tool[Any]]:
        return list(self._tools.values())

    def names(self) -> list[str]:
        return list(self._tools)

    def remove(self, name: str) -> None:
        self._tools.pop(name, None)

    def clear(self) -> None:
        self._tools.clear()

    def __len__(self) -> int:
        return len(self._tools)

    # ---------- 执行（对外最小门面） ----------
    async def execute(self, name: str, inputs: dict[str, Any]) -> ToolResult:
        """便捷执行：找不到 / 异常均收敛为 ToolResult.failure，不抛裸异常。"""
        tool = self._tools.get(name)
        if tool is None:
            return ToolResult.failure(404, f"tool not found: {name}")
        try:
            out = await tool.execute(inputs)
            return out if isinstance(out, ToolResult) else ToolResult.success(out)
        except Exception as exc:  # noqa: BLE001
            logger.exception("tool %s failed", name)
            return ToolResult.failure(4001, f"tool {name} error: {exc}")


# 进程级默认注册表单例（与 get_settings 同理：全局共享，容器启动时填充工具；
# 需要隔离的多实例编排可自行 new ToolRegistry 注入 AgentContext）
_PROCESS_REGISTRY = ToolRegistry()


def get_default_registry() -> ToolRegistry:
    """返回进程级默认注册表。"""
    return _PROCESS_REGISTRY