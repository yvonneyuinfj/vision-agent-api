"""任务领域模型（a.md §13 models 层）。"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

# 任务状态常量（自 app/repositories/task_repo.py 迁入，repositories 引用 models 反向不依赖）
STATUS_PENDING = "pending"
STATUS_RUNNING = "running"
STATUS_AWAITING_REVIEW = "awaiting_review"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"


class TaskDetail(BaseModel):
    """任务详情（task_service.get() 返回的散装 dict 定型化）。"""

    task_id: str
    trace_id: str
    scene: str | None = None
    mode: str = "single"
    status: str = STATUS_PENDING
    capability_used: dict[str, Any] | None = None
    steps: list[dict[str, Any]] = Field(default_factory=list)
    standardized: list[dict[str, Any]] = Field(default_factory=list)
    grade: dict[str, Any] | None = None
    knowledge: list[dict[str, Any]] = Field(default_factory=list)
    review: dict[str, Any] | None = None
    report: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    created_at: str | None = None
    updated_at: str | None = None
    completed_at: str | None = None
