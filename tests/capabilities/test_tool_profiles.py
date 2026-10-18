"""工具响应 profile 回归：偏差外壳归一（联调适配 #1）。

真实交付工具 blade_defect_detector（荧光渗透）返回 data 外壳而非规范 result 外壳；
apply_tool_profile 必须把它无损增补为 canonical 形态，且对规范外壳（mock）零影响。
"""
from __future__ import annotations

import httpx
import pytest

from app.agents.tools.infer_tool import _artifact_filename
from app.capabilities.adapters.base import AdapterContext
from app.capabilities.adapters.http_adapter import HttpToolAdapter
from app.capabilities.adapters.tool_profiles import (
    apply_tool_profile,
    transform_blade_defect_detector,
)
from app.core.errors import BusinessError
from app.models.capability import CapabilityManifest


def _blade_envelope(*, decision: str = "unqualified", detections: list | None = None) -> dict:
    """按交付工具 docs/API_EXAMPLES.md 的实测响应形状构造（2026-09-15 本地验证）。"""
    if detections is None:
        detections = [
            {"class_id": 2, "class_name": "FI", "class_description": "虚假荧光显示",
             "confidence": 0.857, "bbox": [1525.6, 2163.8, 1641.7, 2288.7]},
            {"class_id": 0, "class_name": "BP", "class_description": "模糊的真缺陷",
             "confidence": 0.828, "bbox": [1311.2, 2742.2, 1665.7, 2838.8]},
            {"class_id": 1, "class_name": "SP", "class_description": "清晰真缺陷",
             "confidence": 0.784, "bbox": [1600.6, 2642.1, 1695.7, 2833.6]},
        ]
    true_count = sum(1 for d in detections if d["class_id"] in (0, 1))
    return {
        "code": 0,
        "message": "success",
        "trace_id": "REQ_X",
        "task_id": "T1",
        "data": {
            "tool_name": "blade_defect_detector",
            "tool_version": "1.0.0",
            "api_version": "1.0.0",
            "model_name": "industrial_defect_yolox_s",
            "model_version": "best_ckpt@sha256:4F2C",
            "model_device": "cpu",
            "task_id": "T1",
            "part_id": "PART_001",
            "device_id": "MCP_001",
            "timestamp": "2026-09-05T14:30:00+08:00",
            "image_name": "blade.jpg",
            "model_event_count": len(detections),
            "true_defect_count": true_count,
            "is_defective": decision == "unqualified",
            "business_decision": decision,
            "detections": detections,
            "inference_time_ms": 5094.6,
            "class_definitions": [
                {"class_id": 0, "class_name": "BP", "description": "模糊的真缺陷", "is_true_defect": True},
                {"class_id": 1, "class_name": "SP", "description": "清晰真缺陷", "is_true_defect": True},
                {"class_id": 2, "class_name": "FI", "description": "虚假荧光显示", "is_true_defect": False},
            ],
            "artifacts": [
                {"artifact_id": "A1", "artifact_type": "original_image", "media_type": "image/jpeg",
                 "uri": "http://127.0.0.1:8000/api/v1/detections/REQ_X/original"},
                {"artifact_id": "A4", "artifact_type": "report_pdf", "media_type": "application/pdf",
                 "uri": "http://127.0.0.1:8000/api/v1/detections/REQ_X/report?token=abc"},
            ],
        },
    }


