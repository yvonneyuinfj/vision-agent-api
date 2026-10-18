"""能力注册中心仓储：DB 驱动的能力表读写（纯 DB 事实源）。

- 注册/更新：upsert 幂等 + version 自增（15s 轮询刷新的比对键）
- 查询：按 scene + status 路由（idx_scene_status），status 三态过滤
- 对应 scripts/sql/init.sql::capability_registry 表
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import DateTime, Integer, String, Text, func, select, update
from sqlalchemy.orm import Mapped, mapped_column

from app.repositories.db import Base, create_session_factory

STATUS_ENABLED = "enabled"
STATUS_DISABLED = "disabled"
STATUS_MAINTENANCE = "maintenance"
VALID_STATUS = (STATUS_ENABLED, STATUS_DISABLED, STATUS_MAINTENANCE)

# 决策 36：身份列创建后全锁——换身份＝新建行 + 停用旧行。
# 决策 39：编辑只走 update_fields()（列级 patch），绝不走全字段 upsert()，
# 否则未提交的列会被默认值静默洗掉（timeout_s 120→60、health_endpoint 清空…）。
IMMUTABLE_COLUMNS = frozenset(
    {"id", "capability_id", "tool_name", "version", "created_at", "updated_at"}
)
# 这三列在库里是 JSON 文本；patch 时允许直接传 dict/list（自动序列化）或 JSON 文本
_JSON_COLUMNS = frozenset({"input_schema", "output_schema", "meta_json"})


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class CapabilityRecord(Base):
    __tablename__ = "capability_registry"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    capability_id: Mapped[str] = mapped_column(String(128), unique=True)
    tool_name: Mapped[str] = mapped_column(String(128), index=True)
    scene: Mapped[str] = mapped_column(String(64))
    capability_type: Mapped[str] = mapped_column(String(32), default="defect_detection")
    adapter_type: Mapped[str] = mapped_column(String(32), default="http")
    endpoint: Mapped[str] = mapped_column(String(512))
    health_endpoint: Mapped[str | None] = mapped_column(String(512))
    tool_version: Mapped[str] = mapped_column(String(64))
    model_version: Mapped[str] = mapped_column(String(64))
    api_version: Mapped[str] = mapped_column(String(32), default="v1")
    input_schema: Mapped[str | None] = mapped_column(Text)   # JSON Schema（JSON 文本）
    output_schema: Mapped[str | None] = mapped_column(Text)  # JSON Schema（JSON 文本）
    timeout_s: Mapped[int] = mapped_column(Integer, default=60)
    max_retry: Mapped[int] = mapped_column(Integer, default=2)
    status: Mapped[str] = mapped_column(String(24), default=STATUS_ENABLED)
    description: Mapped[str | None] = mapped_column(Text)
    # 规范 §9：模型权重 SHA256（准入校验与申报一致性比对；模型文件到位后回填）
    model_weights_sha256: Mapped[str | None] = mapped_column(String(64))
    meta_json: Mapped[str | None] = mapped_column(Text)  # default_params/input_required/max_images 等
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class CapabilityRepo:
    """短会话仓储：能力注册中心读写，内部自管 session。"""

    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._sf = session_factory

    async def max_version(self) -> int:
        """返回当前最大 version（轮询刷新比对键，空表为 0）。"""
        async with self._sf() as session:
            row = await session.execute(select(func.max(CapabilityRecord.version)))
            return row.scalar() or 0

    async def get(self, capability_id: str) -> CapabilityRecord | None:
        async with self._sf() as session:
            stmt = select(CapabilityRecord).where(
                CapabilityRecord.capability_id == capability_id
            )
            return (await session.execute(stmt)).scalar_one_or_none()

    async def _get_or_none(self, session, capability_id: str) -> CapabilityRecord | None:
        stmt = select(CapabilityRecord).where(
            CapabilityRecord.capability_id == capability_id
        )
        return (await session.execute(stmt)).scalar_one_or_none()

    async def _max_version(self, session) -> int:
        """会话内全局最大 version（空表为 0），供插入分支推高水位。"""
        row = await session.execute(select(func.max(CapabilityRecord.version)))
        return row.scalar() or 0

    async def get_by_tool_name(self, tool_name: str) -> CapabilityRecord | None:
        async with self._sf() as session:
            stmt = select(CapabilityRecord).where(CapabilityRecord.tool_name == tool_name)
            return (await session.execute(stmt)).scalar_one_or_none()

    async def list_all(self) -> list[CapabilityRecord]:
        async with self._sf() as session:
            rows = await session.execute(
                select(CapabilityRecord).order_by(CapabilityRecord.id)
            )
            return list(rows.scalars())

    async def list_scene_enabled(self, scene: str) -> list[CapabilityRecord]:
        """场景路由：命中 idx_scene_status 只取 enabled 能力。"""
        async with self._sf() as session:
            stmt = (
                select(CapabilityRecord)
                .where(
                    CapabilityRecord.scene == scene,
                    CapabilityRecord.status == STATUS_ENABLED,
                )
                .order_by(CapabilityRecord.id)
            )
            rows = await session.execute(stmt)
            return list(rows.scalars())

    async def upsert(
        self,
        *,
        capability_id: str,
        tool_name: str,
        scene: str,
        capability_type: str = "defect_detection",
        adapter_type: str = "http",
        endpoint: str,
        health_endpoint: str | None = None,
        tool_version: str,
        model_version: str,
        api_version: str = "v1",
        input_schema: dict | None = None,
        output_schema: dict | None = None,
        timeout_s: int = 60,
        max_retry: int = 2,
        status: str = STATUS_ENABLED,
        description: str | None = None,
        model_weights_sha256: str | None = None,
        meta: dict | None = None,
    ) -> bool:
        """幂等注册/更新。返回 True 表示本次变更了 version（外部据此次数轮询）。

        已存在时更新可改名脚本字段并 version+1；不存在时插入
        （version=当前最大 version+1，保证轮询水位单调上升）。
        """
        if status not in VALID_STATUS:
            raise ValueError(f"invalid status: {status}")

        async with self._sf() as session:
            rec = await self._get_or_none(session, capability_id)
            if rec is None:
                # version 必须单调推高全局水位：否则 15s 轮询（MAX(version) 比对）
                # 在已有更高 version 行时永远感知不到新插入的能力
                session.add(
                    CapabilityRecord(
                        capability_id=capability_id,
                        tool_name=tool_name,
                        scene=scene,
                        capability_type=capability_type,
                        adapter_type=adapter_type,
                        endpoint=endpoint,
                        health_endpoint=health_endpoint,
                        tool_version=tool_version,
                        model_version=model_version,
                        api_version=api_version,
                        input_schema=_dumps(input_schema),
                        output_schema=_dumps(output_schema),
                        timeout_s=timeout_s,
                        max_retry=max_retry,
                        status=status,
                        description=description,
                        model_weights_sha256=model_weights_sha256,
                        meta_json=_dumps(meta),
                        version=(await self._max_version(session)) + 1,
                    )
                )
                await session.commit()
                return True

            rec.tool_name = tool_name
            rec.scene = scene
            rec.capability_type = capability_type
            rec.adapter_type = adapter_type
            rec.endpoint = endpoint
            rec.health_endpoint = health_endpoint
            rec.tool_version = tool_version
            rec.model_version = model_version
            rec.api_version = api_version
            rec.input_schema = _dumps(input_schema)
            rec.output_schema = _dumps(output_schema)
            rec.timeout_s = timeout_s
            rec.max_retry = max_retry
            rec.status = status
            rec.description = description
            rec.model_weights_sha256 = model_weights_sha256
            rec.meta_json = _dumps(meta)
            rec.version += 1  # 自增：轮询刷新比对键
            await session.commit()
            return True

    async def insert(
        self,
        *,
        capability_id: str,
        tool_name: str,
        scene: str,
        endpoint: str,
        tool_version: str,
        model_version: str,
        capability_type: str = "defect_detection",
        adapter_type: str = "http",
        health_endpoint: str | None = None,
        api_version: str = "v1",
        input_schema: dict | None = None,
        output_schema: dict | None = None,
        timeout_s: int = 60,
        max_retry: int = 2,
        status: str = STATUS_ENABLED,
        description: str | None = None,
        model_weights_sha256: str | None = None,
        meta: dict | None = None,
    ) -> bool:
        """登记新能力行（决策 42：登记与更新是两条路径，已存在即拒绝，不做隐式 upsert）。

        返回 False 表示 capability_id 已存在。version 取全局最大值 +1，
        保证 15s 轮询水位单调上升（同 upsert 插入分支）。
        """
        if status not in VALID_STATUS:
            raise ValueError(f"invalid status: {status}")
        async with self._sf() as session:
            if await self._get_or_none(session, capability_id) is not None:
                return False
            session.add(
                CapabilityRecord(
                    capability_id=capability_id,
                    tool_name=tool_name,
                    scene=scene,
                    capability_type=capability_type,
                    adapter_type=adapter_type,
                    endpoint=endpoint,
                    health_endpoint=health_endpoint,
                    tool_version=tool_version,
                    model_version=model_version,
                    api_version=api_version,
                    input_schema=_dumps(input_schema),
                    output_schema=_dumps(output_schema),
                    timeout_s=timeout_s,
                    max_retry=max_retry,
                    status=status,
                    description=description,
                    model_weights_sha256=model_weights_sha256,
                    meta_json=_dumps(meta),
                    version=(await self._max_version(session)) + 1,
                )
            )
            await session.commit()
            return True

    async def update_fields(
        self,
        capability_id: str,
        fields: dict[str, Any],
        *,
        expected_version: int | None = None,
    ) -> bool:
        """列级 patch（决策 39）：键未出现＝保留原值，出现＝写新值，值为 None＝清空该列。

        version 自增触发轮询；expected_version 非空时进 WHERE 做乐观锁（决策 42），
        rowcount==0 即冲突或行不存在，由调用方区分。身份列（IMMUTABLE_COLUMNS）拒绝写入。
        """
        illegal = IMMUTABLE_COLUMNS.intersection(fields)
        if illegal:
            raise ValueError(f"immutable columns: {sorted(illegal)}")
        if not fields:
            return False
        values = {
            k: (_dumps(v) if k in _JSON_COLUMNS and isinstance(v, (dict, list)) else v)
            for k, v in fields.items()
        }
        async with self._sf() as session:
            stmt = update(CapabilityRecord).where(
                CapabilityRecord.capability_id == capability_id
            )
            if expected_version is not None:
                stmt = stmt.where(CapabilityRecord.version == expected_version)
            result = await session.execute(
                stmt.values(**values, version=CapabilityRecord.version + 1)
            )
            await session.commit()
            return result.rowcount > 0

    async def set_status(self, capability_id: str, status: str) -> bool:
        """启停/维护切换：status 三态控制 + version 自增（触发缓存刷新）。"""
        if status not in VALID_STATUS:
            raise ValueError(f"invalid status: {status}")
        async with self._sf() as session:
            stmt = (
                update(CapabilityRecord)
                .where(CapabilityRecord.capability_id == capability_id)
                .values(status=status, version=CapabilityRecord.version + 1)
            )
            result = await session.execute(stmt)
            await session.commit()
            return result.rowcount > 0


def _dumps(value: dict | None) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False, default=str)