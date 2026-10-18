"""RAGFlow 系统状态 + 知识库检索——状态为只读探测（失败降级），检索为业务错误语义（RAG 不可用 503）。"""
from __future__ import annotations

import json
import mimetypes
import time
from datetime import datetime
from typing import Any, Literal
from urllib.parse import quote
from zoneinfo import ZoneInfo

import httpx
from fastapi import APIRouter, File, Request, Response, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator

from app.core.config import get_settings
from app.core.errors import BizCode
from app.core.handlers import BusinessError
from app.core.logger import get_logger
from app.core.response import StandardEnvelope
from app.knowledge.ragflow import RAGFlowClient
from app.knowledge.chat_config import ChatAssistantConfig

logger = get_logger("app.api.v1.ragflow")

router = APIRouter(prefix="/api/v1/ragflow", tags=["ragflow"])

_BEIJING = ZoneInfo("Asia/Shanghai")

_NOT_CONFIGURED = {
    "configured": False,
    "reachable": False,
    "doc_count": 0,
    "latest_doc_time": None,
    "version": None,
    "message": "ragflow datasets not configured",
}


def _fmt_day(ms_timestamp: int) -> str:
    # RAGFlow update_time 为 UTC 毫秒时间戳；「最近入库」按北京时间日历日展示
    return datetime.fromtimestamp(ms_timestamp / 1000, tz=_BEIJING).strftime("%Y-%m-%d")


@router.get("/status", response_model=StandardEnvelope[dict])
async def ragflow_status(request: Request) -> StandardEnvelope[dict]:
    """RAGFlow 可达性 + 配置数据集文档统计。一次 GET /datasets 调用拿全。"""
    c = request.app.state.container
    client = c.ragflow if c is not None else None
    dataset_ids = list(getattr(c, "ragflow_dataset_ids", []) or []) if c is not None else []
    if client is None or client.available is False or not dataset_ids:
        return StandardEnvelope.ok(_NOT_CONFIGURED)

    try:
        datasets = await client.list_datasets()
    except Exception as exc:  # noqa: BLE001 探测端点兜底：任何失败 → 不可达
        message = str(exc).splitlines()[0][:120]
        logger.warning("ragflow status probe failed: %s", message)
        return StandardEnvelope.ok(
            {"configured": True, "reachable": False, "doc_count": 0, "latest_doc_time": None, "version": None, "message": message}
        )

    matched = [d for d in datasets if d.get("id") in dataset_ids]
    doc_count = sum(int(d.get("document_count") or 0) for d in matched)
    latest = max((d.get("update_time") for d in matched if d.get("update_time")), default=None)
    return StandardEnvelope.ok(
        {
            "configured": True,
            "reachable": True,
            "doc_count": doc_count,
            "latest_doc_time": _fmt_day(latest) if latest is not None else None,
            # 服务端版本（客户端内缓存；旧版 RAGFlow 无此接口时为 None）
            "version": await client.version(),
            "message": "ok",
        }
    )


class SearchRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=500)
    mode: Literal["semantic", "hybrid", "keyword"] = "hybrid"
    top_k: int = Field(5, ge=1, le=20)


_METHOD_TO_RETRIEVAL = {"semantic": "vector", "hybrid": "hybrid", "keyword": "fulltext"}


def _chat_context(request: Request) -> tuple[Any, RAGFlowClient, str]:
    container = request.app.state.container
    client = container.ragflow
    chat_id = container.ragflow_chat_id
    if client is None or not client.available:
        raise BusinessError(BizCode.RAG_UNAVAILABLE, "知识库聊天服务未配置", http_status=503)
    if not chat_id:
        message = container.ragflow_chat_error or "RAGFlow chat 尚未初始化"
        raise BusinessError(BizCode.RAG_UNAVAILABLE, message, http_status=503)
    return container, client, chat_id


class ChatSessionRequest(BaseModel):
    name: str = Field("新会话", min_length=1, max_length=255)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        name = value.strip()
        if not name:
            raise ValueError("会话名称不能为空")
        return name


class ChatCompletionRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=8000)
    message_id: str = Field(..., min_length=1, max_length=128)


@router.get("/chat", response_model=StandardEnvelope[dict])
async def chat_info(request: Request) -> StandardEnvelope[dict]:
    container = request.app.state.container
    config = container.ragflow_chat_config
    return StandardEnvelope.ok({
        "ready": bool(container.ragflow is not None and container.ragflow_chat_id),
        "chat_id": container.ragflow_chat_id,
        "error": container.ragflow_chat_error,
        "config": config.model_dump(exclude_none=True) if config else None,
    })


