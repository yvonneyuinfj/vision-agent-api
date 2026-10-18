"""generate_report：AI 综合分析用户提示词组装 + 报告 Markdown 渲染。"""
from __future__ import annotations

from app.agents.nodes.generate_report import (
    _build_analysis_prompt,
    _lead_excerpt,
    _render_markdown,
    _validate_refs,
)


def test_build_analysis_prompt_contains_task_data():
    state = {
        "task_id": "T1",
        "trace_id": "trace-1",
        "mode": "single",
        "scene": "borescope",
        "selected_capability": {"tool_name": "borescope_defect_detection_v1"},
        "standardized": [{"defect_type": "点蚀", "severity": "high", "confidence": 0.9}],
        "grade_hint": {"max_severity": "high"},
        "grade": {"level": "review", "summary": "需人工复核"},
    }

    prompt = _build_analysis_prompt(state)

    assert "T1" in prompt
    assert "borescope" in prompt
    assert "点蚀" in prompt
    assert "review" in prompt


def test_build_analysis_prompt_injects_knowledge_hit_count():
    assert "知识库检索命中条数：0" in _build_analysis_prompt({"task_id": "T1", "knowledge": []})


def test_build_analysis_prompt_injects_knowledge_hit_count_nonzero():
    state = {"task_id": "T1", "knowledge": [{"content": "x"}, {"content": "y"}]}

    assert "知识库检索命中条数：2" in _build_analysis_prompt(state)


def test_build_analysis_prompt_tolerates_empty_state():
    prompt = _build_analysis_prompt({"task_id": "T2"})

    assert "T2" in prompt


def test_render_markdown_ai_analysis_section():
    state = {"task_id": "T1", "scene": "borescope"}

    md = _render_markdown(state, "分析正文")

    assert "## 5. AI 综合分析" in md
    assert "分析正文" in md


def test_render_markdown_skips_empty_analysis():
    md = _render_markdown({"task_id": "T1"}, "")

    assert "AI 综合分析" not in md


"""指标解释章节 + 引用校验（AI 综合分析防编造引用锚点）。"""
from app.agents.nodes.generate_report import _render_markdown, _validate_refs


def _knowledge() -> list[dict]:
    return [
        {"content": "点蚀判级标准", "chunk_id": "chunk-a", "doc_name": "spec.pdf", "score": 0.9},
        {"content": "内窥检测方法", "chunk_id": "chunk-b", "doc_name": "method.pdf", "score": 0.7},
    ]


def test_render_markdown_metric_explanations_section_renumbers_ai_analysis():
    md = _render_markdown(
        {"task_id": "T1", "scene": "borescope", "metric_explanations": "### 晶粒度\n解释正文"},
        "分析正文",
    )
    assert "## 5. 指标解释" in md
    assert "### 晶粒度" in md
    assert "## 6. AI 综合分析" in md


def test_render_markdown_without_explanations_keeps_ai_as_section_5():
    md = _render_markdown({"task_id": "T1"}, "分析正文")
    assert "指标解释" not in md
    assert "## 5. AI 综合分析" in md


def test_validate_refs_keeps_valid_citations():
    text = "依据[引用 1](ref://chunk-a)与[引用 2](ref://chunk-b)。"
    assert _validate_refs(text, _knowledge()) == text


def test_validate_refs_strips_fabricated_ref_id():
    cleaned = _validate_refs("依据[引用 1](ref://chunk-x)判级。", _knowledge())
    assert "ref://" not in cleaned
    assert "依据判级。" in cleaned


def test_validate_refs_strips_out_of_range_index():
    cleaned = _validate_refs("见[引用 9](ref://chunk-a)。", _knowledge())
    assert cleaned == "见。"


def test_validate_refs_tolerates_no_citations_and_empty_knowledge():
    assert _validate_refs("无引用正文", []) == "无引用正文"
    assert _validate_refs("依据[引用 1](ref://anything)。", []) == "依据。"


# ---------- RAG 溯源按报告实际引用收敛（_filter_cited） ----------

