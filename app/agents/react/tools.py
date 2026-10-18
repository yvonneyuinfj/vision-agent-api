"""ReAct 工具箱：invoke_capability / retrieve_knowledge / submit_final。

设计定案（决策 6/7）：
- 判断类动作给智能体：选场景、自救重试、检索时机、提交时机；
- 确定性动作焊在工具内部/出口：invoke_capability 返回前自动标准化（复用
  app.capabilities.normalizers），submit_final 只收集提交意图，判级终裁与
  复核门由出口节点运行时执行。

实现分两层：
- impl：真实执行函数 `(args, state, ctx) -> (content, side_state)`，
  side_state 由 react_tools 节点顺序合并进图状态（知识跨轮累积、
  标准化结果最后一次能力调用为准）；
- lc_tools：仅供 `model.bind_tools` 的 schema 包装（LLM 只需要看到
  工具签名与说明，执行走 impl 分发，便于旁路写状态）。
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, Awaitable, Callable

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from app.agents.llm.prompts import format_knowledge_contexts
from app.agents.nodes.rag_retrieval import (
    _apply_score_threshold,
    _merge_knowledge,
    _render_kb_prompt,
)
from app.capabilities.normalizers import (
    max_confidence,
    max_severity,
    min_confidence,
    normalize_result,
)
from app.core.errors import BizCode
from app.core.logger import get_logger
from app.services.deps import AgentContext

logger = get_logger("app.agents.react.tools")

ToolImpl = Callable[[dict[str, Any], dict[str, Any], AgentContext], Awaitable[tuple[str, dict[str, Any]]]]

_SUBMIT_DEFECTS_CAP = 50  # 防御：智能体手写的缺陷列表长度上限


# --------------------------------------------------------------------------
# invoke_capability：场景检测（调用 + 自动标准化，决策 6"焊进工具内部"）
# --------------------------------------------------------------------------
async def _invoke_capability_impl(
    args: dict[str, Any], state: dict[str, Any], ctx: AgentContext
) -> tuple[str, dict[str, Any]]:
    scene = str(args.get("scene") or "").strip().lower()
    calls = int(state.get("capability_calls") or 0)
    max_calls = int(getattr(ctx.settings, "react_max_capability_calls", 3))

    def _tried(ok: bool, defect_count: int | None, summary: str) -> dict[str, Any]:
        """scenes_tried 追加条目（状态栏数据源）：尝试即记录，成败都算一次。"""
        tried = [t for t in (state.get("scenes_tried") or []) if isinstance(t, dict)]
        tried.append(
            {"scene": scene or "?", "ok": ok, "defect_count": defect_count, "summary": summary}
        )
        return {"scenes_tried": tried}

    if not scene:
        return "调用失败：必须提供 scene 参数。", {}
    if calls >= max_calls:
        return (
            f"已达能力调用上限（{max_calls} 次，含自救重试）。"
            "请基于已有结果调用 submit_final 提交，或说明拒检原因。",
            {},
        )
    if ctx.registry is None or ctx.tool_registry is None or ctx.file_repo is None:
        return (
            "平台能力未就绪（注册中心/工具/文件仓库缺失），无法执行检测。",
            _tried(False, None, "平台能力未就绪"),
        )

    capability = ctx.registry.lookup(scene)
    if capability is None:
        registered = sorted({m.scene for m in ctx.registry.list()})
        return (
            f"场景 {scene} 无可用能力。已注册场景：{', '.join(registered) or '无'}。"
            "请重新判断图片场景或提交拒检。",
            {**_tried(False, None, "场景无可用能力"), "capability_calls": calls + 1},
        )

    tool = ctx.tool_registry.get(f"detect/{capability.tool_name}")
    if tool is None:
        return (
            f"检测工具 detect/{capability.tool_name} 未装配，无法执行 {scene} 检测。",
            {**_tried(False, None, "工具未装配"), "capability_calls": calls + 1},
        )

    try:
        image_paths = [str(ctx.file_repo.uri_to_path(uri)) for uri in state.get("input_uris", [])]
        auxiliary_paths = {
            name: str(ctx.file_repo.uri_to_path(uri))
            for name, uri in (state.get("auxiliary") or {}).items()
        }
    except ValueError as exc:
        return (
            f"输入 URI 非法：{exc}",
            {**_tried(False, None, "输入 URI 非法"), "capability_calls": calls + 1},
        )

    result = await tool.execute(
        {
            "task_id": state.get("task_id", ""),
            "trace_id": state.get("trace_id", ""),
            "image_paths": image_paths,
            "auxiliary_paths": auxiliary_paths,
            "parameters": state.get("parameters") or {},
            "file_repo": ctx.file_repo,
        }
    )
    side: dict[str, Any] = {"capability_calls": calls + 1}
    if not result.ok:
        side.update(_tried(False, None, f"检测失败：{result.message}"))
        return (
            f"{scene} 检测失败（错误码 {result.code}）：{result.message}。"
            "可重新判断场景后重试，或基于已有信息提交。",
            side,
        )

    raw_envelope = result.result or {}
    # ToolResult.success({"raw_result": raw})：先解包外壳，与固定图
    # invoke_capability_node（state["raw_result"]=raw）+ normalize_result_node
    # （raw.get("result")）两层取值完全对齐
    raw = raw_envelope.get("raw_result") or {}
    tool_result = raw.get("result") or {}
    entries, family = normalize_result(
        capability.capability_type or "defect_detection", tool_result
    )
    summary = tool_result.get("summary") or (
        "未发现异常" if not entries else f"检出 {len(entries)} 项"
    )
    side.update(
        {
            "scene": scene,
            "selected_capability": capability.model_dump(),
            "raw_result": raw,
            "standardized": entries,
            # 类别定义表通道（与固定图 normalize_result_node 对齐；报告/前端图例消费）
            "class_definitions": tool_result.get("class_definitions") or [],
            "grade_hint": {
                "family": family,
                "status": tool_result.get("status", "unknown"),
                "summary": summary,
                "max_severity": max_severity(entries) if family == "defect" else None,
                "min_confidence": min_confidence(entries) if family == "defect" else None,
                # 判级复核门依据（需求：检测结果低于阈值才转人工）：取最高置信度
                "max_confidence": max_confidence(entries) if family == "defect" else None,
            },
        }
    )
    side.update(_tried(True, len(entries), summary))
    digest = {
        "scene": scene,
        "status": tool_result.get("status", "unknown"),
        "summary": summary,
        "defect_count": len(entries),
        "entries": [
            {
                # 缺陷族键为 defect_type（分类族为 label）：此前只读 type/label，
                # 缺陷条目恒显 null，智能体误判「能力未提供类型字段」并因此
                # 频繁请求人工复核（2026-09-15 T3824612667357509 回归）
                "type": e.get("defect_type") or e.get("type") or e.get("label"),
                "severity": e.get("severity"),
                "confidence": e.get("confidence"),
                # location 供智能体原样回填 submit_final（前端标注框绘制依赖）
                "location": e.get("location"),
                # 多图 fan-out 溯源：条目来自哪张输入图（单图任务无此字段）
                "image_name": e.get("image_name"),
            }
            for e in entries[:10]
        ],
    }
    return json.dumps(digest, ensure_ascii=False), side


class _InvokeCapabilityArgs(BaseModel):
    scene: str = Field(description="检测场景编码（如 borescope/infrared/dr/fpi/fracture/metallography）")


# --------------------------------------------------------------------------
# retrieve_knowledge：知识库检索（何时查、查什么由智能体决定）
# --------------------------------------------------------------------------
async def _retrieve_knowledge_impl(
    args: dict[str, Any], state: dict[str, Any], ctx: AgentContext
) -> tuple[str, dict[str, Any]]:
    query = str(args.get("query") or "").strip()
    if not query:
        return "调用失败：必须提供非空 query。", {}

    tool = ctx.tool_registry.get("knowledge/rag") if ctx.tool_registry is not None else None
    if tool is None:
        return "知识库工具未装配，无法检索（可跳过检索直接提交）。", {}
    if "rag" in ctx.engine_ids:
        # 短路熔断（读取方）：本任务内 RAG 工具链已失败过，不再重复打故障引擎
        return "知识检索在本任务内已熔断（此前调用失败）；请基于已有信息提交结论。", {}

    result = await tool.execute(
        {
            "scene": state.get("scene"),
            "standardized": state.get("standardized") or [],
            "top_k": 5,
            "questions": [query],
        }
    )
    if not result.ok:
        ctx.engine_ids.add("rag")  # 标记熔断：本任务后续检索直接短路
        return f"知识检索失败：{result.message}", {}
    if not isinstance(result.result, dict):
        return "未检索到相关知识条目。", {}

    batch = result.result.get("knowledge") or []
    batch = await asyncio.to_thread(_apply_score_threshold, batch, ctx)
    merged = _merge_knowledge(state.get("knowledge") or [], batch)
    trace = list(state.get("retrieval_trace") or [])
    trace.append(
        {
            "round": len(trace) + 1,
            "queries": [query],
            "hits": len(batch),
            "top_score": round(max((float(k.get("score") or 0.0) for k in batch), default=0.0), 4),
        }
    )
    side = {
        "knowledge": merged,
        "retrieval_trace": trace,
        "knowledge_prompt": await asyncio.to_thread(_render_kb_prompt, state, merged),
    }
    if not batch:
        return f"未检索到与「{query}」相关的知识条目。", side
    return format_knowledge_contexts(batch), side


class _RetrieveKnowledgeArgs(BaseModel):
    query: str = Field(description="检索问题（建议围绕检出缺陷的成因、判级依据或处置标准提问）")


# --------------------------------------------------------------------------
# submit_final：提交检测结论 / 声明拒检（出口安检入口）
# --------------------------------------------------------------------------
async def _submit_final_impl(
    args: dict[str, Any], state: dict[str, Any], ctx: AgentContext
) -> tuple[str, dict[str, Any]]:
    refusal = str(args.get("refusal_reason") or "").strip()
    if refusal:
        return (
            "已记录拒检结论：任务将以失败终态收场，并附原因解释与引导。",
            {"submitted": True, "submit_payload": {"refused": True, "reason": refusal}},
        )

    defects = args.get("defects")
    # 宽容解析：模型高频错误是把数组序列化成 JSON 字符串（如 "[]"），接住它而不是冷拒
    if isinstance(defects, str):
        try:
            defects = json.loads(defects)
        except (json.JSONDecodeError, TypeError):
            pass  # 解析不了保留原值，下面走教学式报错
    if defects is None:
        defects = state.get("standardized") or []
    if not isinstance(defects, list):
        return (
            "提交失败：defects 必须是缺陷条目组成的 JSON 数组，例如：\n"
            '[{"defect_type": "crack", "severity": "low", "confidence": 0.8, '
            '"description": "紫外暗场下盘面可见网状荧光迹痕，疑似裂纹"}]\n'
            "省略 defects 则默认使用最近一次 invoke_capability 的检测结果。\n"
            '提示：若你传的是字符串化的数组（如 "[]"），请改为直接传数组。',
            {},
        )
    defects = [d for d in defects if isinstance(d, dict)][:_SUBMIT_DEFECTS_CAP]

    review_required = bool(args.get("review_required") or False)
    review_reason = str(args.get("review_reason") or "").strip()
    if review_required and not review_reason:
        return (
            "提交被退回：你请求了人工复核（review_required=true），还必须填写 review_reason，"
            "说明异议依据（例如：能力报告无异常，但图像中可见何种缺陷显示、为何疑似漏检/误报）。",
            {},
        )

    if (
        not defects
        and not int(state.get("capability_calls") or 0)
        and not int(state.get("children_settled") or 0)
    ):
        # 联合轨父任务以落定子任务数为检测凭据（决策 10）
        return (
            "提交被退回：尚未执行任何检测。请先调用 invoke_capability；"
            "若图片不属于任何支持场景，请改为填写 refusal_reason 拒检。",
            {},
        )

    payload = {
        "refused": False,
        "defects": defects,
        "summary": str(args.get("summary") or "").strip(),
        "analysis": str(args.get("analysis") or "").strip(),
        "review_required": review_required,
        "review_reason": review_reason,
    }
    ack = "已接收提交，进入出口安检（判级终裁与复核门由运行时强制执行）。"
    if review_required:
        ack = (
            "已接收提交：你的人工复核请求（review_required=true）已随提交送达判级终裁，"
            "任务将以「待人工复核」终态收场。"
        )
    return ack, {
        "submitted": True,
        "submit_payload": payload,
    }


class _SubmitFinalArgs(BaseModel):
    defects: list[dict[str, Any]] | None = Field(
        default=None,
        description=(
            "最终缺陷/结果条目数组；省略则使用最近一次 invoke_capability 的标准化结果。"
            "条目字段：defect_type（类型）、severity（low/medium/high，无依据可不填）、"
            "confidence（0-1，无依据可不填）、description（描述）、"
            "location（原样复制 invoke_capability 返回的 location 对象，勿改写坐标）、"
            "image_name（多图任务必带：原样保留 invoke_capability 返回条目的 image_name，"
            "标注缺陷来自哪张输入图；缺失会导致框在所有图上叠加显示）。"
            "当你的视觉判断与能力结果矛盾时，可在此手写基于视觉证据的条目"
            "（description 注明'视觉判断'），或改用 review_required 请求人工复核"
        ),
    )
    summary: str = Field(default="", description="检测结论一句话摘要")
    analysis: str = Field(default="", description="分析段（Markdown）：缺陷成因/风险/处置建议，将写入报告")
    refusal_reason: str = Field(
        default="",
        description="拒检原因：图片不属于任何支持场景时填写（填写后其余字段忽略）",
    )
    review_required: bool = Field(
        default=False,
        description=(
            "请求人工复核：**仅限**能力结果与你的视觉证据存在实质矛盾时置 true"
            "（能力报无异常但图像清晰可见缺陷显示＝疑似漏检；能力报缺陷但该位置"
            "视觉上明确是伪显示/背景污染＝疑似误报）。"
            "以下情形**不要**请求复核，属正常判级路径：条目字段缺失（类型/严重度未提供）、"
            "存在低置信度条目、检出数量与视觉计数不完全一致、需要补充处置建议。"
            "低置信度结果由运行时阈值规则自动决定是否转人工，不需你代为判断。"
            "置 true 时必须同时填写 review_reason 说明矛盾点"
        ),
    )
    review_reason: str = Field(
        default="",
        description=(
            "请求人工复核的理由：具体说清视觉证据与能力结果的矛盾点"
            "（哪个位置、看到什么、能力报了什么、为何判定为漏检或误报）。"
            "review_required=true 时必填"
        ),
    )


async def _dispatch_guard(**_kwargs: Any) -> str:  # pragma: no cover - 仅满足 schema 包装
    """lc_tools 仅供 bind_tools 取 schema；真实执行经 react_tools 节点按 impl 分发。"""
    raise NotImplementedError("ReAct 工具必须经 react_tools 节点分发执行")


# --------------------------------------------------------------------------
# 工具箱装配
# --------------------------------------------------------------------------
class ReactToolbox:
    """impl 分发表 + bind_tools schema 列表。"""

    def __init__(self) -> None:
        self.impls: dict[str, ToolImpl] = {
            "invoke_capability": _invoke_capability_impl,
            "retrieve_knowledge": _retrieve_knowledge_impl,
            "submit_final": _submit_final_impl,
        }
        self.lc_tools: list[StructuredTool] = [
            StructuredTool.from_function(
                coroutine=_dispatch_guard,
                name="invoke_capability",
                description=(
                    "调用指定场景的缺陷检测能力并自动标准化结果。"
                    "失败时返回错误说明，可重新判断场景后重试。"
                ),
                args_schema=_InvokeCapabilityArgs,
            ),
            StructuredTool.from_function(
                coroutine=_dispatch_guard,
                name="retrieve_knowledge",
                description="按问题检索质检知识库，返回相关判级依据与处置标准条目。",
                args_schema=_RetrieveKnowledgeArgs,
            ),
            StructuredTool.from_function(
                coroutine=_dispatch_guard,
                name="submit_final",
                description=(
                    "提交最终检测结论（出口安检入口：判级与复核门由运行时强制）。"
                    "图片不属于任何支持场景时填写 refusal_reason 拒检。"
                    "当能力结果与你的视觉证据矛盾（疑似漏检/误报）时，"
                    "可手写视觉缺陷条目，或置 review_required=true 并填写 review_reason 请求人工复核。"
                ),
                args_schema=_SubmitFinalArgs,
            ),
        ]

    def export_schemas(self) -> list[dict[str, Any]]:
        """导出工具 schema（会话层/调试用）。"""
        return [
            {"name": t.name, "description": t.description, "schema": t.args_schema.model_json_schema()}
            for t in self.lc_tools
        ]


def build_react_tools(ctx: AgentContext) -> ReactToolbox:
    """装配工具箱（ctx 供 impl 使用；lc_tools 与 ctx 无关）。"""
    _ = ctx  # impl 在分发时接收 ctx，装配阶段无需绑定
    return ReactToolbox()


# BizCode 在拒检出口使用，显式导出供图模块引用
__all__ = [
    "ReactToolbox",
    "build_react_tools",
    "BizCode",
]
