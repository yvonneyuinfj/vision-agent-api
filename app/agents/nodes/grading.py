"""节点：规则判级（需求 §1.2 辅助判级 / §5.1 严重度映射与业务规则配置）。

规则优先：基于标准化结果的确定性阈值判级；LLM 仅用于无法覆盖的补充说明。

规则热更与版本化（评估闭环前提）：
- 进程内缓存以目录内全部 *.yaml 的 mtime 指纹为键，文件编辑后自动失效重载
  （无需重启进程即生效，同时保留"每次节点执行不重复 glob+解析"的缓存价值）；
- 每个判级结果落 rules_hash（生效规则的 SHA-256 前 16 位），判级依据可追溯到
  具体规则版本（复核改判时可比对当时与当前的规则版本差异）。
"""
from __future__ import annotations

import asyncio
import hashlib
import json

from app.core.logger import get_logger
from pathlib import Path
from typing import Any

import yaml

from app.agents.nodes.node_utils import error_state, node
from app.core.errors import BizCode
from app.services.deps import AgentContext

logger = get_logger("app.agents.nodes.grading")

LEVELS = ["pass", "defect", "review", "fail"]

DEFAULT_RULES = {
    "max_severity": {  # 最高严重度 -> 结论级别
        "high": 3,  # 关键缺陷，必须人工复核
        "medium": 2,
        "low": 1,
        "null": 0,
    },
    "confidence_threshold": 0.5,   # 低于该置信度的缺陷不采纳为判级依据
    "need_review": [
        "max_severity >= 3",       # 存在 high 缺陷
        "max_confidence < 0.5",    # 模型最高置信度仍低于阈值 = 检测结果不可信
        "family == classification",# 模式分类结果无严重度，常规转人工确认
    ],
}

# 规则缓存：{rules_dir_key: (mtime 指纹, 解析结果)}——mtime 变化自动重载
_RULES_CACHE: dict[str, tuple[tuple, dict[str, Any]]] = {}


def _rules_mtime_key(rules_dir: Path) -> tuple:
    """目录内全部 *.yaml 的 (路径, mtime_ns, size) 指纹：任一文件变化即失效。"""
    stat = sorted(
        (str(p), p.stat().st_mtime_ns, p.stat().st_size)
        for p in rules_dir.glob("*.yaml")
    )
    return tuple(stat)


def _fingerprintable(obj: Any) -> Any:
    """规则树 JSON 规范化：None 键转 "null"（yaml 的 `null:` 无引号键）、
    非 JSON 标量转字符串——保证 sort_keys 可比较且序列化稳定。"""
    if isinstance(obj, dict):
        return {("null" if k is None else str(k)): _fingerprintable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_fingerprintable(v) for v in obj]
    if obj is None or isinstance(obj, (bool, int, float, str)):
        return obj
    return str(obj)


