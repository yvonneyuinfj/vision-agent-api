"""grading 判级节点回归：级别矩阵 + 零缺陷误判修复（E1）。

E1：status=unknown + 零缺陷曾被兜底分支误判为 "存在低严重度缺陷"（defect），
自相矛盾且被报告页放大展示；修复后零缺陷且能力未报异常 → pass。
"""
from __future__ import annotations

import pytest

from app.agents.nodes.grading import grading_node
from app.core.errors import BizCode
from app.services.deps import AgentContext


def _ctx() -> AgentContext:
    return AgentContext()  # settings=None → 内置默认规则


def _hint(**over) -> dict:
    base = {
        "family": "defect",
        "status": "unknown",
        "summary": "",
        "max_severity": None,
        "min_confidence": None,
        "max_confidence": None,
    }
    base.update(over)
    return base


async def _grade(state: dict) -> dict:
    out = await grading_node(state, _ctx())
    assert out.get("error") is None, f"grading 意外收敛错误: {out.get('error')}"
    return out["grade"]


# --------------------------------------------------------------------------
# E1 核心：零缺陷矩阵
# --------------------------------------------------------------------------
async def test_unknown_status_zero_defects_is_pass_not_defect():
    """E1 主回归：unknown + 0 缺陷 → pass，不再落'存在低严重度缺陷'。"""
    grade = await _grade({"grade_hint": _hint(), "standardized": []})
    assert grade["level"] == "pass"
    assert "低严重度" not in grade["summary"]


async def test_missing_standardized_channel_zero_defects_is_pass():
    """standardized 通道缺失等价零缺陷（旧任务/异常路径），同样不得误判。"""
    grade = await _grade({"grade_hint": _hint()})
    assert grade["level"] == "pass"


async def test_abnormal_status_zero_defects_keeps_defect():
    """能力明确报异常却无条目：证据矛盾，保留缺陷级转人工关注（不静默放行）。"""
    grade = await _grade({"grade_hint": _hint(status="abnormal"), "standardized": []})
    assert grade["level"] == "defect"


# --------------------------------------------------------------------------
# 既有级别矩阵不回归
# --------------------------------------------------------------------------
async def test_normal_status_is_pass():
    grade = await _grade({"grade_hint": _hint(status="normal"), "standardized": []})
    assert grade["level"] == "pass"


async def test_low_severity_defect_is_defect():
    grade = await _grade(
        {
            "grade_hint": _hint(
                status="abnormal", max_severity="low", min_confidence=0.9, max_confidence=0.9
            ),
            "standardized": [{"defect_type": "dimple", "severity": "low", "confidence": 0.9}],
        }
    )
    assert grade["level"] == "defect"


async def test_high_severity_triggers_review():
    grade = await _grade(
        {
            "grade_hint": _hint(
                status="abnormal", max_severity="high", min_confidence=0.9, max_confidence=0.9
            ),
            "standardized": [{"defect_type": "cleavage", "severity": "high", "confidence": 0.9}],
        }
    )
    assert grade["level"] == "review"
    assert "max_severity >= 3" in grade["triggered_rules"]


async def test_low_confidence_triggers_review():
    """检测结果低于设定阈值（最高置信度仍不达标）→ 转人工复核。"""
    grade = await _grade(
        {
            "grade_hint": _hint(
                status="abnormal", max_severity="low", min_confidence=0.4, max_confidence=0.4
            ),
            "standardized": [{"defect_type": "dimple", "severity": "low", "confidence": 0.4}],
        }
    )
    assert grade["level"] == "review"
    assert "max_confidence < 0.5" in grade["triggered_rules"]


async def test_low_confidence_floor_alone_does_not_trigger_review():
    """核心回归（需求：仅当检测结果低于阈值才转人工）。

    模型几乎总伴随弱检出：单个低分噪声框不得把整张图拖进人工复核。
    只要最高置信度达标（检测结果可信），低分条目仍如实留档但按规则自动判级。
    """
    grade = await _grade(
        {
            "grade_hint": _hint(
                status="abnormal",
                max_severity="low",
                min_confidence=0.276091,
                max_confidence=0.672182,
            ),
            "standardized": [
                {"defect_type": "BP", "severity": None, "confidence": 0.672182},
                {"defect_type": "BP", "severity": None, "confidence": 0.53796},
                {"defect_type": "BP", "severity": None, "confidence": 0.276091},
            ],
        }
    )
    assert grade["level"] == "defect"
    assert not any("confidence" in r for r in grade["triggered_rules"])