from app.agents.nodes.generate_report import _filter_cited  # noqa: E402

_KNOWLEDGE = [
    {"content": "甲", "chunk_id": "c1", "doc_name": "a.pdf", "score": 0.9},
    {"content": "乙", "chunk_id": "c2", "doc_name": "b.pdf", "score": 0.7},
    {"content": "丙", "chunk_id": "c3", "doc_name": "c.pdf", "score": 0.6},
]


def test_filter_cited_keeps_only_anchored_knowledge_in_citation_order():
    analysis = "依据[引用 3](ref://c3)与[引用 1](ref://c1)判定。"

    new_analysis, cited = _filter_cited(analysis, _KNOWLEDGE)

    assert [k["chunk_id"] for k in cited] == ["c3", "c1"]
    # 锚点按子集重编号：c3→1，c1→2
    assert "[引用 1](ref://c3)" in new_analysis
    assert "[引用 2](ref://c1)" in new_analysis


def test_filter_cited_dedupes_repeated_anchor():
    analysis = "先[引用 1](ref://c1)，再[引用 1](ref://c1)。"

    new_analysis, cited = _filter_cited(analysis, _KNOWLEDGE)

    assert [k["chunk_id"] for k in cited] == ["c1"]
    assert new_analysis.count("[引用 1](ref://c1)") == 2


def test_filter_cited_no_anchors_means_nothing_cited():
    analysis = "分析正文没有任何引用锚点。"

    new_analysis, cited = _filter_cited(analysis, _KNOWLEDGE)

    assert cited == []
    assert new_analysis == analysis


def test_filter_cited_degraded_empty_analysis_keeps_all():
    # LLM 降级无分析文本：保留全部（阈值过滤后的）知识，溯源不丢
    new_analysis, cited = _filter_cited("", _KNOWLEDGE)

    assert cited == _KNOWLEDGE
    assert new_analysis == ""


def test_filter_cited_drops_entries_without_chunk_id_when_analysis_present():
    """P7b：分析文本非空时，无 chunk_id 的条目不可被锚点引用，直接剔除（无溯源意义）。"""
    knowledge = _KNOWLEDGE + [{"content": "无标识", "chunk_id": None, "doc_name": "d.pdf", "score": 0.5}]
    analysis = "依据[引用 1](ref://c1)判定。"

    new_analysis, cited = _filter_cited(analysis, knowledge)

    assert [k["chunk_id"] for k in cited] == ["c1"]


# ---------- 报告引用格式带页码（P7b） ----------

def test_render_markdown_knowledge_includes_page_when_present():
    from app.agents.nodes.generate_report import _render_markdown

    state = {
        "task_id": "T1",
        "scene": "borescope",
        "selected_capability": {"tool_name": "borescope_v1"},
        "grade": {"level": "pass", "summary": "ok"},
        "grade_hint": {},
        "standardized": [],
        "knowledge": [
            {"content": "正文", "chunk_id": "c1", "doc_name": "手册A.pdf", "score": 0.9, "page": 3},
            {"content": "无页码", "chunk_id": "c2", "doc_name": "手册B.pdf", "score": 0.8},
        ],
        "review": {},
        "raw_result": {},
    }
    md = _render_markdown(state, "")
    assert "手册A.pdf，第 3 页" in md
    assert "手册B.pdf" in md
    assert "手册B.pdf，第" not in md  # 无页码不强行补


# ---------- 缺陷类别语义与标注图呈现（正文不罗列 bbox 坐标，位置信息由缺陷标注图承载） ----------

def _defect_state(standardized, artifacts=None):
    return {
        "task_id": "T1",
        "scene": "fracture",
        "selected_capability": {"tool_name": "fracture_v1"},
        "grade": {"level": "defect", "summary": "ok"},
        "grade_hint": {"status": "abnormal", "max_severity": "medium", "min_confidence": 0.7},
        "standardized": standardized,
        "knowledge": [],
        "review": {},
        "raw_result": {"tool": {"tool_name": "quality_inspection_agent"}, "artifacts": artifacts or []},
    }


