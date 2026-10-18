"""任务服务测试：重试守卫 / 状态重建 + 同 thread 重跑回归（真实 LangGraph + MemorySaver）。

回归主测（test_retry_reruns_full_pipeline_on_same_thread）钉住的语义：
失败终态 thread 用同一 thread_id 重跑时，旧通道值会残留——重建 state 必须显式
置空 error 等全部运行期派生键，否则第一跳路由读到残留 error 会立刻扎回 __fail__
（steps 只会多 2 条而非 3 条）。
"""
from __future__ import annotations

import asyncio
import json

import pytest
import pytest_asyncio
from langgraph.checkpoint.memory import MemorySaver

from app.core.errors import BizCode, BusinessError
from app.models.capability import CapabilityManifest
from app.models.task import (
    STATUS_AWAITING_REVIEW,
    STATUS_FAILED,
    STATUS_PENDING,
    STATUS_RUNNING,
)
from app.repositories.task_repo import TaskRepo
from app.services.deps import AgentContext
from app.services.task_service import TaskService


class _StubRegistry:
    """select_capability 只用到 list()/lookup()，用桩隔离 DB/轮询。"""

    def __init__(self, manifest: CapabilityManifest) -> None:
        self._m = manifest

    def list(self) -> list[CapabilityManifest]:
        return [self._m]

    def lookup(self, scene: str) -> CapabilityManifest | None:
        return self._m if self._m.scene == scene else None


@pytest_asyncio.fixture
async def svc(session_factory):
    repo = TaskRepo(session_factory)
    manifest = CapabilityManifest(
        capability_id="borescope_defect_detection_v1",
        tool_name="borescope_v1",
        scene="borescope",
    )
    # tool_registry=None => invoke_capability 在第 3 节点确定性失败（4001）
    ctx = AgentContext(registry=_StubRegistry(manifest), task_repo=repo, tool_registry=None)
    service = TaskService(ctx, repo, checkpointer=MemorySaver())
    return service, repo


def _inputs(**overrides) -> dict:
    base = {
        "scenes": None,
        "input_uris": ["/api/v1/tasks/x.jpg"],
        "auxiliary": {},
        "parameters": {"threshold": 0.5},
    }
    base.update(overrides)
    return base


def _init_state(task_id: str, inputs: dict) -> dict:
    return {
        "task_id": task_id,
        "trace_id": "trace-r",
        "mode": "single",
        "scene": "borescope",
        "input_uris": inputs["input_uris"],
        "auxiliary": inputs["auxiliary"],
        "parameters": inputs["parameters"],
    }


# ---------- 守卫 ----------

class _FakeGraph:
    """事件流桩：按序吐出预置事件（模拟 interrupt 暂停）。"""

    def __init__(self, events: list):
        self._events = events

    async def astream(self, state, config=None, stream_mode=None):
        for ev in self._events:
            yield ev


async def test_interrupt_persists_paused_grade_with_agent_request(svc):
    """共识（异议机制）：复核暂停期间判级同步落库——任务卡/事件/评估矩阵
    在 awaiting_review 阶段就能看到判级与异议理由（此前仅落 review_json）。"""
    from types import SimpleNamespace

    service, repo = svc
    await repo.create("TIR1", scene="fpi", mode="agent", inputs=_inputs(), trace_id="t")

    grade = {
        "level": "review",
        "summary": "智能体异议，请求人工复核：能力报无异常，但图像可见缺陷显示",
        "triggered_rules": ["agent_request"],
        "auto": True,
    }
    payload = {"question": "智能体异议，请求人工复核：能力报无异常，但图像可见缺陷显示",
               "task_id": "TIR1", "scene": "fpi", "grade": grade, "decision_options": ["confirm"]}
    service._graph = _FakeGraph([{"__interrupt__": [SimpleNamespace(value=payload)]}])

    await service.run_task("TIR1", _init_state("TIR1", _inputs()))

    rec = await repo.get("TIR1")
    assert rec.status == STATUS_AWAITING_REVIEW
    assert json.loads(rec.grade_json)["triggered_rules"] == ["agent_request"]
    assert "智能体异议" in json.loads(rec.grade_json)["summary"]
    assert json.loads(rec.review_json)["grade"]["level"] == "review"
    assert rec.scene == "fpi"  # 暂停期间场景同步落库（事件回报/评估矩阵可见）


