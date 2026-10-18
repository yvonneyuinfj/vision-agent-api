#!/usr/bin/env python3
"""复核数据闭环报表（评估矩阵）：聚合人工复核留痕，输出漏检/误检统计与阈值对照。

用法：
    python scripts/review_matrix.py                          # 走 http://127.0.0.1:8000
    python scripts/review_matrix.py --base-url http://... --limit 1000

数据口径（app/services/review_matrix.py）：
- defects.added     = 人工补漏检数（能力漏检）
- defects.excluded  = 人工判误检数（能力误报）
- low_confidence_band = max_confidence < 阈值转人工人群中，人工确认率 vs 改判率
  （确认率越高阈值越保守——本可自动放行；改判率越高越激进——漏放风险）
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import requests

RESULT_PATH = Path(__file__).resolve().parent / "review_matrix.json"


def fetch(base_url: str, limit: int) -> dict:
    resp = requests.get(
        f"{base_url}/api/v1/tasks/review-matrix", params={"limit": limit}, timeout=30
    )
    resp.raise_for_status()
    body = resp.json()
    if body.get("code") != 0:
        raise RuntimeError(f"review-matrix failed: {body}")
    return body["result"]


def render(matrix: dict) -> str:
    lines: list[str] = []
    total = matrix["total"]
    low = matrix["low_confidence_band"]
    thr = matrix["confidence_threshold"]

    lines.append("## 复核评估矩阵（总览）")
    lines.append("")
    lines.append(f"- 复核任务数：**{matrix['sample_count']}**")
    lines.append(
        f"- 动作分布：confirm {total['actions']['confirmed']} / "
        f"revise {total['actions']['revised']} / reject {total['actions']['rejected']}"
        f"（改判率 {total['revised_rate'] if total['revised_rate'] is not None else '-'}）"
    )
    lines.append(f"- 智能体异议（agent_request）触发：{total['agent_requests']}")
    lines.append("")

    lines.append("### 缺陷级矩阵（能力质量）")
    lines.append("")
    lines.append("| 指标 | 数量 | 含义 |")
    lines.append("|---|---|---|")
    lines.append(f"| added | {total['defects']['added']} | 人工补漏检（能力漏检） |")
    lines.append(f"| excluded | {total['defects']['excluded']} | 人工判误检（能力误报） |")
    lines.append(f"| reclassified | {total['defects']['reclassified']} | 类型改判 |")
    lines.append(f"| severity_changed | {total['defects']['severity_changed']} | 严重度改判 |")
    lines.append(f"| moved | {total['defects']['moved']} | 位置修正 |")
    lines.append(f"| removed | {total['defects']['removed']} | 硬删条目 |")
    lines.append("")

    lines.append(f"### 阈值对照（max_confidence < {thr} 转人工人群）")
    lines.append("")
    low_total = sum(low["actions"].values())
    if low_total:
        confirm_rate = low["actions"]["confirmed"] / low_total
        revised_rate = low["actions"]["revised"] / low_total
        lines.append(f"- 样本量：{low['tasks']}；人工确认率 {confirm_rate:.1%}；改判率 {revised_rate:.1%}")
        if confirm_rate >= 0.9:
            lines.append(
                f"- ⚠️ 确认率 ≥90%：阈值 {thr} 偏保守（大量本可自动放行的任务进了人工队列），"
                "可评估下调 confidence_threshold"
            )
        elif revised_rate >= 0.4:
            lines.append(
                f"- ⚠️ 改判率 ≥40%：阈值 {thr} 偏激进（低置信结论人工常不认可），"
                "需复核能力输出质量或收紧 need_review 规则"
            )
        else:
            lines.append("- 阈值与人工口径基本对齐")
    else:
        lines.append("- 该人群暂无样本（无 max_confidence < 阈值的复核任务）")
    lines.append("")

    by_scene = matrix["by_scene"]
    if by_scene:
        lines.append("### 分场景矩阵")
        lines.append("")
        lines.append("| 场景 | 复核数 | confirm | revise | reject | added | excluded | 改判率 |")
        lines.append("|---|---|---|---|---|---|---|---|")
        for scene, bucket in sorted(by_scene.items()):
            lines.append(
                f"| {scene} | {bucket['tasks']} | {bucket['actions']['confirmed']} "
                f"| {bucket['actions']['revised']} | {bucket['actions']['rejected']} "
                f"| {bucket['defects']['added']} | {bucket['defects']['excluded']} "
                f"| {bucket['revised_rate'] if bucket['revised_rate'] is not None else '-'} |"
            )

    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="人工复核留痕聚合报表（评估矩阵）")
    ap.add_argument("--base-url", default="http://127.0.0.1:8000")
    ap.add_argument("--limit", type=int, default=500, help="聚合最近 N 条复核留痕")
    args = ap.parse_args()

    try:
        matrix = fetch(args.base_url, args.limit)
    except Exception as exc:  # noqa: BLE001
        print(f"获取复核矩阵失败：{exc}", file=sys.stderr)
        return 1

    print(render(matrix))
    RESULT_PATH.write_text(json.dumps(matrix, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n原始数据已落盘：{RESULT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
