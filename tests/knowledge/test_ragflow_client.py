"""RAGFlowClient：数据集列表（系统状态探测的数据源）+ 检索/分块/下载/图片（Task 1 扩展）。"""
from __future__ import annotations

import asyncio

import pytest

from app.core.errors import BizCode, BusinessError
from app.knowledge.ragflow import RAGFlowClient


class _Response:
    def __init__(self, status_code: int, body: dict) -> None:
        self.status_code = status_code
        self._body = body

    def json(self):
        return self._body


class _FakeClient:
    def __init__(self, responses: list[_Response]) -> None:
        self._responses = list(responses)
        self.calls: list[tuple[str, str]] = []

    async def get(self, url: str, params=None) -> _Response:  # noqa: ANN001
        self.calls.append(("get", url))
        return self._responses.pop(0)

    async def aclose(self) -> None:
        return None


def _client(fake: _FakeClient) -> RAGFlowClient:
    c = RAGFlowClient.__new__(RAGFlowClient)
    c._base = "http://ragflow:9380"
    c._headers = {"Authorization": "Bearer k"}
    c._client = fake
    return c


async def test_list_datasets_returns_dataset_rows():
    fake = _FakeClient(
        [
            _Response(
                200,
                {
                    "code": 0,
                    "data": [{"id": "d1", "name": "航发智能质检知识库", "document_count": 5, "update_time": 1787104597408}],
                },
            )
        ]
    )

    rows = await _client(fake).list_datasets()

    assert fake.calls == [("get", "/api/v1/datasets")]
    assert rows[0]["name"] == "航发智能质检知识库"
    assert rows[0]["document_count"] == 5


async def test_list_datasets_raises_biz_error_on_bad_code():
    fake = _FakeClient([_Response(200, {"code": 100, "message": "MethodNotAllowed '405'"})])

    with pytest.raises(BusinessError) as exc_info:
        await _client(fake).list_datasets()

    assert exc_info.value.code == BizCode.RAG_UNAVAILABLE


async def test_update_chat_session_uses_ragflow_patch_contract():
    fake = _FakeClient([])
    calls = []

    async def patch(url, json=None):  # noqa: ANN001
        calls.append((url, json))
        return _Response(200, {"code": 0, "data": {"id": "s1", "name": json["name"]}})

    fake.patch = patch
    session = await _client(fake).update_chat_session(
        "chat1", "s1", name="GH738 疲劳研究"
    )

    assert calls == [
        ("/api/v1/chats/chat1/sessions/s1", {"name": "GH738 疲劳研究"})
    ]
    assert session["name"] == "GH738 疲劳研究"


def _post_response(responses, calls, body):
    async def post(url, json=None):
        calls.append((url, json))
        return _Response(200, body)
    fake = _FakeClient(responses)
    fake.post = post
    fake.get_calls = calls
    return fake


def test_retrieve_semantic_mode_sets_vector_weight():
    calls = []
    body = {"code": 0, "data": {"chunks": [
        {"chunk_id": "c1", "content": "前缘裂纹", "similarity": 0.9,
         "document_keyword": "手册A", "document_id": "d1", "image_id": "i1", "page": 7}]}}
    fake = _post_response({}, calls, body)
    c = _client(fake)
    result = asyncio.run(c.retrieve("q", ["ds1"], retrieval_method="vector"))
    assert calls == [("/api/v1/retrieval",
                      {"question": "q", "dataset_ids": ["ds1"], "page": 1, "page_size": 30,
                       "similarity_threshold": 0.2, "retrieval_method": "vector",
                       "vector_similarity_weight": 0.3})]
    assert result == [{"content": "前缘裂纹", "score": 0.9, "doc_name": "手册A",
                       "chunk_id": "c1", "image_id": "i1", "document_id": "d1", "page": 7}]


def test_retrieve_passes_page_through_when_present():
    """P7a：retrieval 返回的 page 字段透传到 chunk（报告引用溯源到页码）。"""
    body = {"code": 0, "data": {"chunks": [
        {"chunk_id": "c1", "content": "正文", "similarity": 0.8, "page": 3},
        {"chunk_id": "c2", "content": "无页码", "similarity": 0.7},
    ]}}
    fake = _post_response({}, [], body)
    c = _client(fake)
    result = asyncio.run(c.retrieve("q", ["ds1"]))
    assert result[0]["page"] == 3
    assert result[1]["page"] is None


def test_retrieve_fulltext_mode_sets_keyword_weight():
    calls = []
    fake = _post_response({}, calls, {"code": 0, "data": {"chunks": []}})
    c = _client(fake)
    asyncio.run(c.retrieve("q", ["ds1"], retrieval_method="fulltext"))
    assert calls[0][1]["retrieval_method"] == "fulltext"
    assert calls[0][1].get("keyword_similarity_weight") == 0.3
    assert "vector_similarity_weight" not in calls[0][1]


def test_retrieve_hybrid_default_no_weights():
    calls = []
    fake = _post_response({}, calls, {"code": 0, "data": {"chunks": []}})
    c = _client(fake)
    asyncio.run(c.retrieve("q", ["ds1"]))  # 默认 hybrid
    payload = calls[0][1]
    assert payload["retrieval_method"] == "hybrid"
    assert "vector_similarity_weight" not in payload
    assert "keyword_similarity_weight" not in payload


