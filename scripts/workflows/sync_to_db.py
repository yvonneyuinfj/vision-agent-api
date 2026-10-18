#!/usr/bin/env python3
"""将 harness 原生质检平台的全部文件留痕同步到 SQLite（data/qc.db）。

数据来源（全量，含二进制）：
- .dsh/workflow-runs/<runId>/run.json      -> runs（含完整 run_json 快照）/ reports / tasks 判级复核字段
- .dsh/workflow-runs/<runId>/events.jsonl  -> run_events（全审计事件流，含子 Agent childId 链）
- .dsh/workflow-runs/*/artifacts/*.json    -> file_artifacts（qc-evidence，含内容 BLOB）
- data/workflow-tasks/QC-*.json            -> tasks（trace_id/工具三版本/耗时/原始 envelope）+ defects 明细
- data/workflow-tasks/QC-*.review.json     -> reviews（复核记录全字段）
- data/workflow-reports/*.md + artifacts/  -> reports（含正文 report_md）+ file_artifacts（标注图 BLOB）

幂等：INSERT OR REPLACE / UPSERT，可重复执行。零依赖（stdlib sqlite3）。
生产 MySQL：表结构可对照 scripts/sql/init.sql 权威 DDL 平移；二进制建议 MinIO（DB 存 path+sha256）。
"""
import hashlib
import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNS_DIR = ROOT / ".dsh/workflow-runs"
TASKS_DIR = ROOT / "data/workflow-tasks"
REPORTS_DIR = ROOT / "data/workflow-reports"
DB_PATH = ROOT / "data/qc.db"
BLOB_LIMIT = 8 * 1024 * 1024  # 8MB 以下文件内容直接入库

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs(
  run_id TEXT PRIMARY KEY, workflow TEXT, status TEXT, mode TEXT,
  started_at TEXT, ended_at TEXT, wall_clock_ms INTEGER,
  agents_started INTEGER, peak_concurrency INTEGER,
  pending_human_review INTEGER, knowledge_available INTEGER,
  summary TEXT, run_json TEXT);
CREATE TABLE IF NOT EXISTS tasks(
  task_id TEXT PRIMARY KEY, run_id TEXT, seq INTEGER, scene TEXT, image TEXT,
  trace_id TEXT, code INTEGER, tool_status TEXT, tool_summary TEXT,
  grade_level TEXT, final_level TEXT, review_action TEXT, defect_count INTEGER,
  processing_time_ms REAL, tool_name TEXT, tool_version TEXT, model_version TEXT, api_version TEXT,
  ledger_path TEXT, ledger_json TEXT);
CREATE TABLE IF NOT EXISTS defects(
  task_id TEXT, defect_id TEXT, defect_type TEXT, defect_name_cn TEXT,
  confidence REAL, severity TEXT, bbox TEXT,
  PRIMARY KEY(task_id, defect_id));
CREATE TABLE IF NOT EXISTS reviews(
  task_id TEXT PRIMARY KEY, action TEXT, final_level TEXT, opinion TEXT,
  reviewed_by TEXT, decided_at TEXT, mechanism TEXT);
CREATE TABLE IF NOT EXISTS reports(
  run_id TEXT PRIMARY KEY, report_path TEXT, task_count INTEGER,
  headline TEXT, annotated_saved INTEGER, report_md TEXT);
CREATE TABLE IF NOT EXISTS run_events(
  run_id TEXT, seq INTEGER, time_ms INTEGER, type TEXT, data_json TEXT,
  PRIMARY KEY(run_id, seq));
CREATE TABLE IF NOT EXISTS file_artifacts(
  path TEXT PRIMARY KEY, run_id TEXT, task_id TEXT, name TEXT,
  bytes INTEGER, sha256 TEXT, content BLOB);
