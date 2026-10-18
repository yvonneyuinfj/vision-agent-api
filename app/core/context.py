"""trace/task 请求上下文（contextvars），供日志与统一外壳透传 trace_id。"""
from __future__ import annotations

import uuid
from contextvars import ContextVar

_trace_id_var: ContextVar[str | None] = ContextVar("trace_id", default=None)
_task_id_var: ContextVar[str | None] = ContextVar("task_id", default=None)


def new_trace_id() -> str:
    return f"trace-{uuid.uuid4().hex[:16]}"


def get_trace_id() -> str:
    return _trace_id_var.get() or new_trace_id()


def set_trace_id(value: str | None) -> None:
    _trace_id_var.set(value)


def get_task_id() -> str | None:
    return _task_id_var.get()


def set_task_id(value: str | None) -> None:
    _task_id_var.set(value)