"""rag_retrieval 节点：检索完成后渲染 default_kb 提示词（contexts + language 默认中文）。"""
from __future__ import annotations

from app.agents.nodes.rag_retrieval import rag_retrieval_node
from app.agents.tools.registry import ToolResult
from app.services.deps import AgentContext

KNOWLEDGE = [{"content": "点蚀缺陷判级标准内容", "doc_name": "spec.pdf", "score": 0.9}]

# 检索主题由缺陷驱动（需求 §1.2）：带缺陷的标准化结果才能发起有效检索
DEFECTS = [{"defect_type": "pitting", "name": "点蚀"}]


class _FakeRagTool:
    def __init__(self, result: ToolResult) -> None:
        self._result = result
        self.calls = 0

    async def execute(self, inputs: dict) -> ToolResult:
        self.calls += 1
        return self._result


class _FakeRegistry:
    def __init__(self, tool: _FakeRagTool | None) -> None:
        self._tool = tool

    def get(self, name: str):
        return self._tool if name == "knowledge/rag" else None


def _ctx(tool: _FakeRagTool | None) -> AgentContext:
    return AgentContext(tool_registry=_FakeRegistry(tool))


async def test_retrieval_renders_default_kb_prompt_with_contexts_and_default_language():
    ctx = _ctx(_FakeRagTool(ToolResult.success({"knowledge": KNOWLEDGE})))

    result = await rag_retrieval_node(
        {"task_id": "T1", "scene": "borescope", "standardized": DEFECTS}, ctx
    )

    assert result["knowledge"] == KNOWLEDGE
    prompt = result["knowledge_prompt"]
    assert "点蚀缺陷判级标准内容" in prompt
    assert "始终使用中文生成报告" in prompt
    assert "一 · 检测概述" in prompt


async def test_language_from_parameters_overrides_default():
    ctx = _ctx(_FakeRagTool(ToolResult.success({"knowledge": KNOWLEDGE})))

    result = await rag_retrieval_node(
        {"task_id": "T1", "parameters": {"language": "English"}, "standardized": DEFECTS}, ctx
    )

    assert "始终使用English生成报告" in result["knowledge_prompt"]


async def test_zero_defect_task_skips_retrieval_entirely():
    """零缺陷任务没有判级对象：不发起检索（工具零调用），走空知识链。

    回归：无缺陷时检索主题退化为泛化场景问句，召回一堆低相关文献充数，
    详情页「知识库判级依据」与「未检出缺陷」自相矛盾。
    """
    tool = _FakeRagTool(ToolResult.success({"knowledge": KNOWLEDGE}))
    ctx = _ctx(tool)

    result = await rag_retrieval_node(
        {"task_id": "T1", "scene": "fracture", "standardized": []}, ctx
    )

    assert tool.calls == 0
    assert result["knowledge"] == []
    assert result["retrieval_trace"] == []
    assert "未命中任何条目" in result["knowledge_prompt"]


async def test_missing_standardized_key_also_skips_retrieval():
    tool = _FakeRagTool(ToolResult.success({"knowledge": KNOWLEDGE}))
    ctx = _ctx(tool)

    result = await rag_retrieval_node({"task_id": "T1", "scene": "fracture"}, ctx)

    assert tool.calls == 0
    assert result["knowledge"] == []
    assert "未命中任何条目" in result["knowledge_prompt"]


async def test_empty_knowledge_renders_empty_kb_prompt():
    ctx = _ctx(_FakeRagTool(ToolResult.success({"knowledge": []})))

    result = await rag_retrieval_node({"task_id": "T1", "standardized": DEFECTS}, ctx)

    assert result["knowledge"] == []
    prompt = result["knowledge_prompt"]
    assert "未命中任何条目" in prompt
    assert "始终使用中文生成报告" in prompt
    assert "一 · 检测概述" in prompt


async def test_missing_tool_still_renders_empty_kb_prompt():
    ctx = AgentContext(tool_registry=None)

    result = await rag_retrieval_node({"task_id": "T1", "standardized": DEFECTS}, ctx)

    assert result["knowledge"] == []
    assert "未命中任何条目" in result["knowledge_prompt"]


async def test_failed_retrieval_degrades_and_marks_engine():
    ctx = _ctx(_FakeRagTool(ToolResult.failure(4005, "rag unavailable")))

    result = await rag_retrieval_node({"task_id": "T1", "standardized": DEFECTS}, ctx)

    assert result["knowledge"] == []
    assert "未命中任何条目" in result["knowledge_prompt"]
    assert "rag" in ctx.engine_ids


