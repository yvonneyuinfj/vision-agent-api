"""多图 fan-out 回归（2026-09-16 生产事故：多图任务只检了第一张）。

根因：真实工具为单图契约（manifest input_required.images=1），平台把
全部图片塞进同一次请求，工具静默丢弃第一张之外的图。修复：单图契约
+ 多图输入 -> InferTool 逐图调用（治理每图计数）+ 合并响应；
任一图失败整体失败（带图名定位，绝不静默丢图）。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from app.agents.tools.infer_tool import InferTool, _merge_fanout_payloads  # noqa: E402
from app.models.capability import CapabilityManifest  # noqa: E402


def _manifest(images: int = 1) -> CapabilityManifest:
    return CapabilityManifest(
        capability_id="cap_v1",
        tool_name="fpi_defect_detection",
        scene="fpi",
        endpoint="http://127.0.0.1:18016",
        adapter_type="http",
        input_required={"images": images, "image_field": "image"},
    )


class _RecorderAdapter:
    """记录每次调用的图片数，按脚本返回 payload。"""

    tool_name = "fpi_defect_detection"
    scene = "fpi"

    def __init__(self, script: list[dict]) -> None:
        self._script = list(script)
        self.calls: list[list[str]] = []

    async def health(self) -> tuple[bool, str]:
        return True, "ok"

    async def invoke(self, ctx, image_paths, params, *, auxiliary=None):  # noqa: ANN001
        self.calls.append(list(image_paths))
        step = self._script.pop(0) if self._script else {}
        from app.capabilities.adapters.base import RawToolResponse

        if step.get("fail"):
            return RawToolResponse(ok=False, code=step["fail"], message=step.get("msg", "fail"), payload={})
        return RawToolResponse(
            ok=True, code=0, message="success",
            payload={"code": 0, "result": step.get("result", {}), "artifacts": step.get("artifacts", [])},
        )


class _StubRegistry:
    def __init__(self, manifest, adapter) -> None:
        self._manifest = manifest
        self._adapter = adapter

    def get(self, capability_id):  # noqa: ANN001
        return self._manifest

    def get_by_tool_name(self, tool_name):  # noqa: ANN001
        return self._manifest

    def list(self):
        return [self._manifest]

    def list_enabled(self):
        return [self._manifest]

    async def adapter_for(self, capability_id):  # noqa: ANN001
        return self._adapter


def _ok_result(defects, status="abnormal", false_count=0):
    return {"status": status, "summary": f"检出{len(defects)}处", "defects": defects,
            "false_indication_count": false_count}


def _inputs(paths: list[str]) -> dict:
    return {"task_id": "T1", "trace_id": "tr-1", "image_paths": paths,
            "auxiliary_paths": {}, "parameters": {}}


# ---------------------------------------------------------------------------
# _merge_fanout_payloads：纯合并逻辑
# ---------------------------------------------------------------------------
def test_merge_fanout_payloads_defects_and_artifacts():
    """缺陷拼接 + 来源图名 + 全局连号；artifacts 带 __<stem> 后缀。"""
    payloads = [
        ("a.jpg", {"code": 0, "result": _ok_result([
            {"defect_type": "BP", "confidence": 0.8, "location": {"bbox": [1, 2, 3, 4]}}]),
            "artifacts": [{"name": "result_image", "mime_type": "image/jpeg", "uri": "/u/a.png"}],
            "performance": {"processing_time_ms": 100}}),
        ("b.jpg", {"code": 0, "result": _ok_result([
            {"defect_type": "SP", "confidence": 0.6, "description": "清晰真缺陷"}],
            false_count=2),
            "artifacts": [{"name": "result_image", "mime_type": "image/jpeg", "uri": "/u/b.png"}],
            "performance": {"processing_time_ms": 50}}),
    ]
    merged = _merge_fanout_payloads(payloads)

    result = merged["result"]
    assert result["status"] == "abnormal"
    assert result["summary"] == "检测到2处疑似缺陷（2/2张图异常）"
    assert result["image_count"] == 2
    assert result["false_indication_count"] == 2  # 数值求和
    # 缺陷跨图连号 + 来源图名 + description 前缀
    d0, d1 = result["defects"]
    assert d0["defect_id"] == "D001" and d0["image_name"] == "a.jpg"
    assert d1["defect_id"] == "D002" and d1["image_name"] == "b.jpg"
    assert d1["description"].startswith("b.jpg：")
    # artifacts 图名后缀（报告逐图配对 + 落盘不冲突）
    assert merged["artifacts"][0]["name"] == "result_image__a"
    assert merged["artifacts"][1]["name"] == "result_image__b"
    assert merged["performance"]["processing_time_ms"] == 150


def test_merge_fanout_payloads_all_normal():
    """全部图无缺陷：normal + 汇总 summary。"""
    payloads = [
        ("a.jpg", {"code": 0, "result": _ok_result([], status="normal")}),
        ("b.jpg", {"code": 0, "result": _ok_result([], status="normal")}),
    ]
    merged = _merge_fanout_payloads(payloads)
    assert merged["result"]["status"] == "normal"
    assert merged["result"]["summary"] == "未发现明显异常"


# ---------------------------------------------------------------------------
# InferTool.execute：fan-out 触发、失败语义、单图契约透传
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_fanout_single_image_contract_multi_input():
    """单图契约（images=1）+ 多图输入：逐图调用（每次只传 1 张）+ 合并。"""
    script = [
        {"result": _ok_result([{"defect_type": "BP", "confidence": 0.8,
                                "location": {"bbox": [1, 2, 3, 4]}}])},
        {"result": _ok_result([])},
        {"result": _ok_result([{"defect_type": "SP", "confidence": 0.7,
                                "location": {"bbox": [5, 6, 7, 8]}}])},
    ]
    adapter = _RecorderAdapter(script)
    tool = InferTool(_manifest(), _StubRegistry(_manifest(), adapter))

    result = await tool.execute(_inputs(["/tmp/x/a.jpg", "/tmp/x/b.jpg", "/tmp/x/c.jpg"]))

    assert result.ok is True
    # 事故核心断言：每张图各一次调用（不是一次塞三张）
    assert adapter.calls == [["/tmp/x/a.jpg"], ["/tmp/x/b.jpg"], ["/tmp/x/c.jpg"]]
    raw = result.result["raw_result"]
    assert raw["result"]["image_count"] == 3
    assert len(raw["result"]["defects"]) == 2
    assert {d["image_name"] for d in raw["result"]["defects"]} == {"a.jpg", "c.jpg"}


@pytest.mark.asyncio
async def test_fanout_multi_image_contract_no_split():
    """多图契约（images=3）：一次调用传全部图（不拆分，工具自支持多图）。"""
    adapter = _RecorderAdapter([{"result": _ok_result([])}])
    tool = InferTool(_manifest(images=3), _StubRegistry(_manifest(images=3), adapter))

    result = await tool.execute(_inputs(["/tmp/a.jpg", "/tmp/b.jpg"]))

    assert result.ok is True
    assert adapter.calls == [["/tmp/a.jpg", "/tmp/b.jpg"]]


@pytest.mark.asyncio
async def test_fanout_single_image_passthrough():
    """单图输入：不触发 fan-out，原一次调用路径（含 images=1 契约）。"""
    adapter = _RecorderAdapter([{"result": _ok_result([])}])
    tool = InferTool(_manifest(), _StubRegistry(_manifest(), adapter))

    result = await tool.execute(_inputs(["/tmp/a.jpg"]))

    assert result.ok is True
    assert adapter.calls == [["/tmp/a.jpg"]]


@pytest.mark.asyncio
async def test_fanout_any_image_failure_fails_whole_task():
    """任一图失败整体失败（带图名定位），绝不静默丢图。"""
    script = [
        {"result": _ok_result([])},
        {"fail": 3002, "msg": "inference failed"},
    ]
    adapter = _RecorderAdapter(script)
    tool = InferTool(_manifest(), _StubRegistry(_manifest(), adapter))

    result = await tool.execute(_inputs(["/tmp/a.jpg", "/tmp/b.jpg"]))

    assert result.ok is False
    assert result.code == int(3002) or result.code == 3002
    assert "b.jpg" in result.message  # 图名定位失败图


@pytest.mark.asyncio
async def test_fanout_normalizer_keeps_image_name():
    """合并结果经 normalize_defects：image_name 溯源字段保留到 standardized。"""
    from app.capabilities.normalizers import normalize_result

    merged = _merge_fanout_payloads([
        ("a.jpg", {"code": 0, "result": _ok_result([
            {"defect_type": "BP", "confidence": 0.8, "location": {"bbox": [1, 2, 3, 4]}}])}),
        ("b.jpg", {"code": 0, "result": _ok_result([
            {"defect_type": "SP", "confidence": 0.6}])}),
    ])
    entries, family = normalize_result("defect_detection", merged["result"])
    assert family == "defect"
    assert [e["image_name"] for e in entries] == ["a.jpg", "b.jpg"]
