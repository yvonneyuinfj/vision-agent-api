"""RagSearchTool：多 query 检索合并去重 + dataset_ids 直用（无名称→id 解析）。

数据集单一事实源 = config/rules/ragflow_chat.yaml 的 dataset_ids（构造注入，
静态列表或惰性提供者均可）。
"""
from app.agents.tools.rag_tool import RagSearchTool
from app.agents.tools.registry import ToolResult  # noqa: F401


class _FakeSettings:
    ragflow_rerank_model = ""
    rules_dir = None  # P8：默认无场景路由，走全集


class _FakeRagflow:
    available = True

    def __init__(self, per_question: dict[str, list[dict]], docs: list[dict] | None = None):
        self._per = per_question
        self._docs = docs if docs is not None else []
        self.questions: list[str] = []
        self.retrieve_dataset_ids: list[list[str]] = []
        self.retrieve_document_ids: list[list[str] | None] = []
        self.list_calls = 0

    # 签名与 RAGFlowClient.retrieve 逐字对齐（不收 **kwargs）：
    # 调用方注入客户端不支持的关键字参数时，测试必须像生产一样炸出来
    async def retrieve(self, question, dataset_ids, top_k=5,
                       similarity_threshold=0.2, retrieval_method="hybrid",
                       document_ids=None):
        self.questions.append(question)
        self.retrieve_dataset_ids.append(list(dataset_ids))
        self.retrieve_document_ids.append(list(document_ids) if document_ids else document_ids)
        return self._per.get(question, [])

    async def rerank(self, query, chunks, model):
        return [{**c, "rank": c.get("score", 0.0)} for c in chunks]

    async def list_all_documents(self, dataset_id):
        self.list_calls += 1
        return self._docs


def _chunk(chunk_id: str, content: str, score: float) -> dict:
    return {"chunk_id": chunk_id, "content": content, "score": score, "doc_name": "spec.pdf"}


async def test_multi_questions_merge_and_dedupe_by_max_score():
    ragflow = _FakeRagflow({
        "Q1": [_chunk("c1", "点蚀判级标准", 0.6), _chunk("c2", "低置信处理", 0.4)],
        "Q2": [_chunk("c1", "点蚀判级标准", 0.9), _chunk("c3", "内窥方法", 0.5)],
    })
    tool = RagSearchTool(ragflow, _FakeSettings(), dataset_ids=["ds-1"])

    result = await tool.execute({"scene": "borescope", "standardized": [], "top_k": 5,
                                 "questions": ["Q1", "Q2"]})

    assert result.ok
    knowledge = result.result["knowledge"]
    by_id = {k["chunk_id"]: k for k in knowledge}
    assert set(by_id) == {"c1", "c2", "c3"}
    # RRF 融合（跨 query 共识优先）：c1 被两个 query 同时检回且均排首位 -> 排第一；
    # 跨引擎分数不可比，score 为归一化 RRF（最高共识 = 1.0），不再是原始 0.9
    assert [k["chunk_id"] for k in knowledge][0] == "c1"
    assert by_id["c1"]["score"] == 1.0
    assert by_id["c2"]["score"] < 1.0 and by_id["c3"]["score"] < 1.0
    assert ragflow.questions == ["Q1", "Q2"]
    assert ragflow.retrieve_dataset_ids == [["ds-1"], ["ds-1"]]


async def test_rrf_consensus_beats_single_high_rank():
    """共识优先于单一 query 的排位：Q1 中第 1 的独占 chunk vs 两 query 都第 2 的共识 chunk。"""
    ragflow = _FakeRagflow({
        "Q1": [_chunk("solo", "独占证据", 0.99), _chunk("both", "共识证据", 0.5)],
        "Q2": [_chunk("x", "其他", 0.7), _chunk("both", "共识证据", 0.5)],
    })
    tool = RagSearchTool(ragflow, _FakeSettings(), dataset_ids=["ds-1"])

    result = await tool.execute({"standardized": [], "top_k": 5, "questions": ["Q1", "Q2"]})

    knowledge = result.result["knowledge"]
    # both: 1/61 + 1/62 > solo: 1/61 —— 双 query 共识胜出
    assert knowledge[0]["chunk_id"] == "both"
    assert knowledge[0]["score"] == 1.0
    ids = [k["chunk_id"] for k in knowledge]
    assert ids.index("both") < ids.index("solo")


async def test_rrf_single_query_keeps_rank_order():
    """单 query 走原生路径（len==1 分支）：保留引擎排位与原始分，不进 RRF。"""
    ragflow = _FakeRagflow({
        "Q1": [_chunk("a", "甲", 0.3), _chunk("b", "乙", 0.9)],
    })

    result = await RagSearchTool(ragflow, _FakeSettings(), dataset_ids=["ds-1"]).execute(
        {"standardized": [], "top_k": 5, "questions": ["Q1"]})

    knowledge = result.result["knowledge"]
    assert [k["chunk_id"] for k in knowledge] == ["a", "b"]  # 引擎排位序
    assert knowledge[0]["score"] == 0.3  # 原生路径保留引擎分数（非 RRF 归一化）


