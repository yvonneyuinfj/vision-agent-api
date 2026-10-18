"""normalizers 包：按 capability_type 分型标准化。

统一入口 `normalize_result(capability_type, tool_result)` 由 normalize 节点消费；
需按能力类型补充分型时可在此注册映射，节点零改动（对齐 a.md §6.3 / §13）。
"""
from __future__ import annotations

from typing import Any

from app.capabilities.normalizers.classification_normalizer import (
    NormClassification,
    normalize_classifications,
)
from app.capabilities.normalizers.defect_normalizer import (
    NormDefect,
    max_confidence,
    max_severity,
    min_confidence,
    normalize_defects,
)
from app.capabilities.normalizers.measurement_normalizer import (
    NormMeasurement,
    normalize_measurements,
)

__all__ = [
    "NormClassification",
    "NormDefect",
    "NormMeasurement",
    "max_confidence",
    "max_severity",
    "min_confidence",
    "normalize_classifications",
    "normalize_defects",
    "normalize_measurements",
    "normalize_result",
]


def _as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def normalize_result(capability_type: str, tool_result: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
    """按 capability_type 分流标准化，返回 (entries, family)。

    capability_type：defect_detection / measurement / classification（DB 列值）
    family：defect / measurement / classification（判级与报告消费的语义族）
    """
    if capability_type == "measurement":
        family = "measurement"
        entries = [m.model_dump(exclude_none=True) for m in normalize_measurements(tool_result)]
    elif capability_type == "classification":
        family = "classification"
        entries = [
            c.model_dump(exclude_none=True) for c in normalize_classifications(tool_result)
        ]
    else:  # defect_detection（含未知类型兜底）
        family = "defect"
        entries = [d.model_dump(exclude_none=True) for d in normalize_defects(tool_result)]

    return entries, family