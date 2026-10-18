"""AdapterFactory 分发测试（a.md §8）。"""
from __future__ import annotations

import pytest

from app.capabilities.adapters.base import AdapterContext, ToolAdapter
from app.capabilities.adapters.http_adapter import HttpToolAdapter
from app.capabilities.adapters.local_bridge_adapter import LocalSoftwareBridge
from app.capabilities.adapters.mock_adapter import MockCapabilityAdapter
from app.capabilities.adapters.model_service_adapter import ModelServiceAdapter
from app.capabilities.factory import AdapterFactory
from app.models.capability import CapabilityManifest


def _manifest(adapter_type: str) -> CapabilityManifest:
    return CapabilityManifest(
        capability_id=f"cap-{adapter_type}",
        tool_name=f"tool_{adapter_type}",
        capability_type="defect_detection",  # CapabilityType 是 Literal 而非 Enum，不能用 .DEFECT_DETECTION
        scene="borescope",
        endpoint=f"http://localhost:9101/{adapter_type}",
        adapter_type=adapter_type,
        status="enabled",
    )


def test_factory_dispatches_http():
    adapter = AdapterFactory.create(_manifest("http"))
    assert isinstance(adapter, HttpToolAdapter)
    assert isinstance(adapter, ToolAdapter)


def test_factory_dispatches_mock():
    adapter = AdapterFactory.create(_manifest("mock"))
    assert isinstance(adapter, MockCapabilityAdapter)
    assert isinstance(adapter, HttpToolAdapter)  # mock 复用 HTTP 协议


def test_factory_dispatches_local_bridge():
    adapter = AdapterFactory.create(_manifest("local_bridge"))
    assert isinstance(adapter, LocalSoftwareBridge)


def test_factory_dispatches_model_service():
    adapter = AdapterFactory.create(_manifest("model_service"))
    assert isinstance(adapter, ModelServiceAdapter)


def test_factory_rejects_unknown_type():
    m = _manifest("http")
    m.adapter_type = "quantum"  # pydantic v2 默认 validate_assignment=False，赋值不校验
    with pytest.raises(ValueError, match="unknown adapter_type"):
        AdapterFactory.create(m)


@pytest.mark.asyncio
async def test_model_service_adapter_health_false_on_missing_dir(tmp_path):
    m = _manifest("model_service")
    m.endpoint = str(tmp_path / "no_such_model_dir")
    adapter = AdapterFactory.create(m)
    ok, message = await adapter.health()  # ToolAdapter.health() -> tuple[bool, str]
    assert ok is False
    assert message


@pytest.mark.asyncio
async def test_model_service_invoke_raises_business_error(tmp_path):
    from app.capabilities.adapters.base import AdapterContext
    from app.core.errors import BizCode, BusinessError

    m = _manifest("model_service")
    m.endpoint = str(tmp_path / "m")
    adapter = AdapterFactory.create(m)
    ctx = AdapterContext(task_id="T1", trace_id="t1", timeout_s=5, retry_times=0)
    with pytest.raises(BusinessError) as ei:
        await adapter.invoke(ctx, image_paths=[], params={})
    assert ei.value.code == int(BizCode.INTERNAL_ERROR)  # 4001，ModelService 未实现


# --------------------------------------------------------------------------
# LocalSoftwareBridge 异步化：阻塞 IO 下放线程池 + 轮询回收
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_local_bridge_invoke_stages_and_collects(tmp_path):
    """投放 -> 对方写结果文件 -> 轮询回收：全链路异步，不阻塞事件循环。"""
    import json as _json
    import asyncio as _asyncio

    manifest = CapabilityManifest(
        capability_id="dr_local_v1", tool_name="dr_local", scene="dr",
        adapter_type="local_bridge", endpoint="",
    )
    bridge = LocalSoftwareBridge(manifest, str(tmp_path))
    image = tmp_path / "probe.jpg"
    image.write_bytes(b"fake-image")

    async def _write_result_later():
        await _asyncio.sleep(1.5)
        out = tmp_path / "dr_local" / "out" / "T-BR-1.json"
        out.write_text(_json.dumps({"code": 0, "result": {"status": "abnormal"}}), encoding="utf-8")

    writer = _asyncio.create_task(_write_result_later())
    ctx = AdapterContext(task_id="T-BR-1", trace_id="tr-1", timeout_s=8.0, retry_times=0)

    resp = await bridge.invoke(ctx, [str(image)], {"threshold": 0.5})

    assert resp.ok is True
    assert resp.payload["result"]["status"] == "abnormal"
    # 结果文件消费后即删；投放目录清理
    assert not (tmp_path / "dr_local" / "out" / "T-BR-1.json").exists()
    assert not (tmp_path / "dr_local" / "in" / "T-BR-1").exists()
    await writer  # no-op（已完成），防告警


@pytest.mark.asyncio
async def test_local_bridge_invoke_times_out(tmp_path):
    """对方软件不回结果：超时清理投放目录并抛 INFERENCE_TIMEOUT。"""
    manifest = CapabilityManifest(
        capability_id="dr_local_v1", tool_name="dr_local", scene="dr",
        adapter_type="local_bridge", endpoint="",
    )
    bridge = LocalSoftwareBridge(manifest, str(tmp_path))
    image = tmp_path / "probe.jpg"
    image.write_bytes(b"fake-image")
    ctx = AdapterContext(task_id="T-BR-2", trace_id="tr-2", timeout_s=0.1, retry_times=0)

    from app.core.errors import BizCode as _BC, BusinessError as _BE

    with pytest.raises(_BE) as exc_info:
        await bridge.invoke(ctx, [str(image)], {})
    assert exc_info.value.code == _BC.INFERENCE_TIMEOUT
    # 投放目录已清理（不留垃圾输入副本）
    assert not (tmp_path / "dr_local" / "in" / "T-BR-2").exists()