async def test_one_failed_question_tolerated_others_merged():
    ragflow = _FakeRagflow({"Q1": [], "Q2": [_chunk("c3", "内窥方法", 0.5)]})

    result = await RagSearchTool(ragflow, _FakeSettings(), dataset_ids=["ds-1"]).execute(
        {"questions": ["Q1", "Q2"], "standardized": []})

    assert result.ok
    assert [k["chunk_id"] for k in result.result["knowledge"]] == ["c3"]


async def test_no_questions_falls_back_to_builtin_question():
    ragflow = _FakeRagflow({"borescope 检测结果解读": [_chunk("c1", "x", 0.8)]})

    result = await RagSearchTool(ragflow, _FakeSettings(), dataset_ids=["ds-1"]).execute(
        {"scene": "borescope", "standardized": [], "top_k": 5})

    assert result.ok
    assert len(result.result["knowledge"]) == 1
    assert ragflow.questions == ["borescope 检测结果解读"]


async def test_no_dataset_ids_degrades_empty():
    result = await RagSearchTool(_FakeRagflow({}), _FakeSettings(), dataset_ids=[]).execute(
        {"questions": ["Q1"], "standardized": []})

    assert result.ok
    assert result.result["knowledge"] == []


async def test_dataset_ids_provider_callable():
    """容器以惰性提供者注入：运行中（PUT /chat/config）更新 dataset_ids 自动生效。"""
    ragflow = _FakeRagflow({"Q1": [_chunk("c1", "x", 0.8)]})
    live = ["ds-a"]
    tool = RagSearchTool(ragflow, _FakeSettings(), dataset_ids=lambda: live)

    await tool.execute({"questions": ["Q1"], "standardized": []})
    live[:] = ["ds-b"]  # 模拟运行中更新
    await tool.execute({"questions": ["Q1"], "standardized": []})

    assert ragflow.retrieve_dataset_ids == [["ds-a"], ["ds-b"]]


async def test_single_question_with_rerank_model_configured_still_returns_hits():
    """回归：配置了重排模型时，单 query 检索不得因客户端签名不兼容而恒空。

    历史 bug：execute() 把 rerank_model 注入 RAGFlowClient.retrieve()（其签名
    无此参数）→ TypeError 被静默吞掉 → 知识库检索恒返回 0 条。
    _FakeRagflow.retrieve 与真实客户端同签名（无 **kwargs），契约偏离即失败。
    """
    class _RerankSettings:
        ragflow_rerank_model = "bge-reranker-v2-m3"  # 已配置重排模型
        rules_dir = None

    ragflow = _FakeRagflow({
        "荧光渗透裂纹判级": [_chunk("c1", "判级标准", 0.6), _chunk("c2", "处置", 0.5)],
    })
    result = await RagSearchTool(ragflow, _RerankSettings(), dataset_ids=["ds-1"]).execute(
        {"scene": "fpi", "standardized": [], "top_k": 5,
         "questions": ["荧光渗透裂纹判级"]})

    assert result.ok
    assert len(result.result["knowledge"]) == 2
    assert ragflow.questions == ["荧光渗透裂纹判级"]


# ---------- P8：场景级数据集路由（rag_rules.yaml scene_datasets，值为数据集 id） ----------

class _RoutedSettings:
    ragflow_rerank_model = ""
    rules_dir = None  # 测试内直接 monkeypatch load_rag_rules


async def test_scene_datasets_route_overrides_default(monkeypatch):
    """rag_rules.yaml 配置了 scene_datasets 时，按场景路由到专属数据集 id。"""
    from app.agents.tools import rag_tool as rt

    monkeypatch.setattr(rt, "load_rag_rules",
                        lambda _d: {"scene_datasets": {"borescope": ["ds-borescope"]}})

    ragflow = _FakeRagflow({"borescope 检测结果解读": [_chunk("c1", "x", 0.8)]})
    result = await RagSearchTool(ragflow, _RoutedSettings(), dataset_ids=["ds-default"]).execute(
        {"scene": "borescope", "standardized": [], "top_k": 5})

    assert result.ok
    # 只查场景路由的数据集，不查默认全集
    assert ragflow.retrieve_dataset_ids == [["ds-borescope"]]


