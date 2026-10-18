"""迁移：会话/任务时间列 DATETIME -> DATETIME(6)（微秒精度）。

背景（用户反馈：任务轨迹未实时连贯显示）：
MySQL 建表时 created_at 等列落到 datetime（秒级），同一秒内的多条消息
时间戳相同 -> ORDER BY created_at 次序不定 -> 前端按连续同 task_id 聚合的
活动流轨迹组被会话级行插断、碎裂成多块。改 DATETIME(6) 后每条消息保留
微秒级插入时刻，时序恢复唯一确定（每次落库是独立事务，同微秒不可能）。

**本脚本只对"按旧 init.sql 建库"的存量库有意义**：精度自 2026-09-29 起已写进
`scripts/sql/init.sql`（并由 `tests/repositories/test_db_migration.py` 的精度用例守住），
新建库直接就是 DATETIME(6)，不必再跑。

幂等：列已是 datetime(6) 则跳过。
用法：.venv/bin/python scripts/migrate_datetime_fsp6.py
"""
from __future__ import annotations

import pymysql

from app.core.config import get_settings

# 表 -> 列（保持原可空/默认属性，仅升级精度）
COLUMNS: dict[str, list[tuple[str, str]]] = {
    "agent_sessions": [
        ("created_at", "DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)"),
        ("updated_at", "DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)"),
    ],
    "session_messages": [
        ("created_at", "DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)"),
    ],
    "tasks": [
        ("created_at", "DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)"),
        ("updated_at", "DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)"),
        ("finished_at", "DATETIME(6) NULL"),
    ],
}


def main() -> None:
    s = get_settings()
    url = s.database_url
    if not url.startswith("mysql"):
        print(f"非 MySQL（{url.split('://')[0]}），无需迁移。")
        return
    # mysql+aiomysql://user:pwd@host:port/db
    rest = url.split("://", 1)[1]
    cred, hostdb = rest.split("@", 1)
    user, _, password = cred.partition(":")
    hostport, _, db = hostdb.partition("/")
    host, _, port = hostport.partition(":")
    con = pymysql.connect(host=host, port=int(port or 3306), user=user,
                          password=password, database=db)
    try:
        cur = con.cursor()
        for table, cols in COLUMNS.items():
            cur.execute(
                "SELECT COLUMN_NAME, COLUMN_TYPE FROM information_schema.COLUMNS "
                "WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s", (db, table),
            )
            current = {name: ctype for name, ctype in cur.fetchall()}
            for col, definition in cols:
                ctype = current.get(col)
                if ctype is None:
                    print(f"  ! {table}.{col} 不存在，跳过")
                    continue
                if ctype.lower() == "datetime(6)":
                    print(f"  = {table}.{col} 已是 datetime(6)")
                    continue
                sql = f"ALTER TABLE {table} MODIFY COLUMN {col} {definition}"
                print(f"  > {sql}")
                cur.execute(sql)
        con.commit()
        print("迁移完成。")
    finally:
        con.close()


if __name__ == "__main__":
    main()