def _rules_fingerprint(rules: dict[str, Any]) -> str:
    """生效规则的稳定指纹（canonical JSON 的 SHA-256 前 16 位）。

    yaml 的 `null: 0`（无引号）解析出 None 键，直接 sort_keys 会
    TypeError（None 与 str 不可比较）——先规范化再序列化。
    """
    return hashlib.sha256(
        json.dumps(_fingerprintable(rules), ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()[:16]


def _load_rules_cached(rules_dir_key: str) -> dict[str, Any]:
    """按目录 mtime 指纹缓存规则解析结果：编辑后热更，未变时零 IO 解析。"""
    rules_dir = Path(rules_dir_key) if rules_dir_key else None
    if rules_dir is None or not rules_dir.is_dir():
        return DEFAULT_RULES
    try:
        mtime_key = _rules_mtime_key(rules_dir)
    except OSError:
        logger.exception("rules mtime scan failed: %s", rules_dir)
        return DEFAULT_RULES
    cached = _RULES_CACHE.get(rules_dir_key)
    if cached is not None and cached[0] == mtime_key:
        return cached[1]
    for path in sorted(rules_dir.glob("*.yaml")):
        try:
            rules = yaml.safe_load(path.read_text(encoding="utf-8")) or DEFAULT_RULES
        except Exception:  # noqa: BLE001
            logger.exception("rules load failed: %s", path.name)
            return DEFAULT_RULES
        if isinstance(rules, dict):
            _RULES_CACHE[rules_dir_key] = (mtime_key, rules)
            return rules
    return DEFAULT_RULES


def _load_rules(rules_dir: Path | None) -> dict[str, Any]:
    """载入 config/rules/*.yaml；失败回退内置默认规则（mtime 失效缓存 + 热更）。"""
    return _load_rules_cached(str(rules_dir) if rules_dir is not None else "")


def _max_severity_rank(hint: dict[str, Any], rules: dict[str, Any]) -> int:
    severity = hint.get("max_severity")
    mapping = rules.get("max_severity", {})
    return int(mapping.get(severity, mapping.get("null", 0)))


def _confidence_hit(text: str, prefix: str, value: float | None) -> bool:
    """置信度阈值条件求值：支持 `<` / `<=`，阈值取规则文本自身（yaml 即事实源）。"""
    body = text[len(prefix):].strip()
    if body.startswith("<="):
        threshold = float(body[2:].strip())
        return value is not None and value <= threshold
    if body.startswith("<"):
        threshold = float(body[1:].strip())
        return value is not None and value < threshold
    return False


def _eval_conditions(hint: dict[str, Any], rules: dict[str, Any]) -> list[str]:
    """逐条求值 need_review；命中返回原因列表（可审计）。"""
    triggered: list[str] = []
    max_rank = _max_severity_rank(hint, rules)
    family = hint.get("family")

    for condition in rules.get("need_review", []):
        if not isinstance(condition, str):
            continue
        text = condition.strip()
        hit = False
        if text.startswith("max_severity"):
            expected = int(text.replace("max_severity", "").replace(">=", "").strip())
            hit = max_rank >= expected
        elif text.startswith("max_confidence"):
            # 需求语义「检测结果低于设定阈值才转人工」：最高置信度仍不达标
            # = 模型对自己的检出没有把握，结果不可信，转人工兜底。
            # （不再默认用 min_confidence：模型几乎总伴随弱检出，单个低分
            #   噪声框会把每张有检出的图都拖进人工复核。）
            hit = _confidence_hit(text, "max_confidence", hint.get("max_confidence"))
        elif text.startswith("min_confidence"):
            # 规则 DSL 保留该判据（任一低分检出即转复核），供业务按需配置
            hit = _confidence_hit(text, "min_confidence", hint.get("min_confidence"))
        elif text.startswith("family =="):
            expected = text.split("==")[1].strip().strip("'\"")
            hit = family == expected
        if hit:
            triggered.append(condition)
    return triggered


@node("grading")
async def grading_node(state: dict[str, Any], ctx: AgentContext) -> dict[str, Any]:
    hint = state.get("grade_hint") or {}
    if not hint:
        return error_state("grading", BizCode.INTERNAL_ERROR, "missing grade_hint")

    rules = await asyncio.to_thread(
        _load_rules, ctx.settings.rules_path if ctx.settings else None
    )

    conditions = _eval_conditions(hint, rules)
    status = hint.get("status", "unknown")

    if hint.get("review_required"):
        # 智能体异议恒改判人工复核（共识）：不论机械规则原判 pass/defect/fail，
        # 一律交由人工终裁；异议只改处置路由，不抹已检出缺陷，也永不自动放行。
        level = "review"
        reason = str(hint.get("review_reason") or "").strip() or "未填写异议理由"
        summary = f"智能体异议，请求人工复核：{reason}"
        conditions = [*conditions, "agent_request"]
    elif status == "normal":
        level = "pass"
        summary = "未发现缺陷，无需人工复核"
    elif conditions:
        level = "review"
        summary = f"规则命中需要人工复核：{'、'.join(conditions)}"
    elif _max_severity_rank(hint, rules) >= 2 or (hint.get("max_severity") and hint["max_severity"] in ("medium", "high")):
        level = "fail"
        summary = "存在中/高严重度缺陷，判为不合格"
    else:
        # 修复"零缺陷误判"：兜底缺陷级必须以"确有缺陷条目"为前提。
        # 无任何条目且能力未报异常（status=unknown 等）→ 判通过，
        # 避免出现"0 缺陷却显示'存在低严重度缺陷'"的自相矛盾结论。
        entries = state.get("standardized") or []
        has_entry = any(isinstance(e, dict) for e in entries)
        if not has_entry and status != "abnormal":
            level = "pass"
            summary = "未检出缺陷条目，判为通过"
        else:
            level = "defect"
            summary = "存在低严重度缺陷，建议回修"

    grade = {
        "level": level,
        "summary": summary,
        "triggered_rules": conditions,
        "max_severity": hint.get("max_severity"),
        "min_confidence": hint.get("min_confidence"),
        "max_confidence": hint.get("max_confidence"),
        "family": hint.get("family"),
        "auto": True,
        # 规则版本指纹：判级依据可追溯到具体规则版本（复核改判时可比对规则漂移）
        "rules_hash": _rules_fingerprint(rules),
    }
    if hint.get("model_defect_count") is not None:
        # react 轨裁决口径对照（exit_gate 透传）：能力/子任务原始检出数，
        # 报告用它与终裁列表数对照，解释标注图框数与列表数的差异来源
        grade["model_defect_count"] = hint["model_defect_count"]
    return {"grade": grade}