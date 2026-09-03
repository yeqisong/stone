"""FastAPI 应用入口。"""
import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from loguru import logger

from app.config import settings
from app.db.connection import async_engine, check_db_connection
from app.db.schema import init_db, DEFAULT_STRATEGY_CONFIG
from app.api.portfolio import router as portfolio_router
from app.api.treemap import router as treemap_router
from app.api.signals import router as signals_router
from app.api.stock import router as stock_router
from app.api.status import router as status_router
from app.api.risk import router as risk_router
from app.api.stocks import router as stocks_router
from app.api.settings import router as settings_router
from app.api.models import router as models_router
from app.api.functions import router as functions_router
from app.api.features import router as features_router
from app.api.kepl import router as kepl_router
from app.api.dag_types import router as dag_types_router
from app.api.dag_flows import router as dag_flows_router
from app.feishu.webhook import router as feishu_router
from app.auth.auth import verify_password, create_token


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期：启动时初始化数据库，关闭时释放连接。"""
    logger.info(f"Starting Stock Monitor in {settings.APP_ENV} mode...")

    # JWT secret 强度校验：prod 强制，dev 警告
    if len(settings.APP_SECRET_KEY) < 16:
        if settings.APP_ENV == "prod":
            logger.error("APP_SECRET_KEY 长度不足（需要至少16字符），拒绝启动")
            raise RuntimeError("APP_SECRET_KEY must be at least 16 characters")
        else:
            logger.warning("APP_SECRET_KEY 长度不足，请在生产环境设置至少16字符的密钥")

    # 初始化数据库（同步执行，因为是启动时一次性操作）
    from app.db.connection import SyncSessionLocal
    from sqlalchemy import text
    db = SyncSessionLocal()
    try:
        init_db(db)
        logger.info("Database initialized successfully")
        # 补数孤儿任务恢复（仅后端启动时执行：上次异常终止的 running 任务标记 failed；
        # 诊断脚本等旁路实例化 BackfillManager 不会触发，避免误杀运行中的任务）
        from crawler.backfill import BackfillManager
        BackfillManager.get_instance(recover_orphans=True)
        # 初始化 entity_stats（首次启动时 JOIN 计算基线，后续 DAG 每日增量更新）
        try:
            es_rows = db.execute(text(
                "SELECT COUNT(*) FROM entity_stats WHERE total_cells > 0"
            )).scalar() or 0
            if es_rows == 0:
                logger.info("[startup] entity_stats 为空，计算基线（~40s）...")
                from scripts.pipeline import _init_entity_stats
                _init_entity_stats(db)
                db.commit()
                logger.info("[startup] entity_stats 基线计算完成")
        except Exception as e:
            logger.warning(f"[startup] entity_stats 初始化失败（非致命）: {e}")
            db.rollback()

        # 恢复异常终止的模型训练任务
        try:
            r = db.execute(text("UPDATE model_versions SET status='DRAFT' WHERE status='TRAINING'"))
            db.commit()
            if r.rowcount > 0:
                logger.info(f"[startup] 恢复: {r.rowcount} 个模型 TRAINING→DRAFT")
        except Exception:
            pass
    except Exception as e:
        logger.error(f"Database initialization failed: {e}")
        raise
    finally:
        db.close()

    # 保存主事件循环引用，供后台线程唤醒 WS 广播
    from app.api.status import broadcast_dag_status
    from app.signal import set_main_loop
    set_main_loop(asyncio.get_running_loop())
    broadcast_task = asyncio.create_task(broadcast_dag_status())
    # 启动 Cron 定时调度器（后台线程）
    from scripts.cron_scheduler import start_cron_scheduler
    start_cron_scheduler()

    yield

    broadcast_task.cancel()
    await async_engine.dispose()
    logger.info("Application shutdown complete")


app = FastAPI(
    title="个股买卖点监测系统",
    description="Stock Buy/Sell Point Monitoring System",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── 路由注册 ──
app.include_router(portfolio_router, prefix="/api")
app.include_router(treemap_router, prefix="/api")
app.include_router(signals_router, prefix="/api")
app.include_router(stock_router, prefix="/api")
app.include_router(status_router, prefix="/api")
app.include_router(risk_router, prefix="/api")
app.include_router(stocks_router, prefix="/api")
app.include_router(settings_router, prefix="/api")
app.include_router(models_router, prefix="/api")
app.include_router(functions_router, prefix="/api")
app.include_router(features_router, prefix="/api")
app.include_router(kepl_router, prefix="/api")
from app.api.dbexplorer import router as dbex_router
app.include_router(dbex_router, prefix="/api")
app.include_router(dag_types_router)  # /api/dag 已含前缀
app.include_router(dag_flows_router)  # /api/dag 已含前缀
app.include_router(feishu_router)  # /webhook/feishu 不带 /api 前缀

from pydantic import BaseModel

class LoginRequest(BaseModel):
    username: str
    password: str

# 简易内存频率限制
_login_attempts: dict = {}  # ip → [(timestamp, ...)]

@app.post("/api/login")
async def login(request: LoginRequest, req: Request = None):
    """登录接口，返回 JWT Token。频率限制：5次/分钟/IP。"""
    # 频率限制
    import time as _time
    client_ip = req.client.host if req else "unknown"
    now_ts = _time.time()
    attempts = _login_attempts.get(client_ip, [])
    attempts = [t for t in attempts if now_ts - t < 60]
    if len(attempts) >= 5:
        raise HTTPException(status_code=429, detail="登录尝试过于频繁，请1分钟后再试")
    attempts.append(now_ts)
    _login_attempts[client_ip] = attempts

    username = request.username
    password = request.password
    if not verify_password(username, password):
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    token = create_token(username)
    # 登录成功后清除该 IP 的尝试记录
    _login_attempts.pop(client_ip, None)
    return {"ok": True, "token": token, "username": username}


@app.get("/health")
async def health_check():
    """健康检查接口。"""
    db_ok = await check_db_connection()
    return {
        "status": "ok" if db_ok else "degraded",
        "database": "connected" if db_ok else "disconnected",
        "version": "1.0.0",
        "env": settings.APP_ENV,
    }

# 前端静态文件 (放在最后，不影响 API 路由匹配)
app.mount("/", StaticFiles(directory="web-v2/dist", html=True), name="web")
