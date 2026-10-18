"""capabilities 层测试共享 fixture（镜像 app/capabilities/ 分层）。"""
from __future__ import annotations

import pytest_asyncio


@pytest_asyncio.fixture
async def capability_registry(session_factory):
    """DB 驱动能力注册中心（轮询禁用，测试手动 refresh）。"""
    from app.capabilities.registry import CapabilityRegistry
    from app.repositories.capability_repo import CapabilityRepo

    registry = CapabilityRegistry(CapabilityRepo(session_factory), poll_interval_s=3600)
    await registry.refresh()
    yield registry
    await registry.stop()