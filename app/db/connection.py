"""数据库连接管理。开发环境用 SQLite，生产环境用 PostgreSQL。"""
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings

_is_sqlite = "sqlite" in settings.DATABASE_URL

# 异步引擎 (FastAPI 用)
_async_kwargs = {"echo": False}
if not _is_sqlite:
    _async_kwargs.update({"pool_size": 5 if settings.APP_ENV == "prod" else 1, "max_overflow": 10})

async_engine = create_async_engine(settings.DATABASE_URL, **_async_kwargs)

AsyncSessionLocal = async_sessionmaker(
    async_engine,
    class_=AsyncSession,
    expire_on_commit=False,
)

# 同步引擎 (crawler / strategy cron 脚本用)
# SQLite: 使用 NullPool 确保每次 get_sync_db() 获得独立连接，WAL 写入对其他连接立即可见
_sync_kwargs = {"echo": False}
if _is_sqlite:
    from sqlalchemy.pool import NullPool
    _sync_kwargs["poolclass"] = NullPool
else:
    _sync_kwargs["pool_size"] = 2

sync_engine = create_engine(settings.DATABASE_URL_SYNC, **_sync_kwargs)

SyncSessionLocal = sessionmaker(
    sync_engine,
    class_=Session,
    expire_on_commit=False,
)


async def get_db() -> AsyncSession:
    """FastAPI 依赖注入：获取异步数据库会话。"""
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()


def get_sync_db() -> Session:
    """Cron 脚本：获取同步数据库会话。"""
    return SyncSessionLocal()


def is_sqlite() -> bool:
    """当前是否为 SQLite 数据库。"""
    return "sqlite" in settings.DATABASE_URL


def pg_insert_ignore(sql: str, conflict_col: str = None) -> str:
    """将 SQLite 的 INSERT OR IGNORE / INSERT OR REPLACE 转为 PostgreSQL 兼容语法。"""
    if not is_sqlite():
        sql = sql.replace("INSERT OR IGNORE", "INSERT")
        sql = sql.replace("INSERT OR REPLACE", "INSERT")
        if "ON CONFLICT" not in sql:
            if conflict_col:
                sql += f" ON CONFLICT ({conflict_col}) DO NOTHING"
            else:
                sql += " ON CONFLICT DO NOTHING"
    return sql


def pg_insert_upsert(sql: str, conflict_col: str, update_cols: list) -> str:
    """将 SQLite 的 INSERT OR REPLACE 转为 PostgreSQL UPSERT。"""
    if not is_sqlite():
        sql = sql.replace("INSERT OR REPLACE", "INSERT")
        if "ON CONFLICT" not in sql:
            sets = ", ".join(f"{c}=EXCLUDED.{c}" for c in update_cols)
            sql += f" ON CONFLICT ({conflict_col}) DO UPDATE SET {sets}"
    return sql


async def check_db_connection() -> bool:
    """健康检查：验证数据库连接。"""
    try:
        async with AsyncSessionLocal() as session:
            await session.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
