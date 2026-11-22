"""FastAPI 应用入口。"""
import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from loguru import logger

from app.config import settings
from app.db.connection import async_engine, check_db_connection, get_db
from app.db.schema import init_db, DEFAULT_STRATEGY_CONFIG
from app.api.portfolio import router as portfolio_router
from app.api.trades import router as trades_router
from app.api.buy import router as buy_router
from app.api.watch import router as watch_router
from app.api.agent import router as agent_router
from app.api.treemap import router as treemap_router
from app.api.signals import router as signals_router
from app.api.stock import router as stock_router
from app.api.market import router as market_router
from app.api.cb import router as cb_router
from app.api.status import router as status_router
from app.api.risk import router as risk_router
from app.api.stocks import router as stocks_router
from app.api.settings import router as settings_router
from app.api.models import router as models_router
from app.api.functions import router as functions_router
from app.api.features import router as features_router
from app.api.kepl import router as kepl_router
from app.api.dag_types import router as dag_types_router
from app.api.lineage import router as lineage_router
from app.api.dag_flows import router as dag_flows_router
from app.feishu.webhook import router as feishu_router
from app.auth.auth import (
    authorize_http_request,
    authorize_request,
    get_current_user,
    get_principal,
    principal_payload,
    require_admin,
)
from app.auth.security import hash_password, verify_password_hash
from app.auth.service import authenticate, issue_login_token, load_principal


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

        # 回收卡死的 DAG 运行日志：节点都跑在本进程内，启动瞬间不可能有在途运行，
        # 故所有 pending/running 行都属于已死进程（重启/宕机 kill 掉的在途轮次）。
        # 不清扫的话这些行永远停在「待进行/进行中」，运行日志页留下从不清尾的假在途行，
        # 且「看数据表还是看运行日志判断节点跑没跑过」会产生分歧（design/07 §6.1）
        try:
            r = db.execute(text("""
                UPDATE dag_run_log
                SET status='failed', finished_at=now(),
                    detail = coalesce(nullif(detail, '') || '；', '')
                             || '进程重启中断（启动清扫器收尾；幂等节点由下一轮自愈）'
                WHERE status IN ('pending', 'running')
            """))
            db.commit()
            if r.rowcount > 0:
                logger.info(f"[startup] 回收卡死运行日志: {r.rowcount} 行 pending/running→failed")
        except Exception as e:
            logger.warning(f"[startup] 卡死运行日志回收失败（非致命）: {e}")
            db.rollback()

        # tushare 配额计数恢复：内存单例随进程重启归零，会令补数预算/熔断判断
        # （remaining() < 10 中止、≤reserve 收尾）误以为额度充足而超用当日限额
        try:
            from crawler.adapters.tushare_quota import TushareQuota
            _q = TushareQuota.get()
            _q.load(db)
            logger.info(f"[startup] tushare 配额已恢复: 当日已用 {_q.calls_today}/{_q.calls_limit}")
        except Exception as e:
            logger.warning(f"[startup] tushare 配额恢复失败（非致命）: {e}")
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
    # 启动盘中实时提醒（关注池快照 → 规则 → risk_alerts → 既有 WS 推送）
    # 事件 AI worker 先启动：提醒落库后入队，worker 异步解读，不阻塞行情轮询
    from app.intraday_ai import start_ai_event_worker
    start_ai_event_worker()
    from app.intraday_watch import intraday_watch_loop
    intraday_task = asyncio.create_task(intraday_watch_loop())
    # 10 分钟分时市值快照：页面没开也连续采集，避免 d10 跨 30 分钟
    from app.treemap_intraday import intraday_collect_loop
    treemap_task = asyncio.create_task(intraday_collect_loop())
    # 启动 Cron 定时调度器（后台线程）
    from scripts.cron_scheduler import start_cron_scheduler
    start_cron_scheduler()

    yield

    broadcast_task.cancel()
    intraday_task.cancel()
    treemap_task.cancel()
    try:
        await intraday_task          # 让 watcher 走完 finally（线程里的请求自行结束）
    except asyncio.CancelledError:
        pass
    try:
        await treemap_task
    except asyncio.CancelledError:
        pass
    from app.intraday_ai import stop_ai_event_worker
    stop_ai_event_worker()
    # 关闭前落盘配额计数（否则本次进程的最后一段用量丢失）
    try:
        from crawler.adapters.tushare_quota import TushareQuota
        from app.db.connection import SyncSessionLocal
        _db = SyncSessionLocal()
        try:
            TushareQuota.get().persist(_db)
        finally:
            _db.close()
    except Exception as e:
        logger.warning(f"[shutdown] tushare 配额落盘失败: {e}")
    await async_engine.dispose()
    logger.info("Application shutdown complete")