async def test_interrupt_without_grade_only_persists_review_json(svc):
    """场景确认等无 grade 的中断：只落 review_json，不产生空判级。"""
    from types import SimpleNamespace

    service, repo = svc
    await repo.create("TIR2", scene=None, mode="single", inputs=_inputs(), trace_id="t")

    payload = {"confirm_type": "scene", "candidates": ["fpi", "dr"]}
    service._graph = _FakeGraph([{"__interrupt__": [SimpleNamespace(value=payload)]}])

    await service.run_task("TIR2", _init_state("TIR2", _inputs()))

    rec = await repo.get("TIR2")
    assert rec.status == STATUS_AWAITING_REVIEW
    assert json.loads(rec.review_json)["confirm_type"] == "scene"
    assert not rec.grade_json


async def test_retry_unknown_task_raises_404(svc):
    service, _ = svc
    with pytest.raises(BusinessError) as exc:
        await service.retry_task("T404")
    assert exc.value.code == BizCode.TASK_NOT_FOUND
    assert exc.value.http_status == 404


async def test_retry_rejects_non_failed(svc):
    service, repo = svc
    await repo.create("T001", scene="borescope", mode="single", inputs=_inputs(), trace_id="trace-r")
    await repo.set_status("T001", STATUS_RUNNING)
    with pytest.raises(BusinessError) as exc:
        await service.retry_task("T001")
    assert exc.value.code == BizCode.TASK_STATE_CONFLICT
    assert exc.value.http_status == 409
    assert (await repo.get("T001")).status == STATUS_RUNNING  # 未被改动


# ---------- 状态重建 ----------

async def test_retry_rebuilds_state_and_resets_record(svc):
    service, repo = svc
    await repo.create(
        "T002",
        scene="borescope",
        mode="single",
        inputs=_inputs(scenes=["metallography", "borescope"]),
        trace_id="trace-r",
    )
    await repo.set_error("T002", 3002, "boom")  # failed + finished_at + error_json

    prepared = await service.retry_task("T002")

    assert prepared["task_id"] == "T002"
    state = prepared["state"]
    assert state["trace_id"] == "trace-r"
    assert state["scene"] == "borescope"
    assert state["input_uris"] == ["/api/v1/tasks/x.jpg"]
    assert state["parameters"] == {"threshold": 0.5}
    assert state["scenes"] == ["metallography", "borescope"]  # 重试按 inputs_json 重建声明场景
    # 残留通道必须显式清空（尤其 error）
    for key in ("error", "raw_result", "grade_hint", "grade", "review", "report",
                "selected_capability", "error_report"):
        assert state[key] is None
    for key in ("candidate_scenes", "standardized", "knowledge", "retrieval_trace"):
        assert state[key] == []
    assert state["metric_explanations"] == ""

    rec = await repo.get("T002")
    assert rec.status == STATUS_PENDING
    assert rec.finished_at is None
    assert rec.error_json is None
    steps = json.loads(rec.steps_json)
    assert steps[-1]["node"] == "retry"  # 重试标记入审计链


async def test_retry_old_record_without_parameters_defaults_empty(svc):
    service, repo = svc
    legacy = _inputs()
    legacy.pop("parameters")
    await repo.create("T003", scene="borescope", mode="single", inputs=legacy, trace_id="t")
    await repo.set_error("T003", 3002, "boom")

    prepared = await service.retry_task("T003")

    assert prepared["state"]["parameters"] == {}
    assert prepared["state"]["scenes"] == []  # 老记录无 scenes 键 → 重试回退空列表


async def test_prepare_state_carries_declared_scenes(svc):
    service, _ = svc

    prepared = await service.prepare(
        scene=None,
        mode="joint",
        scenes=["metallography", "fracture", "fpi", "borescope"],
        parameters=None,
        input_uris=["/api/v1/tasks/x.jpg"],
        auxiliary={},
    )

    assert prepared["state"]["scenes"] == ["metallography", "fracture", "fpi", "borescope"]


# ---------- 回归主测：同 thread 失败后重跑（残留 error 必须被清） ----------

async def test_retry_reruns_full_pipeline_on_same_thread(svc):
    service, repo = svc
    inputs = _inputs()
    await repo.create("TR1", scene="borescope", mode="single", inputs=inputs, trace_id="trace-r")

    # 第 1 遍：select_capability / resolve_capability / validate / invoke_capability(失败) / explain_error => 5 条 steps，任务 failed
    await service.run_task("TR1", _init_state("TR1", inputs))
    rec = await repo.get("TR1")
    assert rec.status == STATUS_FAILED
    assert json.loads(rec.error_json)["code"] == int(BizCode.INTERNAL_ERROR)
    assert len(json.loads(rec.steps_json)) == 5

    # 重试：原子重置 + 重建 state（error 显式清空）
    prepared = await service.retry_task("TR1")
    assert prepared["state"]["error"] is None
    assert len(json.loads((await repo.get("TR1")).steps_json)) == 6  # +retry 标记

    # 第 2 遍：同 thread 必须完整重跑 5 个节点（若残留 error，只会在 validate 后扎回 __fail__）
    await service.run_task("TR1", prepared["state"])
    rec2 = await repo.get("TR1")
    assert rec2.status == STATUS_FAILED  # 依旧失败（tool_registry 仍缺失），但确实重跑了
    assert json.loads(rec2.error_json)["code"] == int(BizCode.INTERNAL_ERROR)
    assert len(json.loads(rec2.steps_json)) == 11  # 5 + retry + 5
    assert rec2.finished_at is not None