async def test_scene_datasets_unmapped_scene_falls_back_to_default(monkeypatch):
    """未配置场景回退 ragflow_chat.yaml 的 dataset_ids 全集。"""
    from app.agents.tools import rag_tool as rt

    monkeypatch.setattr(rt, "load_rag_rules",
                        lambda _d: {"scene_datasets": {"borescope": ["ds-borescope"]}})

    ragflow = _FakeRagflow({"fracture 检测结果解读": [_chunk("c1", "x", 0.8)]})
    result = await RagSearchTool(ragflow, _RoutedSettings(), dataset_ids=["ds-default"]).execute(
        {"scene": "fracture", "standardized": [], "top_k": 5})

    assert result.ok
    assert ragflow.retrieve_dataset_ids == [["ds-default"]]  # fracture 未配置，回退默认


# ---------- 场景级文档排除（rag_rules.yaml scene_doc_excludes，混库文档名模式过滤） ----------

# 混库文档清单（取自生产知识库的真实命名形态）
_MIXED_DOCS = [
    {"id": "doc-ir", "name": "超声红外热成像技术在航空发动机叶片裂纹的对比研究.pdf"},
    {"id": "doc-borescope", "name": "BD-YOLO：一个基于深度学习的航空发动机孔探图像叶片损伤检测模型.pdf"},
    {"id": "doc-fpi", "name": "1. Crack identification of automobile steering knuckle fluorescent penetrant inspection.pdf"},
    {"id": "doc-fracture", "name": "K465合金涡轮叶片叶身开裂分析_孙旭波.pdf"},
    {"id": "doc-medical", "name": "16_基于深度神经网络的骨质疏松自动检测方法_CC-BY.pdf"},
]

_EXCLUDES = {
    "scene_doc_excludes": {
        "all": ["医学影像", "骨质"],
        "fpi": ["红外", "热像", "thermography", "孔探", "borescope"],
        "infrared": ["荧光", "penetrant", "fluorescent"],
    },
}


async def test_fpi_scene_excludes_infrared_and_borescope_docs(monkeypatch):
    """荧光场景：红外/孔探/医学文献不进检索范围（document_ids 前置过滤），
    通用失效分析（K465 开裂分析）保留——只排方法错位，不伤召回广度。"""
    from app.agents.tools import rag_tool as rt

    monkeypatch.setattr(rt, "load_rag_rules", lambda _d: dict(_EXCLUDES))
    ragflow = _FakeRagflow({"Q1": [_chunk("c1", "x", 0.8)]}, docs=_MIXED_DOCS)

    result = await RagSearchTool(ragflow, _RoutedSettings(), dataset_ids=["ds-1"]).execute(
        {"scene": "fpi", "standardized": [], "top_k": 5, "questions": ["Q1"]})

    assert result.ok
    assert ragflow.retrieve_document_ids == [["doc-fpi", "doc-fracture"]]


async def test_all_patterns_apply_without_scene(monkeypatch):
    """scene 缺失时仍应用 all 级排除（医学等无关文献对任何场景都是噪声）。"""
    from app.agents.tools import rag_tool as rt

    monkeypatch.setattr(rt, "load_rag_rules", lambda _d: dict(_EXCLUDES))
    ragflow = _FakeRagflow({"Q1": [_chunk("c1", "x", 0.8)]}, docs=_MIXED_DOCS)

    await RagSearchTool(ragflow, _RoutedSettings(), dataset_ids=["ds-1"]).execute(
        {"standardized": [], "top_k": 5, "questions": ["Q1"]})

    # 无 scene → 仅 all 排除 doc-medical
    assert ragflow.retrieve_document_ids == [["doc-ir", "doc-borescope", "doc-fpi", "doc-fracture"]]


async def test_no_excludes_configured_retrieves_unfiltered(monkeypatch):
    """完全未配置排除规则：不拉文档清单、不附带 document_ids（请求保持精简）。"""
    from app.agents.tools import rag_tool as rt

    monkeypatch.setattr(rt, "load_rag_rules", lambda _d: {"scene_doc_excludes": {}})
    ragflow = _FakeRagflow({"Q1": [_chunk("c1", "x", 0.8)]}, docs=_MIXED_DOCS)

    await RagSearchTool(ragflow, _RoutedSettings(), dataset_ids=["ds-1"]).execute(
        {"scene": "fracture", "standardized": [], "top_k": 5, "questions": ["Q1"]})

    assert ragflow.retrieve_document_ids == [None]
    assert ragflow.list_calls == 0


async def test_scene_with_only_all_rules_still_filters(monkeypatch):
    """场景无专属键但配置了 all：仍应用 all 级排除（fracture 也滤医学文献）。"""
    from app.agents.tools import rag_tool as rt

    monkeypatch.setattr(rt, "load_rag_rules", lambda _d: dict(_EXCLUDES))
    ragflow = _FakeRagflow({"Q1": [_chunk("c1", "x", 0.8)]}, docs=_MIXED_DOCS)

    await RagSearchTool(ragflow, _RoutedSettings(), dataset_ids=["ds-1"]).execute(
        {"scene": "fracture", "standardized": [], "top_k": 5, "questions": ["Q1"]})

    assert ragflow.retrieve_document_ids == [
        ["doc-ir", "doc-borescope", "doc-fpi", "doc-fracture"]]


