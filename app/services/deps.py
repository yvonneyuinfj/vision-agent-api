"""服务依赖容器：注入 Agent 上下文（Gateway / Registry / Knowledge / Repo / LLM / Tools）。

节点构造函数只接收依赖对象，返回 state->state 纯函数，便于单测注入桩。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.capabilities.registry import CapabilityRegistry
from app.knowledge.ragflow import RAGFlowClient
from app.repositories.file_repo import FileRepo
from app.repositories.task_repo import TaskRepo
from app.agents.llm import LLMService


@dataclass
class AgentContext:
    registry: CapabilityRegistry | None = None
    ragflow: RAGFlowClient | None = None
    file_repo: FileRepo | None = None
    task_repo: TaskRepo | None = None
    llm: LLMService | None = None
    settings: object = None
    engine_ids: set[str] = field(default_factory=set)  # 短路熔断标记
    tool_registry: object = None  # ToolRegistry（编排层统一取工具入口）
    ppt_service: object = None  # PptService（会话工具 generate_ppt 复用文档生成）