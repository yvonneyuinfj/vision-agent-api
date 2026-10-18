"""RAGFlow Chat 代理：会话管理、消息重发删除与 SSE 透传。"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.ragflow import router
from app.core.handlers import register_exception_handlers
from app.services.container import AppContainer


class FakeChatClient:
    available = True

    def __init__(self) -> None:
        self.calls = []

    async def list_datasets(self):
        self.calls.append(("list-datasets",))
        return [
            {
                "id": "ds-1",
                "name": "航发判级标准",
                "description": "现行判级规范与案例",
                "document_count": 12,
                "chunk_count": 318,
                "update_time": 1787104597408,
                "tenant_id": "must-not-leak",
            }
        ]

    async def list_chat_sessions(self, chat_id):
        self.calls.append(("list", chat_id))
        return [{"id": "s1", "chat_id": chat_id, "name": "会话", "messages": []}]

    async def create_chat_session(self, chat_id, name):
        self.calls.append(("create", chat_id, name))
        return {"id": "s2", "chat_id": chat_id, "name": name, "messages": []}

    async def get_chat_session(self, chat_id, session_id):
        self.calls.append(("get", chat_id, session_id))
        return {"id": session_id, "chat_id": chat_id, "name": "会话", "messages": []}

    async def update_chat_session(self, chat_id, session_id, *, name):
        self.calls.append(("update", chat_id, session_id, name))
        return {"id": session_id, "chat_id": chat_id, "name": name, "messages": []}

    async def delete_chat_session(self, chat_id, session_id):
        self.calls.append(("delete-session", chat_id, session_id))

    async def delete_chat_message(self, chat_id, session_id, message_id):
        self.calls.append(("delete-message", chat_id, session_id, message_id))
        return {"id": session_id, "messages": []}

    async def stream_chat_completion(self, chat_id, session_id, question, message_id):
        self.calls.append(("stream", chat_id, session_id, question, message_id))
        yield b'data:{"code":0,"data":{"answer":"hello","reference":{"chunks":[]}}}\n\n'
        yield b'data:{"code":0,"data":true}\n\n'


def make_app(client=None, chat_id="chat-1") -> TestClient:
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(router)
    container = AppContainer()
    container.ragflow = client
    container.ragflow_chat_id = chat_id
    container.ragflow_chat_error = None
    app.state.container = container
    return TestClient(app)


def test_chat_session_crud_proxy():
    fake = FakeChatClient()
    client = make_app(fake)
    assert client.get("/api/v1/ragflow/chat/sessions").json()["result"][0]["id"] == "s1"
    assert client.post("/api/v1/ragflow/chat/sessions", json={"name": "标准查询"}).json()["result"]["id"] == "s2"
    assert client.get("/api/v1/ragflow/chat/sessions/s1").status_code == 200
    renamed = client.patch("/api/v1/ragflow/chat/sessions/s1", json={"name": "GH738 疲劳研究"})
    assert renamed.json()["result"]["name"] == "GH738 疲劳研究"
    assert client.delete("/api/v1/ragflow/chat/sessions/s1").json()["result"] is True
    assert client.delete("/api/v1/ragflow/chat/sessions/s1/messages/m1").status_code == 200
    assert ("delete-message", "chat-1", "s1", "m1") in fake.calls
    assert ("update", "chat-1", "s1", "GH738 疲劳研究") in fake.calls


def test_chat_session_rename_validates_name_and_hides_upstream_error():
    fake = FakeChatClient()
    client = make_app(fake)
    assert client.patch("/api/v1/ragflow/chat/sessions/s1", json={"name": "   "}).status_code == 400

    class FailingUpdateClient(FakeChatClient):
        async def update_chat_session(self, chat_id, session_id, *, name):
            from app.core.errors import BizCode, BusinessError
            raise BusinessError(BizCode.RAG_UNAVAILABLE, "upstream internals")

    response = make_app(FailingUpdateClient()).patch(
        "/api/v1/ragflow/chat/sessions/s1", json={"name": "新名称"}
    )
    assert response.status_code == 503
    assert response.json()["message"] == "会话名称保存失败，请重试"


def test_chat_datasets_returns_human_readable_summaries_only():
    fake = FakeChatClient()
    result = make_app(fake).get("/api/v1/ragflow/chat/datasets").json()["result"]
    assert result == [{
        "id": "ds-1",
        "name": "航发判级标准",
        "description": "现行判级规范与案例",
        "document_count": 12,
        "chunk_count": 318,
        "update_time": 1787104597408,
    }]
    assert ("list-datasets",) in fake.calls


def test_chat_datasets_returns_actionable_error_when_ragflow_fails():
    class FailingDatasetClient(FakeChatClient):
        async def list_datasets(self):
            from app.core.errors import BizCode, BusinessError
            raise BusinessError(BizCode.RAG_UNAVAILABLE, "upstream internals")

    response = make_app(FailingDatasetClient()).get("/api/v1/ragflow/chat/datasets")
    assert response.status_code == 503
    assert response.json()["message"] == "知识库列表加载失败"


def test_chat_completion_preserves_native_sse_reference_stream():
    fake = FakeChatClient()
    client = make_app(fake)
    response = client.post(
        "/api/v1/ragflow/chat/sessions/s1/completions",
        json={"question": "前缘裂纹如何判级？", "message_id": "m1"},
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert '"answer":"hello"' in response.text
    assert '"reference":{"chunks":[]}' in response.text
    assert ("stream", "chat-1", "s1", "前缘裂纹如何判级？", "m1") in fake.calls


def test_chat_routes_return_503_when_startup_chat_is_not_ready():
    response = make_app(None, chat_id=None).get("/api/v1/ragflow/chat/sessions")
    assert response.status_code == 503
    assert response.json()["code"] == 4005


def test_chat_completion_validates_empty_question_and_message_id():
    client = make_app(FakeChatClient())
    path = "/api/v1/ragflow/chat/sessions/s1/completions"
    assert client.post(path, json={"question": "", "message_id": "m1"}).status_code == 400
    assert client.post(path, json={"question": "q", "message_id": ""}).status_code == 400