def test_render_markdown_defect_row_uses_class_semantics_without_bbox():
    """报告可读性契约：缺陷行呈现「代码（中文名）」，bbox 坐标不进正文。"""
    state = _defect_state([{
        "defect_type": "BP", "defect_name_cn": "模糊的真缺陷", "confidence": 0.72,
        "severity": None, "location": {"type": "bbox", "bbox": [10, 20, 300, 400]},
    }])
    md = _render_markdown(state, "")
    assert "缺陷 BP（模糊的真缺陷）" in md
    assert "矩形框 [" not in md
    assert "10, 20, 300, 400" not in md


def test_render_markdown_class_definitions_legend_and_false_count():
    """class_definitions 利用：类别定义图例 + 虚假显示计数进报告正文。"""
    state = _defect_state([{
        "defect_type": "BP", "defect_name_cn": "模糊的真缺陷", "confidence": 0.72,
        "severity": None,
    }])
    state["class_definitions"] = [
        {"class_id": 0, "class_name": "BP", "description": "模糊的真缺陷", "is_true_defect": True},
        {"class_id": 2, "class_name": "FI", "description": "虚假荧光显示", "is_true_defect": False},
    ]
    state["raw_result"]["result"] = {
        "class_definitions": state["class_definitions"],
        "false_indication_count": 1,
    }
    md = _render_markdown(state, "")
    assert "类别定义（工具 class_definitions）" in md
    assert "BP = 模糊的真缺陷（真缺陷）" in md
    assert "FI = 虚假荧光显示（非真缺陷，不计入缺陷列表）" in md
    assert "虚假荧光显示：1 处（非真缺陷，未计入上表）" in md


def test_build_analysis_prompt_injects_class_definitions():
    """LLM 提示词注入类别定义表：三节「代码→中文名+真缺陷语义」转译的数据源。"""
    state = {
        "task_id": "T1",
        "standardized": [],
        "class_definitions": [
            {"class_id": 0, "class_name": "BP", "description": "模糊的真缺陷", "is_true_defect": True},
        ],
    }
    prompt = _build_analysis_prompt(state)
    assert "类别定义表（class_definitions" in prompt
    assert "模糊的真缺陷" in prompt


def test_build_analysis_prompt_class_definitions_falls_back_to_raw_envelope():
    """state 通道缺失时回退工具外壳（result 归一形态 / data 原始形态，兼容 agent 轨旧快照）。"""
    state = {"task_id": "T1", "raw_result": {"data": {"class_definitions": [
        {"class_id": 2, "class_name": "FI", "description": "虚假荧光显示", "is_true_defect": False},
    ]}}}
    assert "虚假荧光显示" in _build_analysis_prompt(state)


def test_render_markdown_defect_row_without_location_has_no_box():
    state = _defect_state([{"defect_type": "dimple", "confidence": 0.8, "severity": "low"}])
    md = _render_markdown(state, "")
    assert "缺陷 dimple" in md
    assert "矩形框" not in md


def test_render_markdown_artifacts_section_lists_annotated_images():
    state = _defect_state(
        [{"defect_type": "cleavage", "confidence": 0.72, "severity": "high",
          "location": {"type": "bbox", "bbox": [1, 2, 3, 4]}}],
        artifacts=[{"name": "artifact_a_annotated.png", "mime": "image/png",
                    "uri": "/api/v1/tasks/T1/files/a_annotated.png?kind=artifacts"}],
    )
    md = _render_markdown(state, "")
    assert "缺陷标注图" in md
    assert "[artifact_a_annotated.png](/api/v1/tasks/T1/files/a_annotated.png?kind=artifacts)" in md