def test_blade_unqualified_maps_to_canonical_defects():
    """BP/SP 进 defects、FI 只计数；status/summary/产物/性能全部归一。"""
    # Act
    out = apply_tool_profile(_blade_envelope())

    # Assert：canonical result
    result = out["result"]
    assert result["status"] == "abnormal"
    assert [d["defect_type"] for d in result["defects"]] == ["BP", "SP"]
    assert result["defects"][0]["defect_id"] == "D001"
    assert result["defects"][0]["defect_name_cn"] == "模糊的真缺陷"
    assert result["defects"][0]["severity"] is None  # 工具未提供，不伪造
    # location 平台契约（对齐 mock 服务）：type/bbox 供前端 DefectOverlay 绘制框，
    # bbox_xyxy 保留工具原字段名供追溯。只给 bbox_xyxy 会让「算法标注图」画不出框。
    assert result["defects"][0]["location"] == {
        "type": "bbox",
        "bbox": [1311.2, 2742.2, 1665.7, 2838.8],
        "bbox_xyxy": [1311.2, 2742.2, 1665.7, 2838.8],
    }
    assert result["false_indication_count"] == 1
    assert "2 处真缺陷" in result["summary"] and "虚假荧光显示" in result["summary"]
    # Assert：透传字段
    assert result["business_decision"] == "unqualified"
    assert result["model_version"] == "best_ckpt@sha256:4F2C"
    assert result["part_id"] == "PART_001"
    # Assert：artifacts 归一（InferTool 消费键：name/mime_type/uri）
    assert [a["name"] for a in out["artifacts"]] == ["original_image", "report_pdf"]
    assert out["artifacts"][1]["mime_type"] == "application/pdf"
    # Assert：performance 合成 + 原 data 无损保留
    assert out["performance"] == {"processing_time_ms": 5094.6}
    assert out["data"]["detections"] == _blade_envelope()["data"]["detections"]
    assert out["code"] == 0 and out["task_id"] == "T1"


def test_blade_qualified_fi_only_is_normal_without_defects():
    """仅 FI（虚假显示）→ status=normal、defects 空，不误报缺陷。"""
    env = _blade_envelope(
        decision="qualified",
        detections=[
            {"class_id": 2, "class_name": "FI", "class_description": "虚假荧光显示",
             "confidence": 0.9, "bbox": [1.0, 2.0, 3.0, 4.0]},
        ],
    )
    result = apply_tool_profile(env)["result"]
    assert result["status"] == "normal"
    assert result["defects"] == []
    assert result["false_indication_count"] == 1
    assert "虚假荧光显示" in result["summary"]


def test_blade_no_events_summary():
    result = apply_tool_profile(_blade_envelope(decision="qualified", detections=[]))["result"]
    assert result["status"] == "normal"
    assert result["summary"] == "未发现明显异常"


def test_class_definitions_drive_true_defect_set():
    """真缺陷集合以工具自报 class_definitions 为准（防硬编码漂移）。"""
    env = _blade_envelope(detections=[
        {"class_id": 0, "class_name": "BP", "class_description": "x", "confidence": 0.8, "bbox": [0, 0, 1, 1]},
    ])
    # 假设工具未来把 class 0 改为不计真缺陷
    env["data"]["class_definitions"][0]["is_true_defect"] = False
    result = apply_tool_profile(env)["result"]
    assert result["defects"] == []
    assert result["false_indication_count"] == 1


def test_class_definitions_passthrough_and_description_enrichment():
    """class_definitions 清洗后随 result 透传（报告二/三节与前端图例数据源）；
    缺陷 description 吸收中文类别描述（脱离定义表也可读）。"""
    result = apply_tool_profile(_blade_envelope())["result"]
    assert result["class_definitions"] == [
        {"class_id": 0, "class_name": "BP", "description": "模糊的真缺陷", "is_true_defect": True},
        {"class_id": 1, "class_name": "SP", "description": "清晰真缺陷", "is_true_defect": True},
        {"class_id": 2, "class_name": "FI", "description": "虚假荧光显示", "is_true_defect": False},
    ]
    assert result["defects"][0]["description"] == "荧光渗透显示 BP（模糊的真缺陷，置信度 0.83）"


def test_blade_invalid_bbox_degrades_to_none():
    """bbox 形态非法（非四元组/非数值）时降级 None：条目保留，前端不画脏框。"""
    env = _blade_envelope(detections=[
        {"class_id": 0, "class_name": "BP", "class_description": "x",
         "confidence": 0.8, "bbox": [1, 2]},
    ])
    result = apply_tool_profile(env)["result"]
    assert len(result["defects"]) == 1
    assert result["defects"][0]["location"]["bbox"] is None
    assert result["defects"][0]["location"]["type"] == "bbox"


def test_compliant_envelope_passes_through_untouched():
    """规范外壳（顶层 result，mock 形态）必须原样透传（同一对象）。"""
    compliant = {"code": 0, "message": "success", "task_id": "T", "trace_id": "R",
                 "result": {"status": "abnormal", "summary": "s", "defects": []},
                 "artifacts": [{"name": "annotated", "mime_type": "image/png", "uri": "/artifacts/x.png"}]}
    assert apply_tool_profile(compliant) is compliant


