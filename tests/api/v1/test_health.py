"""平台健康检查路由测试：存活/数据库状态 + 服务器当日日期（北京时区）。"""
from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

import app.main as main


@pytest.fixture
def client():
    return TestClient(main.create_app())


def test_health_reports_server_date(client):
    resp = client.get("/api/v1/health")
    assert resp.status_code == 200
    data = resp.json()["result"]
    # 无容器（lifespan 未跑）：数据库降级，但服务器日期必须存在且为当日
    assert data["status"] in ("healthy", "degraded")
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", data["server_date"])
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} CST", data["server_datetime"])