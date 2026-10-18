"""标准化：缺陷检测结果（对齐 docs/a.md §6.3 三分类标准化拆分）。

- 统一 severity 枚举（low/medium/high/null），未提供严重度不伪造
- 保留 raw 结构原样（追溯），产出平台 standardized 供判级/报告消费
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel

SEVERITY_RANK: dict[str, int] = {"low": 1, "medium": 2, "high": 3}


class NormDefect(BaseModel):
    defect_id: str | None = None
    defect_type: str = "unknown"
    defect_name_cn: str = ""
    confidence: float | None = None
    severity: str | None = None  # low/medium/high/null，不支持时 None
    location: dict[str, Any] | None = None
    description: str = ""
    # 多图 fan-out 溯源：条目来源图名（单图工具单次调用时不设置）
    image_name: str = ""


def _as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def normalize_defects(result: dict[str, Any]) -> list[NormDefect]:
    """缺陷检测标准化：严重度严格限定枚举，非枚举按不支持处理。"""
    out: list[NormDefect] = []
    for item in result.get("defects") or []:
        if not isinstance(item, dict):
            continue
        severity = item.get("severity")
        if severity is not None and severity not in SEVERITY_RANK:
            severity = None
        out.append(
            NormDefect(
                defect_id=item.get("defect_id"),
                defect_type=item.get("defect_type") or "unknown",
                defect_name_cn=item.get("defect_name_cn") or "",
                confidence=_as_float(item.get("confidence")),
                severity=severity,
                location=item.get("location"),
                description=item.get("description") or "",
                image_name=str(item.get("image_name") or ""),
            )
        )
    return out


def max_severity(entries: list[dict[str, Any]]) -> str | None:
    ranks = [SEVERITY_RANK[e["severity"]] for e in entries if e.get("severity") in SEVERITY_RANK]
    if not ranks:
        return None
    by_rank = {v: k for k, v in SEVERITY_RANK.items()}
    return by_rank[max(ranks)]


def min_confidence(entries: list[dict[str, Any]]) -> float | None:
    confs = [e["confidence"] for e in entries if e.get("confidence") is not None]
    return min(confs) if confs else None


def max_confidence(entries: list[dict[str, Any]]) -> float | None:
    """最高置信度：判级「模型检测结果是否可信」的依据（需求：仅当检测结果
    低于设定阈值时才转人工复核）。min_confidence 是任一弱检出即拉低，会把
    高分缺陷一并拖进人工复核，故判级用 max。"""
    confs = [e["confidence"] for e in entries if e.get("confidence") is not None]
    return max(confs) if confs else None