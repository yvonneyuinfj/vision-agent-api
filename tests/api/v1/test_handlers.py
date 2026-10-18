"""统一异常处理：FastAPI 校验失败 -> 1001；业务异常 -> 外壳（对齐规范 §8）。"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.errors import BizCode, BusinessError
from app.core.handlers import register_exception_handlers
from app.core.response import StandardEnvelope


@pytest.fixture
def client():
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/boom")
    async def boom() -> None:
        raise BusinessError(BizCode.TASK_NOT_FOUND, "task gone", http_status=404)

    @app.get("/validation")
    async def validation(x: int) -> dict:
        return {"x": x}

    return TestClient(app)


def test_validation_error_maps_to_1001(client):
    # Act
    resp = client.get("/validation?x=abc")
    # Assert
    data = resp.json()
    assert data["code"] == int(BizCode.INVALID_REQUEST)
    assert resp.status_code == 400


def test_business_error_maps_to_shell(client):
    # Act
    resp = client.get("/boom")
    # Assert
    data = resp.json()
    assert data["code"] == int(BizCode.TASK_NOT_FOUND)
    assert data["result"] is None
    assert resp.status_code == 404