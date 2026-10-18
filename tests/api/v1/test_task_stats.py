"""任务统计端点 GET /api/v1/tasks/stats：权威全量计数。

核心防回归：/stats 必须注册在 GET /{task_id} 之前（路由顺序陷阱——乱序会让
"stats" 被捕获为 task_id 而 404/500）。这里 stub 掉 task_service，只验证
路由分发与响应外壳；统计语义由 tests/repositories/test_task_repo.py 覆盖。
"""
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.task import router as task_router
from app.core.handlers import register_exception_handlers
from app.services.container import AppContainer


class _FakeTaskService:
    def __init__(self, stats: dict) -> None:
        self._stats = stats

    async def get_stats(self) -> dict:
        return self._stats

    async def get(self, task_id: str) -> None:
        return None  # 未知任务 → TASK_NOT_FOUND 404（与 TaskService.get 语义一致）


def _app(stats):
    container = AppContainer()
    container.task_service = _FakeTaskService(stats)  # 容器字段名见 container.py
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(task_router)
    app.state.container = container
    return TestClient(app)


def test_stats_returns_envelope_with_counts():
    client = _app(
        {
            "total": 42, "today": 3,
            "pending": 1, "running": 0, "awaiting_review": 2,
            "completed": 37, "failed": 2, "canceled": 0,
        }
    )
    resp = client.get("/api/v1/tasks/stats")
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0
    assert body["result"] == {
        "total": 42, "today": 3,
        "pending": 1, "running": 0, "awaiting_review": 2,
        "completed": 37, "failed": 2, "canceled": 0,
    }


def test_stats_route_does_not_shadow_nor_get_shadowed():
    """双向防回归：/stats 命中统计路由（200 而非 404），/tasks/{id} 仍独立存在。

    若注册顺序颠倒（/stats 在 /{task_id} 之后），"stats" 会被当 task_id 捕获，
    走到 stub.get("stats") → 404 而非 200；本用例即失败。
    """
    client = _app({"total": 5, "today": 2, "pending": 0, "running": 0,
                   "awaiting_review": 0, "completed": 3, "failed": 2, "canceled": 0})
    assert client.get("/api/v1/tasks/stats").status_code == 200
    assert client.get("/api/v1/tasks/some-real-task-id").status_code == 404