"""ModelServiceAdapter 骨架（a.md §8）：本地模型服务适配器。

M1 仅提供注册与健康检查骨架：health() 检查 manifest.endpoint 指向的目录
含模型产物标记；invoke() 未实现，明确抛业务错误。真实推理（ONNX Runtime 等）M2 接入。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from app.capabilities.adapters.base import AdapterContext, RawToolResponse, ToolAdapter
from app.core.errors import BizCode, BusinessError
from app.models.capability import CapabilityManifest

# 模型产物标记文件：endpoint 目录存在其一即视为模型就绪
_MODEL_MARKERS = ("model.onnx", "model.bin", "weights", "model.pt")


class ModelServiceAdapter(ToolAdapter):
    """本地模型服务适配器（骨架）：注册可用、调用明确报错。"""

    def __init__(self, capability: CapabilityManifest) -> None:
        self._capability = capability

    async def health(self) -> tuple[bool, str]:
        model_dir = Path(self._capability.endpoint or ".")
        if not model_dir.is_dir():
            return False, f"model dir not found: {model_dir}"
        for marker in _MODEL_MARKERS:
            if (model_dir / marker).exists():
                return True, "model ready"
        return False, f"no model marker under {model_dir}"

    async def invoke(
        self,
        ctx: AdapterContext,
        image_paths: list[str],
        params: dict[str, Any],
        *,
        auxiliary: dict[str, str] | None = None,
    ) -> RawToolResponse:
        raise BusinessError(
            BizCode.INTERNAL_ERROR, "model service adapter not implemented"
        )
