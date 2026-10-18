"""POST /api/v1/ragflow/search：检索三模式映射 + 聚合并集 + rerank 条件降级 + document_id 补齐。

沿用 test_ragflow_status.py 的 _FakeRagflowClient / _Container / _app 模式（本文件直接用
AppContainer 实例承接容器字段，与 spec 一致）；合成词结果 dict 按 spec Produces 契约。
"""
import httpx
import pytest
from fastapi.testclient import TestClient

from app.api.v1.ragflow import router as ragflow_router
from app.core.config import get_settings
from app.core.handlers import register_exception_handlers
from app.services.container import AppContainer


def _hit(chunk_id="c1", score=0.9, doc_name="手册A", document_id="d1", image_id="i1"):
    return {"chunk_id": chunk_id, "content": "前缘裂纹判级标准", "score": score,
            "doc_name": doc_name, "document_id": document_id,
            "dataset_id": None, "image_id": image_id}


def _app(ragflow, dataset_ids=("ds1",)):
    container = AppContainer()
    container.ragflow = ragflow  # 容器字段名以 app/services/container.py 为准
    # 数据集单一事实源 = ragflow_chat.yaml 的 dataset_ids，容器启动时载入；测试直接注入
    container.ragflow_dataset_ids = list(dataset_ids)
    # 重置上一测试可能设置的 rerank_model：get_settings() 是模块级单例，测试间共享实例；
    # 需启用 rerank 的用例在 _app() 之后再显式设置（见 rerank_enabled）。
    get_settings().ragflow_rerank_model = ""
    from fastapi import FastAPI
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(ragflow_router)
    app.state.container = container
    return TestClient(app)


class _FakeRagflowClient:
    def __init__(self, hits=None, docs=None, config=None):
        self.hits = hits or []
        self.docs = docs or [{"id": "d1", "name": "手册A"}]
        self.config = config or {}
        self.retrieve_calls = []

    @property
    def available(self):
        return True

    async def retrieve(self, question, dataset_ids, top_k=5, similarity_threshold=0.2,
                       retrieval_method="hybrid"):
        if self.config.get("retrieve_error"):
            raise self.config["retrieve_error"]  # 连接层异常在 _client.get/post 即抛，不过业务码检查
        self.retrieve_calls.append({"dataset_ids": dataset_ids, "method": retrieval_method, "top_k": top_k})
        return [dict(h, dataset_id=dataset_ids[0]) for h in self.hits]

    async def list_documents(self, dataset_id):
        return self.docs

    async def rerank(self, query, chunks, model):
        if self.config.get("rerank_fail"):
            raise RuntimeError("rerank boom")
        # rank 语义 = 相关性得分（值越高等），假实现给出与检索序不同的排序
        rank_map = {"c0": 2, "c1": 0, "c2": 1}
        return [dict(c, rank=rank_map.get(c.get("chunk_id", ""), 0)) for c in chunks]


