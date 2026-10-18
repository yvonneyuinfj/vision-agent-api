"""节点：知识增强（经 ToolRegistry 动态解析 RAG 工具）。

从 ctx.tool_registry 取 knowledge/rag 工具执行检索；未配置知识库时
工具降级返回空引用（报告标注"无知识支撑"），不阻塞主流程。
零缺陷任务（standardized 为空）没有判级对象，不发起检索，同样走
空知识链——避免泛化场景问句召回低相关文献与判级结论自相矛盾。

检索完成后把检索列表渲染为 default_kb 系统提示词（contexts + language，
默认中文）写入 state['knowledge_prompt']，供下游 LLM 调用挂载。
检索结果为空时渲染空知识声明版提示词（六节 + 语言规则完整保留），
保证知识未命中时 AI 综合分析仍经过 LLM 评判。

规则加载（load_rag_rules / DEFAULT_RAG_RULES）已下沉至
app/agents/tools/rag_rules.py（独立模块，避免 rag_tool ↔ 本节点循环导入）。
"""
from __future__ import annotations

import asyncio
from typing import Any

from app.agents.llm.prompts import DEFAULT_LANGUAGE, build_kb_system_prompt
from app.agents.nodes.node_utils import node
from app.agents.tools.rag_rules import DEFAULT_RAG_RULES, load_rag_rules
from app.agents.tools.rag_tool import RagSearchTool
from app.core.logger import get_logger
from app.services.deps import AgentContext

logger = get_logger("app.agents.nodes.rag_retrieval")


MAX_RETRIEVAL_ROUNDS = 3       # 有界多轮上限：控制单任务检索成本与延迟
RETRIEVAL_SCORE_THRESHOLD = 0.5  # 合并结果 top 分达到阈值即认为可信，跳过判定
MAX_QUERIES_PER_ROUND = 4


def _apply_score_threshold(knowledge: list[dict[str, Any]], ctx: AgentContext) -> list[dict[str, Any]]:
    """仅保留 score 大于 yaml 阈值的引用（等于阈值不采纳，需求措辞「大于」）。"""
    rules_dir = getattr(ctx.settings, "rules_dir", None) if ctx.settings is not None else None
    threshold = float(load_rag_rules(rules_dir).get(
        "knowledge_score_threshold", DEFAULT_RAG_RULES["knowledge_score_threshold"]
    ))
    kept = [k for k in knowledge if float(k.get("score") or 0.0) > threshold]
    if len(kept) < len(knowledge):
        logger.info(
            "knowledge score filter: %d -> %d (threshold=%s)", len(knowledge), len(kept), threshold
        )
    return kept


@node("rag_retrieval")
async def rag_retrieval_node(state: dict[str, Any], ctx: AgentContext) -> dict[str, Any]:
    # 零缺陷任务没有判级对象：检索主题退化为泛化场景问句，召回一堆低相关文献，
    # 与「未检出缺陷」的判级结论自相矛盾（需求 §1.2 检索主题由缺陷驱动）。
    # 直接走空知识链，与未装配 RAG 工具的降级分支同形。
    if not state.get("standardized"):
        return {"knowledge": [], "retrieval_trace": [],
                "knowledge_prompt": await asyncio.to_thread(_render_kb_prompt, state, [])}

    tool_registry = ctx.tool_registry
    tool = tool_registry.get("knowledge/rag") if tool_registry is not None else None
    if tool is None:
        # 降级：RAG 工具未装配（未配置 RAGFlow 等场景）→ 允许空知识链，仍渲染空知识版提示词
        return {"knowledge": [], "retrieval_trace": [], "knowledge_prompt": await asyncio.to_thread(_render_kb_prompt, state, [])}

    language = (state.get("parameters") or {}).get("language") or DEFAULT_LANGUAGE
    base_question = RagSearchTool._build_question(
        {"scene": state.get("scene"), "standardized": state.get("standardized") or []}
    )
    llm = ctx.llm if ctx.llm is not None and ctx.llm.available else None

    knowledge: list[dict[str, Any]] = []
    trace: list[dict[str, Any]] = []
    queries = _cap_queries([base_question] + await _llm_variants(llm, state, language, missing=""))

    for rnd in range(1, MAX_RETRIEVAL_ROUNDS + 1):
        batch = await _fetch(tool, state, queries)
        if batch is None:
            ctx.engine_ids.add("rag")
            break
        knowledge = _merge_knowledge(knowledge, batch)
        top_score = max((float(k.get("score") or 0.0) for k in knowledge), default=0.0)
        entry = {"round": rnd, "queries": queries, "hits": len(batch),
                 "top_score": round(top_score, 4)}
        if top_score >= RETRIEVAL_SCORE_THRESHOLD or rnd == MAX_RETRIEVAL_ROUNDS or llm is None:
            trace.append(entry)
            break
        judged = await _safe_judge(llm, base_question, knowledge)
        entry["judged"] = judged
        trace.append(entry)
        if judged.get("sufficient", True):
            break
        queries = _cap_queries(
            await _llm_variants(llm, state, language, missing=judged.get("missing") or "")
        )
        if not queries:
            break

    # 知识准入：仅 score 大于 yaml 阈值的引用注入模型上下文（rag_rules.yaml）
    knowledge = await asyncio.to_thread(_apply_score_threshold, knowledge, ctx)

    return {"knowledge": knowledge, "retrieval_trace": trace,
            "knowledge_prompt": await asyncio.to_thread(_render_kb_prompt, state, knowledge)}


