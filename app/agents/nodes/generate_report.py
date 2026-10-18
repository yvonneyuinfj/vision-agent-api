"""节点：报告生成（需求 §5.5 报告查看 / §2.2 报告归档和全链路留痕）。

聚合：任务信息、能力信息、标准化结果、RAG 知识引用、判级结论、人工复核记录。
产物落盘为 Markdown（后续可扩展 PDF/Word 模板渲染），URI 供前端下载。
由 LLM 生成 AI 综合分析（default_kb 模板六节结构，对齐详情页，失败降级省略）。
"""
from __future__ import annotations

import asyncio
import datetime
import json
import re
from typing import Any
from zoneinfo import ZoneInfo

from app.agents.nodes.node_utils import error_state, node
from app.core.errors import BizCode
from app.services.deps import AgentContext

_BEIJING = ZoneInfo("Asia/Shanghai")


def _now_beijing() -> datetime.datetime:
    """报告时间固定使用北京时间墙钟（服务器可能部署在非北京时区）。"""
    return datetime.datetime.now(_BEIJING)


# 六节小节标题形如「### 五 · 综合判级结论与处置建议」：编号 + 间隔号，前缀 # 可省略
_SECTION_HEAD_RE = re.compile(r"^\s*(?:#{1,4}\s*)?[一二三四五六]\s*·")


def _section_after(text: str, keyword: str) -> str:
    """抽取标题含 keyword 的小节正文（至下一个「N · 」标题或文末）。"""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if keyword in line and _SECTION_HEAD_RE.match(line):
            body: list[str] = []
            for nxt in lines[i + 1:]:
                if _SECTION_HEAD_RE.match(nxt):
                    break
                body.append(nxt)
            return "\n".join(body).strip()
    return ""


def _lead_excerpt(text: str, cap: int = 160) -> str:
    """AI 分析摘录（会话报告摘要卡，优化轮 D7）：优先取「综合判级结论与处置建议」
    节正文——摘要卡要展示结论而非概述样板；缺失时回退全文首段。
    去 Markdown 记号与引用锚点、压空白、截断。"""
    if not text:
        return ""
    body = _section_after(text, "综合判级结论") or text
    clean = re.sub(r"\[引用[^\]]*\]\(ref://[^)]*\)", "", body)  # 去引用锚点
    clean = re.sub(r"[#*`>]", "", clean)
    clean = re.sub(r"\s+", " ", clean).strip()
    return clean[:cap]


# AI 综合分析中的引用锚点：索引=知识条目编号，引用 ID=条目 chunk_id
_CITATION_RE = re.compile(r"\[引用 (\d+)\]\(ref://([^)]+)\)")


def _validate_refs(analysis: str, knowledge: list[dict[str, Any]]) -> str:
    """剔除越界/编造的引用锚点：索引须落于知识条目范围，引用 ID 须逐字匹配 chunk_id。

    不依赖模型自觉——report 的 LLM 输出先经此校验再渲染。
    """
    if not analysis:
        return analysis
    valid_ids = {k.get("chunk_id") for k in (knowledge or []) if k.get("chunk_id")}

    def _keep(match: re.Match[str]) -> str:
        raw = match.group(0)
        idx = int(match.group(1))
        ref_id = match.group(2)
        if 1 <= idx <= len(knowledge) and ref_id in valid_ids:
            return raw
        return ""

    return _CITATION_RE.sub(_keep, analysis).strip()


