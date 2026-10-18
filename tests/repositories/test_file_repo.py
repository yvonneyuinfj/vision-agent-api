"""FileRepo 回归：业务 URI 校验与安全读取。

- 畸形 URI（段数不足/形态不符）抛 ValueError（映射 400），而非 IndexError（500）
- 读取侧同样执行扩展名白名单（写入侧已有）
- 下载接口对非法 URI 返回规范外壳 400，而非未分类 500
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.core.errors import BizCode
from app.core.handlers import register_exception_handlers
from app.repositories.file_repo import FileRepo


@pytest.fixture
def repo(tmp_path):
    return FileRepo(root=str(tmp_path))


def test_uri_roundtrip(repo):
    uri = repo.uri_for("T1", "probe.jpg", "inputs")
    path = repo.uri_to_path(uri)
    assert path.parts[-3:] == ("inputs", "T1", "probe.jpg")


def test_short_uri_raises_value_error(repo):
    """段数不足的 URI 必须 ValueError（400 语义），而非 IndexError（500）。"""
    with pytest.raises(ValueError):
        repo.uri_to_path("/api/v1/tasks/T1/files")


def test_malformed_shape_raises_value_error(repo):
    with pytest.raises(ValueError):
        repo.uri_to_path("/bogus/shape")


def test_empty_uri_raises_value_error(repo):
    with pytest.raises(ValueError):
        repo.uri_to_path("")


def test_unsafe_extension_on_read_raises(repo):
    """读取侧扩展名白名单：与写入侧一致，禁止任意扩展名读取。"""
    with pytest.raises(ValueError):
        repo.uri_to_path("/api/v1/tasks/T1/files/payload.exe?kind=inputs")


def test_invalid_kind_raises(repo):
    with pytest.raises(ValueError):
        repo.uri_to_path("/api/v1/tasks/T1/files/a.jpg?kind=secrets")


@pytest.mark.asyncio
async def test_download_endpoint_malformed_uri_returns_400(tmp_path):
    """下载接口：uri_to_path ValueError 必须映射为外壳 400，而非兜底 500。"""
    from app.api.v1.task import router

    class _Container:
        def __init__(self, file_repo) -> None:  # noqa: ANN001
            self.file_repo = file_repo

    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(router)
    app.state.container = _Container(FileRepo(root=str(tmp_path)))

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/tasks/T1/files/payload.exe", params={"kind": "inputs"})

    assert resp.status_code == 400
    assert resp.json()["code"] == int(BizCode.INVALID_REQUEST)