CREATE INDEX IF NOT EXISTS idx_tasks_run ON tasks(run_id);
CREATE INDEX IF NOT EXISTS idx_trace ON tasks(trace_id);
"""


def jd(o):
    return json.dumps(o, ensure_ascii=False)


def put_artifact(db, path, run_id=None, task_id=None):
    p = Path(path)
    if not p.exists():
        return
    data = p.read_bytes()
    blob = data if len(data) <= BLOB_LIMIT else None
    db.execute(
        "INSERT OR REPLACE INTO file_artifacts VALUES(?,?,?,?,?,?,?)",
        (str(p.relative_to(ROOT)), run_id, task_id, p.name, len(data),
         hashlib.sha256(data).hexdigest(), sqlite3.Binary(blob) if blob else None))


def main():
    db = sqlite3.connect(DB_PATH)
    db.executescript(SCHEMA)

    # 1) runs / reports / tasks(判级) / events / evidence artifacts
    for rj in sorted(RUNS_DIR.glob("run-*/run.json")):
        raw = rj.read_text()
        d = json.loads(raw)
        rid = d.get("runId") or rj.parent.name
        p = d.get("process") or {}
        c = d.get("cost") or {}
        r = d.get("result") or {}
        ka = r.get("knowledge_available")
        db.execute("INSERT OR REPLACE INTO runs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            rid, d.get("workflow"), d.get("status"), r.get("mode"),
            p.get("startedAt"), p.get("updatedAt"), c.get("wallClockDurationMs"),
            c.get("agentsStarted"), c.get("peakConcurrency"),
            r.get("pending_human_review"), None if ka is None else int(ka),
            r.get("summary"), raw))
        rep = r.get("report")
        if rep and rep.get("report_path"):
            rp = ROOT / rep["report_path"]
            md = rp.read_text() if rp.exists() else None
            db.execute("INSERT OR REPLACE INTO reports VALUES(?,?,?,?,?,?)", (
                rid, rep.get("report_path"), rep.get("task_count"),
                rep.get("headline"), rep.get("annotated_saved"), md))
        for t in (r.get("tasks") or []):
            tid = t.get("task_id")
            if not tid:
                continue
            n = tid.rsplit("-", 1)[-1]
            db.execute("""INSERT INTO tasks(task_id, run_id, seq, scene, grade_level,
                             final_level, review_action, defect_count)
                          VALUES(?,?,?,?,?,?,?,?)
                          ON CONFLICT(task_id) DO UPDATE SET run_id=excluded.run_id,
                            seq=excluded.seq, scene=excluded.scene,
                            grade_level=excluded.grade_level, final_level=excluded.final_level,
                            review_action=excluded.review_action,
                            defect_count=excluded.defect_count""",
                       (tid, rid, int(n) if n.isdigit() else None, t.get("scene"),
                        t.get("grade_level"), t.get("final_level"),
                        t.get("review_action"), t.get("defect_count")))
        ev = rj.parent / "events.jsonl"
        if ev.exists():
            for line in ev.read_text().splitlines():
                if not line.strip():
                    continue
                e = json.loads(line)
                db.execute("INSERT OR REPLACE INTO run_events VALUES(?,?,?,?,?)", (
                    rid, e.get("seq"), e.get("time"), e.get("type"), jd(e.get("data"))))
        for af in sorted((rj.parent / "artifacts").glob("*")):
            put_artifact(db, af, run_id=rid)

    # 2) ledgers（工具链路 envelope）+ defects 展开
    for f in sorted(TASKS_DIR.glob("QC-*.json")):
        if f.name.endswith(".review.json"):
            continue
        raw = f.read_text()
        d = json.loads(raw)
        tid = d.get("task_id")
        if not tid:
            continue
        run_id = tid[len("QC-"):].rsplit("-", 1)[0]
        tool = d.get("tool") or {}
        res = d.get("result") or {}
        defects = res.get("defects") or []
        db.execute("""INSERT INTO tasks(task_id, run_id, image, trace_id, code, tool_status,
                         tool_summary, defect_count, processing_time_ms, tool_name,
                         tool_version, model_version, api_version, ledger_path, ledger_json)
                      VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                      ON CONFLICT(task_id) DO UPDATE SET image=excluded.image,
                        trace_id=excluded.trace_id, code=excluded.code,
                        tool_status=excluded.tool_status, tool_summary=excluded.tool_summary,
                        defect_count=COALESCE(excluded.defect_count, tasks.defect_count),
                        processing_time_ms=excluded.processing_time_ms,
                        tool_name=excluded.tool_name, tool_version=excluded.tool_version,
                        model_version=excluded.model_version, api_version=excluded.api_version,
                        ledger_path=excluded.ledger_path, ledger_json=excluded.ledger_json""",
                   (tid, run_id, (d.get("input") or {}).get("image_name"),
                    d.get("trace_id"), d.get("code"), res.get("status"), res.get("summary"),
                    len(defects), (d.get("performance") or {}).get("processing_time_ms"),
                    tool.get("tool_name"), tool.get("tool_version"),
                    tool.get("model_version"), tool.get("api_version"),
                    str(f.relative_to(ROOT)), raw))
        for x in defects:
            loc = x.get("location") or {}
            bbox = loc.get("bbox") if isinstance(loc, dict) else None
            if bbox is None:
                bbox = x.get("bbox")
            db.execute("INSERT OR REPLACE INTO defects VALUES(?,?,?,?,?,?,?)", (
                tid, x.get("defect_id"), x.get("defect_type"), x.get("defect_name_cn"),
                x.get("confidence"), x.get("severity"), jd(bbox) if bbox is not None else None))
        for a in (d.get("artifacts") or []):
            for cand in (REPORTS_DIR / "artifacts").glob(f"{tid}-{a.get('name', '')}"):
                put_artifact(db, cand, run_id=run_id, task_id=tid)

    # 3) reviews
    for f in sorted(TASKS_DIR.glob("QC-*.review.json")):
        d = json.loads(f.read_text())
        db.execute("INSERT OR REPLACE INTO reviews VALUES(?,?,?,?,?,?,?)", (
            d.get("task_id"), d.get("action"), d.get("final_level"), d.get("opinion"),
            d.get("reviewed_by"), d.get("decided_at"), d.get("mechanism")))

    db.commit()
    print("=== 同步完成（data/qc.db）===")
    for t in ("runs", "tasks", "defects", "reviews", "reports", "run_events", "file_artifacts"):
        n = db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        print(f"  {t:14s} {n}")
    total_blob = db.execute("SELECT COUNT(*), COALESCE(SUM(bytes),0) FROM file_artifacts WHERE content IS NOT NULL").fetchone()
    print(f"  BLOB 入库: {total_blob[0]} 个文件 / {total_blob[1]/1048576:.1f} MB")
    db.close()


if __name__ == "__main__":
    main()