def _filter_cited(analysis: str, knowledge: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    """按报告正文的实际引用锚点收敛知识列表（RAG 溯源 = 报告引用了多少就展示多少）。

    返回 (重编号后的分析文本, 被引用知识子集——按首次引用顺序)：
    - 锚点按子集重编号，正文与「知识库引用」节序号保持一致；
    - 正文无任何锚点 → 无引用，返回空列表；
    - 分析文本为空（LLM 降级）→ 不做收敛，保留全部入参知识；
    - 无 chunk_id 的条目在分析文本非空时直接剔除（不可被锚点引用，展示无溯源意义）。
    """
    if not analysis:
        return analysis, list(knowledge or [])

    ordered_ids: list[str] = []
    for match in _CITATION_RE.finditer(analysis):
        ref_id = match.group(2)
        if ref_id not in ordered_ids:
            ordered_ids.append(ref_id)

    if not ordered_ids:
        return analysis, []

    id_to_new_index = {ref_id: i + 1 for i, ref_id in enumerate(ordered_ids)}

    def _renumber(match: re.Match[str]) -> str:
        ref_id = match.group(2)
        return f"[引用 {id_to_new_index[ref_id]}](ref://{ref_id})"

    renumbered = _CITATION_RE.sub(_renumber, analysis)
    by_id = {k.get("chunk_id"): k for k in (knowledge or []) if k.get("chunk_id")}
    cited = [by_id[ref_id] for ref_id in ordered_ids if ref_id in by_id]
    return renumbered, cited


# 产物图片判定：mime 优先，缺失时按 URI 扩展名兜底
_IMAGE_EXT_RE = re.compile(r"\.(jpe?g|png|tif|tiff)(\?|$)", re.IGNORECASE)


def _is_image_artifact(art: dict[str, Any]) -> bool:
    mime = (art.get("mime") or "").lower()
    return mime.startswith("image/") or bool(_IMAGE_EXT_RE.search(art.get("uri") or ""))


def _pick_compare_pairs(
    artifacts: list[Any], input_uris: list[str]
) -> list[tuple[str, dict[str, Any], dict[str, Any]]]:
    """逐图配对（原图, 标注图）：多图任务报告的核心呈现（2026-09-16 需求）。

    配对策略（artifact name 约定 fan-out 后缀 __<stem>，mock 为 <stem>_annotated）：
    1. 标注图按来源图名（stem 后缀）精确匹配 input_uris 的每张原图
    2. 标注图未带来源标记时按出现顺序与原图一一配对
    3. input_uris 缺失（存量 state/单测）时回退旧逻辑：产物命名/顺序配对
    返回 [(image_name, original_art, annotated_art)]，无标注图时跳过该图。
    """
    images = [
        a for a in artifacts
        if isinstance(a, dict) and a.get("uri") and _is_image_artifact(a)
    ]

    if not input_uris:
        # 存量回退：input_uris 缺失时按产物命名/顺序配对单对（兼容旧 state 形态）
        def _find(*keys: str) -> dict[str, Any] | None:
            return next(
                (a for a in images if any(k in (a.get("name") or "").lower() for k in keys)),
                None,
            )

        original = _find("original_image", "original")
        annotated = _find("result_image", "annotated", "annotation")
        rest = [a for a in images if a is not original and a is not annotated]
        if original is None and rest:
            original = rest.pop(0)
        if annotated is None and rest:
            annotated = rest.pop(0)
        if annotated is None:
            return []
        if original is None:
            # 只有一张图产物（无原图配对）：不渲染对比表（避免同图左右重复），
            # 链接清单保留——存量行为契约
            return []
        return [("检测图", original, annotated)]

    def _find_annotated(stem: str) -> dict[str, Any] | None:
        for a in images:
            name = (a.get("name") or "").lower()
            if f"__{stem.lower()}" in name or name.startswith(f"{stem.lower()}_annotated"):
                return a
        return None

    def _find_original(stem: str) -> dict[str, Any] | None:
        for a in images:
            name = (a.get("name") or "").lower()
            if "original" in name and f"__{stem.lower()}" in name:
                return a
        return None

    pairs: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
    annotated_rest = [
        a for a in images
        if "original" not in (a.get("name") or "").lower()
        and "result_json" not in (a.get("name") or "").lower()
        and not (a.get("name") or "").lower().endswith("_annotated")
    ]
    for uri in input_uris:
        image_name = uri.rstrip("/").rsplit("/", 1)[-1].split("?")[0]
        stem = image_name.rsplit(".", 1)[0] if "." in image_name else image_name
        annotated = _find_annotated(stem)
        if annotated is None and annotated_rest:
            annotated = annotated_rest.pop(0)
        if annotated is None:
            continue
        original = _find_original(stem)
        pairs.append((image_name, original, annotated))
    return pairs


def _render_markdown(state: dict[str, Any], llm_lead: str) -> str:
    task_id = state["task_id"]
    ts = _now_beijing().strftime("%Y-%m-%d %H:%M:%S")
    scene = state.get("scene") or "-"
    capability = state.get("selected_capability") or {}
    grade = state.get("grade") or {}
    raw = state.get("raw_result") or {}

    tool = raw.get("tool") or capability.get("tool_name", "-")
    version = raw.get("tool")
    version_str = (
        f"{version.get('tool_version')}/{version.get('model_version')}"
        if isinstance(version, dict) and version
        else "-"
    )

    lines: list[str] = [
        f"# 智能质检报告 {task_id}",
        "",
        f"- 生成时间：{ts}",
        f"- 检测场景：{scene}",
        f"- 算法工具：{tool}（{version_str}）",
        f"- 判级结论：**{grade.get('level', '-')}**  {grade.get('summary', '')}",
        "",
        "## 1. 结果概览",
        "",
    ]

    hint = state.get("grade_hint") or {}
    lines.append(f"- 状态：{hint.get('status', '-')} | 最高严重度：{hint.get('max_severity', '-')} | 最低置信度：{hint.get('min_confidence', '-')}")
    lines.append("")

    entries = state.get("standardized") or []
    # 人工修订口径（合并口径决策）：缺陷表只列存活的真缺陷——排除项
    # （human_rejected）与改类为非真缺陷类的条目移入虚假显示计数并括号
    # 拆分算法/人工来源；人工新增条目（source=manual）带「人工复核新增」标注。
    result = raw.get("result") or {}
    definitions = state.get("class_definitions") or result.get("class_definitions") or []
    false_classes = {
        d.get("class_name")
        for d in definitions
        if isinstance(d, dict) and not d.get("is_true_defect", True)
    }
    rejected = [
        e for e in entries
        if isinstance(e, dict) and (
            e.get("human_rejected")
            or (e.get("defect_type") in false_classes and (e.get("revised") or {}).get("defect_type") not in false_classes)
        )
    ]
    alive = [
        e for e in entries
        if isinstance(e, dict)
        and not e.get("human_rejected")
        and e.get("defect_type") not in false_classes
    ]
    if entries:
        lines.append("## 2. 标准化检测结果")
        lines.append("")
        lines.append("| 项目 | 数值 |")
        lines.append("|---|---|")
        for entry in alive:
            if "defect_type" in entry:
                value = f"置信度 {entry.get('confidence')} / 严重度 {entry.get('severity')}"
                if entry.get("source") == "manual":
                    value += " · 人工复核新增"
                elif entry.get("source") == "agent_visual":
                    # 智能体视觉判断手写条目：模型标注图上无对应框
                    # （口径对照：标注图框数 < 终裁列表数的差异来源）
                    value += " · 智能体视觉判断"
                    if not entry.get("location"):
                        value += "（无模型标注框）"
                # 类别代码附中文名（class_definitions 语义）；bbox 坐标不进正文——
                # 位置信息由下方缺陷标注图承载（报告可读性契约）
                # 多图 fan-out 时附来源图名（与逐图对比块对应）
                name_cn = entry.get("defect_name_cn") or ""
                label = f"缺陷 {entry.get('defect_type')}"
                if name_cn:
                    label += f"（{name_cn}）"
                if entry.get("image_name"):
                    label += f"〔{entry['image_name']}〕"
                lines.append(f"| {label} | {value} |")
            elif "name" in entry:
                lines.append(f"| 指标 {entry.get('name')} | {entry.get('value')} {entry.get('unit')} |")
            elif "label" in entry:
                lines.append(f"| 分类 {entry.get('label')} | 得分 {entry.get('score')} |")

        # 类别定义表与虚假显示计数（工具 class_definitions 透传）：给类别代码提供
        # 中文名 + 真缺陷/虚假显示语义，报告读者无需对照工具文档
        if definitions:
            defs_text = "；".join(
                f"{d.get('class_name', '-')} = {d.get('description', '-')}"
                f"（{'真缺陷' if d.get('is_true_defect') else '非真缺陷，不计入缺陷列表'}）"
                for d in definitions
                if isinstance(d, dict)
            )
            lines.append("")
            lines.append(f"类别定义（工具 class_definitions）：{defs_text}")
        raw_false = result.get("false_indication_count") or 0
        manual_excluded = len(rejected)
        if manual_excluded:
            # 合并口径：人工排除项并入虚假显示计数，括号拆分算法/人工来源
            breakdown = (
                f"算法判定 {raw_false}，人工复核排除 {manual_excluded}"
                if raw_false else f"人工复核排除 {manual_excluded}"
            )
            lines.append(
                f"虚假荧光显示：{raw_false + manual_excluded} 处（{breakdown}；均非真缺陷，未计入上表）"
            )
        elif raw_false:
            lines.append(f"虚假荧光显示：{raw_false} 处（非真缺陷，未计入上表）")
        if rejected and not any("defect_type" in e for e in alive):
            lines.append("（检出显示已全部经人工复核排除，计入上方虚假显示计数）")

        # 口径对照说明：标注图框数（模型原始检出）≠ 终裁列表数时，
        # 一行讲清差异来源，避免"图上 3 框列表 4 条"的无解释割裂
        model_count = grade.get("model_defect_count")
        agent_added = sum(
            1 for e in alive if isinstance(e, dict) and e.get("source") == "agent_visual"
        )
        if model_count is not None and model_count != len(alive):
            diff_bits = []
            if agent_added:
                diff_bits.append(f"智能体视觉补充 {agent_added} 处（列表带标记，标注图无其框位）")
            if model_count > len(alive):
                diff_bits.append(f"模型检出 {model_count} 处中 {model_count - len(alive)} 处未进入终裁列表")
            lines.append("")
            lines.append(
                f"口径说明：模型检出 {model_count} 处，终裁结论 {len(alive)} 处"
                + (f"——{'；'.join(diff_bits)}" if diff_bits else "")
                + "。标注图为模型推理时刻的原始快照，不随后续裁决重绘。"
            )

        # 缺陷标注图（矩形框渲染产物）：有缺陷检出时才呈现，URI 供前端/报告消费
        artifacts = raw.get("artifacts") or []
        if artifacts:
            lines.append("")
            # 逐图对比（多图任务核心呈现）：每张输入图一个
            # 「原图 vs 模型标注图」对比块，标注图按来源图名配对；
            # 单图任务退化为一个对比块（与既有行为兼容）
            input_uris = state.get("input_uris") or []
            pairs = _pick_compare_pairs(artifacts, input_uris)
            if pairs:
                multi = len(pairs) > 1
                lines.append(
                    "逐图对比（左：原图，右：模型标注缺陷后）"
                    + (f"，共 {len(pairs)} 张检测图" if multi else "")
                    + "："
                )
                lines.append("")
                for image_name, original_art, annotated_art in pairs:
                    header = f"图 {image_name}" if multi else ""
                    if header:
                        lines.append(f"**{header}**")
                        lines.append("")
                    lines.append("| 原图 | 缺陷标注图（模型标注） |")
                    lines.append("|---|---|")
                    orig_cell = (
                        f"![原图]({original_art['uri']})" if original_art is not None else "（无原图产物，见检测输入）"
                    )
                    lines.append(f"| {orig_cell} | ![缺陷标注图]({annotated_art['uri']}) |")
                    lines.append("")
            lines.append("缺陷标注图（矩形框标注产物）：")
            lines.append("")
            for art in artifacts:
                if isinstance(art, dict) and art.get("uri"):
                    lines.append(f"- [{art.get('name', '标注图')}]({art['uri']})")
    else:
        lines.append("（无标准化明细）")
    lines.append("")

    knowledge = state.get("knowledge") or []
    if knowledge:
        lines.append("## 3. 知识库引用")
        lines.append("")
        for idx, k in enumerate(knowledge, 1):
            doc = k.get("doc_name") or "-"
            page = k.get("page")
            page_str = f"，第 {page} 页" if page is not None else ""
            lines.append(f"{idx}. [{doc}{page_str}](score={k.get('score')}) {k.get('content', '')[:120]}")
    else:
        lines.append("## 3. 知识库引用")
        lines.append("")
        lines.append("（未配置 RAGFlow 或未检索到相关条目）")
    lines.append("")

    review = state.get("review") or {}
    lines.append("## 4. 人工复核记录")
    lines.append("")
    lines.append(f"- 复核动作：{review.get('action', 'auto_confirm')}")
    if review.get("opinion"):
        lines.append(f"- 复核意见：{review['opinion']}")
    # 缺陷修订 diff 摘要（human_review 留痕）：一行讲清人工在图上做了什么
    diff = review.get("defects_diff") or {}
    if isinstance(diff, dict) and any(diff.values()):
        parts = []
        for key, label, unit in (
            ("added", "新增漏检", "处"), ("excluded", "排除误检", "处"),
            ("reclassified", "改类", "条"), ("severity_changed", "严重度调整", "条"),
            ("moved", "框位修正", "条"), ("removed", "移除条目", "条"),
        ):
            if diff.get(key):
                parts.append(f"{label} {diff[key]} {unit}")
        lines.append(f"- 缺陷修订：{'、'.join(parts)}（修订后明细见标准化检测结果）")
    if review.get("reviewer"):
        lines.append(f"- 复核人：{review['reviewer']}")
    lines.append("")

    section_no = 5
    explanations = (state.get("metric_explanations") or "").strip()
    if explanations:
        lines.append(f"## {section_no}. 指标解释")
        lines.append("")
        lines.append(explanations)
        lines.append("")
        section_no += 1

    if llm_lead:
        lines.append(f"## {section_no}. AI 综合分析")
        lines.append("")
        lines.append(llm_lead)
        lines.append("")

    lines.append("---")
    lines.append("> 本报告由智能质检 Agent 自动生成，最终判定责任由业务专家确认。")
    return "\n".join(lines)


def _build_analysis_prompt(state: dict[str, Any]) -> str:
    """组装 AI 综合分析用户提示词：任务数据全量注入，结构由 default_kb 系统提示词约束。"""
    capability = state.get("selected_capability") or {}
    raw = state.get("raw_result") or {}
    tool_info = raw.get("tool")
    tool_name = tool_info.get("tool_name") if isinstance(tool_info, dict) else tool_info
    # 类别定义表：state 通道优先，回退工具外壳（result 归一形态 / data 原始形态）
    class_definitions = (
        state.get("class_definitions")
        or (raw.get("result") or {}).get("class_definitions")
        or (raw.get("data") or {}).get("class_definitions")
        or []
    )
    # 人工修订注入（合并口径决策）：明确 human_rejected/manual 标记的分析语义，
    # 防止 LLM 把已排除项当缺陷分析、或漏讲人工补检的干预过程
    review = state.get("review") or {}
    revision_note = ""
    if isinstance(review.get("defects_diff"), dict) and any(review["defects_diff"].values()):
        revision_note = (
            "\n- 人工复核缺陷修订说明：human_rejected=true 的条目已被人工判定排除"
            "（伪显示/无关框），不得作为缺陷逐项分析，应计入虚假显示/排除口径并说明排除理由；"
            "source=manual 的条目为人工复核补检的漏检，按真缺陷分析并注明来自人工复核；"
            "revised 字段保留了修改前原值，叙述人工干预时可用于对照"
        )
    return (
        "请基于以下质检任务数据，按系统提示词要求的六节结构生成「AI 综合分析报告」：\n"
        f"- 任务 ID：{state.get('task_id', '-')}；trace_id：{state.get('trace_id', '-')}\n"
        f"- 检测模式：{state.get('mode') or 'single'}；检测场景：{state.get('scene') or '-'}\n"
        f"- 算法工具：{tool_name or capability.get('tool_name') or '-'}\n"
        f"- 标准化检测结果：{json.dumps(state.get('standardized') or [], ensure_ascii=False)}\n"
        f"- 类别定义表（class_definitions，类别代码→中文名与真缺陷/虚假显示语义）："
        f"{json.dumps(class_definitions, ensure_ascii=False)}\n"
        f"- 判级线索：{json.dumps(state.get('grade_hint') or {}, ensure_ascii=False)}\n"
        f"- 判级结论：{json.dumps(state.get('grade') or {}, ensure_ascii=False)}\n"
        f"- 知识库检索命中条数：{len(state.get('knowledge') or [])}\n"
        f"- 人工复核记录：{json.dumps(review, ensure_ascii=False)}"
        f"{revision_note}"
    )


@node("generate_report")
async def generate_report_node(state: dict[str, Any], ctx: AgentContext) -> dict[str, Any]:
    file_repo = ctx.file_repo
    if file_repo is None:
        return error_state("generate_report", BizCode.INTERNAL_ERROR, "file repo unavailable")

    # LLM AI 综合分析（可选）：失败降级省略该节，不阻塞
    # 挂载 rag_retrieval 渲染的 default_kb 提示词（六节结构 + contexts + language）；
    # 知识未命中时 knowledge_prompt 为空知识声明版（六节结构完整），评判照常进行
    llm_lead = ""
    if ctx.llm is not None and ctx.llm.available:
        try:
            llm_lead = (
                await ctx.llm.chat(
                    _build_analysis_prompt(state),
                    system_prompt=state.get("knowledge_prompt") or None,
                )
            ).strip()
        except Exception:  # noqa: BLE001
            llm_lead = ""

    knowledge = state.get("knowledge") or []
    if llm_lead:
        llm_lead = _validate_refs(llm_lead, knowledge)
    # 溯源收敛：知识列表只保留报告正文实际引用的条目（锚点同步重编号）；
    # 落库 knowledge 随之收敛 → 前端「RAG 知识溯源」报告引用了多少就显示多少
    llm_lead, knowledge = _filter_cited(llm_lead, knowledge)

    report_md = _render_markdown({**state, "knowledge": knowledge}, llm_lead)
    uri = await asyncio.to_thread(
        file_repo.save_text, state["task_id"], "report.md", report_md, kind="reports"
    )

    raw_result = state.get("raw_result") or {}
    report = {
        "report_type": "agent_report",
        "name": f"{state['task_id']}_report.md",
        "uri": uri,
        "version": "1.0.0",
        "created_at": _now_beijing().isoformat(),
        # 标注图等推理产物随报告一并暴露（uri 可直接回前端展示）
        "artifacts": raw_result.get("artifacts") or [],
        # AI 分析首段摘录（会话报告摘要卡展示用，优化轮 D7）：
        # 免前端再拉 report.md，任务快照即可渲染摘要
        "analysis_lead": _lead_excerpt(llm_lead),
    }
    return {"report": report, "knowledge": knowledge}