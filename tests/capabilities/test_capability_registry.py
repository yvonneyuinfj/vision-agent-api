"""能力注册中心（纯 DB 驱动）测试：加载 / 场景路由 / 版本轮询触发刷新 / reload。"""
from __future__ import annotations

import pytest

from app.models.capability import CapabilityManifest
from app.repositories.capability_repo import CapabilityRepo


def _manifest(rec) -> dict:
    return CapabilityManifest.from_record(rec).model_dump()


async def _seed(repo: CapabilityRepo) -> None:
    await repo.upsert(
        capability_id="cap_borescope_v1",
        tool_name="borescope",
        scene="borescope",
        capability_type="defect_detection",
        adapter_type="http",
        endpoint="http://127.0.0.1:9",
        health_endpoint="http://127.0.0.1:9/health",
        tool_version="1.0.0",
        model_version="1.0.0",
        status="enabled",
        description="孔探检测",
        meta={"families": ["defect"], "default_params": {"confidence_threshold": 0.5}},
    )
    await repo.upsert(
        capability_id="cap_ir_disabled_v1",
        tool_name="infrared",
        scene="infrared",
        capability_type="defect_detection",
        adapter_type="http",
        endpoint="http://127.0.0.1:8",
        tool_version="2.0.0",
        model_version="2.0.0",
        status="disabled",
        description="红外检测（已停用）",
    )


@pytest.mark.asyncio
async def test_registry_loads_from_db(capability_registry, session_factory):
    repo = CapabilityRepo(session_factory)
    await _seed(repo)
    await capability_registry.refresh()

    assert len(capability_registry.list()) == 2
    assert len(capability_registry.list_enabled()) == 1
    # from_record 正确反序列化 meta
    m = capability_registry.get("cap_borescope_v1")
    assert m is not None
    assert m.default_params["confidence_threshold"] == 0.5
    assert m.scene == "borescope"


@pytest.mark.asyncio
async def test_lookup_respects_status(capability_registry, session_factory):
    repo = CapabilityRepo(session_factory)
    await _seed(repo)
    await capability_registry.refresh()

    got = capability_registry.lookup("borescope")
    assert got is not None and got.capability_id == "cap_borescope_v1"
    # disabled 不参与路由
    assert capability_registry.lookup("infrared") is None
    assert capability_registry.lookup("nope") is None


@pytest.mark.asyncio
async def test_refresh_picks_up_new_capability(capability_registry, session_factory):
    repo = CapabilityRepo(session_factory)
    await _seed(repo)
    await capability_registry.refresh()
    assert capability_registry.get("cap_new_v1") is None

    # 人工注册新能力 => refresh 后可见（等价 15s 轮询刷新路径）
    await repo.upsert(
        capability_id="cap_new_v1",
        tool_name="fracture",
        scene="fracture",
        capability_type="classification",
        adapter_type="http",
        endpoint="http://127.0.0.1:7",
        tool_version="1.0.0",
        model_version="1.0.0",
        status="enabled",
    )
    await capability_registry.refresh()
    assert capability_registry.get("cap_new_v1") is not None
    assert capability_registry.lookup("fracture") is not None


@pytest.mark.asyncio
async def test_refresh_picks_up_status_toggle(capability_registry, session_factory):
    repo = CapabilityRepo(session_factory)
    await _seed(repo)
    await capability_registry.refresh()
    # 初始 enabled 参与路由
    assert capability_registry.lookup("borescope") is not None
    # DB 侧手动 disable（启停控制）→ refresh 后不再路由
    await repo.set_status("cap_borescope_v1", "disabled")
    await capability_registry.refresh()
    assert capability_registry.lookup("borescope") is None


@pytest.mark.asyncio
async def test_adapter_for_creates_via_factory(capability_registry, session_factory):
    """adapter_for 分发走 AdapterFactory：mock 类型能正常创建，未知类型抛业务错误。"""
    from app.core.errors import BusinessError

    repo = CapabilityRepo(session_factory)
    await repo.upsert(
        capability_id="cap_mock_v1",
        tool_name="mocktool",
        scene="mock",
        capability_type="defect_detection",
        adapter_type="mock",
        endpoint="",
        tool_version="1.0.0",
        model_version="1.0.0",
        status="enabled",
        description="mock 能力",
    )
    await capability_registry.refresh()

    adapter = await capability_registry.adapter_for("cap_mock_v1")
    from app.capabilities.adapters.mock_adapter import MockCapabilityAdapter

    assert isinstance(adapter, MockCapabilityAdapter)
    # 缓存复用：再次获取不重复创建（对象同一）
    assert await capability_registry.adapter_for("cap_mock_v1") is adapter

    # 未知 adapter_type => 工厂 ValueError 归一为 BusinessError(INTERNAL_ERROR)
    unknown = capability_registry.get("cap_mock_v1")
    assert unknown is not None
    unknown.adapter_type = "quantum"  # pydantic v2 默认 validate_assignment=False
    with pytest.raises(BusinessError):
        await capability_registry._adapter_for(unknown)