app = FastAPI(
    title="个股买卖点监测系统",
    description="Stock Buy/Sell Point Monitoring System",
    version="1.0.0",
    lifespan=lifespan,
)


@app.middleware("http")
async def _rbac_middleware(request: Request, call_next):
    """HTTP 请求统一鉴权。

    不能用 app-level dependencies：FastAPI 会把它同样应用到 WebSocket 路由，
    而 HTTPBearer 依赖在 WS scope 下拿不到 Request，握手直接 500。
    """
    from fastapi.responses import JSONResponse
    try:
        await asyncio.to_thread(authorize_http_request, request)
    except HTTPException as e:
        return JSONResponse({"detail": e.detail}, status_code=e.status_code)
    return await call_next(request)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── 路由注册 ──
app.include_router(portfolio_router, prefix="/api")
app.include_router(trades_router, prefix="/api")
app.include_router(buy_router, prefix="/api", dependencies=[Depends(get_current_user)])
app.include_router(watch_router, prefix="/api")
app.include_router(agent_router)  # /api/agent 已含前缀
app.include_router(treemap_router, prefix="/api")
app.include_router(signals_router, prefix="/api")
app.include_router(stock_router, prefix="/api", dependencies=[Depends(get_current_user)])
app.include_router(market_router, prefix="/api", dependencies=[Depends(get_current_user)])
app.include_router(cb_router, prefix="/api", dependencies=[Depends(get_current_user)])
app.include_router(status_router, prefix="/api")
app.include_router(risk_router, prefix="/api", dependencies=[Depends(get_current_user)])
app.include_router(stocks_router, prefix="/api")
app.include_router(settings_router, prefix="/api")
app.include_router(models_router, prefix="/api")
app.include_router(functions_router, prefix="/api")
app.include_router(features_router, prefix="/api")
app.include_router(kepl_router, prefix="/api")
from app.api.news import router as news_router
app.include_router(news_router, prefix="/api")
from app.api.dbexplorer import router as dbex_router
app.include_router(dbex_router, prefix="/api")
from app.api.admin import router as admin_router
app.include_router(admin_router, prefix="/api")
app.include_router(lineage_router, prefix="/api")
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
def login(request: LoginRequest, req: Request = None):
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

    principal, error = authenticate(request.username, request.password)
    if principal is None:
        raise HTTPException(status_code=401, detail=error or "用户名或密码错误")
    token = issue_login_token(principal)
    # 登录成功后清除该 IP 的尝试记录
    _login_attempts.pop(client_ip, None)
    return {"ok": True, "token": token, **principal_payload(principal)}


@app.get("/api/me")
def me(principal=Depends(get_principal)):
    """当前用户信息 + 权限码（前端导航/按钮使用）。"""
    return principal_payload(principal)


class PasswordChangeRequest(BaseModel):
    old_password: str
    new_password: str


