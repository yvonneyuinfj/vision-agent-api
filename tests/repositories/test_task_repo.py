"""任务仓储测试：状态机流转 + JSON 快照追溯（建表与真库同为 MySQL，决策 50 的 R2）。"""
from __future__ import annotations

import json

import pytest

from app.repositories.task_repo import (
    STATUS_AWAITING_REVIEW,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_PENDING,
    STATUS_RUNNING,
    TaskRepo,
)


@pytest.mark.asyncio
async def test_create_and_get(session_factory):
    repo = TaskRepo(session_factory)
    await repo.create(
        "T001",
        scene="borescope",
        mode="single",
        inputs={"input_uris": ["/api/v1/x"], "scenes": ["borescope"]},
        trace_id="trace-1",
    )
    rec = await repo.get("T001")
    assert rec is not None
    assert rec.scene == "borescope"
    assert rec.status == STATUS_PENDING
    assert json.loads(rec.inputs_json)["scenes"] == ["borescope"]
    assert rec.trace_id == "trace-1"


@pytest.mark.asyncio
async def test_set_status_tracks_finish_time(session_factory):
    repo = TaskRepo(session_factory)
    await repo.create("T002", scene=None, mode="single")
    await repo.set_status("T002", STATUS_RUNNING)
    await repo.set_status("T002", STATUS_COMPLETED)

    rec = await repo.get("T002")
    assert rec.status == STATUS_COMPLETED
    assert rec.finished_at is not None  # 终态写入完成时间
    assert rec.updated_at is not None


@pytest.mark.asyncio
async def test_patch_json_serializes_dict_fields(session_factory):
    repo = TaskRepo(session_factory)
    await repo.create("T003", scene="borescope", mode="single")
    await repo.patch_json(
        "T003",
        grade_json={"level": "review", "summary": "需人工复核"},
        review_json={"opinion": "专家确认"},
        capability_used="borescope_v1",
    )
    rec = await repo.get("T003")
    assert json.loads(rec.grade_json)["level"] == "review"
    assert json.loads(rec.review_json)["opinion"] == "专家确认"
    assert rec.capability_used == "borescope_v1"


@pytest.mark.asyncio
async def test_append_step_trace(session_factory):
    repo = TaskRepo(session_factory)
    await repo.create("T004", scene="borescope", mode="single")
    await repo.append_step("T004", "iter", "开始推理")
    await repo.append_step("T004", "grade", "判级完成")

    rec = await repo.get("T004")
    steps = json.loads(rec.steps_json)
    assert len(steps) == 2
    assert steps[0]["node"] == "iter" and steps[0]["seq"] == 1
    assert steps[1]["node"] == "grade" and steps[1]["seq"] == 2


@pytest.mark.asyncio
async def test_set_error_marks_failed(session_factory):
    repo = TaskRepo(session_factory)
    await repo.create("T005", scene="borescope", mode="single")
    await repo.set_error("T005", 3002, "inference failed")

    rec = await repo.get("T005")
    assert rec.status == "failed"
    err = json.loads(rec.error_json)
    assert err["code"] == 3002


@pytest.mark.asyncio
async def test_begin_retry_resets_failed_task(session_factory):
    repo = TaskRepo(session_factory)
    await repo.create("T100", scene="borescope", mode="single")
    await repo.patch_json("T100", grade_json={"level": "fail"}, report_json={"url": "/r"})
    await repo.set_error("T100", 3002, "inference failed")  # failed + finished_at + error_json

    assert await repo.begin_retry("T100") is True

    rec = await repo.get("T100")
    assert rec.status == STATUS_PENDING
    assert rec.finished_at is None
    assert rec.error_json is None
    assert rec.grade_json is None
    assert rec.report_json is None
    # 审计链跨尝试保留：steps 不清空
    assert rec.steps_json is not None


@pytest.mark.asyncio
async def test_begin_retry_rejects_non_failed(session_factory):
    repo = TaskRepo(session_factory)
    for i, st in enumerate([STATUS_RUNNING, STATUS_COMPLETED, STATUS_AWAITING_REVIEW]):
        await repo.create(f"T11{i}", scene="borescope", mode="single")
        await repo.set_status(f"T11{i}", st)
        assert await repo.begin_retry(f"T11{i}") is False
        rec = await repo.get(f"T11{i}")
        assert rec.status == st  # 状态未被改动


@pytest.mark.asyncio
async def test_begin_retry_unknown_task(session_factory):
    repo = TaskRepo(session_factory)
    assert await repo.begin_retry("T404") is False


@pytest.mark.asyncio
async def test_get_by_ids_and_list_filter(session_factory):
    repo = TaskRepo(session_factory)
    for i, (sc, st) in enumerate([("borescope", STATUS_RUNNING), ("infrared", STATUS_COMPLETED)]):
        await repo.create(f"T00{i+6}", scene=sc, mode="single")
        await repo.set_status(f"T00{i+6}", st)

    by_ids = await repo.get_by_ids(["T006", "T007"])
    assert {r.id for r in by_ids} == {"T006", "T007"}

    listed = await repo.list_tasks(status=STATUS_COMPLETED)
    assert [r.id for r in listed] == ["T007"]
    listed2 = await repo.list_tasks(scene="borescope")
    assert [r.id for r in listed2] == ["T006"]