# ---------- 详情契约：input_uris 透出（前端原图回显依赖） ----------

async def test_get_exposes_input_uris_from_inputs_json(svc):
    service, repo = svc
    await repo.create("T011", scene="borescope", mode="single", inputs=_inputs(), trace_id="t")

    detail = await service.get("T011")

    assert detail is not None
    assert detail["input_uris"] == ["/api/v1/tasks/x.jpg"]


async def test_get_input_uris_empty_when_inputs_missing(svc):
    service, repo = svc
    await repo.create("T012", scene="borescope", mode="single", inputs=None, trace_id="t")

    detail = await service.get("T012")

    assert detail is not None
    assert detail["input_uris"] == []


# ---------- 时间序列化（东八区显示修复的 API 边界） ----------

async def test_timestamps_serialize_utc_iso(svc):
    service, repo = svc
    await repo.create("T010", scene="borescope", mode="single", inputs=_inputs(), trace_id="t")
    await repo.set_error("T010", 3002, "boom")  # finished_at 落值

    items = await service.list_tasks()
    listed = next(i for i in items if i["task_id"] == "T010")
    assert listed["created_at"].endswith("+00:00")
    assert listed["updated_at"].endswith("+00:00")

    detail = await service.get("T010")
    for key in ("created_at", "updated_at", "finished_at"):
        assert detail[key].endswith("+00:00")


# ---------- 失败终态持久化（explain_error 收尾） ----------

async def test_failed_run_steps_include_explain_error_and_error_keeps_code(svc):
    service, repo = svc
    await repo.create("TE1", scene="borescope", mode="single", inputs=_inputs(), trace_id="t")
    await service.run_task("TE1", _init_state("TE1", _inputs()))
    rec = await repo.get("TE1")
    assert rec.status == STATUS_FAILED
    error = json.loads(rec.error_json)
    assert error["node"] == "invoke_capability"
    assert error["code"] == int(BizCode.INTERNAL_ERROR)
    assert error["summary"] == error["message"]          # 无 LLM 时回退原始消息
    steps = json.loads(rec.steps_json)
    assert steps[-1]["node"] == "explain_error"


class _SummarizingLLM:
    available = True

    async def summarize_error(self, *, error, task_id=None, scene=None, language="中文"):
        return "检测服务未装配，能力调用失败。"


class _FakeFileRepo:
    def save_text(self, task_id: str, name: str, text: str, kind: str = "reports") -> str:
        return "file:///stub/" + name


async def test_failed_run_persists_summary_and_error_report_with_llm_and_file_repo(session_factory):
    repo = TaskRepo(session_factory)
    manifest = CapabilityManifest(
        capability_id="borescope_defect_detection_v1",
        tool_name="borescope_v1",
        scene="borescope",
    )
    ctx = AgentContext(registry=_StubRegistry(manifest), task_repo=repo,
                       tool_registry=None, llm=_SummarizingLLM(), file_repo=_FakeFileRepo())
    service = TaskService(ctx, repo, checkpointer=MemorySaver())

    await repo.create("TE2", scene="borescope", mode="single", inputs=_inputs(), trace_id="t")
    await service.run_task("TE2", _init_state("TE2", _inputs()))

    rec = await repo.get("TE2")
    assert rec.status == STATUS_FAILED
    error = json.loads(rec.error_json)
    assert "检测服务未装配" in error["summary"]
    report = json.loads(rec.report_json)
    assert report["report_type"] == "error_report"
    assert report["name"] == "TE2_error_report.md"
    assert report["uri"] == "file:///stub/error_report.md"


# ---------- 场景人工确认入口与守卫 ----------

def _scene_pending(payload_override: dict | None = None) -> dict:
    base = {"confirm_type": "scene", "task_id": "TS1",
            "candidates": [{"scene": "borescope", "source": "llm"},
                           {"scene": "infrared", "source": "llm"}]}
    return {**base, **(payload_override or {})}


