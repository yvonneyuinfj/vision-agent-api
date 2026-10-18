"""explain_metrics 节点：LLM 可选增强，任何异常都降级为空串（不得污染 error 通道）。"""
from app.agents.nodes.explain_metrics import explain_metrics_node
from app.services.deps import AgentContext


class _FakeLLM:
    available = True

    def __init__(self, text: str | None = None, explode: bool = False):
        self._text = text
        self._explode = explode
        self.explain_calls = 0

    async def explain_metrics(self, *, standardized, knowledge, language="中文"):
        self.explain_calls += 1
        if self._explode:
            raise RuntimeError("llm down")
        return self._text or ""


def _state(**overrides) -> dict:
    base = {"task_id": "T1", "standardized": [{"defect_type": "点蚀", "severity": "low", "confidence": 0.8}]}
    base.update(overrides)
    return base


async def test_returns_explanation_text_from_llm():
    llm = _FakeLLM(text="### 点蚀\n点蚀是局部腐蚀形态，评判要点为深度与密度。")
    result = await explain_metrics_node(_state(), AgentContext(llm=llm))

    assert result == {"metric_explanations": "### 点蚀\n点蚀是局部腐蚀形态，评判要点为深度与密度。"}
    assert llm.explain_calls == 1


async def test_llm_failure_degrades_to_empty_not_error():
    result = await explain_metrics_node(_state(), AgentContext(llm=_FakeLLM(explode=True)))

    assert result == {"metric_explanations": ""}


async def test_no_standardized_skips_llm():
    llm = _FakeLLM(text="不应调用")
    result = await explain_metrics_node(_state(standardized=[]), AgentContext(llm=llm))

    assert result == {"metric_explanations": ""}
    assert llm.explain_calls == 0


async def test_no_llm_degrades_empty():
    result = await explain_metrics_node(_state(), AgentContext(llm=None))

    assert result == {"metric_explanations": ""}


def test_graph_compiles_with_explain_metrics_node():
    from app.agents.graph import build_single_graph
    from app.services.deps import AgentContext

    graph = build_single_graph(AgentContext())
    assert "explain_metrics" in graph.get_graph().nodes