@pytest.mark.asyncio
async def test_get_stats_empty_db_all_zero(session_factory):
    stats = await TaskRepo(session_factory).get_stats()
    assert stats == {
        "total": 0, "today": 0,
        "pending": 0, "running": 0, "awaiting_review": 0,
        "completed": 0, "failed": 0, "canceled": 0,
    }


@pytest.mark.asyncio
async def test_get_stats_counts_statuses_and_today_boundary(session_factory):
    """各状态全量计数 + 北京「今日」边界：边界点含今，昨一秒不含今。"""
    from datetime import timedelta

    from sqlalchemy import update

    from app.repositories.task_repo import TaskRecord, beijing_today_start_utc

    repo = TaskRepo(session_factory)
    # 今日创建：2 pending → 1 转 running、1 转 completed、1 转 failed
    await repo.create("S1", scene=None, mode="single")
    await repo.create("S2", scene=None, mode="single")
    await repo.create("S3", scene=None, mode="single")
    await repo.create("S4", scene=None, mode="single")
    await repo.set_status("S2", STATUS_RUNNING)
    await repo.set_status("S3", STATUS_COMPLETED)
    await repo.set_status("S4", STATUS_FAILED)
    # 昨日创建（today_start 前 1 秒）：1 pending、1 failed
    await repo.create("S5", scene=None, mode="single")
    await repo.create("S6", scene=None, mode="single")
    await repo.set_status("S6", STATUS_FAILED)
    # 边界用例：恰等于 today_start 的任务也算今日；早 1 秒的不算
    async with session_factory() as session:
        await session.execute(
            update(TaskRecord).where(TaskRecord.id.in_(["S5", "S6"])).values(
                created_at=beijing_today_start_utc() - timedelta(seconds=1)
            )
        )
        await session.commit()

    stats = await repo.get_stats()
    assert stats == {
        "total": 6,
        "today": 4,  # S1-S4（S5/S6 在今日边界之前）
        "pending": 2,   # S1 + S5
        "running": 1,   # S2
        "awaiting_review": 0,
        "completed": 1,  # S3
        "failed": 2,    # S4 + S6
        "canceled": 0,
    }


@pytest.mark.asyncio
async def test_get_stats_today_start_includes_exact_boundary(session_factory):
    from app.repositories.task_repo import TaskRecord, beijing_today_start_utc

    repo = TaskRepo(session_factory)
    await repo.create("B1", scene=None, mode="single")
    async with session_factory() as session:
        from sqlalchemy import update

        await session.execute(
            update(TaskRecord).where(TaskRecord.id == "B1").values(
                created_at=beijing_today_start_utc()
            )
        )
        await session.commit()

    stats = await repo.get_stats()
    assert stats["today"] == 1  # 恰好 = 今日 0 点（≥ 语义）计入


# ---------- find_children：联合父子链（优化轮 E2） ----------

@pytest.mark.asyncio
async def test_find_children_returns_only_matching_parent(session_factory):
    repo = TaskRepo(session_factory)
    await repo.create("P1", scene=None, mode="joint")
    await repo.create("C1", scene="borescope", mode="single",
                      inputs={"auxiliary": {"parent_task_id": "P1"}})
    await repo.create("C2", scene="infrared", mode="single",
                      inputs={"auxiliary": {"parent_task_id": "P1"}})
    await repo.create("C3", scene="dr", mode="single",
                      inputs={"auxiliary": {"parent_task_id": "OTHER"}})
    await repo.create("FREE", scene="dr", mode="single")

    children = await repo.find_children("P1")
    assert [c.id for c in children] == ["C1", "C2"]


@pytest.mark.asyncio
async def test_find_children_like_prefilter_no_false_positive(session_factory):
    """LIKE 粗筛不得把 'P1' 误配到 'P10' 之类的近似 id。"""
    repo = TaskRepo(session_factory)
    await repo.create("P1", scene=None, mode="joint")
    await repo.create("P10", scene=None, mode="joint")
    await repo.create("CX", scene="borescope", mode="single",
                      inputs={"auxiliary": {"parent_task_id": "P10"}})

    assert await repo.find_children("P1") == []
    assert [c.id for c in await repo.find_children("P10")] == ["CX"]


@pytest.mark.asyncio
async def test_find_children_empty_parent_returns_empty(session_factory):
    repo = TaskRepo(session_factory)
    assert await repo.find_children("") == []


@pytest.mark.asyncio
async def test_claim_review_transitions_awaiting_to_running(session_factory):
    """复核受理原子认领：仅 awaiting_review 可翻为 running（并发双击只有一个成功）。"""
    repo = TaskRepo(session_factory)
    await repo.create("TR1", scene="borescope", mode="single")
    await repo.set_status("TR1", STATUS_AWAITING_REVIEW)

    assert await repo.claim_review("TR1") is True
    rec = await repo.get("TR1")
    assert rec.status == STATUS_RUNNING
    # 二次认领（双击/重试）失败，状态不变
    assert await repo.claim_review("TR1") is False
    assert (await repo.get("TR1")).status == STATUS_RUNNING


@pytest.mark.asyncio
async def test_claim_review_rejects_non_awaiting(session_factory):
    repo = TaskRepo(session_factory)
    for i, st in enumerate([STATUS_RUNNING, STATUS_COMPLETED, STATUS_PENDING, STATUS_FAILED]):
        await repo.create(f"TR2{i}", scene="borescope", mode="single")
        await repo.set_status(f"TR2{i}", st)
        assert await repo.claim_review(f"TR2{i}") is False
        assert (await repo.get(f"TR2{i}")).status == st
