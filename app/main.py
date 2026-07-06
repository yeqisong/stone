"""FastAPI 应用入口。"""
import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI
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

    # 初始化数据库（同步执行，因为是启动时一次性操作）
    from app.db.connection import SyncSessionLocal
    from sqlalchemy import text
    db = SyncSessionLocal()
    try:
        init_db(db)
        logger.info("Database initialized successfully")
        # init_db 后重新加载 DAG 拓扑（首次部署时模块级加载会因表不存在而跳过）
        from scripts.pipeline import dag, NODE_FN_MAP
        if not dag._nodes:
            dag.load_from_db(db, NODE_FN_MAP)
            logger.info(f"DAG reloaded: {len(dag._nodes)} nodes")
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
    allow_origins=["*"],
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
app.include_router(stocks_router, prefix="/api")
app.include_router(settings_router, prefix="/api")
app.include_router(models_router, prefix="/api")
app.include_router(functions_router, prefix="/api")
app.include_router(features_router, prefix="/api")
app.include_router(kepl_router, prefix="/api")
app.include_router(dag_types_router)  # /api/dag 已含前缀
app.include_router(dag_flows_router)  # /api/dag 已含前缀
app.include_router(feishu_router)  # /webhook/feishu 不带 /api 前缀

@app.post("/api/login")
async def login(request: dict):
    """登录接口，返回 JWT Token。"""
    username = request.get("username", "")
    password = request.get("password", "")
    if not verify_password(username, password):
        from fastapi import HTTPException
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    token = create_token(username)
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
