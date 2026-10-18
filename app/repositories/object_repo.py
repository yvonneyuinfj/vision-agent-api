"""对象存储文件元数据仓储：内容寻址去重的地基（Hash -> URI 映射）。

表结构权威定义在 scripts/sql/init.sql（生产 MySQL）；此处 ORM 仅承担数据访问，
不含任何建表 DDL（严禁代码硬编码建表语句）。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import BigInteger, DateTime, Integer, String, Text, select
from sqlalchemy.orm import Mapped, mapped_column

from app.repositories.db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class FileObjectRecord(Base):
    __tablename__ = "file_objects"

    sha256: Mapped[str] = mapped_column(String(64), primary_key=True)  # 内容寻址键（文件 SHA-256）
    bucket: Mapped[str] = mapped_column(String(64), default="qc-media-assets")
    object_name: Mapped[str] = mapped_column(String(255))  # {sha256}.{ext}
    uri: Mapped[str] = mapped_column(String(512))          # minio://bucket/object_name（对外引用）
    ext: Mapped[str | None] = mapped_column(String(16))    # 扩展名（不含点）
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    content_type: Mapped[str | None] = mapped_column(String(64))
    first_task_id: Mapped[str | None] = mapped_column(String(64))  # 首次入库任务
    last_task_id: Mapped[str | None] = mapped_column(String(64))   # 最近引用任务
    ref_count: Mapped[int] = mapped_column(Integer, default=1)     # 引用计数（秒传统计）
    meta_json: Mapped[str | None] = mapped_column(Text)            # 扩展元数据（来源等）
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class ObjectRepo:
    """内容寻址查询：先查 Hash 是否已存在，命中即秒传复用。"""

    def __init__(self, session_factory) -> None:  # noqa: ANN001
        self._sf = session_factory

    async def get_by_sha256(self, sha256: str) -> FileObjectRecord | None:
        async with self._sf() as session:
            return await session.get(FileObjectRecord, sha256)

    async def exists(self, sha256: str) -> bool:
        async with self._sf() as session:
            row = await session.execute(
                select(FileObjectRecord.sha256).where(FileObjectRecord.sha256 == sha256)
            )
            return row.first() is not None

    async def upsert(
        self,
        *,
        sha256: str,
        bucket: str,
        object_name: str,
        uri: str,
        ext: str | None,
        size_bytes: int | None,
        content_type: str | None,
        task_id: str | None,
        meta: dict | None = None,
    ) -> bool:
        """写一条 Hash 记录，返回是否新建（False=已存在仅刷新引用）。"""
        async with self._sf() as session:
            rec = await session.get(FileObjectRecord, sha256)
            if rec is not None:
                # 秒传命中：仅刷新引用统计，不重复入库
                rec.ref_count += 1
                rec.last_task_id = task_id
                await session.commit()
                return False
            session.add(
                FileObjectRecord(
                    sha256=sha256,
                    bucket=bucket,
                    object_name=object_name,
                    uri=uri,
                    ext=ext,
                    size_bytes=size_bytes,
                    content_type=content_type,
                    first_task_id=task_id,
                    last_task_id=task_id,
                    ref_count=1,
                    meta_json=json.dumps(meta or {}, ensure_ascii=False),
                )
            )
            await session.commit()
            return True

    async def bump_ref(self, sha256: str, task_id: str | None) -> None:
        """事务性引用计数自增（路由二次上传时使用）。"""
        async with self._sf() as session:
            rec = await session.get(FileObjectRecord, sha256)
            if rec is not None:
                rec.ref_count += 1
                rec.last_task_id = task_id or rec.last_task_id
                await session.commit()