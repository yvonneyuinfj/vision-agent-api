"""RAGFlow 聊天助手配置：本地 YAML 默认文件、严格校验与原子更新。"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


class ChatAssistantConfig(BaseModel):
    """POST /api/v1/chats 可写字段。

    RAGFlow 返回对象还包含 id/tenant_id/create_time/status/kb_names 等只读字段，
    这些字段故意不在模型中，防止把 GET 响应原样回灌到创建接口。
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=255)
    description: str = ""
    icon: str = ""
    dataset_ids: list[str] = Field(default_factory=list)
    language: str = "Chinese"
    llm_id: str = ""
    llm_setting: dict[str, Any] = Field(default_factory=dict)
    prompt_config: dict[str, Any] = Field(default_factory=dict)
    prompt_type: str = "simple"
    rerank_id: str = ""
    similarity_threshold: float = Field(0.2, ge=0, le=1)
    vector_similarity_weight: float = Field(0.3, ge=0, le=1)
    top_n: int = Field(5, ge=1, le=100)
    top_k: int = Field(1024, ge=1)
    do_refer: str | None = None

    @model_validator(mode="after")
    def validate_prompt(self) -> "ChatAssistantConfig":
        sensitive = {"api_key", "apikey", "secret", "password", "token", "access_key"}

        def secret_path(value: Any, path: str = "") -> str | None:
            if isinstance(value, dict):
                for key, nested in value.items():
                    current = f"{path}.{key}" if path else str(key)
                    normalized = str(key).lower().replace("-", "_")
                    if any(marker in normalized for marker in sensitive):
                        return current
                    found = secret_path(nested, current)
                    if found:
                        return found
            elif isinstance(value, list):
                for index, nested in enumerate(value):
                    found = secret_path(nested, f"{path}[{index}]")
                    if found:
                        return found
            return None

        secret = secret_path(self.model_dump())
        if secret:
            raise ValueError(f"配置文件禁止保存密钥字段 {secret}，请在 RAGFlow 服务端配置凭据")
        system = self.prompt_config.get("system", "")
        parameters = self.prompt_config.get("parameters", [])
        uses_knowledge = any(
            isinstance(item, dict) and item.get("key") == "knowledge"
            for item in parameters
        )
        if uses_knowledge and "{knowledge}" not in system:
            raise ValueError("prompt_config.parameters 声明了 knowledge，但 system 中缺少 {knowledge}")
        if self.dataset_ids and not uses_knowledge:
            raise ValueError("配置了 dataset_ids 时，prompt_config.parameters 必须包含 knowledge")
        return self

    def api_payload(self) -> dict[str, Any]:
        payload = self.model_dump(exclude_none=True)
        # do_refer 是部分新版本的持久字段；空值不提交，兼容仍按 quote 控制引用的旧版本。
        if not payload.get("do_refer"):
            payload.pop("do_refer", None)
        return payload


class ChatConfigStore:
    """YAML 落盘：与 config/rules/ 其余规则配置同格式（RAG 配置统一 YAML）。"""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def load(self) -> ChatAssistantConfig:
        try:
            raw = self.path.read_text(encoding="utf-8")
        except FileNotFoundError as exc:
            raise ValueError(f"RAGFlow chat 配置文件不存在: {self.path}") from exc
        try:
            data = yaml.safe_load(raw)
        except yaml.YAMLError as exc:
            raise ValueError(f"RAGFlow chat 配置 YAML 解析失败: {exc}") from exc
        if not isinstance(data, dict):
            raise ValueError(f"RAGFlow chat 配置必须是 YAML 映射: {self.path}")
        try:
            return ChatAssistantConfig.model_validate(data)
        except Exception as exc:  # pydantic 细节不暴露到 API，但日志保留异常链
            raise ValueError(f"RAGFlow chat 配置无效: {exc}") from exc

    def save(self, config: ChatAssistantConfig) -> None:
        """同目录临时文件 + replace，避免进程中断留下半截文件。"""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(f"{self.path.suffix}.tmp")
        temp.write_text(
            yaml.safe_dump(
                config.model_dump(exclude_none=True),
                allow_unicode=True,
                sort_keys=False,
                default_flow_style=False,
            ),
            encoding="utf-8",
        )
        temp.replace(self.path)
