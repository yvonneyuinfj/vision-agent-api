"""标准化：模式分类结果（对齐 docs/a.md §6.3 三分类标准化拆分）。

- 保留 label/score 语义，不做严重度推断；dict 形态 {label: score} 自动展开
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class NormClassification(BaseModel):
    label: str | None = None
    score: float | None = None
    conclusion: str = ""
    note: str = ""


def _as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def normalize_classifications(result: dict[str, Any]) -> list[NormClassification]:
    labels = result.get("labels") or []
    if isinstance(labels, dict):  # {label: score}
        labels = [{"label": k, "score": v} for k, v in labels.items()]
    out: list[NormClassification] = []
    for item in labels:
        if not isinstance(item, dict):
            continue
        score = item.get("score")
        if score is None:  # 0.0 是合法分值，不得用 or 短路回落到 probability
            score = item.get("probability")
        out.append(
            NormClassification(
                label=item.get("label"),
                score=_as_float(score),
                conclusion=item.get("conclusion") or "",
                note=item.get("note") or "",
            )
        )
    return out