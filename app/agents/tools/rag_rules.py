"""RAG 知识规则加载（独立模块，避免 rag_tool ↔ rag_retrieval 循环导入）。

知识准入治理（config/rules/rag_rules.yaml 可覆盖）：
- knowledge_score_threshold：仅 score 大于阈值的引用注入模型上下文，
  避免低相关文献成为报告引用噪声；
- scene_datasets：场景级数据集路由（键=场景编码，值=数据集 id 列表，
  未配置回退 ragflow_chat.yaml 的 dataset_ids 全集）；
- scene_doc_excludes：场景级文档排除（数据集为多场景混库时的轻量元数据过滤：
  键=场景编码或 "all"，值=文档名子串模式列表；命中文档不进该场景检索范围）。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

DEFAULT_RAG_RULES: dict[str, Any] = {
    "knowledge_score_threshold": 0.35,
    "scene_datasets": {},
    "scene_doc_excludes": {},
}
RAG_RULES_FILENAME = "rag_rules.yaml"


def load_rag_rules(rules_dir: str | Path | None) -> dict[str, Any]:
    """载入 {rules_dir}/rag_rules.yaml；缺失/解析失败回退内置默认。"""
    if rules_dir is None:
        return dict(DEFAULT_RAG_RULES)
    path = Path(rules_dir) / RAG_RULES_FILENAME
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - 文件缺失或损坏均回退默认，不阻塞检索
        return dict(DEFAULT_RAG_RULES)
    if not isinstance(data, dict):
        return dict(DEFAULT_RAG_RULES)
    return {**DEFAULT_RAG_RULES, **data}
