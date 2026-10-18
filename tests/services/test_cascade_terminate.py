"""多 Agent 级联终止测试：父任务终态后孤儿子任务收尾（不落僵尸 running 行）。"""
from __future__ import annotations

import json

import pytest
import pytest_asyncio

from app.core.config import Settings
from app.repositories.task_repo import (
    STATUS_AWAITING_REVIEW,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_PENDING,
    STATUS_RUNNING,
    TaskRepo,
)
from app.services.task_service import TaskService
from app.services.deps import AgentContext


@pytest_asyncio.fixture
async def svc(session_factory):
    repo = TaskRepo(session_factory)
    ctx = AgentContext(task_repo=repo, settings=Settings(_env_file=None))
    service = TaskService(ctx, repo, checkpointer=None)
    return service, repo


async def _make_child(repo: TaskRepo, parent_id: str, child_id: str, status: str) -> None:
    """子任务：inputs_json.auxiliary.parent_task_id 挂父子链（find_children 契约）。"""
    await repo.create(
        child_id,
        scene="borescope",
        mode="agent",
        inputs={"input_uris": ["/api/v1/x"], "auxiliary": {"parent_task_id": parent_id}},
    )
    if status != STATUS_PENDING:
        await repo.set_status(child_id, status)


@pytest.mark.asyncio
async def test_parent_failed_terminates_running_and_pending_children(svc):
    """父任务失败：running/pending 子任务级联 failed + 明确原因 + 审计行。"""
    service, repo = svc
    await repo.create("T-PARENT-1", scene=None, mode="joint")
    await _make_child(repo, "T-PARENT-1", "T-CHILD-RUN", STATUS_RUNNING)
    await _make_child(repo, "T-PARENT-1", "T-CHILD-PEND", STATUS_PENDING)
    await _make_child(repo, "T-PARENT-1", "T-CHILD-DONE", STATUS_COMPLETED)

    await service._cascade_orphan_children("T-PARENT-1", parent_terminal="failed")

    for child_id in ("T-CHILD-RUN", "T-CHILD-PEND"):
        rec = await repo.get(child_id)
        assert rec.status == STATUS_FAILED
        err = json.loads(rec.error_json)
        assert "父任务 T-PARENT-1 已终态" in err["message"]
        assert "级联终止" in err["message"]
        steps = json.loads(rec.steps_json)
        assert any(s.get("node") == "cascade_terminate" for s in steps)
    # 已落定子任务不动
    assert (await repo.get("T-CHILD-DONE")).status == STATUS_COMPLETED


@pytest.mark.asyncio
async def test_awaiting_review_children_are_not_orphans(svc):
    """awaiting_review 子任务不算孤儿：结论仍可追溯（人工复核照常进行）。"""
    service, repo = svc
    await repo.create("T-PARENT-2", scene=None, mode="joint")
    await _make_child(repo, "T-PARENT-2", "T-CHILD-AWR", STATUS_AWAITING_REVIEW)

    await service._cascade_orphan_children("T-PARENT-2", parent_terminal="failed")

    assert (await repo.get("T-CHILD-AWR")).status == STATUS_AWAITING_REVIEW


@pytest.mark.asyncio
async def test_cascade_no_children_is_noop(svc):
    """非联合任务（无子任务）：级联扫描零副作用。"""
    service, repo = svc
    await repo.create("T-SOLO", scene="borescope", mode="single")

    await service._cascade_orphan_children("T-SOLO", parent_terminal="terminal")

    assert (await repo.get("T-SOLO")).status == STATUS_PENDING  # 未被动过


@pytest.mark.asyncio
async def test_fail_routes_through_cascade(svc):
    """_fail 统一入口：失败终态自动触发孤儿清理。"""
    service, repo = svc
    await repo.create("T-PARENT-3", scene=None, mode="joint")
    await _make_child(repo, "T-PARENT-3", "T-CHILD-X", STATUS_RUNNING)

    await service._fail("T-PARENT-3", {"code": 4001, "message": "parent graph error"})

    assert (await repo.get("T-PARENT-3")).status == STATUS_FAILED
    assert (await repo.get("T-CHILD-X")).status == STATUS_FAILED
    err = json.loads((await repo.get("T-CHILD-X")).error_json)
    assert "级联终止" in err["message"]
