"""任务详情序列化回归：report.artifacts 必须随 ReportRef 暴露。

曾发生：DB 的 report_json 已含本地化产物（原图/标注图 URI），但 ReportRef
schema 无 artifacts 字段，pydantic 序列化默默丢弃 → 前端报告页拿不到标注图，
「原图 vs 缺陷标注图」对比无从渲染。
"""
from app.api.v1.task import _to_detail

_DATA = {
    "task_id": "T1",
    "trace_id": None,
    "input_uris": ["/api/v1/tasks/_upload/files/a.jpg?kind=inputs"],
    "scene": "fpi",
    "mode": "single",
    "status": "completed",
    "capability_used": "fpi_defect_detection",
    "created_at": "2026-09-15T06:54:33+00:00",
    "updated_at": "2026-09-15T06:58:02+00:00",
    "finished_at": "2026-09-15T06:58:02+00:00",
    "steps": [],
    "standardized": [],
    "grade": None,
    "knowledge": [],
    "review": None,
    "report": {
        "report_type": "agent_report",
        "name": "T1_report.md",
        "uri": "/api/v1/tasks/T1/files/report.md?kind=reports",
        "version": "1.0.0",
        "created_at": "2026-09-15T14:58:02+08:00",  # 落库冗余键：序列化容忍
        "artifacts": [
            {"name": "artifact_original_image", "mime": "image/jpeg",
             "uri": "/api/v1/tasks/T1/files/original.jpg?kind=artifacts"},
            {"name": "artifact_result_image", "mime": "image/jpeg",
             "uri": "/api/v1/tasks/T1/files/result-image.jpg?kind=artifacts"},
        ],
        "analysis_lead": "lead",
    },
    "error": None,
    "parent_task_id": None,
    "children": [],
}


def test_to_detail_exposes_report_artifacts():
    detail = _to_detail(dict(_DATA))
    dumped = detail.model_dump()
    arts = dumped["report"]["artifacts"]
    assert [a["name"] for a in arts] == ["artifact_original_image", "artifact_result_image"]
    assert arts[1]["uri"] == "/api/v1/tasks/T1/files/result-image.jpg?kind=artifacts"
    assert arts[0]["mime"] == "image/jpeg"


def test_to_detail_report_without_artifacts_defaults_empty():
    data = dict(_DATA)
    data["report"] = {
        "report_type": "agent_report",
        "name": "T1_report.md",
        "uri": "/api/v1/tasks/T1/files/report.md?kind=reports",
        "version": "1.0.0",
    }
    detail = _to_detail(data)
    assert detail.model_dump()["report"]["artifacts"] == []
