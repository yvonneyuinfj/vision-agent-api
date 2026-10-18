"""知识库预览三端点：文档 chunks / 图片代理双校验 / 文档下载二进制透传。

沿用 test_ragflow_search.py 的 _FakeRagflowClient / _app 模式；为覆盖二进制形态，
stub 返回类 httpx.Response 的 _Raw（补 async iter_bytes 供 StreamingResponse 消费）。
"""
import httpx
from fastapi.testclient import TestClient

from app.api.v1.ragflow import router as ragflow_router
from app.core.handlers import register_exception_handlers
from app.services.container import AppContainer


def _app(ragflow):
    container = AppContainer()
    container.ragflow = ragflow  # 容器字段名以 app/services/container.py 为准
    container.ragflow_dataset_ids = ["ds1"]  # 预览端点按 URL 直取数据集，此字段仅容器完整性
    from fastapi import FastAPI
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(ragflow_router)
    app.state.container = container
    return TestClient(app)


class _FakeRagflowClient:
    def __init__(self, docs=None, chunks=None, download=None, image=None, docs_error=None):
        self.docs = docs or [{"id": "doc1", "name": "孔探手册.pdf"}]
        self.chunks = chunks or [{"chunk_id": "c1", "content": "正文", "image_id": "i1"}]
        self.download = download or (200, b"\x25\x50\x44\x46", {"content-type": "application/pdf"})
        self.image = image  # None 表示 stub 方法未覆盖（走默认陷阱形态）
        self.docs_error = docs_error  # 非 None 时 list_documents 抛此异常（连接层故障模拟）
        self.download_urls = []

    @property
    def available(self):
        return True

    async def list_documents(self, dataset_id):
        if self.docs_error is not None:
            raise self.docs_error
        return self.docs

    async def list_document_chunks(self, dataset_id, document_id):
        return self.chunks

    async def download_document(self, document_id):
        self.download_urls.append(document_id)
        status, content, headers = self.download
        return _Raw(status, content, headers)

    async def get_image(self, image_id):
        if self.image is None:
            return _Raw(200, b'{"code":13,"message":"not exist"}', {"content-type": "application/json"})
        if isinstance(self.image, BaseException):
            raise self.image
        status, content, headers = self.image
        return _Raw(status, content, headers)


class _Raw:
    def __init__(self, status_code, content, headers):
        self.status_code = status_code
        self.content = content
        self.headers = headers

    async def iter_bytes(self):
        yield self.content


def test_chunks_returns_mapped_list():
    client = _app(_FakeRagflowClient())
    resp = client.get("/api/v1/ragflow/datasets/ds1/documents/doc1/chunks")
    assert resp.status_code == 200
    assert resp.json()["result"] == {
        "document_id": "doc1", "doc_name": "孔探手册.pdf",
        "chunks": [{"chunk_id": "c1", "content": "正文", "image_id": "i1"}]}


def test_chunks_document_missing_returns_404():
    client = _app(_FakeRagflowClient(docs=[{"id": "other", "name": "x"}]))
    resp = client.get("/api/v1/ragflow/datasets/ds1/documents/nope/chunks")
    assert resp.status_code == 404
    assert resp.json()["code"] != 0


def test_image_ok_passthrough_with_content_type():
    client = _app(_FakeRagflowClient(image=(200, b"\x89PNG\r\n", {"content-type": "image/png"})))
    resp = client.get("/api/v1/ragflow/images/i1")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/png"
    assert resp.content == b"\x89PNG\r\n"


def test_image_trap_200_json_body_returns_404():
    """不存在的 image_id 返回 200+JSON 业务体 → 双校验必须拦截为 404。"""
    client = _app(_FakeRagflowClient())  # image=None → 200+JSON 陷阱形态
    resp = client.get("/api/v1/ragflow/images/nope")
    assert resp.status_code == 404


def test_image_http_error_passthrough_404():
    client = _app(_FakeRagflowClient(image=(500, b"boom", {"content-type": "text/plain"})))
    resp = client.get("/api/v1/ragflow/images/i1")
    assert resp.status_code == 404


def test_download_streams_binary_with_filename_header():
    client = _app(_FakeRagflowClient())
    resp = client.get("/api/v1/ragflow/datasets/ds1/documents/doc1/file")
    assert resp.status_code == 200
    assert resp.content == b"\x25\x50\x44\x46"
    assert resp.headers["content-disposition"].startswith("attachment;")
    assert "filename*=UTF-8''" in resp.headers["content-disposition"]
    assert "%E5%AD%94" in resp.headers["content-disposition"]  # "孔" 的 UTF-8 百分号编码


def test_download_document_missing_returns_404():
    client = _app(_FakeRagflowClient(docs=[{"id": "other", "name": "x"}]))
    resp = client.get("/api/v1/ragflow/datasets/ds1/documents/nope/file")
    assert resp.status_code == 404


def test_download_ragflow_error_returns_503():
    client = _app(_FakeRagflowClient(download=(500, b"{}", {"content-type": "application/json"})))
    resp = client.get("/api/v1/ragflow/datasets/ds1/documents/doc1/file")
    assert resp.status_code == 503
    assert resp.json()["code"] == 4005  # BizCode.RAG_UNAVAILABLE


def test_documents_list_error_returns_503_on_chunks_and_download():
    """存在性检查的 list_documents 在 try 内：连接异常 → 503（否则落 500+4001）。"""
    fake = _FakeRagflowClient(docs_error=httpx.ConnectError("boom"))
    for path in (
        "/api/v1/ragflow/datasets/ds1/documents/doc1/chunks",
        "/api/v1/ragflow/datasets/ds1/documents/doc1/file",
    ):
        resp = _app(fake).get(path)
        assert resp.status_code == 503
        assert resp.json()["code"] == 4005  # BizCode.RAG_UNAVAILABLE


# ---- 防御模式防回归（fix round 约定：客户端未配置/不可达同样按状态语义处理） ----
def test_chunks_503_when_client_not_configured():
    client = _app(None)  # RAGFLOW_API_KEY 未配置时 AppContainer 置 ragflow=None
    resp = client.get("/api/v1/ragflow/datasets/ds1/documents/doc1/chunks")
    assert resp.status_code == 503
    assert resp.json()["code"] == 4005  # BizCode.RAG_UNAVAILABLE


def test_image_returns_404_on_connection_error():
    client = _app(_FakeRagflowClient(image=httpx.ConnectError("boom")))
    resp = client.get("/api/v1/ragflow/images/i1")
    assert resp.status_code == 404