"""任务知识引用端点：GET /tasks/{id}/knowledge/{chunk_id}/context|source。

前端 KnowledgeCard「预览原文」依赖这两个端点（补接口清单落地）；
此前无路由落 SPA fallback 返回 HTML，前端解析失败报错。
context 需 RAGFlow 拉同文档前后块；RAGFlow 不可用/文档未命中时降级为
空前后块（self 仍来自任务落库引用），不阻塞预览。source 完全本地。
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.task import router as task_router
from app.core.handlers import register_exception_handlers
from app.services.container import AppContainer

CHUNKS_DOC = [
    {"chunk_id": "c1", "content": "第一段", "image_id": None, "page": 1},
    {"chunk_id": "c2", "content": "第二段（命中块）", "image_id": "img2", "page": 2},
    {"chunk_id": "c3", "content": "第三段", "image_id": None, "page": 3},
    {"chunk_id": "c4", "content": "第四段", "image_id": None, "page": 3},
]

TASK_KNOWLEDGE = [
    {
        "content": "第二段（命中块）",
        "score": 0.9,
        "doc_name": "手册A.pdf",
        "chunk_id": "c2",
        "image_uri": "/api/v1/ragflow/images/img2",
    }
]


class _FakeRagflow:
    available = True

    def __init__(self, chunks=None, docs=None, fail=False):
        self._chunks = CHUNKS_DOC if chunks is None else chunks
        self._docs = [{"id": "doc1", "name": "手册A.pdf"}] if docs is None else docs
        self._fail = fail

    async def list_documents(self, dataset_id):
        if self._fail:
            raise RuntimeError("ragflow down")
        return self._docs

    async def list_document_chunks(self, dataset_id, document_id, page_size=100):
        return self._chunks


class _FakeTaskService:
    def __init__(self, detail):
        self._detail = detail

    async def get(self, task_id):
        return self._detail


def _app(detail, ragflow):
    container = AppContainer()
    container.task_service = _FakeTaskService(detail)
    container.ragflow = ragflow
    # 数据集单一事实源 = ragflow_chat.yaml 的 dataset_ids，容器启动时载入；测试直接注入
    container.ragflow_dataset_ids = ["ds1"]
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(task_router)
    app.state.container = container
    return TestClient(app)


def _detail(knowledge=None):
    return {
        "task_id": "T1",
        "scene": "metallography",
        "mode": "single",
        "status": "completed",
        "created_at": "2026-08-21T00:00:00Z",
        "knowledge": TASK_KNOWLEDGE if knowledge is None else knowledge,
    }


def test_context_returns_prev_self_next():
    client = _app(_detail(), _FakeRagflow())
    resp = client.get("/api/v1/tasks/T1/knowledge/c2/context", params={"n": 1})
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0
    result = body["result"]
    assert result["doc_id"] == "doc1"
    assert result["doc_name"] == "手册A.pdf"
    assert result["self"] == {"chunk_id": "c2", "content": "第二段（命中块）", "page": 2, "score": 0.9}
    assert [c["chunk_id"] for c in result["prev"]] == ["c1"]
    assert [c["chunk_id"] for c in result["next"]] == ["c3"]
    assert result["prev"][0]["content"] == "第一段"


def test_context_radius_n_controls_window():
    client = _app(_detail(), _FakeRagflow())
    resp = client.get("/api/v1/tasks/T1/knowledge/c2/context", params={"n": 2})
    result = resp.json()["result"]
    assert [c["chunk_id"] for c in result["next"]] == ["c3", "c4"]


def test_context_task_not_found():
    client = _app(None, _FakeRagflow())
    resp = client.get("/api/v1/tasks/T404/knowledge/c2/context")
    assert resp.status_code == 404
    assert resp.json()["code"] == 4002


def test_context_chunk_not_in_task_knowledge():
    client = _app(_detail(), _FakeRagflow())
    resp = client.get("/api/v1/tasks/T1/knowledge/other-chunk/context")
    assert resp.status_code == 400
    assert resp.json()["code"] == 1001


def test_context_degrades_when_ragflow_unavailable():
    # RAGFlow 缺席：self 仍可读（落库引用），prev/next 空，不报错
    container_detail = _detail()
    client = _app(container_detail, None)
    resp = client.get("/api/v1/tasks/T1/knowledge/c2/context")
    assert resp.status_code == 200
    result = resp.json()["result"]
    assert result["self"]["chunk_id"] == "c2"
    assert result["prev"] == []
    assert result["next"] == []
    assert result["doc_id"] is None


def test_context_degrades_when_document_unresolved():
    # 文档名在数据集里查不到：降级空前后块
    client = _app(_detail(), _FakeRagflow(docs=[]))
    resp = client.get("/api/v1/tasks/T1/knowledge/c2/context")
    assert resp.status_code == 200
    result = resp.json()["result"]
    assert result["prev"] == [] and result["next"] == []


def test_source_returns_stored_citation_without_ragflow():
    # source 完全本地：RAGFlow 缺席也返回落库引用内容
    client = _app(_detail(), None)
    resp = client.get("/api/v1/tasks/T1/knowledge/c2/source")
    assert resp.status_code == 200
    result = resp.json()["result"]
    assert result == {"doc_name": "手册A.pdf", "page": None, "content": "第二段（命中块）"}


def test_source_prefers_full_chunk_content_from_ragflow():
    # 落库 content 有 2000 字截断；RAGFlow 可查到时取全量块内容
    full = "第二段（命中块）" + "补" * 3000
    chunks = [{"chunk_id": "c2", "content": full, "image_id": "img2", "page": 2}]
    client = _app(_detail(), _FakeRagflow(chunks=chunks))
    resp = client.get("/api/v1/tasks/T1/knowledge/c2/source")
    result = resp.json()["result"]
    assert result["content"] == full
    assert result["page"] == 2