async def _llm_variants(llm, state: dict[str, Any], language: str, *, missing: str) -> list[str]:
    if llm is None:
        return []
    try:
        variants = await llm.build_retrieval_queries(
            scene=state.get("scene"),
            standardized=state.get("standardized") or [],
            language=language,
            missing=missing,
        )
    except Exception:  # noqa: BLE001 - 服务层已降级，此处兜底意外异常
        return []
    return _cap_queries([v for v in (variants or []) if str(v).strip()])


async def _safe_judge(llm, question: str, knowledge: list[dict[str, Any]]) -> dict[str, Any]:
    try:
        return await llm.judge_retrieval(question=question, knowledge=knowledge)
    except Exception:  # noqa: BLE001 - 判定失败 fail-open（不再追加）
        return {"sufficient": True, "missing": ""}


async def _fetch(tool, state: dict[str, Any], queries: list[str]) -> list[dict[str, Any]] | None:
    """执行一轮检索；工具失败返回 None（调用方熔断），成功返回 knowledge 列表。"""
    result = await tool.execute({
        "scene": state.get("scene"),
        "standardized": state.get("standardized") or [],
        "top_k": 5,
        "questions": queries,
    })
    if not result.ok:
        return None
    if not isinstance(result.result, dict):
        return []
    return result.result.get("knowledge") or []


def _cap_queries(queries: list[str]) -> list[str]:
    """保序去重 + 截断到单轮 query 上限。"""
    seen: list[str] = []
    for q in queries:
        q = str(q).strip()
        if q and q not in seen:
            seen.append(q)
    return seen[:MAX_QUERIES_PER_ROUND]


def _merge_knowledge(base: list[dict[str, Any]], extra: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """跨轮合并去重：同一 chunk_id（缺失时按内容前 80 字符）保留最高分。"""
    merged: dict[str, dict[str, Any]] = {}
    for c in base + extra:
        key = c.get("chunk_id") or (str(c.get("content") or "")[:80])
        if not key:
            continue
        score = float(c.get("score") or 0.0)
        prev = float(merged[key].get("score") or 0.0) if key in merged else -1.0
        if score > prev:
            merged[key] = c
    return list(merged.values())


def _render_kb_prompt(state: dict[str, Any], knowledge: list[dict[str, Any]]) -> str:
    """检索列表 -> default_kb 提示词；language 取 parameters.language，默认中文。

    空知识列表渲染空知识声明版（build_kb_system_prompt 内置）；渲染失败
    （模板缺失等）不阻塞主流程，降级为空提示词。
    """
    language = (state.get("parameters") or {}).get("language") or DEFAULT_LANGUAGE
    try:
        return build_kb_system_prompt(knowledge, language=language)
    except Exception:  # noqa: BLE001 - 提示词渲染失败不阻塞主流程
        logger.warning("render default_kb prompt failed; degrade to empty knowledge prompt")
        return ""