def test_retrieve_truncates_to_top_k_and_passes_document_id_none():
    chunks = [{"chunk_id": f"c{i}", "content": f"x{i}", "similarity": float(i),
               "document_keyword": "手册"} for i in range(5)]
    fake = _post_response({}, [], {"code": 0, "data": {"chunks": chunks}})
    c = _client(fake)
    result = asyncio.run(c.retrieve("q", ["ds1"], top_k=3))
    assert len(result) == 3
    assert result[0]["document_id"] is None  # 缺字段透传 None
    assert result[0]["score"] == 4.0


def test_retrieve_falls_back_to_id_field_when_chunk_id_missing():
    """契约实测（2026-08-19）：retrieval 返回的 chunk 标识字段为 id 而非 chunk_id → 双兼容归一化。"""
    fake = _post_response({}, [], {"code": 0, "data": {"chunks": [
        {"id": "c1", "content": "前缘裂纹", "similarity": 0.8, "document_keyword": "手册A", "document_id": "d1"}]}})
    c = _client(fake)
    result = asyncio.run(c.retrieve("q", ["ds1"]))
    assert result[0]["chunk_id"] == "c1"


def test_list_document_chunks_accepts_dict_or_list_shape():
    for data in ({"code": 0, "data": {"chunks": [{"id": "c1", "content": "正文", "image_id": "i1", "page": 3}]}},
                 {"code": 0, "data": [{"chunk_id": "c1", "content": "正文", "image_id": "i1", "page": 3}]}):
        fake = _FakeClient([])
        async def get(url, params=None):  # noqa: ANN001
            return _Response(200, data)
        fake.get = get
        c = _client(fake)
        chunks = asyncio.run(c.list_document_chunks("ds1", "doc1"))
        # page 透传：引用原文预览需要展示命中块所在页
        assert chunks == [{"chunk_id": "c1", "content": "正文", "image_id": "i1", "page": 3}]


def test_list_document_chunks_paginates_past_default_30():
    """契约实测（2026-08-19）：RAGFlow list-chunks 服务端默认 page_size=30 且上限 100，
    GH738 文档 54 chunks 只回前 30，第 1 页外的命中段取不到 → 显式 page_size + 满页续翻。"""
    fake = _FakeClient([])
    seen = []
    page1 = [{"id": f"p1c{i}", "content": f"x{i}", "image_id": None} for i in range(100)]  # 满页 → 触发续翻
    page2 = [{"id": f"p2c{i}", "content": f"y{i}", "image_id": None} for i in range(24)]

    async def get(url, params=None):
        seen.append((url, dict(params or {})))
        return _Response(200, {"code": 0, "data": {"chunks": page1 if len(seen) == 1 else page2}})

    fake.get = get
    chunks = asyncio.run(_client(fake).list_document_chunks("ds1", "doc1"))
    assert len(chunks) == 124
    assert [u for u, _ in seen] == [
        "/api/v1/datasets/ds1/documents/doc1/chunks",
        "/api/v1/datasets/ds1/documents/doc1/chunks",
    ]
    assert seen[0][1] == {"page": 1, "page_size": 100}
    assert seen[1][1] == {"page": 2, "page_size": 100}
    assert chunks[0]["chunk_id"] == "p1c0" and chunks[-1]["chunk_id"] == "p2c23"


def test_list_documents_accepts_dict_data_shape():
    """契约实测（2026-08-19）：datasets/{id}/documents 的 data 为 {docs, total} dict 而非数组。"""
    fake = _FakeClient({})
    calls = []
    async def get(url, **kwargs):
        calls.append(url)
        return _Response(200, {"code": 0, "data": {"docs": [{"id": "d1", "name": "手册A"}], "total": 1}})
    fake.get = get
    c = _client(fake)
    docs = asyncio.run(c.list_documents("ds1"))
    assert docs == [{"id": "d1", "name": "手册A"}]


def test_download_document_and_get_image_pass_through_raw():
    class RawResp:
        def __init__(self, status, content, headers):
            self.status_code = status; self.content = content; self.headers = headers
    fake = _FakeClient({})
    hits = []
    async def get(url, **kwargs):
        hits.append(url)
        return RawResp(200, b"\x25\x50\x44\x46", {"content-type": "application/pdf"})
    fake.get = get
    c = _client(fake)
    doc = asyncio.run(c.download_document("doc1"))
    img = asyncio.run(c.get_image("i1"))
    assert hits == ["/api/v1/documents/doc1",
                    "/api/v1/documents/images/i1"]
    assert doc.content[:4] == b"\x25\x50\x44\x46"
    assert img.status_code == 200
def test_image_uri_points_to_platform_proxy_route():
    # 前端经平台代理取图：URI 必须命中 app/api/v1/ragflow.py 的 /images/{image_id} 路由，
    # 旧前缀 /api/kb/images/ 无路由，会落 SPA fallback 返回 HTML（预览原文报错根因）
    c = _client(_FakeClient({}))
    assert c.image_uri("abc123") == "/api/v1/ragflow/images/abc123"


def test_client_ignores_shell_proxy_env(monkeypatch):
    """RAGFlow 是本机/内网服务：不得被 shell 的 HTTP(S)_PROXY 劫持。

    回归场景（2026-09-16）：宿主 shell 设了代理，httpx 默认 trust_env=True
    把 localhost:9380 也送进代理，代理不识别 localhost → 启动期
    sync_ragflow_chat 抛 httpx.ConnectError、chat 功能降级。
    """
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("ALL_PROXY", "http://127.0.0.1:1")

    c = RAGFlowClient("http://localhost:9380", "k")

    assert c._client._trust_env is False
    assert c._client._mounts == {}  # 无代理挂载点

    asyncio.run(c.aclose())
