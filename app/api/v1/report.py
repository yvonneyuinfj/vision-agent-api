"""报告查询路由（a.md §13：GET /api/v1/reports/{task_id}）。"""
from __future__ import annotations

from fastapi import APIRouter, Request

from app.core.errors import BizCode, BusinessError
from app.core.response import StandardEnvelope
from app.services.report_service import ReportService

router = APIRouter(prefix="/api/v1/reports", tags=["reports"])


def _svc(request: Request) -> ReportService:
    c = request.app.state.container
    if c is None or c.task_repo is None or c.file_repo is None:
        raise BusinessError(BizCode.INTERNAL_ERROR, "container not ready")
    # container 已暴露 task_repo/file_repo 属性（container.py:34-35）
    return ReportService(task_repo=c.task_repo, file_repo=c.file_repo)


@router.get("/{task_id}", response_model=StandardEnvelope[dict])
async def get_report(request: Request, task_id: str) -> StandardEnvelope[dict]:
    result = await _svc(request).get(task_id)
    return StandardEnvelope.ok(result)