@app.post("/api/me/password")
def change_my_password(body: PasswordChangeRequest, principal=Depends(get_principal)):
    """修改自己的密码；成功后 token_version+1，返回新 token。"""
    from sqlalchemy import text
    from app.db.connection import get_sync_db

    if len(body.new_password or "") < 8:
        raise HTTPException(status_code=422, detail="新密码至少 8 位")
    db = get_sync_db()
    try:
        row = db.execute(text(
            "SELECT password_hash FROM users WHERE id = :uid"
        ), {"uid": principal.user_id}).fetchone()
        if not row or not verify_password_hash(body.old_password, row[0]):
            raise HTTPException(status_code=400, detail="旧密码错误")
        try:
            new_hash = hash_password(body.new_password)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))
        db.execute(text("""
            UPDATE users
            SET password_hash = :p, must_change_password = false,
                token_version = token_version + 1,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = :uid
        """), {"p": new_hash, "uid": principal.user_id})
        db.commit()
        fresh = load_principal(principal.user_id, db=db)
        return {"ok": True, "token": issue_login_token(fresh), **principal_payload(fresh)}
    finally:
        db.close()


@app.post("/api/me/reauth")
def reauth(body: dict, principal=Depends(get_principal)):
    """二次认证：校验当前密码，返回短期 step-up token（供审批使用）。"""
    from sqlalchemy import text
    from app.db.connection import get_sync_db
    from app.auth.service import create_token

    password = (body or {}).get("password", "")
    db = get_sync_db()
    try:
        row = db.execute(text(
            "SELECT password_hash FROM users WHERE id = :uid"
        ), {"uid": principal.user_id}).fetchone()
        if not row or not verify_password_hash(password, row[0]):
            raise HTTPException(status_code=400, detail="密码错误")
        return {
            "ok": True,
            "stepup_token": create_token(
                principal.user_id, principal.username,
                principal.token_version, principal.perm_version,
                expires_hours=1,
            ),
        }
    finally:
        db.close()


@app.post("/api/me/feishu/bind-code")
async def feishu_bind_code(db=Depends(get_db), principal=Depends(get_principal)):
    """生成一次性飞书绑定码（10 分钟有效），到飞书发送「绑定 123456」。"""
    from app.feishu.identity import issue_bind_code

    r = await issue_bind_code(db, principal.user_id)
    if r.get("error"):
        raise HTTPException(400, r["error"])
    return r


@app.delete("/api/me/feishu/bind")
async def feishu_unbind(db=Depends(get_db), principal=Depends(get_principal)):
    """解除当前用户与飞书账号的绑定。"""
    from app.feishu.identity import unbind

    return await unbind(db, principal.user_id)


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
@app.middleware("http")
async def _no_cache_html(request, call_next):
    """HTML 一律 no-cache：index.html 引用的 JS 哈希每次构建都变，缓存住就会跑旧包。"""
    resp = await call_next(request)
    if resp.headers.get("content-type", "").startswith("text/html"):
        resp.headers["Cache-Control"] = "no-cache, must-revalidate"
    return resp


from pathlib import Path as _Path
_dist_dir = _Path("web-v2/dist")
if _dist_dir.is_dir():
    app.mount("/", StaticFiles(directory=str(_dist_dir), html=True), name="web")
else:
    # 开发/测试 worktree 可能尚未构建前端；API 仍可用
    logger.warning("web-v2/dist 不存在，跳过前端静态挂载")


def _validate_route_permissions() -> None:
    """启动自检：每条 API/WS 路由必须有 public/auth_only/permission 规则。"""
    from fastapi.routing import APIRoute, APIWebSocketRoute
    from app.auth.route_permissions import ROUTE_RULES, resolve_rule
    from app.auth.definitions import PERMISSION_CODES

    for rule in ROUTE_RULES:
        if rule.permission and rule.permission not in PERMISSION_CODES:
            raise RuntimeError(f"路由规则引用未知权限码: {rule.permission}")

    unmatched = []
    for route in app.routes:
        if isinstance(route, APIWebSocketRoute):
            if resolve_rule("GET", route.path) is None:
                unmatched.append(("WS", route.path))
            continue
        if not isinstance(route, APIRoute):
            continue
        for method in sorted(route.methods or []):
            if method in ("HEAD", "OPTIONS"):
                continue
            if resolve_rule(method, route.path) is None:
                unmatched.append((method, route.path))
    if unmatched:
        raise RuntimeError(f"路由未声明权限: {unmatched[:10]}（共 {len(unmatched)} 条）")


_validate_route_permissions()
