"""节点：异常原因总结（失败任务的收尾，__fail__ 之后无条件执行）。

LLM 可用时生成可读的原因总结（summary），并把失败上下文渲染为 error_report.md 落盘；
LLM/文件系统失败均降级：summary 回退原始 message，error_report 为 None。
本节点出边为无条件 END——绝不把 error 再路由回 __fail__（会造成死循环）。
"""
from __future__ import annotations

import asyncio
import datetime
import json
from typing import Any
from zoneinfo import ZoneInfo

from app.agents.llm.prompts import DEFAULT_LANGUAGE
from app.agents.nodes.node_utils import node
from app.services.deps import AgentContext

_BEIJING = ZoneInfo("Asia/Shanghai")


@node("explain_error")
async def explain_error_node(state: dict[str, Any], ctx: AgentContext) -> dict[str, Any]:
    error = state.get("error") or {}
    message = error.get("message") or "任务执行失败"

    summary = ""
    llm = ctx.llm
    if llm is not None and llm.available:
        language = (state.get("parameters") or {}).get("language") or DEFAULT_LANGUAGE
        try:
            summary = await llm.summarize_error(
                error=error,
                task_id=state.get("task_id"),
                scene=state.get("scene"),
                language=language,
            )
        except Exception:  # noqa: BLE001 - 可选增强
            summary = ""
    summary = (summary or "").strip() or message

    report_ref = None
    if ctx.file_repo is not None:
        try:
            uri = await asyncio.to_thread(
                ctx.file_repo.save_text,
                state["task_id"],
                "error_report.md",
                _render_error_markdown(state, error, summary),
                kind="reports",
            )
            report_ref = {
                "report_type": "error_report",
                "name": f"{state['task_id']}_error_report.md",
                "uri": uri,
                "version": "1.0.0",
                "created_at": datetime.datetime.now(_BEIJING).isoformat(),
            }
        except Exception:  # noqa: BLE001 - 落盘失败不改变失败结论
            report_ref = None

    return {"error": {**error, "summary": summary}, "error_report": report_ref}


def _render_error_markdown(state: dict[str, Any], error: dict[str, Any], summary: str) -> str:
    ts = datetime.datetime.now(_BEIJING).strftime("%Y-%m-%d %H:%M:%S")
    context = {"error": error, "scene": state.get("scene"), "mode": state.get("mode"),
               "trace_id": state.get("trace_id")}
    return "\n".join([
        f"# 智能质检任务失败说明 {state.get('task_id', '-')}",
        "",
        f"- 生成时间：{ts}",
        f"- 失败节点：{error.get('node', '-')}",
        "",
        "## 1. 失败概要",
        "",
        summary,
        "",
        "## 2. 失败上下文",
        "",
        "```json",
        json.dumps(context, ensure_ascii=False, default=str, indent=2),
        "```",
        "",
        "---",
        "> 该任务未通过质检流程，请按上述原因处理后重试或转人工处理。",
    ])