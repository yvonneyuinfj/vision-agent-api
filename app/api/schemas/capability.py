"""能力注册中心对外模型（转发到 app.models.capability，保持 import 兼容）。

业务模型（CapabilityManifest/Summary/Health）统一收敛于 app/models/ 分层包
（决策 11：gateway 只留 adapter；本文件仅作兼容别名，勿新增定义）。
"""
from __future__ import annotations

from app.models.capability import (  # noqa: F401  # re-export 转发
    CapabilityHealth,
    CapabilityManifest,
    CapabilitySummary,
)

__all__ = ["CapabilityHealth", "CapabilityManifest", "CapabilitySummary"]