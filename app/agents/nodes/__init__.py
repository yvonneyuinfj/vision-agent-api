"""节点包：每个节点一个文件，纯函数工厂模式，禁止在节点内直接操作持久层以外的跨层调用。

节点从 AgentContext（app/services/deps.py）注入依赖，保证可单测。
"""
from __future__ import annotations

from app.agents.nodes.generate_report import generate_report_node
from app.agents.nodes.grading import grading_node
from app.agents.nodes.human_review import human_review_node, REVIEW_REQUIRED_LEVELS
from app.agents.nodes.invoke_capability import invoke_capability_node
from app.agents.nodes.normalize_result import normalize_result_node
from app.agents.nodes.rag_retrieval import rag_retrieval_node
from app.agents.nodes.resolve_capability import resolve_capability_node
from app.agents.nodes.scene_confirm import scene_confirm_node
from app.agents.nodes.select_capability import select_capability_node
from app.agents.nodes.validate import validate_node

__all__ = [
    "generate_report_node",
    "grading_node",
    "human_review_node",
    "REVIEW_REQUIRED_LEVELS",
    "invoke_capability_node",
    "normalize_result_node",
    "rag_retrieval_node",
    "resolve_capability_node",
    "scene_confirm_node",
    "select_capability_node",
    "validate_node",
]
