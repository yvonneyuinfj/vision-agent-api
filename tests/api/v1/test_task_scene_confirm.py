"""POST /tasks/{id}/scene-confirm：场景人工确认恢复端点（桩容器 + 统一异常外壳）。"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.task import router
from app.core.errors import BizCode, BusinessError
from app.core.handlers import register_exception_handlers


class _StubService:
    def __init__(self):
        self.calls: list[tuple] = []

    async def confirm_scene(self, task_id: str, scene: str) -> None:
        self.calls.append((task_id, scene))


def _client(task_service) -> TestClient:
    app = FastAPI()
    register_exception_handlers(app)

    class _Container:
        pass

    container = _Container()
    container.task_service = task_service
    app.state.container = container
    app.include_router(router)
    return TestClient(app)


def test_scene_confirm_forwards_to_service():
    svc = _StubService()
    client = _client(svc)

    resp = client.post("/api/v1/tasks/T1/scene-confirm", json={"scene": "borescope", "opinion": "ok"})

    assert resp.status_code == 200
    data = resp.json()
    assert data["code"] == 0
    assert data["result"] == {"task_id": "T1", "scene": "borescope"}
    assert svc.calls == [("T1", "borescope")]


def test_scene_confirm_maps_conflict_to_envelope():
    class _Conflict:
        async def confirm_scene(self, task_id: str, scene: str) -> None:
            raise BusinessError(BizCode.TASK_STATE_CONFLICT, "not awaiting", http_status=409)

    resp = _client(_Conflict()).post("/api/v1/tasks/T1/scene-confirm", json={"scene": "borescope"})

    assert resp.status_code == 409
    assert resp.json()["code"] == int(BizCode.TASK_STATE_CONFLICT)


def test_scene_confirm_validates_required_scene_field():
    resp = _client(_StubService()).post("/api/v1/tasks/T1/scene-confirm", json={"opinion": "x"})

    assert resp.status_code == 400
    assert resp.json()["code"] == int(BizCode.INVALID_REQUEST)