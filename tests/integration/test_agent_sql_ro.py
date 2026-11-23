"""Agent 原始 SQL 的数据库层最小权限验证（需 PostgreSQL）。"""
import pytest
from sqlalchemy import text

from app.agent.tools import execute_tool
from app.db.connection import SyncSessionLocal, get_sync_db
from app.db.schema import init_db


@pytest.fixture(scope="module", autouse=True)
def setup_database():
    db = SyncSessionLocal()
    try:
        init_db(db)
    finally:
        db.close()


def test_query_sql_reads_shared_table():
    r = execute_tool(0, "query_sql",
                     {"sql": "SELECT count(*) AS n FROM trade_calendar"},
                     permissions={"agent.sql.raw"})
    assert "error" not in r, r
    assert r["row_count"] == 1


def test_readonly_role_denies_personal_tables():
    """即使绕过 SQL 白名单，stone_ro 也没有个人表 SELECT 权限。"""
    db = get_sync_db()
    try:
        db.execute(text("SET TRANSACTION READ ONLY"))
        db.execute(text("SET LOCAL ROLE stone_ro"))
        with pytest.raises(Exception):
            db.execute(text("SELECT count(*) FROM users"))
    finally:
        db.rollback()
        db.close()


def test_dangerous_functions_blocked_before_db():
    r = execute_tool(0, "query_sql",
                     {"sql": "SELECT pg_read_binary_file('/etc/passwd')"},
                     permissions={"agent.sql.raw"})
    assert "不允许" in r.get("error", "") or "不允许" in str(r)
