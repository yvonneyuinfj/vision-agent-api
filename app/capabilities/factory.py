"""适配器工厂（a.md §8）：按 CapabilityManifest.adapter_type 统一分发。"""
from __future__ import annotations

from app.capabilities.adapters.base import ToolAdapter
from app.capabilities.adapters.http_adapter import HttpToolAdapter
from app.capabilities.adapters.local_bridge_adapter import LocalSoftwareBridge
from app.capabilities.adapters.mock_adapter import MockCapabilityAdapter
from app.capabilities.adapters.model_service_adapter import ModelServiceAdapter
from app.core.config import get_settings
from app.models.capability import CapabilityManifest


class AdapterFactory:
    """能力适配器工厂：消除注册中心的 if/else 分发（registry.adapter_for）。"""

    @staticmethod
    def create(capability: CapabilityManifest) -> ToolAdapter:
        adapter_type = capability.adapter_type
        if adapter_type == "http":
            return HttpToolAdapter(capability)
        if adapter_type == "mock":
            return MockCapabilityAdapter(capability)
        if adapter_type == "local_bridge":
            # work_root 语义与现有 registry.adapter_for 一致：data_path/bridges 子目录
            return LocalSoftwareBridge(
                capability, str(get_settings().data_path / "bridges")
            )
        if adapter_type == "model_service":
            return ModelServiceAdapter(capability)
        raise ValueError(f"unknown adapter_type: {adapter_type}")
