"""pytest 根级 conftest：路径引导 + 跨子目录共享 fixture。

子目录按功能模块镜像 app/ 层级：
    tests/api/v1/       接口层（路由/异常映射/中间件）
    tests/services/     服务层（Storage/RAG/LLM）
    tests/agents/       编排层（LangGraph 节点/工具注册）
    tests/repositories/ 持久层（仓储 CRUD）
    tests/core/         core 层（统一外壳/错误码/配置）
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

# 保证 import app 包（根路径）
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests import db_base  # noqa: E402  （依赖上面的 ROOT 注入）


@pytest.fixture(scope="session")
def mysql_schema():
    """session 起始重建一次独立 schema；用例级隔离见 db_engine。

    刻意**跑完不删库**：留现场便于失败后手工查数，下次 session 起始会 DROP 重建。
    非 autouse：纯逻辑用例（替身 / monkeypatch）不该被"MySQL 必须在线"卡住。
    """
    try:
        db_base.bootstrap_schema()
    except Exception as exc:  # noqa: BLE001 - 前置不满足时给出可执行的修复指引
        pytest.exit(
            f"测试基座无法重建 MySQL schema `{db_base.test_schema()}`：{exc}\n"
            "DB 用例的前置是 MySQL 在线且账号有建库权限；"
            "连接参数取 QC_TEST_DATABASE_URL，缺省沿用 .env 的 DATABASE_URL（只换 schema 名）。",
            returncode=1,
        )


@pytest_asyncio.fixture
async def db_engine(mysql_schema):
    """共享引擎：表结构来自 scripts/sql/init.sql，用例开始前 TRUNCATE 全表。

    提供 engine 与 session_factory 两级，供仓储 / 服务测试复用。
    """
    from app.repositories.db import create_session_factory

    engine = create_async_engine(
        db_base.async_url(),
        echo=False,
        pool_pre_ping=True,
    )
    async with engine.begin() as conn:
        for stmt in db_base.truncate_statements():
            await conn.execute(text(stmt))
    yield engine, create_session_factory(engine)
    await engine.dispose()


@pytest_asyncio.fixture
async def session_factory(db_engine):
    """仅取 session_factory（针对仓储测试的友好局部 fixture）。"""
    _, sf = db_engine
    return sf