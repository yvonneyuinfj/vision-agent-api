"""知识检索工具：RAG（检索 + 引用溯源）。

把 rag 节点的检索行为抽为可注册 Tool（name = knowledge/rag），
LangGraph 按名称解析；未配置 RAGFlow / 知识库为空时降级返回空引用（不阻塞主流程）。

数据集单一事实源：config/rules/ragflow_chat.yaml 的 dataset_ids（经容器注入，
支持静态列表或惰性提供者）——不再做数据集名称→id 的服务端解析。
"""
from __future__ import annotations

import time
import xml.etree.ElementTree as ET  # noqa: F401 - 引用解析扩展点

from collections.abc import Callable, Sequence
from typing import Any

from app.agents.tools.rag_rules import load_rag_rules
from app.agents.tools.registry import Tool, ToolResult
from app.core.logger import get_logger
from app.knowledge.ragflow import RAGFlowClient

logger = get_logger("app.agents.tools.rag_tool")

REF_TYPES = ("标准", "历史报告", "期刊", "维修资料")

DatasetIdsSource = Sequence[str] | Callable[[], Sequence[str]] | None

# 文档清单进程内缓存 TTL：知识库文档低频变动，场景过滤不必每次检索都拉清单
_DOCS_CACHE_TTL_S = 600.0

# RRF 融合常数 k（标准取 60）：score = Σ 1/(k + rank)，rank 为 1-based 排位
_RRF_K = 60


def _raw_score(chunk: dict[str, Any]) -> float:
    """引擎原始分数（rank 回退）：RRF 平序时的次级排序键。"""
    try:
        return float(chunk.get("score", chunk.get("rank", 0.0)) or 0.0)
    except (TypeError, ValueError):
        return 0.0


