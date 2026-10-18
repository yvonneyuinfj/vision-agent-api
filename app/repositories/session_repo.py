"""会话仓储：会话 + 消息流持久化（短会话模式，内部自管 session）。"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import DateTime, String, Text, delete, func, select, update
from sqlalchemy.dialects import mysql
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.orm import Mapped, mapped_column

from app.models.session import KIND_ACTIVITY, KIND_CHAIN, KIND_REVIEW_CARD, KIND_ROUTING, KIND_TASK_CARD, ROLE_USER, SESSION_ACTIVE
from app.repositories.db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _dt6() -> DateTime:
    """时间列：MySQL 的 DATETIME 默认秒级，必须显式 (6) 才保留微秒——同秒上传的多条消息
    靠 `(created_at, id)` 排序，而 id 是随机串，精度掉到秒级即退化为随机序（前端轨迹组碎裂）。
    `scripts/sql/init.sql` 的对应列同为 DATETIME(6)，两侧必须一起改。"""
    return DateTime(timezone=True).with_variant(mysql.DATETIME(fsp=6), "mysql")


def new_message_id() -> str:
    return f"M{uuid.uuid4().hex[:18]}"


class SessionRecord(Base):
    __tablename__ = "agent_sessions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    title: Mapped[str | None] = mapped_column(String(256))
    status: Mapped[str] = mapped_column(String(16), default=SESSION_ACTIVE)
    created_at: Mapped[datetime] = mapped_column(_dt6(), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        _dt6(), default=utcnow, onupdate=utcnow
    )


class SessionMessageRecord(Base):
    __tablename__ = "session_messages"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(64), index=True)
    role: Mapped[str] = mapped_column(String(16))
    kind: Mapped[str] = mapped_column(String(24), default="text")
    content: Mapped[str] = mapped_column(Text, default="")
    images_json: Mapped[str | None] = mapped_column(Text)
    task_id: Mapped[str | None] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(_dt6(), default=utcnow)


class SessionRepo:
    """会话/消息 CRUD；task_id → 会话定位（任务落定事件回注入口）。

    消息旁路监听（流式协议）：消息新增/原地更新提交成功后触发
    ``_message_listener(session_id)``——SSE 事件总线的唤醒信号源。
    监听器异常不得影响落库主流程（与 TaskRepo 步骤旁路同语义）。
    """

    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._sf = session_factory
        self._message_listener: Any = None

    def set_message_listener(self, listener: Any) -> None:
        """晚绑定注入唤醒回调 (session_id) -> None（容器装配期挂事件总线）。"""
        self._message_listener = listener

    def _fire_message_listener(self, session_id: str) -> None:
        listener = self._message_listener
        if listener is None:
            return
        try:
            listener(session_id)
        except Exception:  # noqa: BLE001 - 旁路唤醒失败不阻断落库（轮询兜底）
            pass

    # ---------- 会话 ----------
    async def create(self, session_id: str, title: str | None) -> None:
        async with self._sf() as session:
            session.add(SessionRecord(id=session_id, title=title))
            await session.commit()

    async def get(self, session_id: str) -> SessionRecord | None:
        async with self._sf() as session:
            return await session.get(SessionRecord, session_id)

    async def list_recent(self, limit: int = 50) -> list[tuple[SessionRecord, str | None]]:
        """最近活跃会话 + 最后一条消息文本（列表页摘要）。"""
        async with self._sf() as session:
            rows = await session.execute(
                select(SessionRecord)
                .where(SessionRecord.status == SESSION_ACTIVE)
                .order_by(SessionRecord.updated_at.desc())
                .limit(limit)
            )
            sessions = list(rows.scalars())
            out: list[tuple[SessionRecord, str | None]] = []
            for s in sessions:
                last = await session.execute(
                    select(SessionMessageRecord.content)
                    .where(
                        SessionMessageRecord.session_id == s.id,
                        # 活动流/思考链消息 content 是结构化 JSON，不适合做列表预览
                        SessionMessageRecord.kind.not_in([KIND_ACTIVITY, KIND_CHAIN, KIND_ROUTING]),
                    )
                    .order_by(SessionMessageRecord.created_at.desc(), SessionMessageRecord.id.desc())
                    .limit(1)
                )
                out.append((s, last.scalar_one_or_none()))
            return out

    async def rename(self, session_id: str, title: str) -> None:
        async with self._sf() as session:
            await session.execute(
                update(SessionRecord)
                .where(SessionRecord.id == session_id)
                .values(title=title, updated_at=utcnow())
            )
            await session.commit()

    async def touch(self, session_id: str) -> None:
        async with self._sf() as session:
            await session.execute(
                update(SessionRecord)
                .where(SessionRecord.id == session_id)
                .values(updated_at=utcnow())
            )
            await session.commit()

    async def delete(self, session_id: str) -> None:
        """硬删除会话及其全部消息（会话轨无审计复用诉求）。"""
        async with self._sf() as session:
            await session.execute(
                delete(SessionMessageRecord).where(SessionMessageRecord.session_id == session_id)
            )
            await session.execute(delete(SessionRecord).where(SessionRecord.id == session_id))
            await session.commit()

    # ---------- 消息 ----------
    async def add_message(
        self,
        session_id: str,
        *,
        role: str,
        kind: str = "text",
        content: str = "",
        images: list[dict] | None = None,
        task_id: str | None = None,
        message_id: str | None = None,
    ) -> str:
        mid = message_id or new_message_id()
        async with self._sf() as session:
            session.add(
                SessionMessageRecord(
                    id=mid,
                    session_id=session_id,
                    role=role,
                    kind=kind,
                    content=content,
                    images_json=json.dumps(images, ensure_ascii=False) if images else None,
                    task_id=task_id,
                )
            )
            await session.execute(
                update(SessionRecord)
                .where(SessionRecord.id == session_id)
                .values(updated_at=utcnow())
            )
            await session.commit()
        self._fire_message_listener(session_id)
        return mid

    async def update_message(
        self, session_id: str, message_id: str, content: str, *, task_id: str | None = None,
    ) -> bool:
        """原地更新既有消息内容（共识轮：思考链工具行的结果回写）。

        task_id 非 None 时一并回填归属列（派发类调用落行时任务尚未创建，
        执行完才已知任务 id）。返回是否命中行；未命中由调用方降级为新增。
        """
        values: dict[str, Any] = {"content": content}
        if task_id is not None:
            values["task_id"] = task_id
        async with self._sf() as session:
            res = await session.execute(
                update(SessionMessageRecord)
                .where(
                    SessionMessageRecord.id == message_id,
                    SessionMessageRecord.session_id == session_id,
                )
                .values(**values)
            )
            await session.commit()
            hit = bool(res.rowcount)
        if hit:
            self._fire_message_listener(session_id)
        return hit

    async def list_messages(self, session_id: str) -> list[SessionMessageRecord]:
        async with self._sf() as session:
            rows = await session.execute(
                select(SessionMessageRecord)
                .where(SessionMessageRecord.session_id == session_id)
                .order_by(SessionMessageRecord.created_at.asc(), SessionMessageRecord.id.asc())
            )
            return list(rows.scalars())

    async def find_session_by_task(self, task_id: str) -> str | None:
        """任务落定事件回注：按任务卡片消息定位所属会话。"""
        async with self._sf() as session:
            row = await session.execute(
                select(SessionMessageRecord.session_id)
                .where(SessionMessageRecord.task_id == task_id)
                .order_by(SessionMessageRecord.created_at.asc(), SessionMessageRecord.id.asc())
                .limit(1)
            )
            return row.scalar_one_or_none()

    async def has_review_card(self, session_id: str, task_id: str) -> bool:
        """启动补卡对账的幂等判定：该任务的终裁复核卡是否已在本会话落库。"""
        async with self._sf() as session:
            row = await session.execute(
                select(SessionMessageRecord.id)
                .where(
                    SessionMessageRecord.session_id == session_id,
                    SessionMessageRecord.task_id == task_id,
                    SessionMessageRecord.kind == KIND_REVIEW_CARD,
                )
                .limit(1)
            )
            return row.scalar_one_or_none() is not None

    async def latest_user_images(self, session_id: str) -> list[dict]:
        """最近一条带图用户消息的图片引用（launch_inspection 派生任务的输入）。"""
        async with self._sf() as session:
            row = await session.execute(
                select(SessionMessageRecord.images_json)
                .where(
                    SessionMessageRecord.session_id == session_id,
                    SessionMessageRecord.role == ROLE_USER,
                    SessionMessageRecord.images_json.is_not(None),
                )
                .order_by(SessionMessageRecord.created_at.desc(), SessionMessageRecord.id.desc())
                .limit(1)
            )
            raw = row.scalar_one_or_none()
            if not raw:
                return []
            try:
                images = json.loads(raw)
                return images if isinstance(images, list) else []
            except (json.JSONDecodeError, TypeError):
                return []

    async def pending_user_images(self, session_id: str, *, max_images: int = 10) -> list[dict]:
        """待送检图片聚合：最近一次派发（任务卡）之后的所有用户图片，按时间正序。

        多条消息分批上传（"这是正面"/"这是背面"）的全部纳入；上次派发前的
        图片不再重复送检（任务卡即消费水位线）。URI 去重（同一张图重发不重复计）。
        """
        async with self._sf() as session:
            # 水位线：最近一张任务卡的 created_at（无任务卡 = 会话从未派发，全部纳入）
            boundary = await session.execute(
                select(SessionMessageRecord.created_at)
                .where(
                    SessionMessageRecord.session_id == session_id,
                    SessionMessageRecord.kind == KIND_TASK_CARD,
                )
                .order_by(SessionMessageRecord.created_at.desc(), SessionMessageRecord.id.desc())
                .limit(1)
            )
            since = boundary.scalar_one_or_none()

            stmt = select(SessionMessageRecord.images_json).where(
                SessionMessageRecord.session_id == session_id,
                SessionMessageRecord.role == ROLE_USER,
                SessionMessageRecord.images_json.is_not(None),
            )
            if since is not None:
                stmt = stmt.where(SessionMessageRecord.created_at > since)
            stmt = stmt.order_by(SessionMessageRecord.created_at.asc(), SessionMessageRecord.id.asc())
            rows = await session.execute(stmt)

            aggregated: list[dict] = []
            seen_uris: set[str] = set()
            for raw in rows.scalars():
                try:
                    images = json.loads(raw)
                except (json.JSONDecodeError, TypeError):
                    continue
                if not isinstance(images, list):
                    continue
                for img in images:
                    if not isinstance(img, dict):
                        continue
                    uri = str(img.get("uri") or "")
                    if not uri or uri in seen_uris:
                        continue
                    seen_uris.add(uri)
                    aggregated.append(img)
                    if len(aggregated) >= max_images:
                        return aggregated
            return aggregated
