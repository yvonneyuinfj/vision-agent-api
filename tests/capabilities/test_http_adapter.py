"""HttpToolAdapter 调用治理回归：单次调用超时必须采用 AdapterContext.timeout_s。

manifest.timeout_s（InferTool 透传为 ctx.timeout_s）是运维配置的治理预算，
适配器不得以硬编码客户端超时替代。
"""
from __future__ import annotations

import pytest

from app.capabilities.adapters.base import AdapterContext
from app.capabilities.adapters.http_adapter import HttpToolAdapter
from app.core.errors import BizCode, BusinessError
from app.models.capability import CapabilityManifest


class _FakeResponse:
    status_code = 200

    def json(self):
        return {"code": 0, "message": "ok", "task_id": "T1"}


class _FakeClient:
    """捕获 post 调用参数的 httpx.AsyncClient 替身。"""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def post(self, url, data=None, files=None, timeout=None, headers=None):  # noqa: ANN001
        self.calls.append({"url": url, "timeout": timeout, "data": data, "headers": headers})
        return _FakeResponse()

    async def aclose(self) -> None:
        return None


def _make_adapter() -> HttpToolAdapter:
    manifest = CapabilityManifest(
        capability_id="cap_v1",
        tool_name="kap_tool",
        scene="kap_scene",
        endpoint="http://127.0.0.1:9",
    )
    return HttpToolAdapter(manifest)


@pytest.mark.asyncio
async def test_invoke_honors_context_timeout(tmp_path):
    # Arrange
    adapter = _make_adapter()
    await adapter._client.aclose()  # 释放真实客户端，避免测试泄漏
    fake = _FakeClient()
    adapter._client = fake
    image = tmp_path / "probe.jpg"
    image.write_bytes(b"fake-image")
    ctx = AdapterContext(task_id="T1", trace_id="tr-1", timeout_s=5.0, retry_times=1)

    # Act
    resp = await adapter.invoke(ctx, [str(image)], {})

    # Assert
    assert resp.ok is True
    assert len(fake.calls) == 1
    assert fake.calls[0]["timeout"] == 5.0


class _HealthFakeResponse:
    def __init__(self, status_code: int, body: dict) -> None:
        self.status_code = status_code
        self._body = body

    def json(self):
        return self._body


async def test_health_accepts_status_healthy_form(monkeypatch):
    """容器推理服务形态：无 code 字段，status=healthy。"""
    import types

    adapter = _make_adapter()

    async def fake_get(url):
        return _HealthFakeResponse(200, {"status": "healthy", "version": "3.4.5"})

    monkeypatch.setattr(adapter._client, "get", fake_get)
    ok, message = await adapter.health()
    assert ok is True
    assert message == "ok"


async def test_health_accepts_code_zero_form(monkeypatch):
    """规范形态：code=0。"""
    adapter = _make_adapter()

    async def fake_get(url):
        return _HealthFakeResponse(200, {"code": 0, "message": "success"})

    monkeypatch.setattr(adapter._client, "get", fake_get)
    ok, _ = await adapter.health()
    assert ok is True


async def test_health_reports_unhealthy_on_500(monkeypatch):
    adapter = _make_adapter()

    async def fake_get(url):
        return _HealthFakeResponse(500, {"code": 1000, "message": "boom"})

    monkeypatch.setattr(adapter._client, "get", fake_get)
    ok, _ = await adapter.health()
    assert ok is False


async def test_health_reports_unhealthy_on_transport_error(monkeypatch):
    adapter = _make_adapter()

    async def fake_get(url):
        raise ConnectionError("refused")

    monkeypatch.setattr(adapter._client, "get", fake_get)
    ok, message = await adapter.health()
    assert ok is False
    assert "refused" in message


@pytest.mark.asyncio
async def test_invoke_passes_scene_form_field(tmp_path):
    """manifest.scene 作为 scene 表单字段下发：mock 据此做场景默认缺陷回退。"""
    adapter = _make_adapter()
    await adapter._client.aclose()
    fake = _FakeClient()
    adapter._client = fake
    image = tmp_path / "probe.jpg"
    image.write_bytes(b"fake-image")
    ctx = AdapterContext(task_id="T1", trace_id="tr-1", timeout_s=5.0, retry_times=0)

    await adapter.invoke(ctx, [str(image)], {})

    sent = fake.calls[0]["data"]
    assert sent["scene"] == "kap_scene"


def test_adapter_ignores_shell_proxy_env(monkeypatch):
    """算法工具是本机/内网端点：不得被 shell 代理环境变量劫持（同 RAGFlowClient）。"""
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("ALL_PROXY", "http://127.0.0.1:1")

    adapter = _make_adapter()

    assert adapter._client._trust_env is False
    assert adapter._client._mounts == {}
    # localhost 仍被统一改写为 127.0.0.1（防 OrbStack/容器隧道劫持 IPv6 解析）
    assert str(adapter._client.base_url).startswith("http://127.0.0.1")

    import asyncio

    asyncio.run(adapter.close())


# ---- 5xx 有限重试 + health_endpoint（治理契约回归） ----

class _StatusResponse:
    def __init__(self, status_code: int, body: dict | None = None) -> None:
        self.status_code = status_code
        self._body = body if body is not None else {"code": 0, "message": "ok"}

    def json(self):
        return self._body


