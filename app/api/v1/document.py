"""文档生成路由：PPT 制作（提交 / 轮询），复用 tasks 表与文件下载链路。

- POST /api/v1/documents/ppt：表单参数 + 可选参考资料（.txt/.md/.docx/.pdf），
  建一条 mode='document' 的任务，后台生成，立即返回 task_id；
- GET  /api/v1/documents/{task_id}：轮询状态，完成后给产物下载 URI；
- 下载复用既有 GET /api/v1/tasks/{id}/files/{name}?kind=artifacts（安全白名单 + 路径穿越防护）。

严格分层：路由只做校验与外壳，生成逻辑在 services.ppt_service。
"""
from __future__ import annotations

import json
import uuid
from typing import Any

from fastapi import APIRouter, BackgroundTasks, File, Form, Request, UploadFile

from app.core.context import new_trace_id
from app.core.errors import BizCode, BusinessError
from app.core.logger import get_logger
from app.core.response import StandardEnvelope
from app.models.task import STATUS_PENDING

logger = get_logger("app.api.documents")

router = APIRouter(prefix="/api/v1/documents", tags=["documents"])

# 资料上传边界（PPT 参考料只做文本抽取，设上限防内存滥用）
_MAX_MATERIALS = 8
_MAX_MATERIAL_BYTES = 20 * 1024 * 1024
# 模板上限（.pptx 母版可能含图，给宽松一点）
_MAX_TEMPLATE_BYTES = 50 * 1024 * 1024


def _new_task_id() -> str:
    return f"T{int(uuid.uuid4().hex[:14], 16)}"[:18]


def _ppt_service(request: Request):
    c = request.app.state.container
    if c is None or getattr(c, "ppt_service", None) is None or c.task_repo is None:
        raise BusinessError(BizCode.INTERNAL_ERROR, "container not ready")
    return c.ppt_service, c.task_repo


@router.post("/ppt", response_model=StandardEnvelope[dict])
async def submit_ppt(
    request: Request,
    background: BackgroundTasks,
    topic: str | None = Form(default=None),
    title: str | None = Form(default=None),
    audience: str | None = Form(default=None),
    pages: str | None = Form(default=None),
    style: str | None = Form(default=None),
    request_text: str | None = Form(default=None, alias="request"),
    outline_json: str | None = Form(default=None),
    materials: list[UploadFile] = File(default=[]),
    template: UploadFile | None = File(default=None),
) -> StandardEnvelope[dict]:
    """提交 PPT 生成任务。

    可给主题/补充要求（后端自动生成大纲），或直接给自定义大纲 outline_json（跳过 LLM）；
    可选上传 .pptx 模板，成品套用其母版/主题/背景/Logo。
    """
    params: dict[str, Any] = {
        "topic": (topic or title or "").strip(),
        "audience": (audience or "").strip(),
        "pages": (pages or "").strip(),
        "style": (style or "简洁商务").strip(),
        "request": (request_text or "").strip(),
    }

    # 自定义大纲：存在则优先（service 层据此跳过 LLM）；非法直接拒绝，不静默降级
    if outline_json and outline_json.strip():
        try:
            raw = json.loads(outline_json)
        except json.JSONDecodeError:
            raise BusinessError(BizCode.INVALID_REQUEST, "自定义大纲格式非法（需 JSON）", http_status=400)
        if isinstance(raw, list):
            raw = {"sections": raw}
        if not isinstance(raw, dict) or not (raw.get("sections") or []):
            raise BusinessError(BizCode.INVALID_REQUEST, "自定义大纲至少需要一个章节", http_status=400)
        params["outline"] = raw

    has_outline = "outline" in params
    if not params["topic"] and not params["request"] and not has_outline:
        raise BusinessError(
            BizCode.INVALID_REQUEST, "请至少填写演示主题、补充要求或自定义大纲", http_status=400
        )

    picked = [m for m in materials if m and m.filename]
    if len(picked) > _MAX_MATERIALS:
        raise BusinessError(BizCode.INVALID_REQUEST, f"参考资料最多 {_MAX_MATERIALS} 个", http_status=400)

    loaded: list[tuple[str, bytes]] = []
    for m in picked:
        data = await m.read()
        if len(data) > _MAX_MATERIAL_BYTES:
            raise BusinessError(
                BizCode.INVALID_REQUEST, f"资料 {m.filename} 超过 20MB 上限", http_status=400
            )
        loaded.append((m.filename or "material", data))

    # 可选 .pptx 模板
    template_bytes: bytes | None = None
    template_name: str | None = None
    if template is not None and template.filename:
        if not template.filename.lower().endswith(".pptx"):
            raise BusinessError(BizCode.INVALID_REQUEST, "模板仅支持 .pptx 格式", http_status=400)
        tb = await template.read()
        if len(tb) > _MAX_TEMPLATE_BYTES:
            raise BusinessError(BizCode.INVALID_REQUEST, "模板超过 50MB 上限", http_status=400)
        template_bytes = tb
        template_name = template.filename

    svc, task_repo = _ppt_service(request)
    task_id = _new_task_id()
    await task_repo.create(
        task_id,
        scene=None,
        mode="document",
        inputs={
            "params": params,
            "materials": [name for name, _ in loaded],
            "template": template_name,
        },
        trace_id=new_trace_id(),
    )
    background.add_task(svc.run, task_id, params, loaded, template_bytes)
    return StandardEnvelope.ok(
        {"task_id": task_id, "status": STATUS_PENDING}, message="document submitted", task_id=task_id
    )


@router.get("/{task_id}", response_model=StandardEnvelope[dict])
async def get_document(request: Request, task_id: str) -> StandardEnvelope[dict]:
    """轮询文档任务：pending/running → completed(带下载 URI) / failed(带错误)。"""
    _, task_repo = _ppt_service(request)

    row = await task_repo.get(task_id)
    if row is None:
        raise BusinessError(BizCode.TASK_NOT_FOUND, f"document {task_id} not found", http_status=404)
    if (row.mode or "") != "document":
        raise BusinessError(BizCode.INVALID_REQUEST, f"task {task_id} is not a document job", http_status=400)

    report = json.loads(row.report_json) if row.report_json else {}
    error = json.loads(row.error_json) if row.error_json else {}
    result: dict[str, Any] = {"task_id": task_id, "status": row.status}
    if report:
        result.update(
            {
                "artifact_uri": report.get("uri"),
                "filename": report.get("filename"),
                "slide_count": report.get("slide_count"),
                "engine": report.get("engine"),
                "material_notes": report.get("material_notes") or [],
            }
        )
    if error:
        result["error"] = error.get("message", "")
    return StandardEnvelope.ok(result, task_id=task_id)