@router.get("/chat/datasets", response_model=StandardEnvelope[list])
async def chat_datasets(request: Request) -> StandardEnvelope[list]:
    """返回配置页所需的数据集摘要，避免前端暴露或猜测内部 ID。"""
    container = request.app.state.container
    client = container.ragflow
    if client is None or not client.available:
        raise BusinessError(BizCode.RAG_UNAVAILABLE, "RAGFlow 未配置", http_status=503)
    try:
        datasets = await client.list_datasets()
    except (httpx.HTTPError, BusinessError) as exc:
        raise BusinessError(BizCode.RAG_UNAVAILABLE, "知识库列表加载失败", http_status=503) from exc

    fields = ("id", "name", "description", "document_count", "chunk_count", "update_time")
    return StandardEnvelope.ok([
        {field: row.get(field) for field in fields}
        for row in datasets
        if row.get("id") and row.get("name")
    ])


@router.put("/chat/config", response_model=StandardEnvelope[dict])
async def update_chat_config(
    request: Request, body: ChatAssistantConfig
) -> StandardEnvelope[dict]:
    container = request.app.state.container
    if container.ragflow is None:
        raise BusinessError(BizCode.RAG_UNAVAILABLE, "RAGFlow 未配置", http_status=503)
    try:
        chat = await container.sync_ragflow_chat(body, persist=True)
    except (httpx.HTTPError, OSError) as exc:
        logger.warning("ragflow chat config update failed: %s", exc)
        raise BusinessError(
            BizCode.RAG_UNAVAILABLE, "聊天配置更新失败，请检查 RAGFlow 服务", http_status=503
        ) from exc
    return StandardEnvelope.ok({
        "chat_id": container.ragflow_chat_id,
        "config": body.model_dump(exclude_none=True),
        "chat": chat,
    })


@router.get("/chat/sessions", response_model=StandardEnvelope[list])
async def list_chat_sessions(request: Request) -> StandardEnvelope[list]:
    _, client, chat_id = _chat_context(request)
    try:
        sessions = await client.list_chat_sessions(chat_id)
    except httpx.HTTPError as exc:
        raise BusinessError(BizCode.RAG_UNAVAILABLE, "会话列表加载失败", http_status=503) from exc
    return StandardEnvelope.ok(sessions)


@router.post("/chat/sessions", response_model=StandardEnvelope[dict])
async def create_chat_session(
    request: Request, body: ChatSessionRequest
) -> StandardEnvelope[dict]:
    _, client, chat_id = _chat_context(request)
    try:
        session = await client.create_chat_session(chat_id, body.name.strip())
    except httpx.HTTPError as exc:
        raise BusinessError(BizCode.RAG_UNAVAILABLE, "新建会话失败", http_status=503) from exc
    return StandardEnvelope.ok(session)


@router.get("/chat/sessions/{session_id}", response_model=StandardEnvelope[dict])
async def get_chat_session(request: Request, session_id: str) -> StandardEnvelope[dict]:
    _, client, chat_id = _chat_context(request)
    try:
        session = await client.get_chat_session(chat_id, session_id)
    except httpx.HTTPError as exc:
        raise BusinessError(BizCode.RAG_UNAVAILABLE, "会话加载失败", http_status=503) from exc
    return StandardEnvelope.ok(session)


@router.patch("/chat/sessions/{session_id}", response_model=StandardEnvelope[dict])
async def update_chat_session(
    request: Request, session_id: str, body: ChatSessionRequest
) -> StandardEnvelope[dict]:
    """重命名会话并持久化到 RAGFlow。"""
    _, client, chat_id = _chat_context(request)
    try:
        session = await client.update_chat_session(
            chat_id, session_id, name=body.name.strip()
        )
    except (httpx.HTTPError, BusinessError) as exc:
        raise BusinessError(
            BizCode.RAG_UNAVAILABLE, "会话名称保存失败，请重试", http_status=503
        ) from exc
    return StandardEnvelope.ok(session)


@router.delete("/chat/sessions/{session_id}", response_model=StandardEnvelope[bool])
async def delete_chat_session(request: Request, session_id: str) -> StandardEnvelope[bool]:
    _, client, chat_id = _chat_context(request)
    try:
        await client.delete_chat_session(chat_id, session_id)
    except httpx.HTTPError as exc:
        raise BusinessError(BizCode.RAG_UNAVAILABLE, "删除会话失败", http_status=503) from exc
    return StandardEnvelope.ok(True)


