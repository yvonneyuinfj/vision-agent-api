"""能力写侧门禁纯函数单测（决策 29/31/37/40/43/46）。

不碰 DB、不碰 HTTP：门禁规则的回归全部在这里锁住，端点行为另有仓储用例。
"""
from __future__ import annotations

import pytest

from app.services.capability_gate import (
    ALLOWED_SCENES,
    append_change_log,
    mask_headers,
    merge_meta_patch,
    validate_meta,
    validate_response_sample,
)


def _sample(**over):
    base = {"code": 0, "result": {"status": "abnormal", "defects": [], "summary": "s"}}
    base.update(over)
    return base


# ---------- 样例门禁（决策 29/31） ----------
@pytest.mark.parametrize(
    "capability_type, result, expected",
    [
        ("defect_detection", {"status": "normal", "defects": []}, []),
        ("defect_detection", {"status": "normal"}, ["result 缺少 defects"]),
        ("measurement", {"status": "normal", "summary": "晶粒 3.2um"}, []),
        ("measurement", {"status": "normal"}, ["result 缺少 summary"]),
        ("classification", {"status": "normal", "summary": "沿晶"}, []),
        ("classification", {"status": "normal"}, ["result 缺少 summary"]),
    ],
)
def test_gate_branch_follows_capability_type(capability_type, result, expected):
    assert validate_response_sample({"code": 0, "result": result}, capability_type) == expected


def test_code_must_be_zero():
    assert validate_response_sample(_sample(code=3002), "defect_detection") == [
        "code 必须为 0（样例实测 3002）"
    ]


def test_defects_must_be_array_and_artifacts_need_name_uri():
    errors = validate_response_sample(
        _sample(result={"status": "normal", "defects": "oops"}, artifacts=[{"name": "x"}, {"uri": "u"}]),
        "defect_detection",
    )
    assert "result.defects 必须是数组" in errors
    assert "artifacts[0] 缺少 uri" in errors
    assert "artifacts[1] 缺少 name" in errors


def test_non_object_sample_rejected():
    assert validate_response_sample([], "defect_detection") == ["样例必须是 JSON 对象（顶层为 {…}）"]
    assert validate_response_sample({"code": 0}, "defect_detection") == [
        "缺少 result 对象（平台归一后的结论容器）"
    ]


# ---------- scene 白名单（决策 37） ----------
def test_scene_whitelist_is_six_routing_keys():
    assert set(ALLOWED_SCENES) == {
        "borescope", "infrared", "dr", "fpi", "fracture", "metallography"
    }


# ---------- headers 掩码（决策 43） ----------
def test_headers_masked_to_key_and_last4():
    masked = mask_headers({"X-API-Key": "sk-live-abcd1234", "Short": "abc"})
    assert masked == {"X-API-Key": "****1234", "Short": "****"}
    assert "abcd1234" not in str(masked)


def test_mask_headers_tolerates_junk():
    assert mask_headers(None) == {}
    assert mask_headers("nope") == {}


# ---------- meta 浅合并（决策 40/43） ----------
def test_meta_merge_keeps_unknown_keys():
    merged = merge_meta_patch(
        {"note": "keep", "custom_field": {"deep": 1}, "headers": {"A": "1", "B": "2"}},
        {"note": "new", "headers": {"B": None, "C": "3"}},
    )
    assert merged["note"] == "new"
    assert merged["custom_field"] == {"deep": 1}  # 未经 pydantic 往返，未知键不丢
    assert merged["headers"] == {"A": "1", "C": "3"}  # headers 逐键合并，null 即删


def test_meta_merge_explicit_null_drops_subkey():
    merged = merge_meta_patch({"default_params": {"a": 1}, "note": "x"}, {"note": None})
    assert "note" not in merged
    assert merged["default_params"] == {"a": 1}


def test_meta_headers_must_be_object():
    with pytest.raises(ValueError):
        merge_meta_patch({}, {"headers": ["not", "object"]})


def test_validate_meta_rejects_non_string_header_value():
    # registry.refresh() 对装载失败的行只 log 后跳过 —— 写入口必须挡住
    assert validate_meta({"headers": {"X-API-Key": None}})
    assert validate_meta({"default_params": {"a": 1}}) == []


# ---------- 留痕（决策 46） ----------
def test_change_log_prepends_and_bounds():
    meta: dict = {}
    for i in range(25):
        append_change_log(
            meta, reason=f"r{i}", operator=None, action="update", changed_fields=["endpoint"]
        )
    assert len(meta["change_log"]) == 20
    assert meta["change_log"][0]["reason"] == "r24"
    assert meta["change_log"][0]["operator"] == "未填"
