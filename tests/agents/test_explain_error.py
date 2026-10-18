"""explain_error 节点：失败任务的可读异常总结 + 异常报告落盘（可降级）。"""
from app.agents.nodes.explain_error import explain_error_node
from app.services.deps import AgentContext


class _FakeLLM:
    available = True

    def __init__(self, text: str | None = None):
        self._text = text

    async def summarize_error(self, *, error, task_id=None, scene=None, language="中文"):
        if self._text is None:
            raise RuntimeError("llm down")
        return self._text


class _FakeFileRepo:
    def __init__(self, uri="file:///stub/error_report.md", fail=False):
        self._uri = uri
        self._fail = fail
        self.saved: list[tuple] = []

    def save_text(self, task_id: str, name: str, text: str, kind: str = "reports") -> str:
        if self._fail:
            raise RuntimeError("disk down")
        self.saved.append((task_id, name, text, kind))
        return self._uri


async def test_llm_summary_and_report_saved():
    llm = _FakeLLM("检测服务未装配：能力调用工具缺失，属环境配置问题。")
    repo = _FakeFileRepo()
    ctx = AgentContext(llm=llm, file_repo=repo)

    result = await explain_error_node(
        {"task_id": "T1", "scene": "borescope",
         "error": {"node": "invoke_capability", "code": 4001, "message": "tool missing"}},
        ctx,
    )

    assert result["error"]["summary"] == "检测服务未装配：能力调用工具缺失，属环境配置问题。"
    assert result["error_report"]["report_type"] == "error_report"
    assert result["error_report"]["name"] == "T1_error_report.md"
    assert len(repo.saved) == 1
    md = repo.saved[0][2]
    assert "## 1. 失败概要" in md
    assert "tool missing" in md


async def test_llm_failure_falls_back_to_raw_message_no_report_ok():
    result = await explain_error_node(
        {"task_id": "T1", "error": {"node": "invoke_capability", "code": 4001, "message": "tool missing"}},
        AgentContext(llm=_FakeLLM(None), file_repo=None),
    )

    assert result["error"]["summary"] == "tool missing"
    assert result["error_report"] is None


async def test_file_save_failure_degrades_report_none():
    result = await explain_error_node(
        {"task_id": "T1", "error": {"node": "invoke_capability", "code": 4001, "message": "x"}},
        AgentContext(llm=_FakeLLM("总结"), file_repo=_FakeFileRepo(fail=True)),
    )

    assert result["error"]["summary"] == "总结"
    assert result["error_report"] is None