@router.delete(
    "/chat/sessions/{session_id}/messages/{message_id}",
    response_model=StandardEnvelope[dict],
)
async def delete_chat_message(
    request: Request, session_id: str, message_id: str
) -> StandardEnvelope[dict]:
    _, client, chat_id = _chat_context(request)
    try:
        session = await client.delete_chat_message(chat_id, session_id, message_id)
    except httpx.HTTPError as exc:
        raise BusinessError(BizCode.RAG_UNAVAILABLE, "消息撤回失败", http_status=503) from exc
    return StandardEnvelope.ok(session)


@router.post("/chat/sessions/{session_id}/completions")
async def stream_chat_completion(
    request: Request, session_id: str, body: ChatCompletionRequest
) -> StreamingResponse:
    _, client, chat_id = _chat_context(request)

    async def stream():
        try:
            async for chunk in client.stream_chat_completion(
                chat_id, session_id, body.question.strip(), body.message_id
            ):
                yield chunk
        except Exception as exc:  # 响应开始后不能再改 HTTP 状态，用 SSE 业务错误收口
            logger.warning("ragflow chat stream failed: %s", exc)
            payload = {"code": 4005, "message": "知识库对话中断，请重试", "data": False}
            yield f"data:{json.dumps(payload, ensure_ascii=False)}\n\n".encode()

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _fill_missing_document_ids(client: RAGFlowClient, hits: list[dict[str, Any]]) -> None:
    """RAGFlow 检索未返回 document_id 时，按 document_keyword(=doc_name) 反查补齐；查不到保持 None。"""
    by_dataset: dict[str, list[dict[str, Any]]] = {}
    for hit in hits:
        if not hit.get("document_id"):
            by_dataset.setdefault(hit["dataset_id"], []).append(hit)
    for ds_id, missing in by_dataset.items():
        docs = await client.list_documents(ds_id)
        name2id = {d.get("name"): d.get("id") for d in docs}
        for hit in missing:
            if hit.get("doc_name") in name2id:
                hit["document_id"] = name2id[hit["doc_name"]]


# 路由前缀已在 APIRouter(prefix="/api/v1/ragflow") 定义，此处不再重复
@router.post("/search", response_model=StandardEnvelope[dict])
async def search_knowledge(request: Request, body: SearchRequest) -> StandardEnvelope[dict]:
    started = time.monotonic()
    settings = get_settings()
    container = request.app.state.container
    dataset_ids = list(getattr(container, "ragflow_dataset_ids", []) or [])
    if not dataset_ids:
        raise BusinessError(
            BizCode.RAG_UNAVAILABLE,
            "RAGFlow 数据集未配置，请检查 config/rules/ragflow_chat.yaml 的 dataset_ids",
        )
    client: RAGFlowClient = container.ragflow
    if client is None or not client.available:  # RAGFLOW_API_KEY 未配置时容器置 None；与 status 端点同模式防御
        raise BusinessError(BizCode.RAG_UNAVAILABLE, "知识库服务未配置或不可用", http_status=503)
    try:
        retrieval_method = _METHOD_TO_RETRIEVAL[body.mode]
        hits: list[dict[str, Any]] = []
        for ds_id in dataset_ids:  # 逐 dataset 检索并打标，避免混查归属不明
            for hit in await client.retrieve(
                body.question, [ds_id], top_k=body.top_k, retrieval_method=retrieval_method
            ):
                hits.append({**hit, "dataset_id": ds_id})
        if hits:
            await _fill_missing_document_ids(client, hits)
    except httpx.HTTPError as exc:  # 连接/超时在 _client.get/post 即抛（不过 _check）→ 统一业务错误 503
        logger.warning("knowledge search failed (ragflow unreachable): %s", exc)
        raise BusinessError(BizCode.RAG_UNAVAILABLE, "知识库检索不可用，请稍后重试", http_status=503) from exc
    if settings.ragflow_rerank_model:
        try:
            hits = await client.rerank(body.question, hits, settings.ragflow_rerank_model)
        except Exception as exc:  # rerank 失败降级，保持检索序
            logger.warning("rerank failed, fallback to retrieval order: %s", exc)
        else:
            # rerank 的 rank 字段是相关性得分（值越高等），须降序
            hits.sort(key=lambda h: h.get("rank", 0), reverse=True)
    else:
        hits.sort(key=lambda h: h.get("score", 0.0), reverse=True)
    hits = hits[: body.top_k]
    return StandardEnvelope.ok({
        "query": body.question,
        "mode": body.mode,
        "elapsed_ms": int((time.monotonic() - started) * 1000),
        "hits": hits,
    })


