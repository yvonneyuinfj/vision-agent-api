"""任务路由：提交 / 详情 / 文件下载 / 人工复核 / 历史查询。

严格分层：路由层只做参数校验与外壳封装，业务(图编排/复核/追溯)全部交给
services.task_service。异步执行通过 FastAPI BackgroundTasks，不阻塞请求线程。
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from app.core.logger import get_logger
import os
from typing import Any

from fastapi import APIRouter, BackgroundTasks, File, Form, Query, Request, UploadFile
from fastapi.responses import FileResponse

from app.api.schemas.task import (
    ReportRef,
    ReviewRequest,
    SceneConfirmRequest,
    StepTraceItem,
    TaskDetail,
    TaskStats,
    TaskStatus,
    TaskSummary,
)
from app.core.errors import BizCode, BusinessError
from app.core.response import PageMeta, PageResult, StandardEnvelope

logger = get_logger("app.api.tasks")

router = APIRouter(prefix="/api/v1/tasks", tags=["tasks"])

ALLOWED_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".tif", ".tiff"}
ALLOWED_KINDS = {"inputs", "artifacts", "reports"}


def _svc(request: Request):
    c = request.app.state.container
    if c is None or c.task_service is None:
        raise BusinessError(BizCode.INTERNAL_ERROR, "container not ready")
    return c.task_service


@router.post("", response_model=StandardEnvelope[dict])
async def submit_task(
    request: Request,
    background: BackgroundTasks,
    files: list[UploadFile] = [],
    scene: str | None = Form(default=None),
    mode: str = Form(default="single"),
    parameters: str | None = Form(default=None),
    scenes: str | None = Form(default=None),  # 多场景联合质检
) -> StandardEnvelope[dict]:
    """提交任务：上传原图（及配套输入），随后后台执行 LangGraph 流程。

    mode：
    - single：单场景固定流水线（需 scene）；
    - agent：单场景 ReAct 智能体轨（场景由智能体自主判定）；
    - joint：多场景联合质检（ReAct 联合轨，需 scenes；决策 8/10）。
    """
    if not files:
        raise BusinessError(BizCode.INVALID_REQUEST, "no image uploaded", http_status=400)
    scene_list = [s.strip().lower() for s in (scenes or "").split(",") if s.strip()]
    if mode not in ("single", "agent", "joint"):
        raise BusinessError(
            BizCode.INVALID_REQUEST,
            f"unknown mode: {mode}（支持 single=固定流水线 / agent=ReAct 智能体轨 / joint=联合质检）",
            http_status=400,
        )
    if mode == "joint" and not scene_list:
        raise BusinessError(
            BizCode.INVALID_REQUEST,
            "joint 联合质检必须提供 scenes（逗号分隔的候选场景列表，至少 1 个）",
            http_status=400,
        )

    container = request.app.state.container
    file_repo = container.file_repo
    storage = container.storage  # StorageService | None（未配置 MinIO 时降级）
    svc = _svc(request)

    # ---- 落盘原图（严格扩展名/类型校验，对齐规范 §6）+ MinIO 内容寻址去重 ----
    input_uris: list[str] = []
    auxiliary: dict[str, str] = {}
    for f in files:
        ext = os.path.splitext(f.filename or "")[1].lower()
        if ext not in ALLOWED_IMAGE_EXTS:
            continue
        content = await f.read()
        if not content:
            raise BusinessError(BizCode.INVALID_IMAGE, f"{f.filename} is empty", http_status=422)
        uri = file_repo.save_bytes("_upload", f.filename, content, kind="inputs")
        input_uris.append(uri)
        if storage is not None:
            try:
                # 去重上传：同内容只存一份，失败不阻断主流程（本地副本仍在）
                meta = await storage.store_bytes(
                    content, filename=f.filename, task_id="_upload", meta={"scene": scene}
                )
                logger.info("upload sha256=%s deduped=%s", meta["sha256"], meta["deduped"])
            except Exception:  # noqa: BLE001
                logger.exception("minio store failed, fallback to local only: %s", f.filename)

    if not input_uris:
        raise BusinessError(
            BizCode.UNSUPPORTED_IMAGE_FORMAT, "no valid image provided", http_status=415
        )

    param_dict: dict[str, Any] = {}
    if parameters:
        try:
            param_dict = json.loads(parameters)
        except Exception:
            raise BusinessError(
                BizCode.INVALID_PARAMETER, "parameters must be valid JSON", http_status=422
            ) from None
        if not isinstance(param_dict, dict):
            raise BusinessError(
                BizCode.INVALID_PARAMETER, "parameters must be an object", http_status=422
            )

    prepared = await svc.prepare(
        scene=scene,
        mode=mode,
        scenes=scene_list,
        parameters=param_dict,
        input_uris=input_uris,
        auxiliary=auxiliary,
    )
    task_id = prepared["task_id"]
    state = prepared["state"]

    # 后台执行（不等待人工复核完成即返回；进度经 GET /tasks/{id} 查询）
    background.add_task(svc.run_task, task_id, state)

    return StandardEnvelope.ok(
        {"task_id": task_id, "mode": mode}, message="task submitted", task_id=task_id
    )


@router.get("/stats", response_model=StandardEnvelope[TaskStats])
async def get_task_stats(request: Request) -> StandardEnvelope[TaskStats]:
    """任务统计：全量权威计数。必须声明在 /{task_id} 之前，否则 "stats" 被捕获为 task_id。"""
    svc = _svc(request)
    return StandardEnvelope.ok(TaskStats(**await svc.get_stats()))


@router.get("/review-matrix", response_model=StandardEnvelope[dict])
async def get_review_matrix(
    request: Request, limit: int = Query(default=500, ge=1, le=5000)
) -> StandardEnvelope[dict]:
    """复核数据闭环（评估矩阵）：人工复核留痕聚合为漏检/误检统计与阈值对照。

    必须声明在 /{task_id} 之前（同 /stats）。聚合口径见 app/services/review_matrix.py。
    """
    from app.services.review_matrix import aggregate_review_matrix, collect_review_matrix

    container = request.app.state.container
    if container is None or container.task_repo is None:
        raise BusinessError(BizCode.INTERNAL_ERROR, "container not ready")

    tasks = await collect_review_matrix(container.task_repo, limit=limit)
    # 阈值对照口径与判级规则一致（grade_rules.yaml 的 confidence_threshold）
    threshold = 0.5
    try:
        rules_dir = getattr(container.settings, "rules_dir", None)
        if rules_dir:
            from app.agents.nodes.grading import _load_rules

            rules = await asyncio.to_thread(_load_rules, Path(rules_dir))
            threshold = float(rules.get("confidence_threshold", 0.5))
    except Exception:  # noqa: BLE001 - 规则读取失败回退默认阈值
        pass
    matrix = await asyncio.to_thread(aggregate_review_matrix, tasks, confidence_threshold=threshold)
    return StandardEnvelope.ok(matrix)


@router.get("/{task_id}", response_model=StandardEnvelope[TaskDetail])
async def get_task(request: Request, task_id: str) -> StandardEnvelope[TaskDetail]:
    svc = _svc(request)
    data = await svc.get(task_id)
    if data is None:
        raise BusinessError(BizCode.TASK_NOT_FOUND, f"task {task_id} not found", http_status=404)
    return StandardEnvelope.ok(_to_detail(data), task_id=task_id)


@router.get("/{task_id}/trace", response_model=StandardEnvelope[dict])
async def get_task_trace(request: Request, task_id: str) -> StandardEnvelope[dict]:
    """ReAct 思考链（检查点完整轨迹归一化）；固定流水线任务返回空条目列表。"""
    svc = _svc(request)
    return StandardEnvelope.ok(await svc.trace(task_id), task_id=task_id)


# ---------- 知识引用原文预览（前端 KnowledgeCard「预览原文」） ----------

async def _citation_item(svc, task_id: str, chunk_id: str) -> dict[str, Any]:
    """取任务落库引用列表中匹配的 knowledge 项；任务/引用缺失给可读业务错误。"""
    data = await svc.get(task_id)
    if data is None:
        raise BusinessError(BizCode.TASK_NOT_FOUND, f"task {task_id} not found", http_status=404)
    for item in data.get("knowledge") or []:
        if item.get("chunk_id") == chunk_id:
            return item
    raise BusinessError(
        BizCode.INVALID_REQUEST, f"chunk {chunk_id} not in task knowledge", task_id=task_id
    )


def _ragflow_client(request: Request):
    """RAGFlow 客户端；未配置/不可用返回 None（调用方降级而非报错）。"""
    c = request.app.state.container
    client = getattr(c, "ragflow", None) if c is not None else None
    if client is None or not getattr(client, "available", False):
        return None
    return client


def _dataset_ids(request: Request) -> list[str]:
    """知识库 dataset ids（单一事实源 = ragflow_chat.yaml，容器启动时载入）。"""
    c = request.app.state.container
    return list(getattr(c, "ragflow_dataset_ids", []) or []) if c is not None else []


async def _resolve_document(
    client, dataset_ids: list[str], item: dict[str, Any]
) -> tuple[str, dict[str, Any]] | None:
    """按 document_id（新任务落库）或 doc_name 反查 (dataset_id, document)。"""
    target_doc_id = item.get("document_id")
    for dataset_id in dataset_ids:
        try:
            docs = await client.list_documents(dataset_id)
        except Exception as exc:  # noqa: BLE001 - 单数据集失败容忍，其余继续
            logger.warning("resolve document: list documents %s failed: %s", dataset_id, exc)
            continue
        for doc in docs:
            if (target_doc_id and doc.get("id") == target_doc_id) or doc.get("name") == item.get("doc_name"):
                return dataset_id, doc
    return None


def _chunk_ref(chunk: dict[str, Any]) -> dict[str, Any]:
    return {"chunk_id": chunk.get("chunk_id"), "content": chunk.get("content", ""), "page": chunk.get("page")}


@router.get("/{task_id}/knowledge/{chunk_id}/context", response_model=StandardEnvelope[dict])
async def knowledge_context(
    request: Request, task_id: str, chunk_id: str, n: int = Query(2, ge=1, le=10)
) -> StandardEnvelope[dict]:
    """引用上下文：命中块 + 同文档前后各 n 块（RAGFlow 缺席/未命中降级为空窗）。"""
    svc = _svc(request)
    item = await _citation_item(svc, task_id, chunk_id)
    client = _ragflow_client(request)

    doc_id: str | None = None
    self_page: int | None = None
    prev: list[dict[str, Any]] = []
    nxt: list[dict[str, Any]] = []
    if client is not None:
        resolved = await _resolve_document(client, _dataset_ids(request), item)
        if resolved is not None:
            dataset_id, doc = resolved
            doc_id = doc.get("id")
            try:
                chunks = await client.list_document_chunks(dataset_id, doc_id)
            except Exception as exc:  # noqa: BLE001 - 分块拉取失败降级空窗
                logger.warning("knowledge context: list chunks failed: %s", exc)
                chunks = []
            index = next(
                (i for i, c in enumerate(chunks) if c.get("chunk_id") == chunk_id), None
            )
            if index is not None:
                self_page = chunks[index].get("page")
                prev = [_chunk_ref(c) for c in chunks[max(0, index - n):index]]
                nxt = [_chunk_ref(c) for c in chunks[index + 1:index + 1 + n]]

    return StandardEnvelope.ok(
        {
            "doc_id": doc_id,
            "doc_name": item.get("doc_name") or "",
            "page": self_page,
            "self": {
                "chunk_id": chunk_id,
                "content": item.get("content") or "",
                "page": self_page,
                "score": item.get("score"),
            },
            "prev": prev,
            "next": nxt,
        },
        task_id=task_id,
    )


@router.get("/{task_id}/knowledge/{chunk_id}/source", response_model=StandardEnvelope[dict])
async def knowledge_source(request: Request, task_id: str, chunk_id: str) -> StandardEnvelope[dict]:
    """引用原文：落库内容兜底；RAGFlow 可查到时替换为全量块内容（落库有 2000 字截断）。"""
    svc = _svc(request)
    item = await _citation_item(svc, task_id, chunk_id)
    content = item.get("content") or ""
    page: int | None = None

    client = _ragflow_client(request)
    if client is not None:
        resolved = await _resolve_document(client, _dataset_ids(request), item)
        if resolved is not None:
            dataset_id, doc = resolved
            try:
                chunks = await client.list_document_chunks(dataset_id, doc.get("id"))
            except Exception as exc:  # noqa: BLE001
                logger.warning("knowledge source: list chunks failed: %s", exc)
                chunks = []
            hit = next((c for c in chunks if c.get("chunk_id") == chunk_id), None)
            if hit is not None and hit.get("content"):
                content = hit["content"]
                page = hit.get("page")

    return StandardEnvelope.ok(
        {"doc_name": item.get("doc_name") or "", "page": page, "content": content},
        task_id=task_id,
    )


@router.get("", response_model=StandardEnvelope[PageResult[TaskSummary]])
async def list_tasks(
    request: Request,
    scene: str | None = Query(default=None),
    status: TaskStatus | None = Query(default=None),
    limit: int = Query(default=20, le=100),
) -> StandardEnvelope[PageResult[TaskSummary]]:
    svc = _svc(request)
    records = await svc.list_tasks(scene=scene, status=status, limit=limit)
    items = [
        TaskSummary(
            task_id=r["task_id"],
            scene=r["scene"],
            mode=r["mode"],
            status=r["status"],
            created_at=r["created_at"],
            updated_at=r["updated_at"],
            parent_task_id=r.get("parent_task_id"),
            image_count=r.get("image_count", 0),
        )
        for r in records
    ]
    return StandardEnvelope.ok(
        PageResult(items=items, meta=PageMeta(total=len(items), page=1, page_size=limit))
    )


@router.get("/{task_id}/files/{name}")
async def download_file(request: Request, task_id: str, name: str, kind: str = "inputs"):
    """安全文件下载（kind 白名单 + 路径穿越防护，规范 §14）。"""
    if kind not in ALLOWED_KINDS:
        raise BusinessError(BizCode.INVALID_REQUEST, f"invalid kind: {kind}", http_status=400)
    container = request.app.state.container
    file_repo = container.file_repo
    try:
        path = file_repo.uri_to_path(f"/api/v1/tasks/{task_id}/files/{name}?kind={kind}")
    except ValueError as exc:
        raise BusinessError(BizCode.INVALID_REQUEST, f"invalid file uri: {exc}", http_status=400) from exc
    if not path.is_file():
        raise BusinessError(BizCode.TASK_NOT_FOUND, "file not found", http_status=404)
    media_type = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".md": "text/markdown",
        ".pdf": "application/pdf",
        ".csv": "text/csv",
        ".json": "application/json",
        ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    }.get(path.suffix, "application/octet-stream")
    return FileResponse(path, media_type=media_type, filename=path.name)


@router.post("/{task_id}/review", response_model=StandardEnvelope[dict])
async def review_task(request: Request, task_id: str, body: ReviewRequest) -> StandardEnvelope[dict]:
    """人工复核恢复（对 awaiting_review 的任务注入决定，HITL）。"""
    svc = _svc(request)
    await svc.review_task(
        task_id,
        action=body.action,
        opinion=body.opinion,
        override_grade=body.override_grade,
        defects=body.defects,
    )
    return StandardEnvelope.ok(
        {"task_id": task_id, "review": body.model_dump()},
        message=f"review {body.action} accepted",
        task_id=task_id,
    )


@router.post("/{task_id}/scene-confirm", response_model=StandardEnvelope[dict])
async def confirm_scene(request: Request, task_id: str, body: SceneConfirmRequest) -> StandardEnvelope[dict]:
    """场景人工确认恢复（对 awaiting_review + confirm_type=scene 的任务注入选择，HITL）。"""
    svc = _svc(request)
    await svc.confirm_scene(task_id, body.scene)
    return StandardEnvelope.ok(
        {"task_id": task_id, "scene": body.scene},
        message=f"scene {body.scene} confirmed",
        task_id=task_id,
    )


@router.post("/{task_id}/retry", response_model=StandardEnvelope[dict])
async def retry_task(request: Request, task_id: str, background: BackgroundTasks) -> StandardEnvelope[dict]:
    """失败任务重试：服务层原子重置为 pending 后，后台重跑完整流水线（同提交路径）。"""
    svc = _svc(request)
    prepared = await svc.retry_task(task_id)
    background.add_task(svc.run_task, task_id, prepared["state"])
    return StandardEnvelope.ok({"task_id": task_id}, message="task retried", task_id=task_id)


def _to_detail(data: dict[str, Any]) -> TaskDetail:
    report = data.get("report")
    return TaskDetail(
        task_id=data["task_id"],
        trace_id=data.get("trace_id"),
        input_uris=data.get("input_uris") or [],
        scene=data.get("scene"),
        mode=data.get("mode"),
        status=data.get("status"),
        capability_used=data.get("capability_used"),
        created_at=data["created_at"],
        updated_at=data.get("updated_at") or data["created_at"],
        finished_at=data.get("finished_at"),
        steps=[StepTraceItem.model_validate(s) for s in (data.get("steps") or [])],
        standardized=data.get("standardized") or [],
        class_definitions=data.get("class_definitions") or [],
        grade=data.get("grade"),
        knowledge=data.get("knowledge") or [],
        review=data.get("review"),
        report=ReportRef.model_validate(report) if report else None,
        error=data.get("error"),
        parent_task_id=data.get("parent_task_id"),
        children=data.get("children") or [],
    )