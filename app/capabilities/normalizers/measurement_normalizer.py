"""标准化：定量测量结果（对齐 docs/a.md §6.3 三分类标准化拆分）。

- 保留原始指标名与单位，附加计量说明，不做严重度推断
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class NormMeasurement(BaseModel):
    name: str
    value: float | None = None
    unit: str = ""
    note: str = ""


def _as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def normalize_measurements(result: dict[str, Any]) -> list[NormMeasurement]:
    items = result.get("measurements") or result.get("metrics") or []
    out: list[NormMeasurement] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        out.append(
            NormMeasurement(
                name=str(item.get("name") or item.get("key") or "metric"),
                value=_as_float(item.get("value")),
                unit=item.get("unit") or "",
                note=item.get("note") or "",
            )
        )
    return out