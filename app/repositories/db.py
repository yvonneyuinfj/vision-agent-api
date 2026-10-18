"""Async SQLAlchemy 基础设施（运行库＝MySQL，唯一）。

SQLite 在本仓库只剩两处，且都与本模块无关：LangGraph 断点库（`CHECKPOINTER_PATH`，
内网 PG 仍为规划中）、`scripts/workflows/sync_to_db.py` 的离线留痕（决策 50 的 R3/R4）。
"""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.core.config import get_settings
from app.core.logger import get_logger

logger = get_logger("app.repositories.db")


class Base(DeclarativeBase):
    pass


def create_engine() -> AsyncEngine:
    """平台业务库引擎：MySQL 唯一运行库（建表见 `init_db`）。"""
    return create_async_engine(
        get_settings().database_url,
        echo=False,
        pool_pre_ping=True,
    )


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker:
    return async_sessionmaker(engine, expire_on_commit=False)


async def init_db(engine: AsyncEngine) -> None:
    """建表：从 scripts/sql/init.sql 执行权威 DDL（严禁代码内硬编码建表语句）。

    只认 MySQL：SQLite 等价脚本随测试基座迁到 MySQL 后已移除（决策 50 的 R2），非 MySQL
    直接报错而不是再按另一份 DDL 建表——两份脚本曾各自漂移（DDL 建秒级 DATETIME、ORM 却
    声明 DATETIME(6)），同一份出处才看得见这类矛盾。
    """
    if engine.dialect.name != "mysql":
        raise RuntimeError(
            f"运行库方言 {engine.dialect.name!r} 没有对应 DDL：平台唯一运行库是 MySQL，"
            "建表脚本只有 scripts/sql/init.sql。"
        )

    statements = init_statements()
    if not statements:
        return
    async with engine.begin() as conn:
        for stmt in statements:
            await conn.execute(text(stmt))

    # 列级增量迁移：DDL 脚本只对空库生效（CREATE TABLE IF NOT EXISTS），
    # 已存在表新增的可空列在此对账补齐（启动期幂等 ALTER，失败不阻断启动）。
    await _ensure_columns(engine)


def init_statements() -> list[str]:
    """权威 DDL（scripts/sql/init.sql）的语句清单，库级语句已过滤。

    测试基座与启动建表共用这一个出处。
    """
    from app.core.config import BASE_DIR

    path = BASE_DIR / "scripts" / "sql" / "init.sql"
    if not path.is_file():
        raise FileNotFoundError(f"DDL script not found: {path}")
    return _split_sql(path.read_text(encoding="utf-8"))


# (table, column, DDL snippet) —— 只注册「可空/带默认值」的增量列；
# 破坏性变更（改类型/删列/加非空无默认值列）仍需正式迁移流程。
_INCREMENTAL_COLUMNS: list[tuple[str, str, str]] = [
    ("capability_registry", "model_weights_sha256", "CHAR(64) NULL"),
    # 工具类别定义表（class_definitions 透传落库，报告二/三节类别语义数据源）
    ("tasks", "class_definitions_json", "TEXT NULL"),
]


async def _ensure_columns(engine: AsyncEngine) -> None:
    """对账 ORM 声明的增量列与实际表结构，缺列则 ALTER TABLE ADD COLUMN。

    列是否存在走 information_schema（MySQL 唯一运行库，见 `init_db`）。
    """
    for table, column, ddl in _INCREMENTAL_COLUMNS:
        try:
            if not await _column_exists(engine, table, column):
                async with engine.begin() as conn:
                    await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))
                logger.info("incremental migration: %s.%s added", table, column)
        except Exception:  # noqa: BLE001 - 迁移失败不阻断启动（列缺时后续查询会报错，日志已留痕）
            logger.exception("incremental migration failed for %s.%s", table, column)


async def _column_exists(engine: AsyncEngine, table: str, column: str) -> bool:
    async with engine.connect() as conn:
        rows = await conn.execute(
            text(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_schema = DATABASE() AND table_name = :t AND column_name = :c"
            ),
            {"t": table, "c": column},
        )
        return rows.first() is not None


def _split_sql(sql: str) -> list[str]:
    r"""取出 CREATE TABLE / CREATE INDEX 语句，过滤注释、空行与**库级语句**。

    库级语句（`CREATE DATABASE` / `USE \`quality_inspection\``）必须整条丢弃：它们把
    会话默认库改成脚本里写死的运行库名，于是"建到哪个库"由 DDL 文件而非连接 URL 决定。
    测试基座要在独立 schema 上执行同一份 DDL，不滤掉这两条就会把表打进运行库。
    """
    body = "\n".join(
        line
        for line in sql.splitlines()
        if line.strip() and not line.strip().startswith("--")
    )
    return [
        stmt.strip()
        for stmt in body.split(";")
        if stmt.strip() and not stmt.strip().upper().startswith(("CREATE DATABASE", "USE "))
    ]


async def ping(engine: AsyncEngine) -> bool:
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))
        return True