async def test_confidence_at_threshold_does_not_trigger_review():
    """阈值边界：max_confidence == 0.5 视为达标（规则为严格小于），不转人工。"""
    grade = await _grade(
        {
            "grade_hint": _hint(
                status="abnormal", max_severity="low", min_confidence=0.5, max_confidence=0.5
            ),
            "standardized": [{"defect_type": "dimple", "severity": "low", "confidence": 0.5}],
        }
    )
    assert grade["level"] == "defect"


async def test_classification_family_triggers_review():
    grade = await _grade(
        {
            "grade_hint": _hint(family="classification", status="abnormal"),
            "standardized": [{"class": "III"}],
        }
    )
    assert grade["level"] == "review"
    assert "family == classification" in grade["triggered_rules"]


async def test_missing_grade_hint_converges_error():
    out = await grading_node({"standardized": []}, _ctx())
    assert out["error"]["code"] == int(BizCode.INTERNAL_ERROR)


async def test_grade_audit_fields_present():
    """判级审计字段（供报告页/复核门引用）结构稳定。"""
    grade = await _grade({"grade_hint": _hint(status="normal"), "standardized": []})
    for key in (
        "level", "summary", "triggered_rules", "max_severity",
        "min_confidence", "max_confidence", "family", "auto",
    ):
        assert key in grade


# --------------------------------------------------------------------------
# 智能体异议恒改判人工复核（共识：override 机制）
# --------------------------------------------------------------------------
async def test_agent_request_overrides_normal_pass_to_review():
    """核心回归：能力报 normal 本判 pass，但智能体异议 → 恒改 review。"""
    grade = await _grade(
        {
            "grade_hint": _hint(
                status="normal",
                review_required=True,
                review_reason="能力报无异常，但图像可见网状荧光显示，疑似漏检",
            ),
            "standardized": [],
        }
    )
    assert grade["level"] == "review"
    assert "agent_request" in grade["triggered_rules"]
    assert "疑似漏检" in grade["summary"]


async def test_agent_request_overrides_fail_to_review():
    """中严重度本判 fail，异议同样改路由人工（人终裁，而非系统拍板）。"""
    grade = await _grade(
        {
            "grade_hint": _hint(
                status="abnormal",
                max_severity="medium",
                min_confidence=0.9,
                max_confidence=0.9,
                review_required=True,
                review_reason="疑似伪显示",
            ),
            "standardized": [{"defect_type": "pore", "severity": "medium", "confidence": 0.9}],
        }
    )
    assert grade["level"] == "review"
    assert "agent_request" in grade["triggered_rules"]


async def test_agent_request_without_reason_falls_back_to_placeholder():
    """review_required=true 但理由为空：不崩，占位文案。"""
    grade = await _grade(
        {"grade_hint": _hint(status="normal", review_required=True), "standardized": []}
    )
    assert grade["level"] == "review"
    assert "未填写异议理由" in grade["summary"]


async def test_no_agent_request_keeps_mechanical_grading():
    """review_required=false（默认）不改变原机械判级。"""
    grade = await _grade(
        {"grade_hint": _hint(status="normal", review_required=False), "standardized": []}
    )
    assert grade["level"] == "pass"
    assert "agent_request" not in grade["triggered_rules"]


# --------------------------------------------------------------------------
# 规则版本化 + 热更（mtime 失效缓存）
# --------------------------------------------------------------------------
async def test_grade_records_rules_hash():
    """判级结果落规则指纹：判级依据可追溯到具体规则版本。"""
    grade = await _grade({"grade_hint": _hint(status="normal"), "standardized": []})

    assert "rules_hash" in grade
    assert len(grade["rules_hash"]) == 16
    # 同配置下指纹稳定（可跨任务比对）
    grade2 = await _grade({"grade_hint": _hint(status="normal"), "standardized": []})
    assert grade2["rules_hash"] == grade["rules_hash"]


