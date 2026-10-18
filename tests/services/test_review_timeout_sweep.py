"""HITL 复核超时扫描测试：升级标记 + 幂等 + 永不自动裁决。"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio

from app.core.config import Settings
from app.repositories.task_repo import STATUS_AWAITING_REVIEW, TaskRecord, TaskRepo
from app.services.task_service import TaskService
from app.services.deps import AgentContext


@pytest_asyncio.fixture
async def svc(session_factory):
    repo = TaskRepo(session_factory)
    ctx = AgentContext(task_repo=repo, settings=Settings(_env_file=None))
    service = TaskService(ctx, repo, checkpointer=None)
    return service, repo


async def _make_awaiting(repo: TaskRepo, task_id: str, *, hours_old: float = 100.0) -> None:
    await repo.create(task_id, scene="borescope", mode="agent")
    await repo.set_status(task_id, STATUS_AWAITING_REVIEW)
    # 回拨 updated_at 模拟滞留时长。必须与 utcnow() 同约定写 UTC 墙钟：MySQL 的 DATETIME
    # 不存时区，写成局部时间会让"36 秒滞留"落到 8 小时后，未超时的反向用例便一直白过。
    stale = datetime.now(timezone.utc) - timedelta(hours=hours_old)
    async with repo._sf() as session:
        rec = await session.get(TaskRecord, task_id)
        rec.updated_at = stale
        await session.commit()


@pytest.mark.asyncio
async def test_sweep_marks_stale_review_with_audit_step(svc):
    """滞留任务被标记：审计行 review_timeout 落库，状态不变（仍 awaiting_review）。"""
    service, repo = svc
    await _make_awaiting(repo, "T-STALE-1", hours_old=100)

    stale = await service.sweep_stale_reviews(timeout_s=3600)  # 1 小时阈值

    assert [s["task_id"] for s in stale] == ["T-STALE-1"]
    rec = await repo.get("T-STALE-1")
    assert rec.status == STATUS_AWAITING_REVIEW  # 状态绝不变更
    steps = json.loads(rec.steps_json)
    assert any(s.get("node") == "review_timeout" and "升级提醒" in s.get("message", "") for s in steps)
    assert "不自动裁决" in [s.get("message", "") for s in steps if s.get("node") == "review_timeout"][0]


@pytest.mark.asyncio
async def test_sweep_is_idempotent(svc):
    """重复扫描不重复追加升级行（幂等）。"""
    service, repo = svc
    await _make_awaiting(repo, "T-STALE-2", hours_old=100)

    await service.sweep_stale_reviews(timeout_s=3600)
    await service.sweep_stale_reviews(timeout_s=3600)

    rec = await repo.get("T-STALE-2")
    steps = json.loads(rec.steps_json)
    timeout_steps = [s for s in steps if s.get("node") == "review_timeout"]
    assert len(timeout_steps) == 1


@pytest.mark.asyncio
async def test_sweep_skips_fresh_and_ignores_zero_timeout(svc):
    """未超时的任务不标记；timeout_s=0（关闭）直接返回空。"""
    service, repo = svc
    await _make_awaiting(repo, "T-FRESH", hours_old=0.01)

    # 1 小时阈值下 36 秒滞留不触发
    assert await service.sweep_stale_reviews(timeout_s=3600) == []
    rec = await repo.get("T-FRESH")
    assert not any(s.get("node") == "review_timeout" for s in json.loads(rec.steps_json))

    # timeout_s=0：功能关闭
    assert await service.sweep_stale_reviews(timeout_s=0) == []


@pytest.mark.asyncio
async def test_sweep_defaults_to_settings_review_timeout(svc):
    """缺省 timeout_s 从 settings.review_timeout_s 取值。"""
    service, repo = svc
    await _make_awaiting(repo, "T-DEFAULT", hours_old=100)
    service._ctx.settings.review_timeout_s = 3600

    stale = await service.sweep_stale_reviews()

    assert [s["task_id"] for s in stale] == ["T-DEFAULT"]
