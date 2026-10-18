"""金样本期望检查器测试：加载/前缀匹配/断言语义（评估资产的地基）。"""
from __future__ import annotations

from pathlib import Path

from scripts.eval_expectations import (
    DEFAULT_EXPECTATIONS_PATH,
    check_row,
    load_expectations,
    matched_sample_name,
)


def test_load_builtin_expectations_covers_all_samples():
    """内置期望文件可加载且覆盖全部 9 个样本（含 class1_1 硬标准）。"""
    expectations = load_expectations()

    assert expectations, "config/eval/samples.yaml 未加载到期望"
    images = sorted(
        p.stem
        for p in (Path(__file__).resolve().parents[2] / "docs" / "image").iterdir()
        if p.suffix.lower() in {".jpg", ".jpeg", ".png"}
    )
    for stem in images:
        assert matched_sample_name(stem, expectations) is not None, f"样本 {stem} 无期望条目"


def test_load_builtin_class1_1_hard_assertion():
    expectations = load_expectations()

    assert expectations["class1_1"] == {"status": "awaiting_review", "agent_request": True}
    assert expectations["class5_1"]["grade_level"] == "pass"
    assert expectations["class5_1"]["scene"] == "fracture"


def test_load_missing_or_broken_file_returns_empty(tmp_path):
    assert load_expectations(tmp_path / "no.yaml") == {}
    broken = tmp_path / "broken.yaml"
    broken.write_text("samples: [1, 2, 3]", encoding="utf-8")  # samples 非 dict
    assert load_expectations(broken) == {}


def test_check_row_passes_and_reports_violations():
    expect = {"status": "awaiting_review", "agent_request": True}
    ok_row = {"status": "awaiting_review", "agent_request": True, "grade_level": "review"}
    assert check_row(expect, ok_row) == []

    bad_row = {"status": "completed", "agent_request": False}
    violations = check_row(expect, bad_row)
    assert any("status" in v for v in violations)
    assert any("agent_request" in v for v in violations)


def test_check_row_exec_failure_is_violation():
    expect = {"status": "completed"}
    row = {"status": "submit_failed", "error": "connection refused"}
    violations = check_row(expect, row)
    assert len(violations) == 1
    assert "connection refused" in violations[0]


def test_check_row_empty_expect_passes():
    assert check_row({}, {"status": "whatever"}) == []


def test_matched_sample_name_prefix_semantics():
    expectations = {"class3": {}, "class3_1": {}}
    # 最长前缀优先：class3_1.jpg 命中 class3_1 而非 class3
    assert matched_sample_name("class3_1", expectations) == "class3_1"
    assert matched_sample_name("class3_9", expectations) == "class3"
    assert matched_sample_name("class9", expectations) is None


def test_builtin_expectations_file_is_current():
    """期望文件与默认路径一致（防止移动后加载到旧文件）。"""
    assert DEFAULT_EXPECTATIONS_PATH.name == "samples.yaml"
    assert DEFAULT_EXPECTATIONS_PATH.exists()
