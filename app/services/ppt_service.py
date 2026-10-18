"""PPT 生成服务：大纲（内网 LLM / 降级模板）→ 渲染 .pptx → 落盘为任务产物。

复用 tasks 表与文件下载链路（mode='document'）：产物 URI 走
`/api/v1/tasks/{id}/files/{name}?kind=artifacts`，前端按 task_id 轮询状态、下载。
LLM 网关不可用时自动降级为确定性模板，保证"提交即出片"。
"""
from __future__ import annotations

import json
import re
from typing import Any

from app.core.logger import get_logger
from app.models.task import STATUS_COMPLETED, STATUS_FAILED, STATUS_RUNNING
from app.services.doc_parse import extract_text
from app.services.ppt_render import render_pptx

logger = get_logger("app.services.ppt")

# 参考资料喂给 LLM 的截断上限（字符），防止超长文档撑爆上下文
_MATERIAL_CAP = 6000

_SYSTEM_PROMPT = (
    "你是专业的演示文稿策划助手。只输出一个 JSON 对象，不要任何解释或代码围栏。"
    "结构严格为：{\"title\": str, \"subtitle\": str, "
    "\"sections\": [{\"heading\": str, \"bullets\": [str, ...]}], \"closing\": str}。"
    "要求：title 为汇报主题；subtitle 一句话副标题；sections 数量约等于目标页数，"
    "每节 heading 为页标题、bullets 为该页 2-5 条要点（每条约 10-40 字）；closing 为末页致谢。"
    "内容须围绕给定主题与参考资料，使用简体中文。"
)


def _parse_outline(text: str) -> dict[str, Any] | None:
    """把 LLM 回复解析成大纲 dict；不合规返回 None（触发降级）。"""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z]*\n?", "", cleaned)
        cleaned = re.sub(r"\n?```$", "", cleaned)
    try:
        start, end = cleaned.index("{"), cleaned.rindex("}")
        data = json.loads(cleaned[start : end + 1])
    except (ValueError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("sections"), list):
        return None
    sections: list[dict[str, Any]] = []
    for sec in data["sections"]:
        if not isinstance(sec, dict):
            continue
        bullets = [str(b).strip() for b in (sec.get("bullets") or []) if str(b).strip()]
        sections.append({"heading": str(sec.get("heading") or "").strip(), "bullets": bullets})
    if not sections:
        return None
    return {
        "title": str(data.get("title") or "").strip(),
        "subtitle": str(data.get("subtitle") or "").strip(),
        "sections": sections,
        "closing": str(data.get("closing") or "谢谢观看").strip(),
    }


def _normalize_outline(raw: Any) -> dict[str, Any] | None:
    """校验/规整一份手动大纲 dict；无有效 sections 返回 None。"""
    if not isinstance(raw, dict):
        return None
    sections: list[dict[str, Any]] = []
    for sec in raw.get("sections") or []:
        if not isinstance(sec, dict):
            continue
        heading = str(sec.get("heading") or "").strip()
        bullets_raw = sec.get("bullets")
        if isinstance(bullets_raw, str):
            bullets = [ln.strip(" -\u2022*\t") for ln in bullets_raw.splitlines() if ln.strip()]
        else:
            bullets = [str(b).strip() for b in (bullets_raw or []) if str(b).strip()]
        if heading or bullets:
            sections.append({"heading": heading, "bullets": bullets})
    if not sections:
        return None
    return {
        "title": str(raw.get("title") or "").strip(),
        "subtitle": str(raw.get("subtitle") or "").strip(),
        "sections": sections,
        "closing": str(raw.get("closing") or "谢谢观看").strip(),
    }


def _manual_outline(params: dict[str, Any]) -> dict[str, Any] | None:
    """若请求带了自定义大纲（params['outline']）且合规，返回规整后大纲，否则 None。"""
    raw = params.get("outline")
    return _normalize_outline(raw) if isinstance(raw, dict) else None


def _build_prompt(params: dict[str, Any], materials_text: str) -> str:
    topic = str(params.get("topic") or params.get("title") or "").strip()
    audience = str(params.get("audience") or "").strip()
    style = str(params.get("style") or "简洁商务").strip()
    pages = str(params.get("pages") or "").strip()
    request = str(params.get("request") or "").strip()
    lines = [
        f"主题：{topic or '（未填写，请据参考资料拟定）'}",
        f"目标听众：{audience or '通用听众'}",
        f"期望页数：{pages or '约 8 页'}",
        f"呈现风格：{style}",
    ]
    if request:
        lines.append(f"补充要求：{request}")
    if materials_text:
        lines.append("参考资料（节选）：\n" + materials_text[:_MATERIAL_CAP])
    return "\n".join(lines)