@pytest.mark.asyncio
async def test_invoke_retries_on_503_then_succeeds(tmp_path, monkeypatch):
    """服务端 503（模型未就绪）属瞬时故障：有限重试后成功（docstring 契约）。"""
    import httpx as _httpx

    adapter = _make_adapter()
    await adapter._client.aclose()
    calls = {"n": 0}

    async def fake_post(url, data=None, files=None, timeout=None, headers=None):  # noqa: ANN001
        calls["n"] += 1
        if calls["n"] == 1:
            return _StatusResponse(503, {"code": 3001, "message": "model not ready"})
        return _StatusResponse(200)

    fake = _FakeClient()
    fake.post = fake_post
    adapter._client = fake
    image = tmp_path / "probe.jpg"
    image.write_bytes(b"fake-image")
    ctx = AdapterContext(task_id="T1", trace_id="tr-1", timeout_s=5.0, retry_times=1)

    resp = await adapter.invoke(ctx, [str(image)], {})

    assert resp.ok is True
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_invoke_does_not_retry_on_4xx(tmp_path):
    """4xx 参数错误重试无意义：立即上抛（只调用一次）。"""
    adapter = _make_adapter()
    await adapter._client.aclose()
    calls = {"n": 0}

    async def fake_post(url, data=None, files=None, timeout=None, headers=None):  # noqa: ANN001
        calls["n"] += 1
        return _StatusResponse(422, {"code": 2001, "message": "bad param"})

    fake = _FakeClient()
    fake.post = fake_post
    adapter._client = fake
    image = tmp_path / "probe.jpg"
    image.write_bytes(b"fake-image")
    ctx = AdapterContext(task_id="T1", trace_id="tr-1", timeout_s=5.0, retry_times=3)

    with pytest.raises(BusinessError) as exc_info:
        await adapter.invoke(ctx, [str(image)], {})

    assert calls["n"] == 1
    assert exc_info.value.code == BizCode.INVALID_PARAMETER


@pytest.mark.asyncio
async def test_invoke_5xx_exhausts_retries_then_raises(tmp_path):
    """持续 503：重试耗尽后上抛业务错（不再无限打故障实例）。"""
    adapter = _make_adapter()
    await adapter._client.aclose()
    calls = {"n": 0}

    async def fake_post(url, data=None, files=None, timeout=None, headers=None):  # noqa: ANN001
        calls["n"] += 1
        return _StatusResponse(503, {"code": 3001, "message": "model not ready"})

    fake = _FakeClient()
    fake.post = fake_post
    adapter._client = fake
    image = tmp_path / "probe.jpg"
    image.write_bytes(b"fake-image")
    ctx = AdapterContext(task_id="T1", trace_id="tr-1", timeout_s=5.0, retry_times=2)

    with pytest.raises(BusinessError) as exc_info:
        await adapter.invoke(ctx, [str(image)], {})

    assert calls["n"] == 3  # 首次 + 2 次重试
    assert exc_info.value.code == BizCode.MODEL_NOT_READY


async def test_health_uses_manifest_health_endpoint(monkeypatch):
    """manifest.health_endpoint 生效：探活路径不再硬编码 /health。"""
    manifest = CapabilityManifest(
        capability_id="cap_v2",
        tool_name="hz_tool",
        scene="kap_scene",
        endpoint="http://127.0.0.1:9",
        health_endpoint="/healthz",
    )
    adapter = HttpToolAdapter(manifest)
    seen = {"url": None}

    async def fake_get(url):
        seen["url"] = url
        return _HealthFakeResponse(200, {"code": 0, "message": "ok"})

    monkeypatch.setattr(adapter._client, "get", fake_get)
    ok, _ = await adapter.health()

    assert ok is True
    assert seen["url"] == "/healthz"


async def test_health_supports_absolute_url_endpoint(monkeypatch):
    """DB 清单契约回归：health_endpoint 是完整 URL（http://host/health）。

    此前把完整 URL 当相对路径拼接（/http://...）导致探活 404、
    系统设置页「运行状态」全部异常。
    """
    manifest = CapabilityManifest(
        capability_id="cap_v3",
        tool_name="abs_tool",
        scene="kap_scene",
        endpoint="http://127.0.0.1:8002",
        health_endpoint="http://127.0.0.1:8002/health",
    )
    adapter = HttpToolAdapter(manifest)
    seen = {"url": None}

    async def fake_get(url):
        seen["url"] = url
        return _HealthFakeResponse(200, {"status": "healthy"})

    monkeypatch.setattr(adapter._client, "get", fake_get)
    ok, _ = await adapter.health()

    assert ok is True
    assert seen["url"] == "http://127.0.0.1:8002/health"  # 绝对 URL 原样使用


async def test_health_absolute_url_path_shorthand(monkeypatch):
    """路径形态（/healthz）仍相对 base_url 请求；无前导斜杠也兼容。"""
    manifest = CapabilityManifest(
        capability_id="cap_v4",
        tool_name="path_tool",
        scene="kap_scene",
        endpoint="http://127.0.0.1:8002",
        health_endpoint="healthz",
    )
    adapter = HttpToolAdapter(manifest)
    seen = {"url": None}

    async def fake_get(url):
        seen["url"] = url
        return _HealthFakeResponse(200, {"code": 0, "message": "ok"})

    monkeypatch.setattr(adapter._client, "get", fake_get)
    ok, _ = await adapter.health()

    assert ok is True
    assert seen["url"] == "/healthz"
