"""LLM 子系统：langchain 模型调用（Provider 抽象）+ 系统提示词加载。"""
from app.agents.llm.prompts import (
    DEFAULT_KB_TEMPLATE_ID,
    DEFAULT_LANGUAGE,
    DEFAULT_PROMPT_TEMPLATE_PATH,
    TOOL_SELECTION_TEMPLATE_ID,
    build_kb_system_prompt,
    format_knowledge_contexts,
    load_system_prompt,
)
from app.agents.llm.provider import PROVIDERS, create_chat_model
from app.agents.llm.service import LLMService

__all__ = [
    "DEFAULT_KB_TEMPLATE_ID",
    "DEFAULT_LANGUAGE",
    "DEFAULT_PROMPT_TEMPLATE_PATH",
    "LLMService",
    "PROVIDERS",
    "TOOL_SELECTION_TEMPLATE_ID",
    "build_kb_system_prompt",
    "create_chat_model",
    "format_knowledge_contexts",
    "load_system_prompt",
]
