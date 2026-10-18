"""db 建表与增量迁移对账（MySQL 基座，决策 50 的 R2）。

四条各自守一处会真出事的不变量：
  1. 存量库缺增量列时启动必须补上（类别定义表落库的数据源），且重复启动幂等；
  2. `init_statements()` 不得带库级语句——带上一次，测试基座就会把表打进运行库；
  3. DDL 的时间列精度必须与 ORM 的 `DATETIME(6)` 一致——秒级列会让同秒写入的消息/任务
     时序塌缩（消息 id 是随机串，tie-break 等于没有），而这种情况在 SQLite 基座下测不出来；
  4. 用例期间连接落在的库必须是独立 schema。
"""
from __future__ import annotations

import pytest
from sqlalchemy import text

from app.repositories.db import init_db, init_statements

from tests import db_base

# ORM 用 _dt6() 声明的时间列（排序依赖它们，精度不一致即静默劣化）
_MICROSECOND_COLUMNS = {
    ("tasks", "created_at"),
    ("tasks", "updated_at"),
    ("tasks", "finished_at"),
    ("agent_sessions", "created_at"),
    ("agent_sessions", "updated_at"),
    ("session_messages", "created_at"),
}


async def _columns(engine) -> set[tuple[str, str]]:
    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT table_name, column_name FROM information_schema.columns "
                    "WHERE table_schema = DATABASE()"
                )
            )
        ).fetchall()
    return {(r[0], r[1]) for r in rows}


async def test_ensure_columns_backfills_class_definitions_json(db_engine):
    """存量库（缺新列）重启：DDL 的 CREATE TABLE IF NOT EXISTS 跳过，只能靠幂等 ALTER 补齐。"""
    engine, _ = db_engine
    async with engine.begin() as conn:
        await conn.execute(text("ALTER TABLE tasks DROP COLUMN class_definitions_json"))
    assert ("tasks", "class_definitions_json") not in await _columns(engine)

    await init_db(engine)  # 模拟旧库上的服务重启

    assert ("tasks", "class_definitions_json") in await _columns(engine)


async def test_ensure_columns_idempotent_on_second_startup(db_engine):
    """二次启动幂等：列已存在时不重复 ALTER、不阻断启动。"""
    engine, _ = db_engine
    await init_db(engine)
    await init_db(engine)  # 第二次启动：对账应全部跳过

    cols = await _columns(engine)
    assert ("tasks", "class_definitions_json") in cols
    assert ("capability_registry", "model_weights_sha256") in cols


async def test_init_statements_drop_database_level_statements():
    """库级语句必须被滤掉：`USE quality_inspection` 会让"建到哪个库"由文件而不是连接 URL 决定。"""
    statements = init_statements()
    assert statements
    for stmt in statements:
        assert not stmt.upper().startswith(("CREATE DATABASE", "USE ")), (
            f"库级语句漏进建表清单：{stmt[:60]}"
        )


@pytest.mark.parametrize("table,column", sorted(_MICROSECOND_COLUMNS))
async def test_time_columns_have_microsecond_precision(db_engine, table, column):
    """按 DDL 建出来的库，时间列必须是 DATETIME(6)（与 ORM 的 _dt6() 同一出处）。"""
    engine, _ = db_engine
    async with engine.connect() as conn:
        fsp = (
            await conn.execute(
                text(
                    "SELECT datetime_precision FROM information_schema.columns "
                    "WHERE table_schema = DATABASE() AND table_name = :t AND column_name = :c"
                ),
                {"t": table, "c": column},
            )
        ).scalar()
    assert fsp == 6, (
        f"{table}.{column} 精度为 {fsp}：同秒写入的行只能按随机 id 排序。"
        "改 scripts/sql/init.sql（DATETIME(6) + CURRENT_TIMESTAMP(6)），不要只改 ORM。"
    )


async def test_base_fixture_is_connected_to_the_test_schema(db_engine):
    """隔离守卫：用例期间连接落在的库必须就是 `QC_TEST_SCHEMA`，绝不是运行库。"""
    engine, _ = db_engine
    async with engine.connect() as conn:
        current = (await conn.execute(text("SELECT DATABASE()"))).scalar()
    assert current == db_base.test_schema() != "quality_inspection"