async def test_non_dict_result_degrades_to_empty_knowledge():
    """检索结果非 dict（契约外）时降级为空知识，不崩溃。"""
    ctx = _ctx(_FakeRagTool(ToolResult.success([{"content": "x"}])))

    result = await rag_retrieval_node({"task_id": "T1", "standardized": DEFECTS}, ctx)

    assert result["knowledge"] == []
    assert "未命中任何条目" in result["knowledge_prompt"]


"""多轮检索：有界循环（MAX_RETRIEVAL_ROUNDS）+ 首轮命中短路 LLM 判定。"""
from app.agents.nodes.rag_retrieval import MAX_RETRIEVAL_ROUNDS


class _FakeLLM:
    """记录调用，按轮次切换行为和返回。"""

    available = True

    def __init__(self, *, variants: list[str] | None = None,
                 judged: dict | None = None):
        self._variants = list(variants or [])
        self._judged = judged or {"sufficient": True, "missing": ""}
        self.missing_seen: list[str] = []
        self.rewrite_calls = 0
        self.judge_calls = 0

    async def build_retrieval_queries(self, *, scene, standardized,
                                       language, missing=""):
        self.rewrite_calls += 1
        self.missing_seen.append(missing)
        if self.rewrite_calls == 1 and self._variants:
            return self._variants
        return [f"{missing} 判级标准"] if missing else []

    async def judge_retrieval(self, *, question, knowledge):
        self.judge_calls += 1
        return self._judged


class _SequencedRagTool:
    """按调用次序返回预置结果（None 表示工具失败熔断）。"""

    def __init__(self, batches: list[list[dict] | None]):
        self._batches = list(batches)

    async def execute(self, inputs: dict):
        if not self._batches:
            return ToolResult.success({"knowledge": []})
        batch = self._batches.pop(0)
        if batch is None:
            return ToolResult.failure(4005, "rag unavailable")
        return ToolResult.success({"knowledge": batch})


class _FakeRegistry:
    def __init__(self, tool):
        self._tool = tool
    def get(self, name: str):
        return self._tool if name == "knowledge/rag" else None


def _k(chunk_id: str, content: str, score: float) -> dict:
    return {"chunk_id": chunk_id, "content": content, "score": score, "doc_name": "spec.pdf"}


async def test_high_score_first_round_skips_judge_and_rewrite():
    llm = _FakeLLM(variants=["变体一", "变体二"])
    ctx = AgentContext(tool_registry=_FakeRegistry(_SequencedRagTool([[_k("c1", "判级标准", 0.9)]])),
                       llm=llm)

    result = await rag_retrieval_node(
        {"task_id": "T1", "scene": "borescope", "standardized": DEFECTS}, ctx
    )

    assert len(result["knowledge"]) == 1
    assert len(result["retrieval_trace"]) == 1          # 高置信首轮即停
    assert result["retrieval_trace"][0]["round"] == 1
    assert llm.rewrite_calls == 1                        # 仅首轮改写一次
    assert llm.judge_calls == 0                          # 不做充分性判定
    assert "判级标准" in result["knowledge_prompt"]


async def test_low_score_then_judge_insufficient_triggers_second_round(tmp_path):
    llm = _FakeLLM(judged={"sufficient": False, "missing": "缺少点蚀判级条款"})
    ctx = _ctx_with_rules(_SequencedRagTool([
        [_k("c1", "弱相关", 0.1)],
        [_k("c2", "点蚀判级条款正文", 0.3)],
        None,
    ]), _no_threshold_dir(tmp_path), llm=llm)

    result = await rag_retrieval_node({"task_id": "T1", "standardized": DEFECTS}, ctx)

    chunks = {k["chunk_id"] for k in result["knowledge"]}
    assert chunks == {"c1", "c2"}                        # 两轮结果合并
    trace = result["retrieval_trace"]
    assert len(trace) == 2
    assert trace[0]["judged"]["sufficient"] is False
    assert llm.missing_seen[1] == "缺少点蚀判级条款"      # 第二轮改写带缺口焦点
    assert "点蚀判级条款正文" in result["knowledge_prompt"]


async def test_judge_sufficient_stops_after_first_round_even_low_score(tmp_path):
    llm = _FakeLLM(judged={"sufficient": True, "missing": ""})
    ctx = _ctx_with_rules(_SequencedRagTool([
        [_k("c1", "相关但低分", 0.1)],
        [_k("c2", "不应出现", 0.9)],
    ]), _no_threshold_dir(tmp_path), llm=llm)

    result = await rag_retrieval_node({"task_id": "T1", "standardized": DEFECTS}, ctx)

    assert [k["chunk_id"] for k in result["knowledge"]] == ["c1"]
    assert len(result["retrieval_trace"]) == 1
    assert llm.judge_calls == 1


