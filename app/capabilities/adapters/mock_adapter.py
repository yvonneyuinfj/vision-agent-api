"""Mock 适配器：mock_service 为 HTTP 服务，协议与 HttpToolAdapter 完全一致。

独立类价值：adapter_type=mock 时类型可识别，未来 mock 特有逻辑（注入故障/延迟）有落点。
"""
from __future__ import annotations

from app.capabilities.adapters.http_adapter import HttpToolAdapter


class MockCapabilityAdapter(HttpToolAdapter):
    """mock_service 能力适配器（协议同 HttpToolAdapter）。"""
