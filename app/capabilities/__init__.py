"""能力注册中心分层包（对齐 docs/a.md §13 目录结构）。

- models/       能力领域模型（已迁往 app/models，见 app/models/capability.py）
- registry.py     DB 驱动注册中心（内存缓存 + 15s version 轮询 + reload）
- normalizers/    分型标准化（defect/measurement/classification 拆分）
"""
from app.models.capability import CapabilityManifest, CapabilitySummary
from app.capabilities.registry import CapabilityRegistry

__all__ = ["CapabilityManifest", "CapabilityRegistry", "CapabilitySummary"]