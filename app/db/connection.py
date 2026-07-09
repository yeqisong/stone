"""数据库连接管理。统一使用 PostgreSQL。"""
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings

# 异步引擎 (FastAPI 用)
async_engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,
    pool_size=5 if settings.APP_ENV == "prod" else 1,
    max_overflow=10,
    pool_pre_ping=True,  # DB 重启后自动检测并刷新死连接
)

AsyncSessionLocal = async_sessionmaker(
    async_engine,
    class_=AsyncSession,
    expire_on_commit=False,
)

# 同步引擎 (crawler / strategy cron 脚本用)
sync_engine = create_engine(
    settings.DATABASE_URL_SYNC,
    echo=False,
    pool_size=5,
    max_overflow=10,
    pool_timeout=30,  # 30s 超时，避免永久阻塞
    pool_pre_ping=True,  # DB 重启后自动检测并刷新死连接
)

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


async def check_db_connection() -> bool:
    """健康检查：验证数据库连接。"""
    try:
        async with AsyncSessionLocal() as session:
            await session.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