def _outline_from_template(params: dict[str, Any], materials_text: str) -> dict[str, Any]:
    """无 LLM 时的确定性大纲：主题 + 需求要点 + 资料要点 + 结论。"""
    topic = str(params.get("topic") or params.get("title") or "演示文稿").strip()
    audience = str(params.get("audience") or "").strip()
    request = str(params.get("request") or "").strip()

    def _lines(src: str, limit: int) -> list[str]:
        out = [ln.strip(" -•*\t") for ln in src.splitlines() if ln.strip()]
        return [ln[:60] for ln in out[:limit]]

    sections: list[dict[str, Any]] = []
    if request:
        sections.append({"heading": "需求与目标", "bullets": _lines(request, 6) or [request[:60]]})
    if materials_text:
        sections.append({"heading": "参考资料要点", "bullets": _lines(materials_text, 6)})
    sections.append(
        {
            "heading": "内容概览",
            "bullets": [f"围绕「{topic}」展开", audience and f"面向：{audience}", "关键结论与下一步"],
        }
    )
    sections = [s for s in sections if s["bullets"]]
    return {
        "title": topic,
        "subtitle": audience or "智能生成演示大纲",
        "sections": sections,
        "closing": "谢谢观看",
    }


class PptService:
    """PPT 生成门面（依赖注入：llm / file_repo / task_repo，均可缺省降级）。"""

    def __init__(self, *, llm: Any | None, file_repo: Any, task_repo: Any) -> None:
        self._llm = llm
        self._file_repo = file_repo
        self._task_repo = task_repo

    async def build_outline(self, params: dict[str, Any], materials_text: str) -> tuple[dict[str, Any], str]:
        """返回 (大纲, 引擎标记 'manual'|'llm'|'template')。手动大纲优先；LLM 不可用/解析失败降级模板。"""
        manual = _manual_outline(params)
        if manual is not None:
            return manual, "manual"
        if self._llm is not None and getattr(self._llm, "available", False):
            try:
                reply = await self._llm.chat(_build_prompt(params, materials_text), system_prompt=_SYSTEM_PROMPT)
                outline = _parse_outline(reply or "")
                if outline is not None:
                    return outline, "llm"
                logger.warning("ppt outline from LLM unparsable; fallback to template")
            except Exception as exc:  # noqa: BLE001 - 网关异常降级，不让任务失败
                logger.warning("ppt LLM outline failed; fallback to template: %s", exc)
        return _outline_from_template(params, materials_text), "template"

    async def run(
        self,
        task_id: str,
        params: dict[str, Any],
        materials: list[tuple[str, bytes]],
        template_bytes: bytes | None = None,
    ) -> None:
        """后台执行：解析资料 → 大纲（手动/LLM/模板）→ 渲染（可套模板）→ 落盘产物 → 写状态。"""
        await self._task_repo.set_status(task_id, STATUS_RUNNING)
        await self._task_repo.append_step(task_id, "document", "开始生成演示文稿")
        try:
            texts: list[str] = []
            notes: list[str] = []
            # 手动大纲无需资料喂给 LLM，跳过解析省时
            if _manual_outline(params) is None:
                for name, data in materials:
                    text, err = extract_text(name, data)
                    if err:
                        notes.append(f"{name}: {err}")
                    if text:
                        texts.append(f"# {name}\n{text}")
            materials_text = "\n\n".join(texts)

            outline, engine = await self.build_outline(params, materials_text)
            blob = await _to_thread(
                render_pptx, outline, str(params.get("style") or "简洁商务"), template_bytes
            )
            uri = self._file_repo.save_bytes(task_id, "presentation.pptx", blob, kind="artifacts")

            await self._task_repo.patch_json(
                task_id,
                report_json={
                    "uri": uri,
                    "filename": "presentation.pptx",
                    "format": "pptx",
                    "engine": engine,
                    "slide_count": len(outline.get("sections") or []) + 2,
                    "material_notes": notes,
                },
            )
            engine_label = {"llm": "LLM 大纲", "template": "模板大纲", "manual": "自定义大纲"}.get(engine, "大纲")
            await self._task_repo.set_status(task_id, STATUS_COMPLETED)
            await self._task_repo.append_step(task_id, "document", f"演示文稿已生成（{engine_label}）")
        except Exception as exc:  # noqa: BLE001 - 后台任务异常收敛为 failed
            logger.exception("ppt generation failed for task %s", task_id)
            await self._task_repo.patch_json(
                task_id, error_json={"message": f"{type(exc).__name__}: {exc}"[:400]}
            )
            await self._task_repo.set_status(task_id, STATUS_FAILED)


async def _to_thread(fn, *args: Any) -> Any:
    """把 CPU 型渲染放到线程池，避免阻塞事件循环。"""
    import asyncio

    return await asyncio.to_thread(fn, *args)
