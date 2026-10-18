"""任务仓储：任务状态机 + 中间结果快照（全程追溯的持久化底座）。"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import DateTime, Integer, String, Text, func, select, text, update
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import Mapped, mapped_column

from app.core.context import new_trace_id
from app.core.logger import get_logger
from app.models.task import (
    STATUS_AWAITING_REVIEW,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_PENDING,
    STATUS_RUNNING,
)
from app.repositories.db import Base, create_session_factory, ping

logger = get_logger("app.repositories.task_repo")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _dt6() -> DateTime:
    """时间列：MySQL 的 DATETIME 默认秒级，必须显式 (6) 才保留微秒。

    同秒写入的任务/消息靠 `(created_at, id)` 排序，而 id 是随机串——精度掉到秒级即退化为
    随机序。`scripts/sql/init.sql` 的对应列同为 DATETIME(6)，两侧必须一起改。
    """
    return DateTime(timezone=True).with_variant(mysql.DATETIME(fsp=6), "mysql")


def beijing_today_start_utc() -> datetime:
    """北京时区「今日 0 点」对应的 UTC 时刻（UTC+8 恒差无夏令时，先构造再转回 UTC）。"""
    beijing_now = datetime.now(timezone(timedelta(hours=8)))
    return beijing_now.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)


# 统计口径与 api/schemas/task.py 的 TaskStatus 六态保持一致（canceled 当前未产出，恒 0）
STATS_STATUSES = (
    STATUS_PENDING,
    STATUS_RUNNING,
    STATUS_AWAITING_REVIEW,
    STATUS_COMPLETED,
    STATUS_FAILED,
    "canceled",
)


class TaskRecord(Base):
    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    trace_id: Mapped[str | None] = mapped_column(String(64), default=new_trace_id)
    scene: Mapped[str | None] = mapped_column(String(32))
    mode: Mapped[str] = mapped_column(String(16), default="single")
    status: Mapped[str] = mapped_column(String(24), default=STATUS_PENDING, index=True)
    capability_used: Mapped[str | None] = mapped_column(String(64))
    inputs_json: Mapped[str | None] = mapped_column(Text)       # 输入引用（原图/CSV 落盘 uri）
    raw_results_json: Mapped[str | None] = mapped_column(Text)  # 算法原始外壳
    standardized_json: Mapped[str | None] = mapped_column(Text) # 标准化结果
    class_definitions_json: Mapped[str | None] = mapped_column(Text)  # 工具类别定义表（代码→中文名+真缺陷语义）
    knowledge_json: Mapped[str | None] = mapped_column(Text)    # 引用 + 解读
    grade_json: Mapped[str | None] = mapped_column(Text)        # 判级结论
    review_json: Mapped[str | None] = mapped_column(Text)       # 复核意见
    report_json: Mapped[str | None] = mapped_column(Text)       # 报告引用
    steps_json: Mapped[str] = mapped_column(Text, default="[]") # 节点追踪（审计）
    error_json: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(_dt6(), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        _dt6(), default=utcnow, onupdate=utcnow
    )
    finished_at: Mapped[datetime | None] = mapped_column(_dt6())
    node_index: Mapped[int] = mapped_column(Integer, default=0)


class TaskRepo:
    """短会话仓储：Graph 节点与路由均可直接调用，内部自管 session。"""

    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._sf = session_factory
        # 步骤事件旁路监听器（会话实时活动流入口，优化轮 D2）。
        # 由编排层（TaskService）注入；每次 append_step 落库后回调。
        self._step_listener: Any | None = None

    def set_step_listener(self, listener: Any | None) -> None:
        """注入/卸载步骤事件监听器；listener 签名 (task_id, node, message) -> Awaitable。"""
        self._step_listener = listener

    async def ping(self) -> bool:
        async with self._sf() as session:
            await session.execute(select(1))
            return True

    async def server_info(self) -> tuple[str, str | None]:
        """数据库选型 + 服务端版本（供 /health 监控展示）：失败不阻断，版本降级为 None。"""
        async with self._sf() as session:
            conn = await session.connection()
            product = conn.engine.dialect.name  # mysql / sqlite / postgresql
            try:
                ver = (await conn.execute(text("SELECT VERSION()"))).scalar()
            except Exception:  # noqa: BLE001 个别方言不支持 SELECT VERSION()
                await session.rollback()
                ver = None
            return product, (str(ver).strip()[:40] if ver else None)

    async def create(
        self,
        task_id: str,
        *,
        scene: str | None,
        mode: str,
        inputs: dict | None = None,
        trace_id: str | None = None,
    ) -> None:
        async with self._sf() as session:
            session.add(
                TaskRecord(
                    id=task_id,
                    trace_id=trace_id,
                    scene=scene,
                    mode=mode,
                    inputs_json=json.dumps(inputs or {}, ensure_ascii=False),
                )
            )
            await session.commit()

    async def get(self, task_id: str) -> TaskRecord | None:
        async with self._sf() as session:
            return await session.get(TaskRecord, task_id)

    async def get_by_ids(self, task_ids: list[str]) -> list[TaskRecord]:
        async with self._sf() as session:
            rows = await session.execute(select(TaskRecord).where(TaskRecord.id.in_(task_ids)))
            return list(rows.scalars())

    async def set_status(self, task_id: str, status: str) -> None:
        async with self._sf() as session:
            rec = await session.get(TaskRecord, task_id)
            if rec is None:
                raise LookupError(f"task {task_id} not found")
            rec.status = status
            if status in (STATUS_COMPLETED, STATUS_FAILED):
                rec.finished_at = utcnow()
            await session.commit()

    async def patch_json(self, task_id: str, **fields: Any) -> None:
        """以 JSON 快照逐字段覆盖任务中间结果（追溯用，幂等）。

        约定：形如 foo_json 的字段名 => 值自动 JSON 序列化存储。
        """
        async with self._sf() as session:
            rec = await session.get(TaskRecord, task_id)
            if rec is None:
                raise LookupError(f"task {task_id} not found")
            for key, value in fields.items():
                if value is None or not hasattr(rec, key):
                    continue
                if key.endswith("_json") and not isinstance(value, str):
                    value = json.dumps(value, ensure_ascii=False, default=str)
                setattr(rec, key, value)
            await session.commit()

    async def append_step(self, task_id: str, node: str, message: str) -> None:
        """追加节点执行痕迹（审计留痕的最小单元）。"""
        async with self._sf() as session:
            rec = await session.get(TaskRecord, task_id)
            if rec is None:
                raise LookupError(f"task {task_id} not found")
            steps = json.loads(rec.steps_json or "[]")
            rec.node_index += 1
            steps.append(
                {
                    "seq": rec.node_index,
                    "node": node,
                    "message": message,
                    "ts": utcnow().isoformat(),
                }
            )
            rec.steps_json = json.dumps(steps, ensure_ascii=False)
            await session.commit()
        listener = self._step_listener
        if listener is not None:
            try:
                await listener(task_id, node, message)
            except Exception:  # noqa: BLE001 - 事件旁路失败不阻断审计/任务流
                logger.exception("step listener failed task=%s node=%s", task_id, node)

    async def set_error(self, task_id: str, code: int, message: str) -> None:
        async with self._sf() as session:
            rec = await session.get(TaskRecord, task_id)
            if rec is None:
                raise LookupError(f"task {task_id} not found")
            rec.error_json = json.dumps({"code": code, "message": message}, ensure_ascii=False)
            rec.status = STATUS_FAILED
            rec.finished_at = utcnow()
            await session.commit()

    async def begin_retry(self, task_id: str) -> bool:
        """失败任务重试的原子重置：仅当 status=failed 时翻回 pending 并清空中间快照。

        一条条件 UPDATE 保证「守卫 + 重置」原子：并发双击只有一个成功（rowcount=1），
        避免两个并行的图执行踩坏同一 checkpointer thread。清空 finished_at/error 与各
        阶段快照，但保留 steps_json / node_index（跨尝试的追加式审计链）。
        """
        async with self._sf() as session:
            result = await session.execute(
                update(TaskRecord)
                .where(TaskRecord.id == task_id, TaskRecord.status == STATUS_FAILED)
                .values(
                    status=STATUS_PENDING,
                    finished_at=None,
                    error_json=None,
                    raw_results_json=None,
                    standardized_json=None,
                    class_definitions_json=None,
                    knowledge_json=None,
                    grade_json=None,
                    review_json=None,
                    report_json=None,
                    capability_used=None,
                    updated_at=utcnow(),
                )
            )
            await session.commit()
            return result.rowcount == 1

    async def claim_review(self, task_id: str) -> bool:
        """复核受理的原子认领：仅当 status=awaiting_review 时翻为 running。

        与 begin_retry 同构的条件 UPDATE：复核提交改为「即时受理 + 后台恢复」后，
        并发双击 / 前端重试只有一个请求能认领成功（rowcount=1），其余得到 409，
        保证同一 checkpointer thread 不会被两个并行恢复踩坏。
        """
        async with self._sf() as session:
            result = await session.execute(
                update(TaskRecord)
                .where(TaskRecord.id == task_id, TaskRecord.status == STATUS_AWAITING_REVIEW)
                .values(status=STATUS_RUNNING, updated_at=utcnow())
            )
            await session.commit()
            return result.rowcount == 1

    async def list_tasks(
        self, *, scene: str | None = None, status: str | None = None, limit: int = 20
    ) -> list[TaskRecord]:
        async with self._sf() as session:
            stmt = select(TaskRecord)
            if scene:
                stmt = stmt.where(TaskRecord.scene == scene)
            if status:
                stmt = stmt.where(TaskRecord.status == status)
            stmt = stmt.order_by(TaskRecord.created_at.desc(), TaskRecord.id.desc()).limit(limit)
            rows = await session.execute(stmt)
            return list(rows.scalars())

    async def list_reviewed(self, *, limit: int = 500) -> list[TaskRecord]:
        """带复核留痕的任务（复核数据闭环/评估矩阵的数据底座）。

        只取 review_json 非空的任务（含复核决定与复核门中断快照），
        按创建时间倒序；动作真伪由服务层按 review['action'] 精确过滤。
        """
        async with self._sf() as session:
            stmt = (
                select(TaskRecord)
                .where(TaskRecord.review_json.is_not(None))
                .order_by(TaskRecord.created_at.desc(), TaskRecord.id.desc())
                .limit(limit)
            )
            rows = await session.execute(stmt)
            return list(rows.scalars())

    async def list_stale_awaiting_review(self, *, before: Any, limit: int = 100) -> list[TaskRecord]:
        """超时滞留复核门任务（HITL 超时扫描数据源）。

        awaiting_review 且 updated_at 早于 before 的任务：复核已等待过久，
        需升级标记提醒（只标记绝不自动裁决——智能体异议/低置信度任务
        永不自动放行是业务红线）。
        """
        async with self._sf() as session:
            stmt = (
                select(TaskRecord)
                .where(TaskRecord.status == STATUS_AWAITING_REVIEW, TaskRecord.updated_at < before)
                .order_by(TaskRecord.updated_at.asc(), TaskRecord.id.asc())
                .limit(limit)
            )
            rows = await session.execute(stmt)
            return list(rows.scalars())

    async def find_children(self, parent_id: str) -> list[TaskRecord]:
        """联合父任务的子任务列表（优化轮 E2）。

        父子链存于子任务 inputs_json 的 auxiliary.parent_task_id。SQLite 无
        JSON 索引，先以 LIKE 粗筛、再解析 inputs_json 精确校验，避免误配。
        """
        if not parent_id:
            return []
        needle = f'"parent_task_id": "{parent_id}"'
        async with self._sf() as session:
            rows = await session.execute(
                select(TaskRecord)
                .where(TaskRecord.inputs_json.like(f"%{needle}%"))
                .order_by(TaskRecord.created_at.asc(), TaskRecord.id.asc())
            )
            out: list[TaskRecord] = []
            for rec in rows.scalars():
                try:
                    inputs = json.loads(rec.inputs_json) if rec.inputs_json else {}
                except (json.JSONDecodeError, TypeError):
                    continue
                if str((inputs.get("auxiliary") or {}).get("parent_task_id") or "") == parent_id:
                    out.append(rec)
            return out

    async def get_stats(self) -> dict[str, int]:
        """权威全量统计（全表聚合，不随 limit/分页截断）：

        - 各状态计数：GROUP BY status（无该状态的值为 0）
        - today：北京时间「今日 0 点」之后创建的任务数
        - total：任务总量（各状态计数之和）
        """
        async with self._sf() as session:
            rows = await session.execute(
                select(TaskRecord.status, func.count()).group_by(TaskRecord.status)
            )
            counts = {status: 0 for status in STATS_STATUSES}
            counts.update({status: count for status, count in rows})
            today_count = await session.scalar(
                select(func.count()).where(TaskRecord.created_at >= beijing_today_start_utc())
            )
            return {**counts, "today": today_count or 0, "total": sum(counts.values())}