def test_render_markdown_artifacts_original_vs_annotated_comparison():
    """原图 + 标注图产物齐备时渲染并排对比表（内嵌图片，报告页/下载 md 可见）。"""
    state = _defect_state(
        [{"defect_type": "BP", "confidence": 0.67, "severity": None,
          "location": {"type": "bbox", "bbox": [1, 2, 3, 4]}}],
        artifacts=[
            {"name": "artifact_original_image", "mime": "image/jpeg",
             "uri": "/api/v1/tasks/T1/files/original.jpg?kind=artifacts"},
            {"name": "artifact_result_image", "mime": "image/jpeg",
             "uri": "/api/v1/tasks/T1/files/result-image.jpg?kind=artifacts"},
            {"name": "artifact_result_json", "mime": "application/json",
             "uri": "/api/v1/tasks/T1/files/result-json.json?kind=artifacts"},
        ],
    )
    md = _render_markdown(state, "")
    assert "逐图对比（左：原图，右：模型标注缺陷后）" in md
    assert (
        "| ![原图](/api/v1/tasks/T1/files/original.jpg?kind=artifacts) "
        "| ![缺陷标注图](/api/v1/tasks/T1/files/result-image.jpg?kind=artifacts) |"
    ) in md
    # 非图像产物不进对比表，但仍在链接清单
    assert "result-json.json" in md
    assert "![原图](/api/v1/tasks/T1/files/result-json.json" not in md


def test_render_markdown_artifacts_comparison_positional_fallback():
    """命名不可辨（mock 产物无 original/result 关键词）时按出现顺序取前两张图片。"""
    state = _defect_state(
        [{"defect_type": "crack", "confidence": 0.9, "severity": "high"}],
        artifacts=[
            {"name": "artifact_input.png", "mime": "image/png", "uri": "/u/1.png"},
            {"name": "artifact_out.png", "mime": "image/png", "uri": "/u/2.png"},
        ],
    )
    md = _render_markdown(state, "")
    assert "| ![原图](/u/1.png) | ![缺陷标注图](/u/2.png) |" in md


def test_render_markdown_artifacts_single_image_no_comparison():
    """只有一张图片产物时不渲染对比表（避免同图左右重复），链接清单保留。"""
    state = _defect_state(
        [{"defect_type": "crack", "confidence": 0.9, "severity": "high"}],
        artifacts=[{"name": "artifact_a_annotated.png", "mime": "image/png", "uri": "/u/a.png"}],
    )
    md = _render_markdown(state, "")
    assert "逐图对比" not in md
    assert "[artifact_a_annotated.png](/u/a.png)" in md


def test_render_markdown_no_artifacts_section_without_defects():
    # 无缺陷明细时不渲染标注图节（避免空框噪声）
    state = _defect_state([], artifacts=[{"name": "x.png", "uri": "/u"}])
    md = _render_markdown(state, "")
    assert "（无标准化明细）" in md
    assert "缺陷标注图" not in md


async def test_generate_report_node_payload_includes_artifacts():
    """节点级回归：report 载荷携带标注图产物，markdown 缺陷行不罗列 bbox 坐标。

    防止 generate_report_node 内引用未定义变量导致整图失败（曾发生：
    节点引用 _render_markdown 局部变量 raw，NameError 使任务误判 failed）。
    """
    from types import SimpleNamespace

    from app.agents.nodes.generate_report import generate_report_node

    class _FakeFileRepo:
        def __init__(self):
            self.saved_content = ""

        def save_text(self, task_id, name, content, kind="reports"):
            self.saved_content = content
            return f"/api/v1/tasks/{task_id}/files/{name}?kind={kind}"

    repo = _FakeFileRepo()
    ctx = SimpleNamespace(file_repo=repo, llm=None, task_repo=None)
    artifacts = [{"name": "artifact_a_annotated.png", "mime": "image/png",
                  "uri": "/api/v1/tasks/T1/files/a_annotated.png?kind=artifacts"}]
    state = {
        "task_id": "T1",
        "scene": "fracture",
        "standardized": [{"defect_type": "cleavage", "confidence": 0.7, "severity": "high",
                          "location": {"type": "bbox", "bbox": [1, 2, 3, 4]}}],
        "grade": {"level": "review"},
        "grade_hint": {"status": "abnormal"},
        "raw_result": {"artifacts": artifacts},
        "knowledge": [],
    }

    out = await generate_report_node(state, ctx)

    assert "error" not in out
    assert out["report"]["artifacts"] == artifacts
    assert "缺陷 cleavage" in repo.saved_content
    assert "矩形框 [" not in repo.saved_content
    assert "缺陷标注图" in repo.saved_content
    # 优化轮 D7：无 LLM 分析时摘录为空串（键恒在，前端免判空）
    assert out["report"]["analysis_lead"] == ""