def test_rules_cache_hot_reload_on_mtime_change(tmp_path):
    """规则文件编辑后自动失效重载：无需重启进程。"""
    from pathlib import Path

    import yaml

    from app.agents.nodes.grading import _RULES_CACHE, _load_rules

    rules_file = tmp_path / "grade_rules.yaml"
    rules_file.write_text(
        yaml.safe_dump({"confidence_threshold": 0.5, "need_review": ["max_confidence < 0.5"]}),
        encoding="utf-8",
    )

    rules_v1 = _load_rules(tmp_path)
    assert rules_v1["confidence_threshold"] == 0.5
    # 缓存命中（无重解析）
    assert _load_rules(tmp_path) is rules_v1

    # 编辑规则文件（mtime 变化）-> 热更生效
    rules_file.write_text(
        yaml.safe_dump({"confidence_threshold": 0.7, "need_review": ["max_severity >= 3"]}),
        encoding="utf-8",
    )
    rules_v2 = _load_rules(tmp_path)
    assert rules_v2["confidence_threshold"] == 0.7
    assert rules_v2 is not rules_v1
    # 缓存表已换新指纹
    key = str(tmp_path)
    assert key in _RULES_CACHE
    assert _RULES_CACHE[key][1] is rules_v2


def test_rules_dir_missing_falls_back_to_defaults(tmp_path):
    """规则目录不存在：回退内置默认规则（不炸）。"""
    from app.agents.nodes.grading import DEFAULT_RULES, _load_rules

    assert _load_rules(tmp_path / "no_such_dir") is DEFAULT_RULES


# --------------------------------------------------------------------------
# 规则指纹鲁棒性回归（生产事故 2026-09-16：None 键炸 sort_keys）
# --------------------------------------------------------------------------
def test_rules_fingerprint_tolerates_yaml_none_keys():
    """yaml `null: 0`（无引号）解析出 None 键：指纹不炸且稳定。

    生产事故：grade_rules.yaml 的 max_severity.null 键为 NoneType，
    json.dumps(sort_keys=True) 抛 "TypeError: '<' not supported
    between instances of 'NoneType' and 'str'"，判级节点整体失败。
    """
    from app.agents.nodes.grading import _rules_fingerprint

    rules = {
        "max_severity": {"high": 3, "medium": 2, "low": 1, None: 0},  # None 键！
        "confidence_threshold": 0.5,
        "need_review": ["max_severity >= 3"],
    }
    fp1 = _rules_fingerprint(rules)
    fp2 = _rules_fingerprint(dict(rules))  # 拷贝后指纹不变（稳定）

    assert len(fp1) == 16
    assert fp1 == fp2
    # None 键与 "null" 字符串键同义：指纹一致（规范化收敛）
    rules_str_key = {
        "max_severity": {"high": 3, "medium": 2, "low": 1, "null": 0},
        "confidence_threshold": 0.5,
        "need_review": ["max_severity >= 3"],
    }
    assert _rules_fingerprint(rules_str_key) == fp1


async def test_grade_with_builtin_rules_yaml_none_key_does_not_crash():
    """真实规则文件（含 null: 0 None 键）跑判级节点：不崩、落指纹。"""
    from app.agents.nodes.grading import _load_rules, _rules_fingerprint

    from pathlib import Path

    rules = _load_rules(Path("config/rules"))
    # 事故现场：真实 yaml 含 None 键（max_severity.null）
    assert None in rules.get("max_severity", {}) or "null" in rules.get("max_severity", {})

    grade = await _grade({"grade_hint": _hint(status="abnormal", max_severity="medium"),
                          "standardized": [{"defect_type": "pore", "severity": "medium"}]})
    assert grade["level"] in ("fail", "defect", "review")
    assert len(grade["rules_hash"]) == 16
    # 指纹与直接计算一致
    assert grade["rules_hash"] == _rules_fingerprint(rules)


# --------------------------------------------------------------------------
# react 轨口径对照：model_defect_count 透传（报告图注数据源）
# --------------------------------------------------------------------------
async def test_grade_passes_model_defect_count_when_present():
    """hint 带 model_defect_count（react 轨）：grade 原样透传。"""
    grade = await _grade({
        "grade_hint": _hint(status="abnormal", max_severity="medium", model_defect_count=3),
        "standardized": [{"defect_type": "pore", "severity": "medium"}],
    })
    assert grade["model_defect_count"] == 3


async def test_grade_omits_model_defect_count_for_single_track():
    """单轨/会话轨 hint 无该字段：grade 不落键（前端不渲染口径差异行）。"""
    grade = await _grade({
        "grade_hint": _hint(status="abnormal", max_severity="high"),
        "standardized": [{"defect_type": "crack", "severity": "high"}],
    })
    assert "model_defect_count" not in grade
