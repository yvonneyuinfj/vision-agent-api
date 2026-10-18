#!/usr/bin/env python3
"""复核桥接服务：workflow 复核队列 <-> 前端复核页面（Qwen_html_20260908_mhf78k7na.html）。

架构（两段式父级中介 HITL 的前端页面形态）：
- run 内 reviewer 子 Agent 把待复核记录写入 data/review-queue/pending/<task_id>.json，
  然后有界轮询 data/review-queue/decisions/<task_id>.json 等待前端决定；
- 本服务为前端页面提供：队列读写 API + 原图/标注图托管 + 页面托管 + run 状态 API；
- 用户在页面提交决定 -> 原子写入 decisions/ -> reviewer 读到 -> run 内原生完成
  复核留痕（review.json）→ 综合研判 → 报告，全程无需聊天弹窗。

启动：.venv/bin/python scripts/workflows/review_bridge.py    → http://127.0.0.1:8010/
依赖：仅 .venv 已有的 FastAPI + uvicorn。队列目录不存在时自动创建。
"""
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parents[2]
QUEUE = ROOT / "data" / "review-queue"
PENDING = QUEUE / "pending"
DECISIONS = QUEUE / "decisions"
ORIG = ROOT / "docs" / "image"
ANN = ROOT / "data" / "workflow-reports" / "artifacts"
RUNS = ROOT / ".dsh" / "workflow-runs"
PAGE = ROOT / "Qwen_html_20260908_mhf78k7na.html"
ACTIONS = {"confirm", "revise", "reject", "requeue"}

for _d in (PENDING, DECISIONS):
    _d.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="QC Review Bridge", version="1.0")


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def safe_file(base: Path, name: str) -> Path:
    """防目录穿越：仅允许 base 下的 basename。"""
    p = (base / os.path.basename(name)).resolve()
    if not str(p).startswith(str(base.resolve()) + os.sep) and p.parent != base.resolve():
        raise HTTPException(400, "invalid name")
    return p


def mtime_iso(f: Path) -> str:
    return datetime.fromtimestamp(f.stat().st_mtime, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Decision(BaseModel):
    action: str
    opinion: str = ""
    reviewer: str = "frontend-user"


@app.get("/health")
def health():
    pend = {f.stem for f in PENDING.glob("*.json")}
    dec = {f.stem for f in DECISIONS.glob("*.json")}
    return {"ok": True, "pending": len(pend - dec), "decided": len(dec), "time": now_iso()}


@app.get("/")
def page():
    if not PAGE.exists():
        raise HTTPException(404, "page not found")
    return FileResponse(PAGE, headers={"Cache-Control": "no-store"})


@app.get("/api/reviews/pending")
def pending_list():
    dec = {f.stem for f in DECISIONS.glob("*.json")}
    items = []
    for f in sorted(PENDING.glob("*.json"), key=lambda x: x.stat().st_mtime):
        if f.stem in dec:
            continue
        try:
            d = json.loads(f.read_text())
        except Exception:
            continue
        items.append({
            "task_id": d.get("task_id", f.stem),
            "scene": d.get("scene"),
            "scene_zh": d.get("scene_zh"),
            "image": d.get("image"),
            "grade_summary": (d.get("grade") or {}).get("summary"),
            "defect_count": len(d.get("defects") or []),
            "created_at": mtime_iso(f),
        })
    return {"items": items}


@app.get("/api/reviews/decided")
def decided_list():
    items = []
    for f in sorted(DECISIONS.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True)[:20]:
        try:
            items.append(json.loads(f.read_text()))
        except Exception:
            continue
    return {"items": items}


@app.get("/api/reviews/{task_id}")
def review_detail(task_id: str):
    f = safe_file(PENDING, task_id + ".json")
    if not f.exists():
        raise HTTPException(404, "no pending review for " + task_id)
    d = json.loads(f.read_text())
    d["created_at"] = mtime_iso(f)
    return d


@app.post("/api/reviews/{task_id}/decision")
def submit_decision(task_id: str, dec: Decision):
    pf = safe_file(PENDING, task_id + ".json")
    if not pf.exists():
        raise HTTPException(404, "no pending review for " + task_id)
    if dec.action not in ACTIONS:
        raise HTTPException(400, "action must be one of " + "/".join(sorted(ACTIONS)))
    target = safe_file(DECISIONS, task_id + ".json")
    if target.exists():
        raise HTTPException(409, "decision already submitted for " + task_id)
    payload = {
        "task_id": task_id,
        "action": dec.action,
        "opinion": dec.opinion,
        "reviewer": dec.reviewer or "frontend-user",
        "decided_at": now_iso(),
    }
    fd, tmp = tempfile.mkstemp(dir=str(DECISIONS), suffix=".tmp")
    with os.fdopen(fd, "w") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, target)  # 原子落盘，reviewer 轮询不会读到半截文件
    return {"ok": True, **payload}


def _load_run(rj: Path):
    try:
        return json.loads(rj.read_text())
    except Exception:
        return None


@app.get("/api/runs/latest")
def runs_latest():
    best = None
    for rj in RUNS.glob("run-*/run.json"):
        d = _load_run(rj)
        if not d:
            continue
        started = (d.get("process") or {}).get("startedAt") or ""
        if best is None or started > best[0]:
            best = (started, d)
    if best is None:
        return {"run": None}
    d = best[1]
    p = d.get("process") or {}
    r = d.get("result") or {}
    phases = [{"name": i.get("title"), "status": i.get("status")}
              for i in p.get("items", []) if i.get("kind") == "phase"]
    return {"run": {
        "run_id": d.get("runId"), "status": d.get("status"), "mode": r.get("mode"),
        "started_at": p.get("startedAt"), "phases": phases,
        "progress": p.get("progress"),
        "headline": (r.get("report") or {}).get("headline"),
    }}


@app.get("/api/runs")
def runs_list():
    out = []
    for rj in sorted(RUNS.glob("run-*/run.json"), key=lambda x: x.stat().st_mtime, reverse=True):
        d = _load_run(rj)
        if not d:
            continue
        p = d.get("process") or {}
        r = d.get("result") or {}
        out.append({
            "run_id": d.get("runId"), "status": d.get("status"), "mode": r.get("mode"),
            "started_at": p.get("startedAt"), "tasks": len(r.get("tasks") or []),
            "wall_clock_ms": (d.get("cost") or {}).get("wallClockDurationMs"),
        })
    return {"items": out}


@app.get("/images/original/{name}")
def img_original(name: str):
    f = safe_file(ORIG, name)
    if not f.exists():
        raise HTTPException(404, "image not found")
    return FileResponse(f, headers={"Cache-Control": "no-store"})


@app.get("/images/annotated/{name}")
def img_annotated(name: str):
    f = safe_file(ANN, name)
    if not f.exists():
        raise HTTPException(404, "annotated not available (yet)")
    return FileResponse(f, headers={"Cache-Control": "no-store"})


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8010, log_level="warning", access_log=False)
