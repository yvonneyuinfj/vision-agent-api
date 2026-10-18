"""报告查询路由测试（a.md §13 api/v1/report.py）。

路由经 request.app.state.container 取 repo（不走 Depends），测试构造容器 stub 挂到 app.state。
注意：外壳字段为 result（StandardEnvelope），非 data。
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.v1.report import router
from app.core.errors import BizCode
from app.core.handlers import register_exception_handlers


class _FakeTaskRepo:
    def __init__(self, row) -> None:  # noqa: ANN001
        self._row = row

    async def get(self, task_id: str):  # noqa: ANN001
        return self._row


class _FakeFileRepo:
    def __init__(self, content: str = "", dir: Path | None = None) -> None:
        self._content = content
        self._dir = dir

    def uri_to_path(self, uri: str) -> Path:
        # 成功用例：真实落盘文件（report_service 走 path.exists()/read_text）
        if self._content:
            assert self._dir is not None  # tmp_path 夹具注入，不写系统 /tmp 残留
            path = self._dir / Path(uri).name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(self._content, encoding="utf-8")
            return path
        return Path("/nonexistent")


def _app(task_repo: _FakeTaskRepo, file_repo: _FakeFileRepo) -> FastAPI:
    class _Container:
        def __init__(self, task_repo, file_repo) -> None:  # noqa: ANN001
            self.task_repo = task_repo
            self.file_repo = file_repo

    app = FastAPI()
    register_exception_handlers(app)  # 与 create_app 同构：BusinessError -> 外壳
    app.include_router(router)
    app.state.container = _Container(task_repo, file_repo)
    return app


def _ok_row() -> SimpleNamespace:
    return SimpleNamespace(
        id="T1",
        status="completed",
        report_json=(
            '{"name": "T1_report.md", "uri": "reports/T1.md",'
            ' "created_at": "2026-08-18T10:00:00"}'
        ),
    )


@pytest.mark.asyncio
async def test_get_report_success(tmp_path: Path):
    # Arrange
    app = _app(
        _FakeTaskRepo(_ok_row()),
        _FakeFileRepo(content="# 智能质检报告 T1\n", dir=tmp_path),
    )

    # Act
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/reports/T1")

    # Assert
    assert resp.status_code == 200
    payload = resp.json()["result"]
    assert payload["meta"]["task_id"] == "T1"
    assert payload["content"].startswith("# 智能质检报告")


@pytest.mark.asyncio
async def test_get_report_task_not_found():
    app = _app(_FakeTaskRepo(None), _FakeFileRepo())
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/reports/T404")
    assert resp.status_code == 404
    assert resp.json()["code"] == int(BizCode.TASK_NOT_FOUND)


@pytest.mark.asyncio
async def test_get_report_not_generated():
    app = _app(
        _FakeTaskRepo(SimpleNamespace(id="T2", status="pending", report_json=None)),
        _FakeFileRepo(),
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/reports/T2")
    assert resp.status_code == 404
    assert resp.json()["code"] == int(BizCode.TASK_NOT_FOUND)
