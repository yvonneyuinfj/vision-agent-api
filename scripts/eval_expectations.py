"""金样本期望检查器（纯函数，供 eval_samples.py 与单测共用）。

边界集 + 保留集语义：
- 期望文件 config/eval/samples.yaml 描述每个样本的应然终态
  （status / scene / grade_level / agent_request 任选其子集）；
- check_row 把单次评估结果与期望逐项对照，返回违规描述列表
  （空列表 = 通过）；
- 未配置期望的样本不参与断言（记录为基线，不预判对错）。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

DEFAULT_EXPECTATIONS_PATH = (
    Path(__file__).resolve().parent.parent / "config" / "eval" / "samples.yaml"
)

# 可断言字段 -> 评估行取值函数（行字段名与 eval_samples.collect 对齐）
_ROW_FIELDS = {
    "status": lambda row: row.get("status"),
    "scene": lambda row: row.get("scene"),
    "grade_level": lambda row: row.get("grade_level"),
    "agent_request": lambda row: bool(row.get("agent_request")),
}


def load_expectations(path: Path | str | None = None) -> dict[str, dict[str, Any]]:
    """载入期望文件：{sample_prefix: {field: expected}}；文件缺失/空返回空表。"""
    file = Path(path) if path else DEFAULT_EXPECTATIONS_PATH
    try:
        data = yaml.safe_load(file.read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001 - 期望文件损坏按无期望处理（记录为基线）
        return {}
    samples = data.get("samples") or {}
    if not isinstance(samples, dict):
        return {}
    expectations: dict[str, dict[str, Any]] = {}
    for name, spec in samples.items():
        expect = spec.get("expect") if isinstance(spec, dict) else None
        if isinstance(expect, dict) and expect:
            expectations[str(name)] = {
                k: v for k, v in expect.items() if k in _ROW_FIELDS
            }
    return expectations


def check_row(expect: dict[str, Any], row: dict[str, Any]) -> list[str]:
    """单样本期望对照：返回违规描述（空列表 = 通过）。

    提交失败的行（status=submit_failed / error 非空）直接判不通过。
    """
    violations: list[str] = []
    if not expect:
        return violations

    if row.get("status") == "submit_failed" or (row.get("error") and not row.get("grade_level")):
        violations.append(f"评估执行失败：{row.get('error') or row.get('status')}")
        return violations

    for field, expected in expect.items():
        actual = _ROW_FIELDS[field](row)
        if actual != expected:
            violations.append(f"{field}: 期望 {expected!r}，实际 {actual!r}")
    return violations


def matched_sample_name(sample: str, expectations: dict[str, dict[str, Any]]) -> str | None:
    """样本名前缀匹配期望条目（与 eval_samples 的 --samples 前缀语义一致）。"""
    for name in sorted(expectations, key=len, reverse=True):
        if sample.startswith(name):
            return name
    return None
