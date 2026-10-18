"""CapabilityRepo 写侧原语单测（决策 39/42/45）。

这里锁的是「列级 patch」这一条硬约束：编辑绝不能把未提交的列洗成默认值
（全字段 upsert() 会把 timeout_s=120 改回 60、health_endpoint 清空、meta 整块丢）。
"""
from __future__ import annotations

import pytest

from app.models.capability import CapabilityManifest
from app.repositories.capability_repo import (
    STATUS_DISABLED,
    STATUS_ENABLED,
    CapabilityRepo,
)


@pytest.fixture
def repo(session_factory):
    return CapabilityRepo(session_factory)


async def _insert(repo, cid: str = "cap_a_v1", **over):
    kwargs = dict(
        capability_id=cid, tool_name="tool_a", scene="fpi",
        endpoint="http://127.0.0.1:18016", health_endpoint="http://127.0.0.1:18016/health",
        tool_version="1.0.0", model_version="m1", timeout_s=120, max_retry=4,
        description="真工具", model_weights_sha256="A" * 64,
        meta={"headers": {"X-API-Key": "k-1"}, "default_params": {"part_id": "P"}},
    )
    kwargs.update(over)
    return await repo.insert(**kwargs)


async def test_insert_then_duplicate_rejected(repo):
    assert await _insert(repo) is True
    assert await _insert(repo) is False  # 决策 42：登记不做隐式更新
    assert await repo.get("cap_a_v1") is not None


async def test_update_fields_keeps_columns_that_were_not_sent(repo):
    await _insert(repo)
    before = await repo.get("cap_a_v1")

    assert await repo.update_fields("cap_a_v1", {"description": "改了文案"}, expected_version=before.version)

    after = await repo.get("cap_a_v1")
    assert after.description == "改了文案"
    # 未提交的列必须原样活着 —— 这是全字段 upsert() 做不到的
    assert after.timeout_s == 120
    assert after.max_retry == 4
    assert after.health_endpoint == "http://127.0.0.1:18016/health"
    assert after.model_weights_sha256 == "A" * 64
    assert '"X-API-Key"' in after.meta_json


async def test_explicit_null_clears_column(repo):
    await _insert(repo)
    before = await repo.get("cap_a_v1")

    await repo.update_fields("cap_a_v1", {"health_endpoint": None, "timeout_s": 30}, expected_version=before.version)

    after = await repo.get("cap_a_v1")
    assert after.health_endpoint is None
    assert after.timeout_s == 30


async def test_optimistic_lock_rejects_stale_version(repo):
    await _insert(repo)
    before = await repo.get("cap_a_v1")

    assert await repo.update_fields("cap_a_v1", {"description": "x"}, expected_version=before.version + 99) is False
    assert (await repo.get("cap_a_v1")).description == "真工具"


async def test_each_patch_bumps_version_for_polling(repo):
    await _insert(repo)
    v0 = (await repo.get("cap_a_v1")).version
    await repo.update_fields("cap_a_v1", {"description": "a"}, expected_version=v0)
    v1 = (await repo.get("cap_a_v1")).version
    assert v1 == v0 + 1
    assert await repo.max_version() == v1


async def test_immutable_columns_rejected(repo):
    await _insert(repo)
    for col in ("tool_name", "capability_id", "version", "id"):
        with pytest.raises(ValueError):
            await repo.update_fields("cap_a_v1", {col: "hack"})


async def test_dict_json_columns_serialized_and_loadable(repo):
    await _insert(repo)
    before = await repo.get("cap_a_v1")
    await repo.update_fields(
        "cap_a_v1",
        {"output_schema": {"code": 0, "result": {"status": "normal", "defects": []}}},
        expected_version=before.version,
    )

    rec = await repo.get("cap_a_v1")
    assert rec.output_schema.lstrip().startswith("{")  # 落库是 JSON 文本
    manifest = CapabilityManifest.from_record(rec)  # 装载不再因文本列而炸
    assert manifest.output_schema["code"] == 0
    assert manifest.headers == {"X-API-Key": "k-1"}


async def test_set_status_roundtrip(repo):
    await _insert(repo)
    assert await repo.set_status("cap_a_v1", STATUS_DISABLED) is True
    assert (await repo.get("cap_a_v1")).status == STATUS_DISABLED
    assert await repo.set_status("cap_a_v1", STATUS_ENABLED) is True
    assert await repo.set_status("cap_missing", STATUS_ENABLED) is False
    with pytest.raises(ValueError):
        await repo.set_status("cap_a_v1", "onfire")