def test_unknown_tool_and_malformed_pass_through():
    assert apply_tool_profile({"code": 0, "data": {"tool_name": "other_tool"}}) == {
        "code": 0, "data": {"tool_name": "other_tool"}}
    assert apply_tool_profile({"code": 1003, "message": "bad image", "data": None}) == {
        "code": 1003, "message": "bad image", "data": None}
    assert apply_tool_profile("not-a-dict") == "not-a-dict"


# ---------- http_adapter 集成 ----------

class _BladeFakeResponse:
    def __init__(self, body: dict, status_code: int = 200) -> None:
        self._body = body
        self.status_code = status_code

    def json(self):
        return self._body


class _BladeFakeClient:
    def __init__(self, body: dict, status_code: int = 200) -> None:
        self._body = body
        self._status = status_code

    async def post(self, url, data=None, files=None, timeout=None, headers=None):  # noqa: ANN001
        return _BladeFakeResponse(self._body, self._status)

    async def aclose(self) -> None:
        return None


def _make_adapter() -> HttpToolAdapter:
    manifest = CapabilityManifest(
        capability_id="fpi_defect_detection_v1",
        tool_name="fpi_defect_detector",
        scene="fpi",
        endpoint="http://127.0.0.1:9",
    )
    return HttpToolAdapter(manifest)


async def test_adapter_invoke_normalizes_blade_envelope(tmp_path):
    """经 invoke 全链：RawToolResponse.payload 已含 canonical result/artifacts。"""
    adapter = _make_adapter()
    await adapter._client.aclose()
    adapter._client = _BladeFakeClient(_blade_envelope())
    image = tmp_path / "probe.jpg"
    image.write_bytes(b"fake-image")
    ctx = AdapterContext(task_id="T1", trace_id="tr", timeout_s=5.0, retry_times=0)

    resp = await adapter.invoke(ctx, [str(image)], {})

    assert resp.ok is True
    assert resp.performance_ms == 5094.6
    assert resp.payload["result"]["status"] == "abnormal"
    assert len(resp.payload["result"]["defects"]) == 2
    assert resp.payload["artifacts"][0]["uri"].startswith("http://")


async def test_adapter_error_envelope_still_raises(tmp_path):
    """业务错误外壳（data=null）不受 profile 影响，仍按 HTTP 状态映射抛错。"""
    adapter = _make_adapter()
    await adapter._client.aclose()
    adapter._client = _BladeFakeClient(
        {"code": 1003, "message": "Invalid image file", "trace_id": "R", "task_id": "T", "data": None},
        status_code=400,
    )
    image = tmp_path / "probe.jpg"
    image.write_bytes(b"fake-image")
    ctx = AdapterContext(task_id="T1", trace_id="tr", timeout_s=5.0, retry_times=0)

    with pytest.raises(BusinessError) as exc_info:
        await adapter.invoke(ctx, [str(image)], {})
    assert "Invalid image file" in str(exc_info.value)


# ---------- InferTool 产物文件名推导 ----------

def _fake_resp(content_type: str) -> httpx.Response:
    return httpx.Response(200, headers={"content-type": content_type})


def test_artifact_filename_derivation():
    resp = _fake_resp("image/jpeg")
    # 无扩展名 + 带签名 query
    assert _artifact_filename("http://h/api/v1/detections/R/original?token=a.b", resp) == "original.jpg"
    assert _artifact_filename("http://h/api/v1/detections/R/report", _fake_resp("application/pdf")) == "report.pdf"
    assert _artifact_filename("http://h/api/v1/detections/R/result-json", _fake_resp("application/json")) == "result-json.json"
    # 已有扩展名保持不变（mock 形态）
    assert _artifact_filename("/artifacts/T/annotated.png", resp) == "annotated.png"
    # 未知 Content-Type 兜底 .bin（save_bytes 会拒绝 → 优雅降级不崩任务）
    assert _artifact_filename("http://h/x/foo", _fake_resp("application/octet-stream")) == "foo.bin"


def test_transform_is_pure_function():
    """转换不改动入参（原响应可追溯）。"""
    env = _blade_envelope()
    snapshot = repr(env)
    transform_blade_defect_detector(env)
    assert repr(env) == snapshot
