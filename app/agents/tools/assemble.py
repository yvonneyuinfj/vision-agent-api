"""工具自动装配：把能力注册中心的每个 manifest 动态挂载为 Tool，并注册知识工具。

容器启动时调用 `assemble_tools(...)` 返回配置好的 ToolRegistry；
LangGraph 节点按名称解析工具，新增算法场景只新增 manifest + 重新装配，图零改动。

治理接线：装配期创建共享 AdapterPool（per-tool 信号量 + 熔断器，参数取
gateway_concurrency / gateway_breaker_threshold），全部 InferTool 复用同一池，
refresh 重建时同样复用（熔断/限流状态跨刷新保留，不因清单变化清零）。
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from app.agents.tools.registry import ToolRegistry
from app.agents.tools.infer_tool import InferTool
from app.agents.tools.rag_tool import RagSearchTool
from app.capabilities.adapters.base import AdapterPool
from app.capabilities.registry import CapabilityRegistry
from app.knowledge.ragflow import RAGFlowClient

DatasetIdsSource = Sequence[str] | Callable[[], Sequence[str]] | None


def _build_pool(settings: Any) -> AdapterPool | None:
    """按配置构建共享治理池；settings 缺失（测试/脚本）返回 None（不加治理）。"""
    if settings is None:
        return None
    concurrency = getattr(settings, "gateway_concurrency", None)
    threshold = getattr(settings, "gateway_breaker_threshold", None)
    if concurrency is None or threshold is None:
        return None
    return AdapterPool(int(concurrency), int(threshold))


def assemble_tools(
    capability_registry: CapabilityRegistry | None,
    ragflow: RAGFlowClient | None,
    settings: Any = None,
    dataset_ids: DatasetIdsSource = None,
) -> ToolRegistry:
    """装配默认工具注册表。

    - 配置了 on_refresh：registry 轮询刷新时自动重建本表，新增算法零改动
    - 每个 capability manifest → 注册一个 InferTool（detect/<tool_name>）
    - RAGFlow 可用（配置了 API Key）→ 注册 RagSearchTool
    - dataset_ids：知识检索数据集（单一事实源 = ragflow_chat.yaml 的 dataset_ids）；
      支持传惰性提供者（容器传 lambda），运行中更新自动生效
    """
    pool = _build_pool(settings)
    registry = _build_registry(capability_registry, ragflow, settings, dataset_ids, pool)

    async def _on_refresh(_manifests) -> None:
        """轮询刷新后重建工具表：新增/停用能力自动生效（决策 6/C 自动刷新）。

        pool 显式透传复用：能力清单变化不清零熔断与限流状态。
        """
        rebuilt = _build_registry(capability_registry, ragflow, settings, dataset_ids, pool)
        registry.clear()
        for tool in rebuilt.list():
            registry.register(tool)

    if capability_registry is not None:
        capability_registry.on_refresh = _on_refresh
    return registry


def _build_registry(
    capability_registry: CapabilityRegistry | None,
    ragflow: RAGFlowClient | None,
    settings: Any = None,
    dataset_ids: DatasetIdsSource = None,
    pool: AdapterPool | None = None,
) -> ToolRegistry:
    registry = ToolRegistry()
    if capability_registry is not None:
        for manifest in capability_registry.list_enabled():
            registry.register(InferTool(manifest, capability_registry, pool=pool))
    if ragflow is not None:
        registry.register(RagSearchTool(ragflow, settings, dataset_ids=dataset_ids))
    return registry