def test_search_hybrid_default_mode_and_payload():
    client = _app(_FakeRagflowClient(hits=[_hit()]))
    resp = client.post("/api/v1/ragflow/search", json={"question": "孔探"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0
    assert body["result"]["mode"] == "hybrid"
    assert body["result"]["query"] == "孔探"
    assert body["result"]["elapsed_ms"] >= 0
    assert body["result"]["hits"] == [dict(_hit(), dataset_id="ds1")]
    assert client.app.state.container.ragflow.retrieve_calls == [
        {"dataset_ids": ["ds1"], "method": "hybrid", "top_k": 5}]


def test_search_three_modes_map_to_retrieval_method():
    for mode, method in (("semantic", "vector"), ("hybrid", "hybrid"), ("keyword", "fulltext")):
        client = _app(_FakeRagflowClient(hits=[_hit()]))
        resp = client.post("/api/v1/ragflow/search", json={"question": "q", "mode": mode})
        assert resp.status_code == 200
        got = client.app.state.container.ragflow.retrieve_calls
        assert got[-1]["method"] == method, f"mode {mode} -> {method}"
        assert got[-1]["top_k"] == 5


def test_search_top_k_passed_and_aggregation_truncates():
    hits = [_hit(chunk_id=f"c{i}", score=1.0 - i * 0.1) for i in range(6)]
    client = _app(_FakeRagflowClient(hits=hits))
    resp = client.post("/api/v1/ragflow/search", json={"question": "q", "top_k": 4})
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["result"]["hits"]) == 4
    assert client.app.state.container.ragflow.retrieve_calls[0]["top_k"] == 4


def test_search_rerank_enabled_orders_by_rank_keeps_score():
    hits = [_hit(chunk_id=f"c{i}", score=0.1 + i) for i in range(3)]  # 检索序 c0 c1 c2
    client = _app(_FakeRagflowClient(hits=hits, config={"rerank_model": "bge-reranker"}))
    get_settings().ragflow_rerank_model = "bge-reranker"
    resp = client.post("/api/v1/ragflow/search", json={"question": "q"})
    assert resp.status_code == 200
    body = resp.json()
    # rank 降序（c0=2, c2=1, c1=0）与检索序不同 → 证明 rerank 生效
    assert [h["chunk_id"] for h in body["result"]["hits"]] == ["c0", "c2", "c1"]
    assert [h["score"] for h in body["result"]["hits"]] == [0.1, 2.1, 1.1]  # score 保留原相似度
    assert [h["dataset_id"] for h in body["result"]["hits"]] == ["ds1"] * 3


def test_search_rerank_failure_degrades_to_original_order():
    hits = [_hit(chunk_id=f"c{i}", score=0.1 + i) for i in range(3)]
    client = _app(_FakeRagflowClient(hits=hits, config={"rerank_model": "m", "rerank_fail": True}))
    get_settings().ragflow_rerank_model = "m"
    resp = client.post("/api/v1/ragflow/search", json={"question": "q"})
    assert resp.status_code == 200
    body = resp.json()
    assert [h["chunk_id"] for h in body["result"]["hits"]] == ["c0", "c1", "c2"]  # 降级原序
    assert body["code"] == 0


def test_search_rerank_disabled_keeps_similarity_order():
    hits = [_hit(chunk_id=f"c{i}", score=0.1 + i) for i in range(3)]
    client = _app(_FakeRagflowClient(hits=hits, config={}))  # rerank_model 空
    resp = client.post("/api/v1/ragflow/search", json={"question": "q"})
    assert resp.status_code == 200
    assert [h["chunk_id"] for h in resp.json()["result"]["hits"]] == ["c2", "c1", "c0"]
    assert client.app.state.container.ragflow.retrieve_calls[0]["dataset_ids"] == ["ds1"]


def test_search_fills_missing_document_id_from_documents():
    hits = [_hit(document_id=None, doc_name="手册A")]
    client = _app(_FakeRagflowClient(hits=hits, docs=[{"id": "d1", "name": "手册A"}]))
    resp = client.post("/api/v1/ragflow/search", json={"question": "q"})
    assert resp.status_code == 200
    assert resp.json()["result"]["hits"][0]["document_id"] == "d1"


def test_search_datasets_not_configured_returns_503():
    client = _app(_FakeRagflowClient(hits=[]), dataset_ids=())
    resp = client.post("/api/v1/ragflow/search", json={"question": "q"})
    assert resp.status_code == 503
    assert resp.json()["code"] == 4005


def test_search_returns_503_when_client_not_configured():
    client = _app(None)  # RAGFLOW_API_KEY 未配置时 AppContainer 置 ragflow=None
    resp = client.post("/api/v1/ragflow/search", json={"question": "q"})
    assert resp.status_code == 503
    assert resp.json()["code"] == 4005  # BizCode.RAG_UNAVAILABLE


def test_search_returns_503_on_connection_error():
    client = _app(_FakeRagflowClient(hits=[_hit()], config={"retrieve_error": httpx.ConnectError("boom")}))
    resp = client.post("/api/v1/ragflow/search", json={"question": "q"})
    assert resp.status_code == 503
    assert resp.json()["code"] == 4005


def test_search_empty_hits_returns_empty_array():
    client = _app(_FakeRagflowClient(hits=[]))
    resp = client.post("/api/v1/ragflow/search", json={"question": "不存在的东西"})
    assert resp.status_code == 200
    assert resp.json()["result"]["hits"] == []


def test_search_validates_question_and_mode():
    client = _app(_FakeRagflowClient())
    # 项目全局 handler 将校验错误映射为 HTTP 400 + code 1001（见 test_handlers.py），非 FastAPI 默认 422
    assert client.post("/api/v1/ragflow/search", json={"question": ""}).status_code == 400
    assert client.post("/api/v1/ragflow/search", json={"question": "q", "mode": "nope"}).status_code == 400
    assert client.post("/api/v1/ragflow/search", json={"question": "q", "top_k": 0}).status_code == 400