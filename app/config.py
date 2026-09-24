"""应用配置管理。读取 .env 文件和环境变量。"""
from pydantic_settings import BaseSettings
from pathlib import Path


class Settings(BaseSettings):
    # ── 应用 ──
    APP_ENV: str = "dev"  # dev | prod
    APP_SECRET_KEY: str = "kdao-dev-secret-key-change-in-production"  # prod 必须通过环境变量覆盖
    JWT_EXPIRE_HOURS: int = 24

    # ── 数据库 ──
    DATABASE_URL: str = "postgresql+asyncpg://stock:stock123@localhost:5432/stock_monitor"
    DATABASE_URL_SYNC: str = "postgresql+psycopg2://stock:stock123@localhost:5432/stock_monitor"

    # ── Redis ──
    REDIS_URL: str = "redis://localhost:6379/0"
    REDIS_PASSWORD: str = ""

    # ── 飞书 ──
    FEISHU_APP_ID: str = ""
    FEISHU_APP_SECRET: str = ""
    FEISHU_VERIFY_TOKEN: str = ""
    # 出站告警自定义机器人 webhook（可选；配置后模型 IC 衰减 DEGRADED 时推送）
    FEISHU_ALERT_WEBHOOK: str = ""

    # ── DeepSeek ──
    DEEPSEEK_API_KEY: str = ""
    DEEPSEEK_BASE_URL: str = "https://api.deepseek.com"
    DEEPSEEK_MODEL: str = "deepseek-chat"

    # ── LLM 通用别名（OpenAI 兼容协议；情绪打分等新链路用，GLM/DeepSeek 可切换：
    #    未配置时回退 DEEPSEEK_*；切 GLM 示例：
    #    LLM_BASE_URL=https://open.bigmodel.cn/api/paas/v4  LLM_MODEL=glm-4-flash）──
    LLM_API_KEY: str = ""
    LLM_BASE_URL: str = ""
    LLM_MODEL: str = ""

    @property
    def llm_config(self) -> dict:
        """解析生效的 LLM 配置：LLM_* 优先，回退 DEEPSEEK_*。"""
        return {
            "api_key": self.LLM_API_KEY or self.DEEPSEEK_API_KEY,
            "base_url": self.LLM_BASE_URL or self.DEEPSEEK_BASE_URL,
            "model": self.LLM_MODEL or self.DEEPSEEK_MODEL,
        }

    # ── 登录认证 ──
    LOGIN_USERNAME: str = "admin"
    LOGIN_PASSWORD: str = ""  # 必须通过环境变量 LOGIN_PASSWORD 设置

    # ── TuShare ──
    TUSHARE_TOKEN: str = ""

    # ── CORS ──
    CORS_ORIGINS: list = ["http://localhost:3000", "http://localhost:8000", "https://s.pmlab.top"]

    # ── 服务器 ──
    DOMAIN: str = "localhost"

    # ── 日志 ──
    LOG_LEVEL: str = "INFO"
    LOG_DIR: Path = Path("logs")

    # ── 数据采集 ──
    CUTOFF_DATE: str = "2021-01-01"  # baostock 数据起始日期（跳过此前退市的股票）

    model_config = dict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


settings = Settings()
