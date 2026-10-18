"""统一响应外壳（对齐规范 §7）。

所有平台接口返回:
{code, message, task_id, trace_id, tool, result}
其中 result 为业务数据；错误时 result=None（算法特有结果放 result 内，与工具壳一致）。
"""
from __future__ import annotations

from typing import Any, Generic, TypeVar

from pydantic import BaseModel, Field

from app.core import context
from app.core.config import get_settings
from app.core.errors import BizCode

T = TypeVar("T")


class ToolInfo(BaseModel):
    """平台对外身份（对应规范工具版本信息）。"""

    tool_name: str
    tool_version: str
    api_version: str = "v1"


class StandardEnvelope(BaseModel, Generic[T]):
    code: int = 0
    message: str = "success"
    task_id: str | None = None
    trace_id: str | None = None
    tool: ToolInfo
    result: T | None = None

    def to_shell(self) -> dict[str, Any]:
        """序列化为统一外壳：保留 result/task_id 的 null（对齐规范 §7 错误必含 result）。"""
        return self.model_dump()

    @classmethod
    def ok(
        cls,
        result: T | None = None,
        *,
        code: int = 0,
        message: str = "success",
        task_id: str | None = None,
    ) -> "StandardEnvelope[T]":
        """成功外壳：trace_id 自动取请求上下文。"""
        return cls(
            code=code,
            message=message,
            task_id=task_id or context.get_task_id(),
            trace_id=context.get_trace_id(),
            tool=cls._tool(),
            result=result,
        )

    @classmethod
    def err(
        cls,
        code: BizCode | int,
        message: str,
        *,
        http_status: int | None = None,  # noqa: ARG002 仅供可读性；HTTP 状态由处理器决定
        task_id: str | None = None,
        trace_id: str | None = None,
    ) -> "StandardEnvelope[None]":
        return cls(
            code=int(code),
            message=message,
            task_id=task_id or context.get_task_id(),
            trace_id=trace_id or context.get_trace_id(),
            tool=cls._tool(),
            result=None,
        )

    @staticmethod
    def _tool() -> ToolInfo:
        s = get_settings()
        return ToolInfo(tool_name=s.tool_name, tool_version=s.tool_version)


# 分页元数据（规范建议的元数据字段）
class PageMeta(BaseModel):
    total: int
    page: int = 1
    page_size: int = 20


class PageResult(BaseModel, Generic[T]):
    items: list[T]
    meta: PageMeta = Field(default_factory=lambda: PageMeta(total=0))