async def test_confirm_scene_unknown_task_404(svc):
    service, _ = svc
    with pytest.raises(BusinessError) as exc:
        await service.confirm_scene("T404", "borescope")
    assert exc.value.http_status == 404


async def test_confirm_scene_rejects_not_awaiting(svc):
    service, repo = svc
    await repo.create("TS1", scene=None, mode="single", inputs=_inputs(), trace_id="t")
    with pytest.raises(BusinessError) as exc:
        await service.confirm_scene("TS1", "borescope")
    assert exc.value.code == BizCode.TASK_STATE_CONFLICT
    assert exc.value.http_status == 409


async def test_confirm_scene_rejects_review_payload(svc):
    service, repo = svc
    await repo.create("TS2", scene=None, mode="single", inputs=_inputs(), trace_id="t")
    await repo.patch_json("TS2", review_json={"question": "人工复核"})
    await repo.set_status("TS2", STATUS_AWAITING_REVIEW)
    with pytest.raises(BusinessError) as exc:
        await service.confirm_scene("TS2", "borescope")
    assert exc.value.http_status == 409


async def test_confirm_scene_rejects_scene_outside_candidates(svc):
    service, repo = svc
    await repo.create("TS3", scene=None, mode="single", inputs=_inputs(), trace_id="t")
    await repo.patch_json("TS3", review_json=_scene_pending())
    await repo.set_status("TS3", STATUS_AWAITING_REVIEW)
    with pytest.raises(BusinessError) as exc:
        await service.confirm_scene("TS3", "dr")
    assert exc.value.code == BizCode.INVALID_REQUEST
    assert exc.value.http_status == 400


async def test_review_rejects_scene_confirm_pending_task(svc):
    service, repo = svc
    await repo.create("TS4", scene=None, mode="single", inputs=_inputs(), trace_id="t")
    await repo.patch_json("TS4", review_json=_scene_pending())
    await repo.set_status("TS4", STATUS_AWAITING_REVIEW)
    with pytest.raises(BusinessError) as exc:
        await service.review_task("TS4", action="confirm", opinion="ok")
    assert exc.value.code == BizCode.TASK_STATE_CONFLICT
    assert exc.value.http_status == 409


class _TwoSceneRegistry:
    def __init__(self, *scenes: str) -> None:
        self._scenes = scenes

    def list(self) -> list[CapabilityManifest]:
        return [CapabilityManifest(capability_id=f"{s}_v1", tool_name=f"{s}_v1", scene=s)
                for s in self._scenes]

    def lookup(self, scene: str) -> CapabilityManifest | None:
        for m in self.list():
            if m.scene == scene:
                return m
        return None


class _AmbiguousLLM:
    available = True

    async def extract_scene(self, text: str, scenes: list[str], *, images=None) -> list[dict]:
        return [
            {"scene": "borescope", "source": "llm"},
            {"scene": "infrared", "source": "llm", "role": "alternative"},
        ]


class _SettingsStub:
    scene_confirm_enabled = True


async def test_confirm_scene_resumes_graph_with_chosen_scene(session_factory):
    """正常恢复：图在 select_capability 暂停后，confirm_scene 以所选场景续跑全流程。"""
    repo = TaskRepo(session_factory)
    ctx = AgentContext(registry=_TwoSceneRegistry("borescope", "infrared"),
                       settings=_SettingsStub(), llm=_AmbiguousLLM(), task_repo=repo,
                       tool_registry=None)
    service = TaskService(ctx, repo, checkpointer=MemorySaver())

    await repo.create("TS5", scene=None, mode="single", inputs=_inputs(), trace_id="t")
    state = {"task_id": "TS5", "trace_id": "t", "mode": "single", "scene": None,
             "input_uris": ["/api/v1/tasks/x.jpg"], "auxiliary": {},
             "parameters": {"threshold": 0.5}}
    await service.run_task("TS5", state)

    rec = await repo.get("TS5")
    assert rec.status == STATUS_AWAITING_REVIEW
    assert json.loads(rec.review_json)["confirm_type"] == "scene"

    await service.confirm_scene("TS5", "infrared")

    # 恢复已改为「即时受理 + 后台执行」：等后台图跑完落定（running -> failed）
    rec2 = await repo.get("TS5")
    for _ in range(200):
        if rec2.status != STATUS_RUNNING:
            break
        await asyncio.sleep(0.01)
        rec2 = await repo.get("TS5")
    assert rec2.status == STATUS_FAILED  # 恢复后图继续执行，invoke 确定性失败（tool_registry 缺失）
    assert rec2.scene == "infrared"      # 所选场景透传并持久化