async def test_loop_bounded_by_max_rounds(tmp_path):
    llm = _FakeLLM(judged={"sufficient": False, "missing": "继续找"})
    ctx = _ctx_with_rules(_SequencedRagTool([
        [_k("c1", "x", 0.1)], [_k("c2", "x", 0.1)], [_k("c3", "x", 0.1)],
        [_k("c4", "不该存在", 0.9)],
    ]), _no_threshold_dir(tmp_path), llm=llm)

    result = await rag_retrieval_node({"task_id": "T1", "standardized": DEFECTS}, ctx)

    chunk_ids = {k["chunk_id"] for k in result["knowledge"]}
    assert len(result["retrieval_trace"]) == MAX_RETRIEVAL_ROUNDS  # 硬上限
    assert "c4" not in chunk_ids                                 # 第 4 批从未被执行
    assert "c1" in chunk_ids


async def test_no_llm_single_round_with_base_question():
    ctx = AgentContext(tool_registry=_FakeRegistry(_SequencedRagTool([[_k("c1", "x", 0.2)]])),
                       llm=None)

    result = await rag_retrieval_node(
        {"task_id": "T1", "scene": "borescope", "standardized": DEFECTS}, ctx
    )

    assert len(result["retrieval_trace"]) == 1
    assert result["retrieval_trace"][0]["queries"][0] == "borescope 检测结果解读；pitting 缺陷判级标准；点蚀 测量方法"


# ---------- 知识准入阈值（config/rules/rag_rules.yaml::knowledge_score_threshold） ----------

class _SettingsStub:
    def __init__(self, rules_dir: str) -> None:
        self.rules_dir = rules_dir


def _ctx_with_rules(tool: _FakeRagTool, rules_dir, llm=None) -> AgentContext:
    return AgentContext(
        tool_registry=_FakeRegistry(tool), settings=_SettingsStub(str(rules_dir)), llm=llm
    )


def _no_threshold_dir(tmp_path):
    """阈值置 0 的规则目录：多轮编排回归与准入阈值语义隔离。"""
    (tmp_path / "rag_rules.yaml").write_text(
        "knowledge_score_threshold: 0\n", encoding="utf-8"
    )
    return tmp_path


_MIXED_SCORES = [
    {"content": "高分引用内容", "doc_name": "a.pdf", "score": 0.9, "chunk_id": "hi"},
    {"content": "中分引用内容", "doc_name": "b.pdf", "score": 0.5, "chunk_id": "mid"},
    {"content": "低分引用内容", "doc_name": "c.pdf", "score": 0.2, "chunk_id": "lo"},
]


async def test_knowledge_filtered_by_yaml_threshold(tmp_path):
    # yaml 阈值 0.6：仅 0.9 进模型上下文，低分项连提示词都不出现
    (tmp_path / "rag_rules.yaml").write_text(
        "knowledge_score_threshold: 0.6\n", encoding="utf-8"
    )
    ctx = _ctx_with_rules(_FakeRagTool(ToolResult.success({"knowledge": _MIXED_SCORES})), tmp_path)

    result = await rag_retrieval_node(
        {"task_id": "T1", "scene": "borescope", "standardized": DEFECTS}, ctx
    )

    assert [k["chunk_id"] for k in result["knowledge"]] == ["hi"]
    assert "高分引用内容" in result["knowledge_prompt"]
    assert "中分引用内容" not in result["knowledge_prompt"]
    assert "低分引用内容" not in result["knowledge_prompt"]


async def test_threshold_is_strict_greater_than(tmp_path):
    # 等于阈值不进上下文（需求措辞：置信度「大于」阈值）
    (tmp_path / "rag_rules.yaml").write_text(
        "knowledge_score_threshold: 0.5\n", encoding="utf-8"
    )
    ctx = _ctx_with_rules(_FakeRagTool(ToolResult.success({"knowledge": _MIXED_SCORES})), tmp_path)

    result = await rag_retrieval_node(
        {"task_id": "T1", "scene": "borescope", "standardized": DEFECTS}, ctx
    )

    assert [k["chunk_id"] for k in result["knowledge"]] == ["hi"]


async def test_threshold_default_applies_when_rag_rules_absent(tmp_path):
    # rag_rules.yaml 缺失：回退内置默认阈值 0.35（0.2 被滤除）
    ctx = _ctx_with_rules(_FakeRagTool(ToolResult.success({"knowledge": _MIXED_SCORES})), tmp_path)

    result = await rag_retrieval_node(
        {"task_id": "T1", "scene": "borescope", "standardized": DEFECTS}, ctx
    )

    assert [k["chunk_id"] for k in result["knowledge"]] == ["hi", "mid"]
