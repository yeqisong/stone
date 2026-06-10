"""应用配置管理。读取 .env 文件和环境变量。"""
from pydantic_settings import BaseSettings
from pathlib import Path


class Settings(BaseSettings):
    # ── 应用 ──
    APP_ENV: str = "dev"  # dev | prod
    APP_SECRET_KEY: str = "dev-secret-change-in-production"
    JWT_EXPIRE_HOURS: int = 24

    # ── 数据库 ──
    DATABASE_URL: str = "sqlite+aiosqlite:///./data/stock_monitor.db"
    DATABASE_URL_SYNC: str = "sqlite:///./data/stock_monitor.db"

    # ── Redis ──
    REDIS_URL: str = "redis://localhost:6379/0"
    REDIS_PASSWORD: str = ""

    # ── 飞书 ──
    FEISHU_APP_ID: str = ""
    FEISHU_APP_SECRET: str = ""
    FEISHU_VERIFY_TOKEN: str = ""

    # ── DeepSeek ──
    DEEPSEEK_API_KEY: str = ""
    DEEPSEEK_BASE_URL: str = "https://api.deepseek.com"
    DEEPSEEK_MODEL: str = "deepseek-chat"

    # ── 登录认证 ──
    LOGIN_USERNAME: str = "admin"
    LOGIN_PASSWORD: str = "admin123"

    # ── 服务器 ──
    DOMAIN: str = "localhost"

    # ── 日志 ──
    LOG_LEVEL: str = "INFO"
    LOG_DIR: Path = Path("logs")

    # ── 数据采集 ──
    CUTOFF_DATE: str = "2021-01-01"  # baostock 数据起始日期（跳过此前退市的股票）

    model_config = dict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


settings = Settings()