# ---------------- 资料入库三端点：列文档 / 上传 / 触发解析 ----------------
# 前端「资料入库」页的数据面：写侧此前无路由（客户端已具备 upload/trigger/list 能力，此处补齐）。
# 防御模式与 search 一致：容器 None / available=False → 503；连接/超时经 httpx.HTTPError → 503。


class ParseRequest(BaseModel):
    document_ids: list[str] = Field(..., min_length=1)


@router.get("/datasets/{dataset_id}/documents", response_model=StandardEnvelope[list])
async def ingest_list_documents(
    request: Request, dataset_id: str
) -> StandardEnvelope[list]:
    client: RAGFlowClient = request.app.state.container.ragflow
    if client is None or not client.available:
        raise BusinessError(BizCode.RAG_UNAVAILABLE, "知识库服务未配置或不可用", http_status=503)
    try:
        docs = await client.list_all_documents(dataset_id)
    except (httpx.HTTPError, BusinessError) as exc:
        logger.warning("ingest list documents failed (ragflow unreachable): %s", exc)
        raise BusinessError(BizCode.RAG_UNAVAILABLE, "文档列表加载失败", http_status=503) from exc
    fields = ("id", "name", "size", "suffix", "run", "create_time", "update_time")
    return StandardEnvelope.ok([{f: d.get(f) for f in fields} for d in docs if d.get("id")])


@router.post("/datasets/{dataset_id}/documents", response_model=StandardEnvelope[list])
async def ingest_upload_documents(
    request: Request, dataset_id: str, files: list[UploadFile] = File(...)
) -> StandardEnvelope[list]:
    client: RAGFlowClient = request.app.state.container.ragflow
    if client is None or not client.available:
        raise BusinessError(BizCode.RAG_UNAVAILABLE, "知识库服务未配置或不可用", http_status=503)
    items: list[tuple[str, bytes]] = []
    for f in files:
        items.append((f.filename or "document", await f.read()))
    if not items:
        raise BusinessError(BizCode.INVALID_REQUEST, "未收到文件", http_status=400)
    try:
        uploaded = await client.upload_documents(dataset_id, items)
    except (httpx.HTTPError, BusinessError) as exc:
        logger.warning("ingest upload failed (ragflow unreachable): %s", exc)
        raise BusinessError(BizCode.RAG_UNAVAILABLE, "文档上传失败，请稍后重试", http_status=503) from exc
    fields = ("id", "name", "size", "suffix", "run", "create_time", "update_time")
    return StandardEnvelope.ok([{f: d.get(f) for f in fields} for d in uploaded if d.get("id")])


@router.post("/datasets/{dataset_id}/chunks", response_model=StandardEnvelope[bool])
async def ingest_trigger_parse(
    request: Request, dataset_id: str, body: ParseRequest
) -> StandardEnvelope[bool]:
    client: RAGFlowClient = request.app.state.container.ragflow
    if client is None or not client.available:
        raise BusinessError(BizCode.RAG_UNAVAILABLE, "知识库服务未配置或不可用", http_status=503)
    try:
        await client.trigger_chunks(dataset_id, body.document_ids)
    except (httpx.HTTPError, BusinessError) as exc:
        logger.warning("ingest parse trigger failed: %s", exc)
        raise BusinessError(BizCode.RAG_UNAVAILABLE, "解析触发失败，请稍后重试", http_status=503) from exc
    return StandardEnvelope.ok(True)


# ---------------- 预览三端点：文档 chunks / 图片代理 / 文档下载（Task 3） ----------------
# 防御模式与 search 端点一致：容器置 None（RAGFLOW_API_KEY 未配置）或 available=False 一律 503；
# 连接/超时异常（HTTPError 在 _client.get 即抛，不过 _check）按端点语义映射 503/404。


@router.get("/datasets/{dataset_id}/documents/{document_id}/chunks",
            response_model=StandardEnvelope[dict])
async def document_chunks(request: Request, dataset_id: str, document_id: str) -> StandardEnvelope[dict]:
    client: RAGFlowClient = request.app.state.container.ragflow
    if client is None or not client.available:
        raise BusinessError(BizCode.RAG_UNAVAILABLE, "知识库服务未配置或不可用", http_status=503)
    try:  # 存在性检查在 try 内：list_documents 连接异常同样归 503（try 内 raise 404 非 HTTPError，不吞）
        docs = await client.list_documents(dataset_id)
        doc = next((d for d in docs if d.get("id") == document_id), None)
        if doc is None:
            raise BusinessError(BizCode.INVALID_REQUEST, "document not found", http_status=404)
        chunks = await client.list_document_chunks(dataset_id, document_id)
    except httpx.HTTPError as exc:  # 连接/超时即抛 → 统一业务错误 503
        logger.warning("document chunks failed (ragflow unreachable): %s", exc)
        raise BusinessError(BizCode.RAG_UNAVAILABLE, "知识库原文不可用，请稍后重试", http_status=503) from exc
    clean = [{"chunk_id": c.get("chunk_id"), "content": c.get("content", ""),
              "image_id": c.get("image_id")} for c in chunks]
    return StandardEnvelope.ok({"document_id": document_id,
                                "doc_name": doc.get("name", ""), "chunks": clean})


