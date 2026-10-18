"""sync 护栏单测（决策 32）。

被护住的现实事故：内置表把 fpi 写成 mock 8002 + mock 自报版本，重跑 sync 就会
把已对接真工具的行洗回 tool_version=0.1.0、描述称 Mock、endpoint 指回 8002，
并把 UI 登记的认证头整块丢掉。plan_update_fields 是这条链路的唯一决策点。
"""
from __future__ import annotations

import json

from app.services.capability_gate import merge_meta_patch
from scripts.sync_capabilities import (
    BUILTIN_CAPABILITIES,
    SYNC_PROTECTED_COLUMNS,
    plan_update_fields,
    row_is_hand_registered,
)

MOCK_IDENTITY = {"tool_version": "0.1.0", "model_version": "1.0.0", "api_version": "v1"}
FPI = BUILTIN_CAPABILITIES["fpi_defect_detection"]
METAL = BUILTIN_CAPABILITIES["metallography_defect_detection"]


def test_capability_type_declares_analysis_tools():
    """决策 31：金相是定量分析、DR 是结构分析，内置表不能再自称 defect_detection。"""
    assert METAL["capability_type"] == "measurement"
    assert BUILTIN_CAPABILITIES["dr_defect_detection"]["capability_type"] == "classification"
    assert FPI["capability_type"] == "defect_detection"


def test_fpi_row_declares_delivered_tool_not_mock():
    """决策 47 取证结论进表：endpoint/身份/权重指纹来自实跑，不来自 mock 自报。"""
    assert FPI["endpoint"] == "http://127.0.0.1:18016"
    assert FPI["identity"]["tool_version"] == "1.0.0"
    assert FPI["model_weights_sha256"].startswith("4F2CA9AB")
    assert "Mock" not in FPI["description"]
    # 真工具 parameters 必填 part_id/device_id（实测 {} 直接 422/1001）
    assert set(FPI["meta"]["default_params"]) == {"part_id", "device_id"}


def test_hand_registered_rows_are_left_alone():
    """内置表写 mock 8002，库里指真实 18013 ⇒ 该行不由本表描述，sync 整行跳过。"""
    assert row_is_hand_registered("http://127.0.0.1:18013", METAL["endpoint"]) is True
    assert row_is_hand_registered(METAL["endpoint"], METAL["endpoint"]) is False
    assert row_is_hand_registered(None, METAL["endpoint"]) is False  # 新行照常插入


def test_matching_row_writes_declared_identity_not_mock_self_report():
    """endpoint 与内置表一致时才刷新；fpi 表内给了 identity，取它而非 mock 的 0.1.0。"""
    fields = plan_update_fields(FPI, MOCK_IDENTITY, force=False)
    assert fields["tool_version"] == "1.0.0"
    assert fields["api_version"] == "1.0.0"

    fields = plan_update_fields(METAL, MOCK_IDENTITY, force=False)
    assert fields["tool_version"] == "0.1.0"  # mock 拓扑行：自报身份可用


def test_guard_never_touches_manually_registered_facts():
    fields = plan_update_fields(FPI, MOCK_IDENTITY, force=False)
    for col in SYNC_PROTECTED_COLUMNS:
        assert col not in fields
    assert fields["description"] == FPI["description"]
    assert fields["timeout_s"] == 60


def test_guard_writes_only_declared_keys():
    """缺席即不写：否则 spec.get(..., 默认值) 会把真工具的 timeout/指纹洗成默认。"""
    bare = {
        "capability_id": "x_v1", "scene": "fpi", "capability_type": "defect_detection",
        "endpoint": "http://127.0.0.1:9", "tool_name": "t",
    }
    fields = plan_update_fields(bare, MOCK_IDENTITY, force=False)
    assert "timeout_s" not in fields
    assert "max_retry" not in fields
    assert "model_weights_sha256" not in fields
    assert "description" not in fields


def test_force_signals_full_reset_by_returning_nothing():
    assert plan_update_fields(FPI, MOCK_IDENTITY, force=True) == {}


def test_meta_patch_preserves_ui_registered_headers():
    """sync 的 meta 写入必须绕开 headers 子键（决策 40/43 的合并语义）。"""
    db_meta = json.loads(
        '{"default_params": {"part_id": "REAL"}, "headers": {"X-API-Key": "sk-secret-9999"}}'
    )
    patch = {k: v for k, v in FPI["meta"].items() if k != "headers"}
    merged = merge_meta_patch(db_meta, patch)
    assert merged["headers"] == {"X-API-Key": "sk-secret-9999"}
    assert merged["default_params"] == {"part_id": "UNKNOWN", "device_id": "QC-PLATFORM"}
