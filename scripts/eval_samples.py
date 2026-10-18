#!/usr/bin/env python3
"""样本回归评估（共识轮验收资产）：把 docs/image/ 带标注样本全量跑 ReAct 轨，输出判级矩阵。

用法：
    python scripts/eval_samples.py                     # 全量样本，默认 http://127.0.0.1:8000
    python scripts/eval_samples.py --samples class1_1  # 只跑指定样本（文件名前缀匹配）
    python scripts/eval_samples.py --base-url http://... --timeout 600
    python scripts/eval_samples.py --check             # 金样本期望断言（边界集+保留集）

每个样本以 mode=agent 派发（场景由智能体自主判定），轮询至终态
（completed / failed / awaiting_review），收集状态、判级、缺陷数、
是否触发智能体异议（triggered_rules 含 agent_request）。
结果打印 Markdown 矩阵并落盘 scripts/eval_results.json。

期望断言（--check，金样本回归）：期望文件 config/eval/samples.yaml 描述每个
样本的应然终态（status/scene/grade_level/agent_request 子集）；逐样本对照，
全部通过退出码 0，任一违规打印明细并以退出码 2 结束。无期望的样本仅记录
为基线（不预判对错）。接入真实算法能力后需按真实标注重新标定期望。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))

from eval_expectations import (  # noqa: E402
    check_row,
    load_expectations,
    matched_sample_name,
)

SAMPLES_DIR = Path(__file__).resolve().parent.parent / "docs" / "image"
RESULT_PATH = Path(__file__).resolve().parent / "eval_results.json"
TERMINAL = {"completed", "failed", "awaiting_review"}


def submit_task(base_url: str, image: Path) -> str:
    with image.open("rb") as fh:
        resp = requests.post(
            f"{base_url}/api/v1/tasks",
            data={"mode": "agent"},
            files={"files": (image.name, fh, "image/jpeg")},
            timeout=30,
        )
    resp.raise_for_status()
    body = resp.json()
    if body.get("code") != 0:
        raise RuntimeError(f"submit failed: {body}")
    return body["result"]["task_id"]


def wait_terminal(base_url: str, task_id: str, timeout: float) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        resp = requests.get(f"{base_url}/api/v1/tasks/{task_id}", timeout=30)
        resp.raise_for_status()
        detail = resp.json()["result"]
        if detail.get("status") in TERMINAL:
            return detail
        time.sleep(8)
    raise TimeoutError(f"{task_id} 未在 {timeout:.0f}s 内落定")


def collect(image: Path, base_url: str, timeout: float) -> dict:
    started = time.monotonic()
    row: dict = {"sample": image.stem, "task_id": "", "status": "submit_failed",
                 "scene": None, "grade_level": None, "defect_count": 0,
                 "agent_request": False, "review_reason": "", "grade_summary": "",
                 "error": "", "elapsed_s": 0}
    try:
        task_id = submit_task(base_url, image)
        row["task_id"] = task_id
        detail = wait_terminal(base_url, task_id, timeout)
        grade = detail.get("grade") or {}
        rules = grade.get("triggered_rules") or []
        review = detail.get("review") or {}
        row.update(
            status=detail.get("status"),
            scene=detail.get("scene"),
            grade_level=grade.get("level"),
            defect_count=len(detail.get("standardized") or []),
            agent_request="agent_request" in rules,
            grade_summary=grade.get("summary") or "",
            error=(detail.get("error") or {}).get("message") or "",
        )
        # 异议理由：判级摘要里截取（格式固定为"智能体异议，请求人工复核：<理由>"）
        summary = row["grade_summary"]
        if row["agent_request"] and "：" in summary:
            row["review_reason"] = summary.split("：", 1)[1]
        _ = review  # 复核决定详情（如需）可在此展开
    except Exception as exc:  # noqa: BLE001 - 单样本失败不中断矩阵
        row["error"] = str(exc)
    row["elapsed_s"] = round(time.monotonic() - started, 1)
    return row


def main() -> int:
    ap = argparse.ArgumentParser(description="带标注样本 ReAct 轨回归评估")
    ap.add_argument("--base-url", default="http://127.0.0.1:8000")
    ap.add_argument("--timeout", type=float, default=600, help="单样本等待终态超时（秒）")
    ap.add_argument("--samples", nargs="*", default=None, help="样本名前缀过滤（如 class1_1）")
    ap.add_argument(
        "--check",
        action="store_true",
        help="金样本期望断言（config/eval/samples.yaml）：违规时以退出码 2 结束",
    )
    args = ap.parse_args()

    images = sorted(p for p in SAMPLES_DIR.iterdir() if p.suffix.lower() in {".jpg", ".jpeg", ".png"})
    if args.samples:
        images = [p for p in images if any(p.stem.startswith(s) for s in args.samples)]
    if not images:
        print("无可评估样本", file=sys.stderr)
        return 1

    expectations = load_expectations() if args.check else {}

    print(f"评估 {len(images)} 个样本（mode=agent，逐个串行执行）…\n")
    rows: list[dict] = []
    for i, image in enumerate(images, 1):
        print(f"[{i}/{len(images)}] {image.name} …", flush=True)
        row = collect(image, args.base_url, args.timeout)
        row["expect_violations"] = []
        if args.check:
            name = matched_sample_name(row["sample"], expectations)
            if name is not None:
                row["expect_violations"] = check_row(expectations[name], row)
        rows.append(row)
        verdict = ""
        if args.check:
            verdict = " ✅" if not row["expect_violations"] else " ❌ " + "；".join(row["expect_violations"])
        print(f"    → status={row['status']} grade={row['grade_level']} "
              f"defects={row['defect_count']} agent_request={row['agent_request']} "
              f"({row['elapsed_s']}s){verdict}")

    # ---- Markdown 判级矩阵 ----
    print("\n## 判级矩阵")
    print("| 样本 | 任务 | 状态 | 场景 | 判级 | 缺陷数 | 智能体异议 | 判级摘要/错误 | 耗时s |" + (" 期望 |" if args.check else ""))
    print("|---|---|---|---|---|---|---|---|---|" + ("---|" if args.check else ""))
    for r in rows:
        note = r["grade_summary"] if r["grade_summary"] else r["error"]
        note = (note[:60] + "…") if len(note) > 60 else note
        extra = ""
        if args.check:
            if r["expect_violations"]:
                extra = " ❌ " + "；".join(r["expect_violations"])
            elif matched_sample_name(r["sample"], expectations):
                extra = " ✅"
            else:
                extra = " 基线"
        print(f"| {r['sample']} | {r['task_id'] or '-'} | {r['status']} | {r['scene'] or '-'} "
              f"| {r['grade_level'] or '-'} | {r['defect_count']} | "
              f"{'✅' if r['agent_request'] else '-'} | {note} | {r['elapsed_s']} |{extra} |")

    RESULT_PATH.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n结果已落盘：{RESULT_PATH}")

    if args.check:
        violations = [(r["sample"], r["expect_violations"]) for r in rows if r["expect_violations"]]
        if violations:
            print(f"\n金样本期望断言失败（{len(violations)} 个样本违规）：", file=sys.stderr)
            for sample, v in violations:
                for line in v:
                    print(f"  - {sample}: {line}", file=sys.stderr)
            return 2
        checked = sum(1 for r in rows if matched_sample_name(r["sample"], expectations))
        print(f"\n金样本期望断言通过：{checked}/{len(rows)} 个样本参与断言（其余为基线记录）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
