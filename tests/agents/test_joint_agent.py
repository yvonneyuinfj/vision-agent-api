"""联合质检层（mode=joint ReAct 轨）回归。

覆盖决策 8/10 的关键不变量：
- 联合工具箱：launch_child_task 幂等/候选校验/上限/父子关系透传；
  get_child_status 只认本任务派发的子任务；await_children 登记等待；
- await_children 节点：无子任务拒绝、interrupt 载荷、唤醒后确定性聚合；
- 路由：route_after_tools_joint 返回映射键（含 await）；
- 出口完备性：联合父任务以 children_settled 为检测凭据；
- task_service：await_children 中断不进入人工复核语义；
  _maybe_resume_parent 仅在父图等待且全部子任务落定时唤醒。
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from langchain_core.messages import AIMessage

from app.agents.react.graph import build_react_graph, route_after_tools_joint
from app.agents.react.joint_tools import JointToolbox
from app.agents.react.nodes import await_children_node, exit_gate_node
from app.agents.react.tools import _submit_final_impl
from app.core.errors import BizCode
from app.models.capability import CapabilityManifest
from app.services.deps import AgentContext
from app.services.task_service import _is_child_wait


# --------------------------------------------------------------------------
# 桩件
# --------------------------------------------------------------------------
class _Registry:
    def __init__(self, scenes: list[str] | None = None):
        self._scenes = scenes or ["fracture", "metallography", "fpi"]

    def list(self):
        return [
            CapabilityManifest(
                capability_id=f"{s}_v1", tool_name=f"{s}_tool", scene=s,
                capability_type="defect_detection",
            )
            for s in self._scenes
        ]

    def lookup(self, scene: str):
        for m in self.list():
            if m.scene == scene:
                return m
        return None


class _FakeTaskService:
    """prepare/run_task 记录器：验证父子任务契约。"""

    def __init__(self):
        self.prepared: list[dict] = []
        self.run_scheduled: list[str] = []
        self._details: dict[str, dict] = {}
        self._counter = 0

    def seed_detail(self, task_id: str, detail: dict):
        self._details[task_id] = detail

    async def prepare(self, *, scene, mode, scenes, parameters, input_uris, auxiliary):
        self._counter += 1
        task_id = f"TCHILD{self._counter}"
        record = {
            "task_id": task_id, "scene": scene, "mode": mode, "scenes": scenes,
            "parameters": parameters, "input_uris": input_uris, "auxiliary": auxiliary,
        }
        self.prepared.append(record)
        return {"task_id": task_id, "state": {"task_id": task_id, "scene": scene, "mode": mode}}

    async def run_task(self, task_id: str, state: dict):
        self.run_scheduled.append(task_id)

    async def get(self, task_id: str):
        return self._details.get(task_id)

    # _run_child 崩溃兜底路径引用
    class _repo:  # noqa: N801 - 镜像属性名
        @staticmethod
        async def set_error(*_a, **_k):
            return None


def _ctx() -> AgentContext:
    return AgentContext(registry=_Registry(), settings=type("S", (), {"react_max_turns": 10})())


def _joint_state(**over: Any) -> dict[str, Any]:
    state: dict[str, Any] = {
        "task_id": "TPARENT",
        "mode": "joint",
        "scenes": ["fracture", "metallography"],
        "input_uris": ["/api/v1/tasks/TPARENT/files/a.png?kind=inputs"],
        "parameters": {},
        "children_launched": {},
        "capability_calls": 0,
    }
    state.update(over)
    return state


def _toolbox(svc: _FakeTaskService | None = None) -> JointToolbox:
    box = JointToolbox()
    box.bind(svc or _FakeTaskService())
    return box


# --------------------------------------------------------------------------
# 路由：联合分支键集
# --------------------------------------------------------------------------
def test_route_after_tools_joint_returns_mapping_keys():
    """联合路由必须返回映射键（含 await），不能返回节点名（线上踩坑回归）。"""
    assert route_after_tools_joint({"error": {"node": "x"}}) == "error"
    assert route_after_tools_joint({"submitted": True}) == "exit"
    assert route_after_tools_joint({"await_children_requested": True}) == "await"
    assert route_after_tools_joint({}) == "agent"
    # submitted 优先于 await（同一轮不应既提交又等待）
    assert route_after_tools_joint({"submitted": True, "await_children_requested": True}) == "exit"


def test_joint_graph_compiles_with_await_node():
    g = build_react_graph(_ctx(), joint=True, toolbox=_toolbox())
    assert "await_children" in g.get_graph().nodes


# --------------------------------------------------------------------------
# launch_child_task
# --------------------------------------------------------------------------
async def test_launch_child_requires_scene():
    box = _toolbox()
    content, side = await box.impls["launch_child_task"]({}, _joint_state(), _ctx())
    assert "scene" in content and side == {}


async def test_launch_child_rejects_scene_outside_candidates():
    box = _toolbox()
    content, side = await box.impls["launch_child_task"]({"scene": "fpi"}, _joint_state(), _ctx())
    assert "候选列表" in content and side == {}


async def test_launch_child_unknown_capability():
    box = _toolbox()
    state = _joint_state(scenes=["fracture", "nosuch"])
    content, _ = await box.impls["launch_child_task"]({"scene": "nosuch"}, state, _ctx())
    assert "无可用能力" in content


async def test_launch_child_happy_path_parent_link_and_params():
    svc = _FakeTaskService()
    box = _toolbox(svc)
    state = _joint_state(parameters={"k": 1})
    content, side = await box.impls["launch_child_task"](
        {"scene": "fracture", "note": "重点关注裂纹"}, state, _ctx()
    )
    payload = json.loads(content)
    child_id = payload["task_id"]
    assert side["children_launched"] == {"fracture": child_id}
    record = svc.prepared[0]
    # 子任务契约：agent 轨、单场景、父任务关联、图片继承、备注透传
    assert record["mode"] == "agent"
    assert record["scenes"] == ["fracture"]
    assert record["auxiliary"]["parent_task_id"] == "TPARENT"
    assert record["auxiliary"]["note"] == "重点关注裂纹"
    assert record["input_uris"] == state["input_uris"]
    assert record["parameters"] == {"k": 1, "note": "重点关注裂纹"}
    await asyncio.sleep(0)  # create_task 需要一个事件循环 tick 才起跑
    assert svc.run_scheduled == [child_id]


async def test_launch_child_idempotent_per_scene():
    svc = _FakeTaskService()
    box = _toolbox(svc)
    state = _joint_state()
    _, side1 = await box.impls["launch_child_task"]({"scene": "fracture"}, state, _ctx())
    state.update(side1)
    content2, side2 = await box.impls["launch_child_task"]({"scene": "fracture"}, state, _ctx())
    assert side2 == {}  # 不产生新的派发
    assert json.loads(content2)["task_id"] == state["children_launched"]["fracture"]
    assert len(svc.prepared) == 1


async def test_launch_child_cap_enforced():
    svc = _FakeTaskService()
    box = _toolbox(svc)
    # 候选 2 个场景 → 上限 4（含 2 个自救补派额度）
    state = _joint_state(children_launched={f"s{i}": f"T{i}" for i in range(4)})
    content, side = await box.impls["launch_child_task"]({"scene": "fracture"}, state, _ctx())
    assert "上限" in content and side == {}


# --------------------------------------------------------------------------
# get_child_status / await_children
# --------------------------------------------------------------------------
async def test_get_child_status_rejects_foreign_task():
    box = _toolbox()
    content, _ = await box.impls["get_child_status"](
        {"task_id": "TOTHER"}, _joint_state(children_launched={"fracture": "T1"}), _ctx()
    )
    assert "不是本任务派发" in content


async def test_get_child_status_returns_snapshot():
    svc = _FakeTaskService()
    svc.seed_detail("T1", {
        "scene": "fracture", "status": "completed",
        "grade": {"level": "pass", "summary": "未发现缺陷"},
        "standardized": [{"defect_type": "cleavage", "severity": "high"}],
    })
    box = _toolbox(svc)
    content, _ = await box.impls["get_child_status"](
        {"task_id": "T1"}, _joint_state(children_launched={"fracture": "T1"}), _ctx()
    )
    snap = json.loads(content)
    assert snap["status"] == "completed" and snap["grade_level"] == "pass"
    assert len(snap["defects"]) == 1


async def test_await_children_requires_dispatched_children():
    box = _toolbox()
    content, side = await box.impls["await_children"]({}, _joint_state(), _ctx())
    assert "尚未派发" in content and side == {}


async def test_await_children_sets_requested_flag():
    box = _toolbox()
    content, side = await box.impls["await_children"](
        {}, _joint_state(children_launched={"fracture": "T1"}), _ctx()
    )
    assert side == {"await_children_requested": True}
    assert "唤醒" in content


# --------------------------------------------------------------------------
# await_children 节点：interrupt 载荷与唤醒聚合
# --------------------------------------------------------------------------
async def test_await_children_node_rejects_without_children():
    out = await await_children_node({"task_id": "T"}, _ctx())
    assert out["error"]["code"] == int(BizCode.INVALID_REQUEST)


async def test_await_children_node_interrupt_payload(monkeypatch):
    from langgraph.errors import GraphInterrupt

    captured: dict[str, Any] = {}

    def fake_interrupt(value):
        captured["value"] = value
        raise GraphInterrupt("paused")  # @node 装饰器仅放行 GraphInterrupt

    monkeypatch.setattr("app.agents.react.nodes.interrupt", fake_interrupt)
    with pytest.raises(GraphInterrupt):
        await await_children_node(
            _joint_state(children_launched={"fracture": "T1", "metallography": "T2"}), _ctx()
        )
    assert captured["value"]["kind"] == "await_children"
    assert captured["value"]["children"] == {"fracture": "T1", "metallography": "T2"}


async def test_await_children_node_wake_aggregates(monkeypatch):
    resume_payload = {
        "children": [
            {"task_id": "T1", "scene": "fracture", "status": "completed",
             "grade_level": "fail", "grade_summary": "高风险",
             "defects": [{"defect_type": "cleavage", "severity": "high"}]},
            {"task_id": "T2", "scene": "metallography", "status": "completed",
             "grade_level": "pass", "grade_summary": "合格", "defects": []},
        ]
    }
    monkeypatch.setattr("app.agents.react.nodes.interrupt", lambda _v: resume_payload)
    ai = AIMessage(content="", tool_calls=[{"name": "await_children", "args": {}, "id": "call_aw"}])
    state = _joint_state(
        children_launched={"fracture": "T1", "metallography": "T2"},
        standardized=[{"defect_type": "preexisting"}],
        messages=[ai],
    )
    out = await await_children_node(state, _ctx())
    # 确定性聚合：原有条目 + 子任务缺陷（补来源场景）
    assert len(out["standardized"]) == 2
    assert out["standardized"][1]["scene"] == "fracture"
    assert out["children_settled"] == 2
    assert out["await_children_requested"] is False
    tool_msg = out["messages"][0]
    assert tool_msg.tool_call_id == "call_aw"
    body = json.loads(tool_msg.content)
    assert len(body["children"]) == 2


# --------------------------------------------------------------------------
# 出口完备性：联合父任务不直接调能力也允许提交
# --------------------------------------------------------------------------
async def test_exit_gate_accepts_joint_parent_without_direct_capability():
    state = _joint_state(
        capability_calls=0,
        children_settled=2,
        submitted=True,
        submit_payload={"refused": False, "defects": [], "summary": "联合结论", "analysis": ""},
    )
    out = await exit_gate_node(state, _ctx())
    assert "error" not in out or out.get("error") is None


async def test_exit_gate_still_rejects_empty_hand_in():
    state = _joint_state(capability_calls=0, children_settled=0,
                         submit_payload={"refused": False, "defects": []})
    out = await exit_gate_node(state, _ctx())
    assert out["error"]["code"] == int(BizCode.INTERNAL_ERROR)


async def test_submit_final_accepts_joint_parent_default_defects():
    # 父任务聚合缺陷为空（子任务全合格）但 children_settled>0：允许空缺陷提交
    state = _joint_state(capability_calls=0, children_settled=2, standardized=[])
    content, side = await _submit_final_impl({"summary": "全部合格"}, state, _ctx())
    assert side["submitted"] is True


# --------------------------------------------------------------------------
# task_service：中断分流与父图唤醒
# --------------------------------------------------------------------------
def test_is_child_wait_discriminates():
    assert _is_child_wait({"kind": "await_children", "children": {}}) is True
    assert _is_child_wait({"goal": "review", "grade": {}}) is False
    assert _is_child_wait(None) is False
    assert _is_child_wait("await_children") is False


class _Rec:
    def __init__(self, task_id, mode="joint", status="running", inputs=None):
        self.id = task_id
        self.mode = mode
        self.status = status
        self.inputs_json = json.dumps(inputs or {})
        self.trace_id = "tr"
        self.scene = None
        self.capability_used = None
        self.created_at = None
        self.updated_at = None
        self.finished_at = None


class _Repo:
    def __init__(self, records: dict[str, _Rec]):
        self._records = records

    async def get(self, task_id):
        return self._records.get(task_id)

    async def list_tasks(self, *, scene=None, status=None, limit=20):
        rows = [r for r in self._records.values() if status is None or r.status == status]
        return rows[:limit]


class _Snapshot:
    def __init__(self, interrupt_value):
        class _T:
            interrupts = [type("I", (), {"value": interrupt_value})()] if interrupt_value else []

        self.tasks = [_T()] if interrupt_value is not None else []


class _JointGraphStub:
    def __init__(self, interrupt_value):
        self._value = interrupt_value

    async def aget_state(self, _config):
        return _Snapshot(self._value)


def _bare_service(monkeypatch, records, interrupt_value, details=None):
    """绕过 __init__ 构造最小 TaskService，桩掉 get/_resume 观测唤醒行为。"""
    from app.services.task_service import TaskService

    svc = TaskService.__new__(TaskService)
    svc._repo = _Repo(records)
    svc._joint_graph = _JointGraphStub(interrupt_value)
    svc._parent_locks = {}
    svc.on_settled = None

    details = details or {}
    resumed: list[tuple[str, Any, str | None]] = []

    async def _get_stub(self, task_id):
        return details.get(task_id)

    async def _resume_stub(self, task_id, command, mode=None):
        resumed.append((task_id, command, mode))

    monkeypatch.setattr(TaskService, "get", _get_stub)
    monkeypatch.setattr(TaskService, "_resume", _resume_stub)
    return svc, resumed


_CHILD_INPUTS = {"auxiliary": {"parent_task_id": "TP"}}


async def test_maybe_resume_parent_noop_without_parent_link(monkeypatch):
    records = {"TC": _Rec("TC", mode="agent", status="completed", inputs={})}
    svc, resumed = _bare_service(monkeypatch, records, {"kind": "await_children"})
    await svc._maybe_resume_parent("TC")
    assert resumed == []


async def test_maybe_resume_parent_noop_when_parent_not_waiting(monkeypatch):
    records = {
        "TC": _Rec("TC", mode="agent", status="completed", inputs=_CHILD_INPUTS),
        "TP": _Rec("TP", mode="joint", status="running"),
    }
    # 检查点无 await_children 中断（例如父图已唤醒或尚未等待）
    svc, resumed = _bare_service(monkeypatch, records, None)
    await svc._maybe_resume_parent("TC")
    assert resumed == []


async def test_maybe_resume_parent_waits_for_all_siblings(monkeypatch):
    records = {
        "TC1": _Rec("TC1", mode="agent", status="completed", inputs=_CHILD_INPUTS),
        "TC2": _Rec("TC2", mode="agent", status="running", inputs=_CHILD_INPUTS),
        "TP": _Rec("TP", mode="joint", status="running"),
    }
    interrupt_value = {"kind": "await_children", "children": {"fracture": "TC1", "metal": "TC2"}}
    svc, resumed = _bare_service(monkeypatch, records, interrupt_value)
    await svc._maybe_resume_parent("TC1")
    assert resumed == []  # TC2 仍在跑：继续等待


async def test_maybe_resume_parent_resumes_when_all_settled(monkeypatch):
    records = {
        "TC1": _Rec("TC1", mode="agent", status="completed", inputs=_CHILD_INPUTS),
        "TC2": _Rec("TC2", mode="agent", status="awaiting_review", inputs=_CHILD_INPUTS),
        "TP": _Rec("TP", mode="joint", status="running"),
    }
    interrupt_value = {"kind": "await_children", "children": {"fracture": "TC1", "metal": "TC2"}}
    details = {
        "TC1": {"scene": "fracture", "status": "completed",
                "grade": {"level": "fail", "summary": "高风险"},
                "standardized": [{"defect_type": "cleavage", "severity": "high"}]},
        "TC2": {"scene": "metal", "status": "awaiting_review",
                "grade": {"level": "review", "summary": "待复核"}, "standardized": []},
    }
    svc, resumed = _bare_service(monkeypatch, records, interrupt_value, details)
    await svc._maybe_resume_parent("TC1")
    assert len(resumed) == 1
    task_id, command, mode = resumed[0]
    assert task_id == "TP" and mode == "joint"
    payload = command.arg if hasattr(command, "arg") else command.resume
    outcomes = payload["children"]
    assert [o["task_id"] for o in outcomes] == ["TC1", "TC2"]
    assert outcomes[0]["grade_level"] == "fail"
    assert outcomes[0]["defects"][0]["defect_type"] == "cleavage"
    # 待复核子任务也算落定（不阻塞父图，父智能体据此标注）
    assert outcomes[1]["status"] == "awaiting_review"


# ---------- reconcile_joint_parents：启动唤醒对账（优化轮 E5） ----------

async def test_reconcile_wakes_waiting_parent_with_settled_children(monkeypatch):
    """重启后父图仍停在 await_children 且子任务全落定 → 对账补发唤醒。"""
    records = {
        "TC1": _Rec("TC1", mode="agent", status="completed", inputs=_CHILD_INPUTS),
        "TC2": _Rec("TC2", mode="agent", status="failed", inputs=_CHILD_INPUTS),
        "TP": _Rec("TP", mode="joint", status="running"),
    }
    interrupt_value = {"kind": "await_children", "children": {"fracture": "TC1", "metal": "TC2"}}
    details = {
        "TC1": {"scene": "fracture", "status": "completed",
                "grade": {"level": "pass", "summary": "无缺陷"}, "standardized": []},
        "TC2": {"scene": "metal", "status": "failed", "grade": None, "standardized": []},
    }
    svc, resumed = _bare_service(monkeypatch, records, interrupt_value, details)
    await svc.reconcile_joint_parents()
    assert len(resumed) == 1
    assert resumed[0][0] == "TP" and resumed[0][2] == "joint"


async def test_reconcile_noop_when_parent_not_waiting(monkeypatch):
    records = {
        "TC1": _Rec("TC1", mode="agent", status="completed", inputs=_CHILD_INPUTS),
        "TP": _Rec("TP", mode="joint", status="running"),
    }
    svc, resumed = _bare_service(monkeypatch, records, None)  # 检查点无等待中断
    await svc.reconcile_joint_parents()
    assert resumed == []


async def test_reconcile_noop_without_running_joint(monkeypatch):
    records = {"TC1": _Rec("TC1", mode="agent", status="completed", inputs=_CHILD_INPUTS)}
    svc, resumed = _bare_service(monkeypatch, records, {"kind": "await_children"})
    await svc.reconcile_joint_parents()
    assert resumed == []


async def test_reconcile_isolates_single_parent_failure(monkeypatch):
    """一个父任务唤醒抛错不阻断其余对账。"""
    records = {
        "TC1": _Rec("TC1", mode="agent", status="completed", inputs={"auxiliary": {"parent_task_id": "TPA"}}),
        "TPA": _Rec("TPA", mode="joint", status="running"),
        "TC2": _Rec("TC2", mode="agent", status="completed", inputs={"auxiliary": {"parent_task_id": "TPB"}}),
        "TPB": _Rec("TPB", mode="joint", status="running"),
    }
    interrupt_value = {"kind": "await_children", "children": {"s": "TC1"}}
    svc, resumed = _bare_service(monkeypatch, records, interrupt_value,
                                 {"TC1": {"status": "completed", "grade": None, "standardized": []},
                                  "TC2": {"status": "completed", "grade": None, "standardized": []}})
    calls: list[str] = []
    orig = svc._resume_joint_if_ready

    async def flaky(parent_id):
        calls.append(parent_id)
        if parent_id == "TPA":
            raise RuntimeError("boom")
        await orig(parent_id)

    svc._resume_joint_if_ready = flaky
    await svc.reconcile_joint_parents()
    assert sorted(calls) == ["TPA", "TPB"]  # TPA 抛错后 TPB 仍被扫描
    assert [t for t, _, _ in resumed] == ["TPB"]