def test_lead_excerpt_prefers_conclusion_section_over_boilerplate():
    """六节结构里摘要卡要结论（第五节），不要概述样板（第一节）。"""
    text = (
        "### 一 · 检测概述\n"
        "- 任务标识：任务 ID T1；trace_id trace-x\n"
        "- 检测模式：agent\n"
        "### 五 · 综合判级结论与处置建议\n"
        "综合判级为通过。未检出缺陷，建议放行。[引用 1](ref://abc)\n"
        "### 六 · 置信度与局限性声明\n"
        "置信度高。\n"
    )
    out = _lead_excerpt(text)
    assert "综合判级为通过" in out
    assert "任务标识" not in out and "检测概述" not in out
    assert "引用" not in out


def test_lead_excerpt_falls_back_to_full_text_without_conclusion():
    out = _lead_excerpt("该件存在疲劳裂纹，建议回修。")
    assert "疲劳裂纹" in out


def test_lead_excerpt_strips_markdown_and_citations():
    text = "## 综合结论\n该件存在**疲劳裂纹**[引用 1](ref://abc)，建议回修。"
    out = _lead_excerpt(text)
    assert "疲劳裂纹" in out
    assert "引用" not in out and "ref://" not in out
    assert "#" not in out and "**" not in out


def test_lead_excerpt_truncates_long_text():
    out = _lead_excerpt("x" * 500, cap=100)
    assert len(out) == 100


def test_lead_excerpt_empty():
    assert _lead_excerpt("") == ""
    assert _lead_excerpt(None) == ""  # type: ignore[arg-type]


# ---------- 人工修订口径（合并口径：排除项进虚假计数、人工新增带标注） ----------

def _defs() -> list[dict]:
    return [
        {"class_id": 0, "class_name": "BP", "description": "模糊的真缺陷", "is_true_defect": True},
        {"class_id": 2, "class_name": "FI", "description": "虚假荧光显示", "is_true_defect": False},
    ]


def test_render_markdown_excludes_rejected_and_badges_manual():
    state = {
        "task_id": "T1",
        "standardized": [
            {"defect_type": "BP", "defect_name_cn": "模糊的真缺陷", "confidence": 0.76,
             "severity": "high", "human_rejected": True, "defect_id": "D001"},
            {"defect_type": "crack", "confidence": None, "severity": "high",
             "source": "manual", "defect_id": "M001"},
        ],
        "class_definitions": _defs(),
        "raw_result": {"result": {"false_indication_count": 1}},
    }
    md = _render_markdown(state, "")
    # 排除项不进缺陷表
    assert "| 缺陷 BP（模糊的真缺陷） |" not in md
    # 人工新增项进表并标注来源
    assert "| 缺陷 crack |" in md and "人工复核新增" in md
    # 虚假计数合并拆分：算法 1 + 人工排除 1
    assert "虚假荧光显示：2 处（算法判定 1，人工复核排除 1；均非真缺陷，未计入上表）" in md


def test_render_markdown_keeps_legacy_false_count_line_without_revision():
    state = {
        "task_id": "T1",
        "standardized": [{"defect_type": "BP", "confidence": 0.7, "severity": "low"}],
        "raw_result": {"result": {"false_indication_count": 3}},
    }
    md = _render_markdown(state, "")
    assert "虚假荧光显示：3 处（非真缺陷，未计入上表）" in md


def test_render_markdown_notes_when_all_defects_rejected():
    state = {
        "task_id": "T1",
        "standardized": [
            {"defect_type": "BP", "confidence": 0.7, "severity": "low", "human_rejected": True},
        ],
        "raw_result": {"result": {"false_indication_count": 0}},
    }
    md = _render_markdown(state, "")
    assert "检出显示已全部经人工复核排除" in md
    assert "虚假荧光显示：1 处（人工复核排除 1；均非真缺陷，未计入上表）" in md


