"""GET /api/v1/ragflow/status：配置/可达/统计三态语义。

数据集单一事实源 = ragflow_chat.yaml 的 dataset_ids（容器字段），按 id 匹配统计。
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1 import ragflow as ragflow_module
from app.core.handlers import register_exception_handlers


class _FakeRagflowClient:
    available = True

    def __init__(self, rows=None, error: Exception | None = None, version: str | None = None) -> None:
        self._rows = rows or []
        self._error = error
        self._version = version

    async def list_datasets(self) -> list[dict]:
        if self._error:
            raise self._error
        return self._rows

    async def version(self) -> str | None:
        return self._version


class _Container:
    def __init__(self, ragflow, dataset_ids=("d1",)) -> None:  # noqa: ANN001
        self.ragflow = ragflow
        self.ragflow_dataset_ids = list(dataset_ids)


def _app(ragflow, dataset_ids=("d1",)) -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(ragflow_module.router)
    app.state.container = _Container(ragflow, dataset_ids)
    return app


def test_status_not_configured_when_no_dataset_ids():
    resp = TestClient(_app(None, dataset_ids=())).get("/api/v1/ragflow/status")

    body = resp.json()
    assert resp.status_code == 200
    assert body["code"] == 0
    assert body["result"] == {
        "configured": False,
        "reachable": False,
        "doc_count": 0,
        "latest_doc_time": None,
        "version": None,
        "message": "ragflow datasets not configured",
    }


def test_status_reports_stats_from_matched_datasets():
    client = _FakeRagflowClient(
        rows=[
            {"id": "d1", "name": "航发智能质检知识库", "document_count": 5, "update_time": 1787104597408},
            {"id": "d2", "name": "其他库", "document_count": 999, "update_time": 111},
        ],
        version="v0.17.2",
    )

    body = TestClient(_app(client, dataset_ids=("d1",))).get("/api/v1/ragflow/status").json()

    result = body["result"]
    assert result["reachable"] is True
    assert result["doc_count"] == 5  # 只统计配置的数据集（d1），不含 d2
    assert result["version"] == "v0.17.2"
    expected = datetime.fromtimestamp(1787104597.408, tz=timezone.utc).strftime("%Y-%m-%d")
    assert result["latest_doc_time"] == expected


def test_status_reports_unreachable_on_error():
    client = _FakeRagflowClient(error=ConnectionError("connection refused"))

    body = TestClient(_app(client)).get("/api/v1/ragflow/status").json()

    assert body["code"] == 0  # HTTP 层面不报错，状态放 result
    result = body["result"]
    assert result["reachable"] is False
    assert result["version"] is None
    assert "connection" in result["message"].lower()


def test_status_latest_doc_time_uses_beijing_date():
    """时区锁定（仲裁保留 +8 语义）：UTC 16:30 = 北京次日 00:30，「最近入库」按北京日历日展示。"""
    ts_ms = datetime(2026, 8, 18, 16, 30, tzinfo=timezone.utc).timestamp() * 1000
    client = _FakeRagflowClient(
        rows=[{"id": "d1", "name": "航发智能质检知识库", "document_count": 1, "update_time": ts_ms}]
    )

    result = TestClient(_app(client)).get("/api/v1/ragflow/status").json()["result"]

    assert result["latest_doc_time"] == "2026-08-19"  # 北京次日，而非 UTC 当日 08-18
