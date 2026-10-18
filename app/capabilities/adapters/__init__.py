"""能力适配器层（对齐 docs/a.md §13 目录结构）：统一工具接入（HTTP / 本地桥接）。"""
from app.capabilities.adapters.base import (
    AdapterContext,
    AdapterPool,
    CircuitBreaker,
    RawToolResponse,
    ToolAdapter,
)
from app.capabilities.adapters.http_adapter import HttpToolAdapter, resolve_base_url
from app.capabilities.adapters.local_bridge_adapter import LocalSoftwareBridge
from app.capabilities.adapters.mock_adapter import MockCapabilityAdapter
from app.capabilities.adapters.model_service_adapter import ModelServiceAdapter

__all__ = [
    "AdapterContext",
    "AdapterPool",
    "CircuitBreaker",
    "HttpToolAdapter",
    "LocalSoftwareBridge",
    "MockCapabilityAdapter",
    "ModelServiceAdapter",
    "RawToolResponse",
    "ToolAdapter",
    "resolve_base_url",
]
