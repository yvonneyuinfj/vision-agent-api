"""报告服务：报告查询下沉（a.md §13 services/report_service.py）。"""
from __future__ import annotations

import json

from app.core.errors import BizCode, BusinessError
from app.models.report import ReportMeta
from app.repositories.file_repo import FileRepo
from app.repositories.task_repo import TaskRepo


class ReportService:
    """报告查询门面：任务存在性 + 报告就绪性校验 + 正文读取。"""

    def __init__(self, task_repo: TaskRepo, file_repo: FileRepo) -> None:
        self._task_repo = task_repo
        self._file_repo = file_repo

    async def get(self, task_id: str) -> dict:
        row = await self._task_repo.get(task_id)
        if row is None:
            raise BusinessError(BizCode.TASK_NOT_FOUND, f"task not found: {task_id}")
        report = row.report_json and json.loads(row.report_json) or {}
        if not report or not report.get("uri"):
            raise BusinessError(
                BizCode.TASK_NOT_FOUND, f"report not generated: {task_id}"
            )
        try:
            path = self._file_repo.uri_to_path(report["uri"])
        except ValueError as exc:
            raise BusinessError(
                BizCode.TASK_NOT_FOUND, f"report uri invalid: {task_id}"
            ) from exc
        content = path.read_text(encoding="utf-8") if path.exists() else ""
        meta = ReportMeta(
            task_id=task_id,
            report_uri=report["uri"],
            format="markdown",
            generated_at=report.get("created_at", ""),
        )
        return {"meta": meta, "content": content}