async def test_patterns_excluding_nothing_skip_filter(monkeypatch):
    """规则存在但没有文档命中：等价于无过滤（None，不下发全量白名单）。"""
    from app.agents.tools import rag_tool as rt

    monkeypatch.setattr(rt, "load_rag_rules",
                        lambda _d: {"scene_doc_excludes": {"dr": ["不存在的方法"]}})
    ragflow = _FakeRagflow({"Q1": [_chunk("c1", "x", 0.8)]}, docs=_MIXED_DOCS)

    await RagSearchTool(ragflow, _RoutedSettings(), dataset_ids=["ds-1"]).execute(
        {"scene": "dr", "standardized": [], "top_k": 5, "questions": ["Q1"]})

    assert ragflow.retrieve_document_ids == [None]


async def test_all_docs_excluded_returns_empty_knowledge_without_retrieval(monkeypatch):
    """全部文档被排除：直接空知识链，不发起检索（场景无可信知识好过错场景知识）。"""
    from app.agents.tools import rag_tool as rt

    monkeypatch.setattr(rt, "load_rag_rules",
                        lambda _d: {"scene_doc_excludes": {"fpi": [".pdf"]}})
    ragflow = _FakeRagflow({"Q1": [_chunk("c1", "x", 0.8)]}, docs=_MIXED_DOCS)

    result = await RagSearchTool(ragflow, _RoutedSettings(), dataset_ids=["ds-1"]).execute(
        {"scene": "fpi", "standardized": [], "top_k": 5, "questions": ["Q1"]})

    assert result.ok
    assert result.result["knowledge"] == []
    assert ragflow.questions == []  # 未发起检索


async def test_doc_listing_failure_fails_open(monkeypatch):
    """文档清单拉取失败：放弃过滤照常检索（fail-open，不误伤召回）。"""
    from app.agents.tools import rag_tool as rt

    monkeypatch.setattr(rt, "load_rag_rules", lambda _d: dict(_EXCLUDES))

    class _BrokenListing(_FakeRagflow):
        async def list_all_documents(self, dataset_id):
            raise RuntimeError("ragflow down")

    ragflow = _BrokenListing({"Q1": [_chunk("c1", "x", 0.8)]})
    result = await RagSearchTool(ragflow, _RoutedSettings(), dataset_ids=["ds-1"]).execute(
        {"scene": "fpi", "standardized": [], "top_k": 5, "questions": ["Q1"]})

    assert result.ok
    assert len(result.result["knowledge"]) == 1
    assert ragflow.retrieve_document_ids == [None]


async def test_doc_listing_cached_across_executes(monkeypatch):
    """TTL 缓存：连续两次检索只拉一次文档清单（清单低频变动，省 API 往返）。"""
    from app.agents.tools import rag_tool as rt

    monkeypatch.setattr(rt, "load_rag_rules", lambda _d: dict(_EXCLUDES))
    ragflow = _FakeRagflow({"Q1": [_chunk("c1", "x", 0.8)]}, docs=_MIXED_DOCS)
    tool = RagSearchTool(ragflow, _RoutedSettings(), dataset_ids=["ds-1"])

    await tool.execute({"scene": "fpi", "standardized": [], "top_k": 5, "questions": ["Q1"]})
    await tool.execute({"scene": "fpi", "standardized": [], "top_k": 5, "questions": ["Q1"]})

    assert ragflow.list_calls == 1
    assert ragflow.retrieve_document_ids == [["doc-fpi", "doc-fracture"]] * 2


async def test_name_pattern_match_is_case_insensitive(monkeypatch):
    """文档名模式大小写不敏感：Thermography / THERMOGRAPHY 同样命中。"""
    from app.agents.tools import rag_tool as rt

    monkeypatch.setattr(rt, "load_rag_rules",
                        lambda _d: {"scene_doc_excludes": {"fpi": ["thermography"]}})
    ragflow = _FakeRagflow(
        {"Q1": [_chunk("c1", "x", 0.8)]},
        docs=[
            {"id": "d1", "name": "A Crack Detection Method Based on Air-Flow Thermography.pdf"},
            {"id": "d2", "name": "K465合金涡轮叶片叶身开裂分析.pdf"},
        ],
    )

    await RagSearchTool(ragflow, _RoutedSettings(), dataset_ids=["ds-1"]).execute(
        {"scene": "fpi", "standardized": [], "top_k": 5, "questions": ["Q1"]})

    assert ragflow.retrieve_document_ids == [["d2"]]
