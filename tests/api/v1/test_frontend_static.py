"""前端静态托管：dist 存在时 / 与 SPA 路由回落 index.html，静态资源直出，API/docs 路由不被遮蔽。"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import app.main as main

INDEX_MARK = "QC-SPA"


@pytest.fixture
def dist_dir(tmp_path):
    """构造一个最小 dist：index.html + 一个带 hash 风格的静态资源。"""
    assets = tmp_path / "assets"
    assets.mkdir()
    (tmp_path / "index.html").write_text(f"<html><body>{INDEX_MARK}</body></html>", encoding="utf-8")
    (assets / "app-abc123.js").write_text("console.log('asset');", encoding="utf-8")
    return tmp_path


@pytest.fixture
def client(dist_dir, monkeypatch):
    monkeypatch.setattr(main, "FRONTEND_DIST", dist_dir)
    return TestClient(main.create_app())


def test_root_serves_index_html(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    assert INDEX_MARK in resp.text


def test_spa_route_falls_back_to_index(client):
    # BrowserRouter 深链（如 /tasks/list）刷新需回落到 index.html
    resp = client.get("/tasks/list")
    assert resp.status_code == 200
    assert INDEX_MARK in resp.text


def test_hashed_asset_served_directly(client):
    resp = client.get("/assets/app-abc123.js")
    assert resp.status_code == 200
    assert "console.log('asset');" in resp.text


def test_missing_asset_returns_envelope_404(client):
    # 带扩展名的未命中资源不得回落成 index.html（否则浏览器按 JS 解析 HTML）
    resp = client.get("/assets/missing.js")
    assert resp.status_code == 404
    data = resp.json()
    assert "code" in data and data["code"] != 0


def test_path_traversal_does_not_leak(client, dist_dir):
    (dist_dir.parent / "secret.txt").write_text("top-secret", encoding="utf-8")
    resp = client.get("/%2e%2e/secret.txt")
    assert resp.status_code != 200
    assert "top-secret" not in resp.text


def test_api_and_docs_routes_not_shadowed(client):
    assert client.get("/docs").status_code == 200


def test_missing_dist_keeps_root_probe(monkeypatch, tmp_path):
    # 无构建产物时退回原根路径探测，不启用前端托管
    monkeypatch.setattr(main, "FRONTEND_DIST", tmp_path / "no-dist")
    resp = TestClient(main.create_app()).get("/")
    assert resp.status_code == 200
    assert "service" in resp.json()
