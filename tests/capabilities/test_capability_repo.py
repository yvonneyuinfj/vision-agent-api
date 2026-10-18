"""能力注册中心（DB 驱动）测试：upsert / 场景路由 / 启停控制 / 轮询刷新语义。"""
from __future__ import annotations

import pytest

from app.repositories.capability_repo import CapabilityRepo

ENABLED = "enabled"
DISABLED = "disabled"
MAINTENANCE = "maintenance"


@pytest.fixture
def cap_repo(session_factory):
    return CapabilityRepo(session_factory)


async def _seed_one(repo: CapabilityRepo, capability_id: str = "kap_v1", **kw) -> bool:
    return await repo.upsert(
        capability_id=capability_id,
        tool_name=kw.pop("tool_name", "kap_tool"),
        scene=kw.pop("scene", "kap_scene"),
        capability_type=kw.pop("capability_type", "defect_detection"),
        adapter_type=kw.pop("adapter_type", "http"),
        endpoint=kw.pop("endpoint", "http://127.0.0.1:9"),
        health_endpoint=kw.pop("health_endpoint", "http://127.0.0.1:9/health"),
        tool_version="1.0.0",
        model_version="1.0.0",
        status=kw.pop("status", ENABLED),
        **kw,
    )


@pytest.mark.asyncio
async def test_upsert_then_get(cap_repo):
    await _seed_one(cap_repo)
    rec = await cap_repo.get("kap_v1")
    assert rec is not None
    assert rec.scene == "kap_scene"
    assert rec.status == ENABLED
    assert rec.version == 1  # 首次插入 version=1


@pytest.mark.asyncio
async def test_upsert_is_idempotent_and_bumps_version(cap_repo):
    await _seed_one(cap_repo)
    await _seed_one(cap_repo, status=MAINTENANCE)
    rec = await cap_repo.get("kap_v1")
    assert rec is not None
    assert rec.status == MAINTENANCE
    assert rec.version == 2  # 重复注册触发 version+1（轮询刷新键）


@pytest.mark.asyncio
async def test_max_version_tracks_changes(cap_repo):
    assert await cap_repo.max_version() == 0  # 空表
    await _seed_one(cap_repo)
    assert await cap_repo.max_version() == 1
    await _seed_one(cap_repo, scene="other")
    assert await cap_repo.max_version() == 2


@pytest.mark.asyncio
async def test_insert_distinct_capability_bumps_max_version(cap_repo):
    """新能力插入必须推高 max_version，否则 15s 轮询永远感知不到新能力。

    场景：能力 A 已更新到 version=2（水位=2）；此时注册新能力 B，
    若 B 落 version=1，MAX(version) 不变，注册中心缓存永不刷新，B 不可路由。
    """
    # Arrange
    await _seed_one(cap_repo, "a_v1")
    await _seed_one(cap_repo, "a_v1", scene="other")  # 更新 A -> version 2
    assert await cap_repo.max_version() == 2

    # Act
    await _seed_one(cap_repo, "b_v1", scene="infrared")  # 全新插入

    # Assert
    assert await cap_repo.max_version() > 2
    rec = await cap_repo.get("b_v1")
    assert rec is not None


@pytest.mark.asyncio
async def test_scene_enabled_routing_filters_status(cap_repo):
    await _seed_one(cap_repo, "a_v1", scene="borescope", status=ENABLED)
    await _seed_one(cap_repo, "b_v1", scene="borescope", status=DISABLED)
    await _seed_one(cap_repo, "c_v1", scene="borescope", status=MAINTENANCE)
    await _seed_one(cap_repo, "d_v1", scene="infrared", status=ENABLED)

    enabled = await cap_repo.list_scene_enabled("borescope")
    assert [r.capability_id for r in enabled] == ["a_v1"]
    assert await cap_repo.list_scene_enabled("infrared") != []


@pytest.mark.asyncio
async def test_set_status_bumps_version(cap_repo):
    await _seed_one(cap_repo)
    ok = await cap_repo.set_status("kap_v1", DISABLED)
    assert ok is True
    rec = await cap_repo.get("kap_v1")
    assert rec is not None and rec.status == DISABLED and rec.version == 2
    # 启停切换本身也要触发轮询 -> version 继续自增
    await cap_repo.set_status("kap_v1", ENABLED)
    rec = await cap_repo.get("kap_v1")
    assert rec is not None and rec.version == 3


@pytest.mark.asyncio
async def test_set_status_rejects_invalid(session_factory, cap_repo):
    await _seed_one(cap_repo)
    with pytest.raises(ValueError):
        await cap_repo.set_status("kap_v1", "bogus")


@pytest.mark.asyncio
async def test_invalid_status_upsert_rejected(cap_repo):
    with pytest.raises(ValueError):
        await _seed_one(cap_repo, status="bogus")