def test_render_markdown_review_section_lists_defect_diff():
    state = {
        "task_id": "T1",
        "standardized": [],
        "review": {
            "action": "revise",
            "opinion": "补漏检",
            "defects_diff": {"added": 1, "excluded": 2, "reclassified": 1,
                             "severity_changed": 0, "moved": 1, "removed": 0},
        },
    }
    md = _render_markdown(state, "")
    assert "- 缺陷修订：新增漏检 1 处、排除误检 2 处、改类 1 条、框位修正 1 条" in md


def test_analysis_prompt_injects_revision_note_only_when_revised():
    base = {"task_id": "T1", "standardized": [], "review": {"action": "confirm"}}
    assert "人工复核缺陷修订说明" not in _build_analysis_prompt(base)
    revised = {
        **base,
        "review": {"action": "revise", "defects_diff": {"added": 1, "excluded": 0}},
    }
    prompt = _build_analysis_prompt(revised)
    assert "human_rejected=true 的条目已被人工判定排除" in prompt
    assert "source=manual 的条目为人工复核补检的漏检" in prompt


# --------------------------------------------------------------------------
# 口径对照：模型标注图框数 vs 终裁列表数（2026-09-16 报告困惑事故）
# --------------------------------------------------------------------------
def test_render_markdown_agent_visual_marker_and_reconciliation():
    """手写条目带「智能体视觉判断」标记；模型数 ≠ 终裁数时输出口径说明行。"""
    state = {
        "task_id": "T1",
        "scene": "fpi",
        "grade": {"level": "review", "model_defect_count": 3},
        "standardized": [
            {"defect_type": "BP", "confidence": 0.76, "severity": None,
             "location": {"type": "bbox", "bbox": [292, 107, 324, 136]}},
            {"defect_type": "SP", "confidence": 0.52, "severity": None,
             "location": {"type": "bbox", "bbox": [291, 108, 325, 136]}},
            {"defect_type": "BP", "confidence": 0.38, "severity": None,
             "location": {"type": "bbox", "bbox": [400, 300, 450, 360]}},
            {"defect_type": "crack", "confidence": 0.9, "severity": "medium",
             "source": "agent_visual", "description": "视觉判断"},
        ],
    }
    md = _render_markdown(state, "")

    # 手写条目标记：无 location 时附「无模型标注框」
    assert "智能体视觉判断" in md
    assert "智能体视觉判断（无模型标注框）" in md
    # 口径说明：模型 3 处 vs 终裁 4 处，点明差异来源
    assert "口径说明：模型检出 3 处，终裁结论 4 处" in md
    assert "智能体视觉补充 1 处" in md
    assert "标注图为模型推理时刻的原始快照" in md


def test_render_markdown_no_reconciliation_when_counts_agree():
    """模型数 = 终裁数：不输出多余的口径说明行（避免噪声）。"""
    state = {
        "task_id": "T1",
        "scene": "fpi",
        "grade": {"level": "defect", "model_defect_count": 1},
        "standardized": [
            {"defect_type": "BP", "confidence": 0.76, "severity": None,
             "location": {"type": "bbox", "bbox": [1, 2, 3, 4]}},
        ],
    }
    md = _render_markdown(state, "")
    assert "口径说明" not in md


def test_render_markdown_reconciliation_covers_model_dropping():
    """智能体裁剪模型条目（图上多列表少）：口径说明覆盖「未进入终裁列表」。"""
    state = {
        "task_id": "T1",
        "scene": "fpi",
        "grade": {"level": "defect", "model_defect_count": 3},
        "standardized": [
            {"defect_type": "BP", "confidence": 0.76, "severity": None,
             "location": {"type": "bbox", "bbox": [292, 107, 324, 136]}},
        ],
    }
    md = _render_markdown(state, "")
    assert "口径说明：模型检出 3 处，终裁结论 1 处" in md
    assert "2 处未进入终裁列表" in md