@router.get("/images/{image_id}")
async def image_proxy(request: Request, image_id: str) -> Response:
    client: RAGFlowClient = request.app.state.container.ragflow
    if client is None or not client.available:
        raise BusinessError(BizCode.RAG_UNAVAILABLE, "知识库服务未配置或不可用", http_status=503)
    try:
        resp = await client.get_image(image_id)
    except httpx.HTTPError as exc:  # 连接/超时即抛 → 图片不可用按 404 占位（与图片双校验同一前端降级面）
        logger.warning("image proxy failed (ragflow unreachable): %s", exc)
        raise BusinessError(BizCode.INVALID_REQUEST, "image not found", http_status=404) from exc
    content_type = resp.headers.get("content-type", "")
    # 已知陷阱：不存在的 image_id 返回 200+JSON 业务体 → 状态码 + content-type 双校验
    if resp.status_code != 200 or not content_type.startswith("image/"):
        raise BusinessError(BizCode.INVALID_REQUEST, "image not found", http_status=404)
    return Response(content=resp.content, media_type=content_type)


@router.get("/datasets/{dataset_id}/documents/{document_id}/file")
async def download_document(request: Request, dataset_id: str, document_id: str) -> StreamingResponse:
    client: RAGFlowClient = request.app.state.container.ragflow
    if client is None or not client.available:
        raise BusinessError(BizCode.RAG_UNAVAILABLE, "知识库服务未配置或不可用", http_status=503)
    try:  # 存在性检查在 try 内：list_documents 连接异常同样归 503（try 内 raise 404/503 非 HTTPError，不吞）
        docs = await client.list_documents(dataset_id)
        doc = next((d for d in docs if d.get("id") == document_id), None)
        if doc is None:
            raise BusinessError(BizCode.INVALID_REQUEST, "document not found", http_status=404)
        resp = await client.download_document(document_id)
    except httpx.HTTPError as exc:  # 连接/超时即抛 → 统一业务错误 503
        logger.warning("document download failed (ragflow unreachable): %s", exc)
        raise BusinessError(BizCode.RAG_UNAVAILABLE, "知识库原文不可用，请稍后重试", http_status=503) from exc
    if resp.status_code != 200:
        raise BusinessError(BizCode.RAG_UNAVAILABLE, "RAGFlow 文档下载失败", http_status=503)
    doc_name = doc.get("name", "document.pdf")
    return StreamingResponse(
        resp.iter_bytes(),
        media_type=resp.headers.get("content-type", "application/octet-stream"),
        headers={"Content-Disposition": _content_disposition(doc_name)},
    )


@router.get("/documents/{document_id}/content")
async def preview_document(
    request: Request, document_id: str, name: str = "document.pdf"
) -> StreamingResponse:
    """引用原文内联代理；API Key 留在服务端，浏览器不直接访问 RAGFlow。"""
    client: RAGFlowClient = request.app.state.container.ragflow
    if client is None or not client.available:
        raise BusinessError(BizCode.RAG_UNAVAILABLE, "知识库服务未配置或不可用", http_status=503)
    try:
        resp = await client.download_document(document_id)
    except httpx.HTTPError as exc:
        raise BusinessError(BizCode.RAG_UNAVAILABLE, "知识库原文不可用", http_status=503) from exc
    if resp.status_code != 200:
        raise BusinessError(BizCode.INVALID_REQUEST, "document not found", http_status=404)
    content_type = resp.headers.get("content-type", "")
    if not content_type or content_type == "application/octet-stream":
        content_type = mimetypes.guess_type(name)[0] or "application/octet-stream"
    return StreamingResponse(
        resp.iter_bytes(),
        media_type=content_type,
        headers={
            "Content-Disposition": f"inline; filename*=UTF-8''{quote(name)}",
            "X-Content-Type-Options": "nosniff",
        },
    )


def _content_disposition(name: str) -> str:
    """RFC 5987：中文文件名用 filename*=UTF-8''percent-encoded。"""
    return f"attachment; filename*=UTF-8''{quote(name)}"