class RagSearchTool(Tool[ToolResult]):
    """知识增强工具：场景 + 缺陷类型 + 测量参数 → 知识库检索 → 引用溯源。"""

    name = "knowledge/rag"
    description = "RAG 知识检索：按场景/缺陷类型/测量参数检索知识库并生成引用"

    def __init__(
        self,
        ragflow: RAGFlowClient | None,
        settings: Any = None,
        dataset_ids: DatasetIdsSource = None,
    ) -> None:
        self._ragflow = ragflow
        self._settings = settings
        # 静态列表（测试/脚本）或惰性提供者（容器：chat 配置更新后自动跟进）
        self._dataset_ids = dataset_ids
        # dataset_id -> (拉取时刻, [(doc_id, doc_name)])；场景文档过滤的清单缓存
        self._docs_cache: dict[str, tuple[float, list[tuple[str, str]]]] = {}

    def _current_dataset_ids(self) -> list[str]:
        ids = self._dataset_ids() if callable(self._dataset_ids) else (self._dataset_ids or [])
        return [str(i) for i in ids if str(i).strip()]

    async def execute(self, inputs: dict[str, Any]) -> ToolResult:
        ragflow = self._ragflow
        if ragflow is None or not ragflow.available:
            # 降级：不配置知识库时跳过（M1 允许空知识链跑通流程，报告标注"无知识支撑"）
            return ToolResult.success({"knowledge": []})

        # 场景级数据集路由：scene → 专属数据集 id（config/rules/rag_rules.yaml 可覆盖），
        # 未配置时回退全量数据集（需求 §5.3 元数据过滤的轻量落地）。
        dataset_ids = self._route_datasets_by_scene(inputs.get("scene"), self._current_dataset_ids())
        if not dataset_ids:
            return ToolResult.success({"knowledge": []})

        # 场景级文档一致性过滤（scene_doc_excludes）：混库时按文档名模式排除
        # 跨方法/跨行业文献，document_ids 前置过滤参与检索排序（fpi 任务不再召回红外文献）
        document_ids = await self._scene_allowed_document_ids(ragflow, inputs.get("scene"), dataset_ids)
        if document_ids is not None and not document_ids:
            logger.info(
                "scene %r: all %d documents excluded by scene_doc_excludes; empty knowledge",
                inputs.get("scene"), len(dataset_ids),
            )
            return ToolResult.success({"knowledge": []})

        questions = self._questions(inputs)

        rerank_model = getattr(self._settings, "ragflow_rerank_model", "")

        if len(questions) == 1:
            # 单 query 路径：retrieve 不支持 rerank_model 透传（客户端签名无此参数，
            # 曾注入导致 TypeError 被静默吞掉、检索恒空）；重排走下方客户端 rerank。
            kwargs: dict[str, Any] = {
                "question": questions[0], "dataset_ids": dataset_ids, "top_k": 5,
                "document_ids": document_ids,
            }
            try:
                chunks = await ragflow.retrieve(**kwargs)
            except Exception:  # noqa: BLE001
                logger.warning("rag retrieve failed; degrade to empty knowledge", exc_info=True)
                return ToolResult.success({"knowledge": []})
            if rerank_model and len(chunks) > 1:
                try:
                    chunks = await ragflow.rerank(questions[0], chunks, rerank_model)
                except Exception:  # noqa: BLE001
                    logger.warning("rag rerank failed; keep retrieval order", exc_info=True)
        else:
            # 多 query 模式（检索改写）：逐 query 检索后 RRF 融合（Reciprocal Rank
            # Fusion）。跨 query 的引擎分数不可比（各引擎内部度量），只有排位可比：
            # score = Σ 1/(k + rank)，被多个改写 query 同时检回的 chunk（共识证据）
            # 排位靠前；rerank 不参与（跨 query 排序语义不成立）
            rrf: dict[str, float] = {}
            best: dict[str, dict[str, Any]] = {}
            for question in questions:
                try:
                    chunks = await ragflow.retrieve(
                        question=question, dataset_ids=dataset_ids, top_k=5,
                        document_ids=document_ids,
                    )
                except Exception:  # noqa: BLE001 - 单条 query 失败容忍，其余继续
                    logger.warning("rag retrieve failed for query=%r; skip", question, exc_info=True)
                    continue
                for rank, c in enumerate(chunks):
                    key = c.get("chunk_id") or (str(c.get("content") or "")[:80])
                    if not key:
                        continue
                    rrf[key] = rrf.get(key, 0.0) + 1.0 / (_RRF_K + rank + 1)
                    prev = best.get(key)
                    if prev is None or _raw_score(c) > _raw_score(prev):
                        best[key] = c
            # RRF 降序 + 原始分数字典序稳定；score 归一化到 0-1（前端相关度口径）
            ordered = sorted(
                best.values(),
                key=lambda c: (
                    -rrf.get(c.get("chunk_id") or (str(c.get("content") or "")[:80]), 0.0),
                    -_raw_score(c),
                ),
            )
            top_rrf = max(rrf.values(), default=0.0)
            for c in ordered:
                key = c.get("chunk_id") or (str(c.get("content") or "")[:80])
                c["score"] = round(rrf.get(key, 0.0) / top_rrf, 4) if top_rrf else 0.0
            chunks = ordered

        knowledge = [
            {
                "content": c.get("content", "")[:2000],
                "score": round(float(c.get("score", c.get("rank", 0.0))), 4),
                "doc_name": c.get("doc_name", ""),
                "chunk_id": c.get("chunk_id"),
                # document_id 落库：引用原文预览按 id 直查文档，缺失时端点按 doc_name 反查
                "document_id": c.get("document_id"),
                # 页码透传：报告引用定位到来源页（需求 §5.3 引用溯源）
                "page": c.get("page"),
                "image_uri": ragflow.image_uri(c["image_id"]) if c.get("image_id") else None,
            }
            for c in chunks
            if c.get("content")
        ]
        return ToolResult.success({"knowledge": knowledge})

    # ---------- 内部 ----------
    def _route_datasets_by_scene(self, scene: str | None, default: list[str]) -> list[str]:
        """按场景路由专属数据集 id（rag_rules.yaml 的 scene_datasets 映射）；未配置回退默认全集。"""
        if not scene:
            return default
        rules_dir = getattr(self._settings, "rules_dir", None) if self._settings is not None else None
        mapping = load_rag_rules(rules_dir).get("scene_datasets") or {}
        if not isinstance(mapping, dict):
            return default
        routed = mapping.get(scene)
        if isinstance(routed, list) and routed:
            return [str(n) for n in routed if str(n).strip()]
        return default

    def _scene_exclude_patterns(self, scene: str | None) -> list[str]:
        """场景排除模式（scene_doc_excludes）："all" 键对一切场景生效 + 场景专属键。"""
        rules_dir = getattr(self._settings, "rules_dir", None) if self._settings is not None else None
        excludes = load_rag_rules(rules_dir).get("scene_doc_excludes") or {}
        if not isinstance(excludes, dict):
            return []
        patterns = [str(p) for p in (excludes.get("all") or [])]
        if scene:
            patterns += [str(p) for p in (excludes.get(str(scene)) or [])]
        return [p.casefold() for p in patterns if p.strip()]

    async def _scene_allowed_document_ids(
        self, ragflow: RAGFlowClient, scene: str | None, dataset_ids: list[str]
    ) -> list[str] | None:
        """场景文档一致性过滤：返回检索 document_ids 白名单。

        None = 不过滤（未配置规则 / 清单拉取失败 fail-open / 没有任何文档命中排除，
        避免无谓的大载荷）；[] = 全部被排除（调用方直接走空知识链）。
        文档名子串匹配、大小写不敏感；模式见 config/rules/rag_rules.yaml。
        """
        patterns = self._scene_exclude_patterns(scene)
        if not patterns:
            return None
        docs = await self._list_all_docs_cached(ragflow, dataset_ids)
        if docs is None:
            return None
        allowed = [
            doc_id for doc_id, name in docs
            if not any(p in name.casefold() for p in patterns)
        ]
        if len(allowed) == len(docs):
            return None  # 无文档命中排除 → 不附带过滤，保持请求精简
        logger.info(
            "scene doc filter (%r): %d/%d documents allowed (excluded by name patterns)",
            scene, len(allowed), len(docs),
        )
        return allowed

    async def _list_all_docs_cached(
        self, ragflow: RAGFlowClient, dataset_ids: list[str]
    ) -> list[tuple[str, str]] | None:
        """全量 (doc_id, doc_name) 清单，按 dataset TTL 缓存；任一拉取失败返回 None（fail-open）。"""
        now = time.monotonic()
        out: list[tuple[str, str]] = []
        for ds_id in dataset_ids:
            hit = self._docs_cache.get(ds_id)
            if hit is not None and now - hit[0] < _DOCS_CACHE_TTL_S:
                out.extend(hit[1])
                continue
            try:
                docs = await ragflow.list_all_documents(ds_id)
            except Exception:  # noqa: BLE001 - 清单不可得时放弃过滤，检索照常（不误伤召回）
                logger.warning("list documents failed for %s; skip scene doc filter", ds_id, exc_info=True)
                return None
            pairs = [(str(d.get("id")), str(d.get("name") or "")) for d in docs if d.get("id")]
            self._docs_cache[ds_id] = (now, pairs)
            out.extend(pairs)
        return out

    @staticmethod
    def _build_question(inputs: dict[str, Any]) -> str:
        """检索主题：场景 + 缺陷类型 + 测量参数（需求 §1.2）。"""
        topics: list[str] = []
        scene = inputs.get("scene")
        if scene:
            topics.append(f"{scene} 检测结果解读")
        for item in inputs.get("standardized") or []:
            if item.get("defect_type"):
                topics.append(f"{item['defect_type']} 缺陷判级标准")
            if item.get("name"):
                topics.append(f"{item['name']} 测量方法")
        return "；".join(dict.fromkeys(topics)) or "工业图像检测结果判级依据"

    @staticmethod
    def _questions(inputs: dict[str, Any]) -> list[str]:
        """retrieval query 列表：优先消费上游检索改写产生的 questions，缺省回退基准问题。"""
        provided = [str(q).strip() for q in (inputs.get("questions") or []) if str(q).strip()]
        return provided or [RagSearchTool._build_question(inputs)]