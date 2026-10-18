"""测试基座（决策 50 的 R2）：真实 MySQL 上的**独立 schema**，不再用临时 SQLite 文件。

为什么值得把单测绑到真库：验收口径与运行库都是 MySQL（决策 49-③），SQLite 基座让
DATETIME(6) 微秒、JSON 列、严格模式下的"数据过长/类型不符"这些真实行为在测试里完全不
可见——测试全绿与连得上真库是两件事。

隔离靠两层，缺一不可：
  - 库级：所有 DB 用例只碰 `QC_TEST_SCHEMA`（默认 `qc_test`），运行库 `quality_inspection`
    永不被碰；schema 在 session 起始 DROP 重建，DDL 出处仍是 `scripts/sql/init.sql`
    （经 `init_statements()` 过滤掉库级语句，否则 `USE` 会把表打进运行库）。
  - 行级：每个用例前 TRUNCATE 全部表，等价于原来"每个用例一个临时 db 文件"的隔离度。

前置声明：跑 DB 用例的机器必须有 MySQL。非 autouse，所以纯逻辑用例（替身 / monkeypatch）
不受这条约束。
"""
from __future__ import annotations

import os
import re

from sqlalchemy import create_engine, make_url
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.repositories.db import init_statements, init_db

DEFAULT_SCHEMA = "qc_test"
_IDENTIFIER = re.compile(r"^[A-Za-z0-9_]+$")
_CREATE_TABLE = re.compile(r"^CREATE TABLE IF NOT EXISTS `?(\w+)`?", re.IGNORECASE)


def test_schema() -> str:
    """schema 名进的是拼接 DDL（标识符不能参数化），故只允许标识符字符。"""
    name = os.getenv("QC_TEST_SCHEMA", DEFAULT_SCHEMA)
    if not _IDENTIFIER.match(name):
        raise RuntimeError(f"QC_TEST_SCHEMA 只允许字母/数字/下划线，当前为 {name!r}")
    return name


def _configured_url() -> URL:
    """连接参数默认沿用 `DATABASE_URL`（同实例、换 schema），也可整条覆盖。"""
    raw = os.getenv("QC_TEST_DATABASE_URL") or get_settings().database_url
    url = make_url(raw)
    if url.get_backend_name() != "mysql":
        raise RuntimeError(
            "测试基座要求 MySQL（决策 50 的 R2），但当前配置的方言是 "
            f"{url.get_backend_name()!r}。请改 .env 的 DATABASE_URL，"
            "或显式给出 QC_TEST_DATABASE_URL=mysql+aiomysql://…"
        )
    return url


def async_url() -> URL:
    """返回 URL 对象而不是 str：`str(URL)` 会把密码渲染成 `***`，
    拿它去连库必然 1045，而且报错看起来像"账号没权限"，极难归因。"""
    return _configured_url().set(database=test_schema())


def _sync_url(*, with_database: bool) -> URL:
    """建库/建表走同步引擎：aiomysql 没有同步入口，用它的依赖 pymysql。"""
    url = _configured_url().set(drivername="mysql+pymysql")
    return url.set(database=test_schema() if with_database else None)


def table_names() -> list[str]:
    return [
        m.group(1)
        for stmt in init_statements()
        if (m := _CREATE_TABLE.match(stmt))
    ]


def truncate_statements() -> list[str]:
    return [f"TRUNCATE TABLE `{name}`" for name in table_names()]


def bootstrap_schema() -> list[str]:
    """重建测试 schema，并按**生产同一入口** `init_db()` 建表。

    刻意不自己执行一份 DDL：`init_db` = `scripts/sql/init.sql` + `_INCREMENTAL_COLUMNS`
    启动期补列对账，只跑前者的基座会缺掉增量列，于"有没有列"的用例便成了隐式顺序依赖
    （谁先跑谁建表）。
    """
    import asyncio

    schema = test_schema()
    server = create_engine(_sync_url(with_database=False), poolclass=NullPool)
    try:
        with server.begin() as conn:
            conn.exec_driver_sql(f"DROP DATABASE IF EXISTS `{schema}`")
            conn.exec_driver_sql(
                f"CREATE DATABASE `{schema}` CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci"
            )
    except Exception as exc:  # noqa: BLE001 - 只补"怎么连上真库"的线索，不吞原错
        raise RuntimeError(
            f"测试基座无法重建 schema `{schema}`（host={_configured_url().host} "
            f"port={_configured_url().port}）：MySQL 是否在线、账号是否有建库权限？{exc}"
        ) from exc
    finally:
        server.dispose()

    async def _build() -> None:
        engine = create_async_engine(async_url(), poolclass=NullPool)
        try:
            await init_db(engine)
        finally:
            await engine.dispose()

    try:
        asyncio.run(_build())
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"测试基座建表失败（schema={schema}）：{exc}") from exc
    return table_names()
