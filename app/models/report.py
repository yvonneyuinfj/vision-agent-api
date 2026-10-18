"""报告元数据（a.md §13 models 层，report_service 查询产物）。"""
from __future__ import annotations

from pydantic import BaseModel


class ReportMeta(BaseModel):
    task_id: str
    report_uri: str
    format: str = "markdown"
    generated_at: str