async def test_review_task_accepts_instantly_and_resumes_in_background(svc):
    """复核提交 = 即时受理 + 后台恢复：认领立即翻 running、回调即达、双击 409。"""
    service, repo = svc
    await repo.create("TSR", scene=None, mode="single", inputs=_inputs(), trace_id="t")
    await repo.patch_json("TSR", review_json={"question": "人工复核"})
    await repo.set_status("TSR", STATUS_AWAITING_REVIEW)

    accepted: list[tuple[str, str]] = []

    async def _on_accepted(task_id: str, action: str) -> None:
        accepted.append((task_id, action))

    service.on_review_accepted = _on_accepted

    await service.review_task("TSR", action="confirm", opinion="ok")
    # 受理即时返回：状态已原子翻为 running，受理回调带动作触达
    assert (await repo.get("TSR")).status == STATUS_RUNNING
    assert accepted == [("TSR", "confirm")]

    # 双击/重试竞态：第二次受理被 409 拒绝
    with pytest.raises(BusinessError) as exc:
        await service.review_task("TSR", action="confirm", opinion="again")
    assert exc.value.http_status == 409

    # 后台恢复最终落定（伪图无复核断点，恢复路径收敛或报错落 failed 均可）
    rec = await repo.get("TSR")
    for _ in range(200):
        if rec.status != STATUS_RUNNING:
            break
        await asyncio.sleep(0.01)
        rec = await repo.get("TSR")
    assert rec.status != STATUS_RUNNING


async def test_interrupt_persists_derived_channels_for_review_ui(svc):
    """复核工作台数据源：中断期 standardized/class_definitions/knowledge/
    capability_used 同步落库——否则复核页「标注图」层没有缺陷框可画
    （与原图无差别）、AI 预判列为空（终局 _persist 要等复核后才执行）。"""
    from types import SimpleNamespace

    service, repo = svc
    await repo.create("TIR5", scene="fpi", mode="agent", inputs=_inputs(), trace_id="t")

    values = {
        "task_id": "TIR5",
        "scene": "fpi",
        "standardized": [{"defect_type": "BP", "defect_name_cn": "模糊的真缺陷",
                          "confidence": 0.76, "severity": None, "location": None}],
        "class_definitions": [{"class_id": 0, "class_name": "BP",
                               "description": "模糊的真缺陷", "is_true_defect": True}],
        "knowledge": [{"content": "x", "score": 0.9, "doc_name": "d.pdf", "chunk_id": "c1"}],
        "selected_capability": {"tool_name": "blade_defect_detector"},
        "grade": {"level": "review", "summary": "s"},
        "review": None,
        "report": None,
    }

    class _SnapshotGraph(_FakeGraph):
        async def aget_state(self, config):
            return SimpleNamespace(values=values)

    payload = {"question": "q", "task_id": "TIR5", "scene": "fpi",
               "grade": values["grade"], "decision_options": ["confirm"]}
    service._graph = _SnapshotGraph([{"__interrupt__": [SimpleNamespace(value=payload)]}])

    await service.run_task("TIR5", _init_state("TIR5", _inputs()))

    rec = await repo.get("TIR5")
    assert rec.status == STATUS_AWAITING_REVIEW
    assert json.loads(rec.standardized_json)[0]["defect_type"] == "BP"
    assert json.loads(rec.class_definitions_json)[0]["class_name"] == "BP"
    assert json.loads(rec.knowledge_json)[0]["doc_name"] == "d.pdf"
    assert rec.capability_used == "blade_defect_detector"
    # 快照里 review/report 为 None：patch_json 跳过，不覆盖中断载荷写入的 review_json
    assert json.loads(rec.review_json)["question"] == "q"


async def test_interrupt_snapshot_failure_does_not_block_review_gate(svc):
    """快照获取失败（如 checkpointer 异常）不阻断复核门：任务照常进入
    awaiting_review，review_json 完整（_FakeGraph 无 aget_state 即此场景）。"""
    from types import SimpleNamespace

    service, repo = svc
    await repo.create("TIR6", scene="fpi", mode="agent", inputs=_inputs(), trace_id="t")

    payload = {"question": "q", "task_id": "TIR6", "scene": "fpi", "decision_options": ["confirm"]}
    service._graph = _FakeGraph([{"__interrupt__": [SimpleNamespace(value=payload)]}])

    await service.run_task("TIR6", _init_state("TIR6", _inputs()))

    rec = await repo.get("TIR6")
    assert rec.status == STATUS_AWAITING_REVIEW
    assert json.loads(rec.review_json)["question"] == "q"
