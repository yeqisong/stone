"""数据状态 API — 交易日历 + 每日下载进度 + 数据完整性。"""
from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect, Body
from sqlalchemy import text
from datetime import date, timedelta

from app.config import settings
from app.db.connection import get_sync_db
from app.signal import _dag_wake_event, wake_dag_broadcast, set_main_loop
from app.auth.auth import get_current_user

# 预加载 pipeline 模块，避免首次 DAG 触发时 cold import 延迟 5-10 秒
from scripts.pipeline import dag

router = APIRouter(tags=["status"])

# 缓存计数结果（5分钟过期）
_count_cache = {'time': 0, 'data': {}}

def _fast_count(db, query: str, key: str, ttl: int = 300) -> int:
    """带缓存的快速计数。"""
    import time
    now = time.time()
    if now - _count_cache['time'] < ttl and key in _count_cache['data']:
        return _count_cache['data'][key]
    try:
        val = db.execute(text(query)).scalar() or 0
        _count_cache['data'][key] = val
        _count_cache['time'] = now
        return val
    except Exception:
        db.rollback()
        return -1


@router.get("/data_status")
def get_data_status(
    month: str = Query(None),
):
    db = get_sync_db()
    try:
        today = date.today()
        if month:
            y, m = map(int, month.split("-"))
            cal_start = date(y, m, 1)
            cal_end = date(y, m + 1, 1) - timedelta(days=1) if m < 12 else date(y + 1, 1, 1) - timedelta(days=1)
        else:
            cal_start = today.replace(day=1)
            cal_end = (today.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)

        # ── 概览 ──
        overview = {}
        result = db.execute(text("SELECT MAX(trade_date) FROM daily_quote"))
        overview["latest_date"] = str(r) if (r := result.scalar()) else None
        try:
            result = db.execute(text("SELECT reltuples::bigint FROM pg_class WHERE relname='daily_quote'"))
            overview["total_rows"] = result.scalar() or 0
        except Exception:
            result = db.execute(text("SELECT COUNT(*) FROM daily_quote"))
            overview["total_rows"] = result.scalar() or 0

        exchanges = {}
        for ex in ("SSE", "SZSE", "BSE"):
            r = db.execute(text("SELECT trade_date FROM daily_quote WHERE exchange=:ex ORDER BY trade_date DESC LIMIT 1"), {"ex": ex})
            row = r.fetchone()
            exchanges[ex] = {"latest_date": str(row[0]) if row else None, "rows": 0}
        result = db.execute(text("SELECT exchange, COUNT(*) FROM stock_master WHERE status='N' AND stock_type='stock' GROUP BY exchange"))
        ex_stocks = {r[0]: r[1] for r in result.fetchall()}
        overview["total_stocks"] = sum(ex_stocks.values())
        for ex in ("SSE", "SZSE", "BSE"):
            exchanges[ex]["stocks"] = ex_stocks.get(ex, 0)
        overview["exchanges"] = exchanges
        result = db.execute(text("SELECT is_trade_day FROM trade_calendar WHERE cal_date=:d LIMIT 1"), {"d": today})
        row = result.fetchone()
        overview["is_trade_day"] = bool(row[0]) if row else None
        overview["today"] = str(today)

        # ── 交易日历 + 每日数据量（从 daily_completeness 表增量读取） ──
        dc_rows = db.execute(text(
            "SELECT trade_date, stock_rows, index_rows, etf_rows, fund_rows, "
            "stock_baseline, index_baseline, etf_baseline, fund_baseline "
            "FROM daily_completeness WHERE trade_date BETWEEN :s AND :e ORDER BY trade_date"
        ), {"s": cal_start, "e": cal_end}).fetchall()
        dc = {str(r[0]): {"stock": r[1] or 0, "index": r[2] or 0, "etf": r[3] or 0, "fund": r[4] or 0,
                           "sb": r[5] or 0, "ib": r[6] or 0, "eb": r[7] or 0, "fb": r[8] or 0} for r in dc_rows}

        tc_rows = db.execute(text(
            "SELECT cal_date, is_trade_day FROM trade_calendar WHERE cal_date BETWEEN :s AND :e ORDER BY cal_date"
        ), {"s": cal_start, "e": cal_end}).fetchall()
        calendar_dates = {str(r[0]): bool(r[1]) for r in tc_rows}

        calendar = []
        missing_dates = []
        current = cal_start
        while current <= cal_end:
            d = str(current)
            is_trade = calendar_dates.get(d, current.weekday() < 5)
            dd = dc.get(d)
            days_ago = (today - current).days
            if dd and is_trade:
                sb = dd.get('sb', 0)
                ib = dd.get('ib', 0)
                eb = dd.get('eb', 0)
                fb = dd.get('fb', 0) or sb
                sp = min(round(dd['stock'] / sb * 100, 1), 100.0) if sb else 0
                ip = min(round(dd['index'] / ib * 100, 1), 100.0) if ib else 0
                ep = min(round(dd['etf']   / eb * 100, 1), 100.0) if eb else 0
                fp = min(round(dd['fund']  / fb * 100, 1), 100.0) if fb else 0
                parts = [p for p, b in [(sp, sb), (ip, ib), (ep, eb), (fp, fb)] if b > 0]
                pct = round(sum(parts) / len(parts), 1) if parts else 0
                rows = dd['stock']
                if days_ago <= 7 and pct < 80 and rows > 0: missing_dates.append(d)
                elif days_ago <= 30 and rows == 0 and current <= today: missing_dates.append(d)
                calendar.append({"date": d, "is_trade_day": True,
                    "completeness": {"rows": rows, "pct": pct, "baseline": sb},
                    "detail": {
                        "stock":  {"actual": dd['stock'],  "baseline": sb, "pct": sp},
                        "index":  {"actual": dd['index'],  "baseline": ib, "pct": ip},
                        "etf":    {"actual": dd['etf'],    "baseline": eb, "pct": ep},
                        "fund":   {"actual": dd['fund'],   "baseline": fb, "pct": fp},
                    },
                    "weekday": current.weekday()})
            else:
                if is_trade and days_ago <= 30 and current <= today: missing_dates.append(d)
                calendar.append({"date": d, "is_trade_day": is_trade, "completeness": {"rows": 0, "pct": 0, "baseline": 0} if is_trade else None, "weekday": current.weekday()})
            current += timedelta(days=1)

        # ── 今日策略 ──
        row = db.execute(text("SELECT metric_value, status, detail, checked_at FROM system_metrics WHERE metric_name='daily_strategy' ORDER BY checked_at DESC LIMIT 1")).fetchone()
        today_strategy = None
        if row:
            import json as _json
            detail = {}
            try: detail = _json.loads(row[2]) if row[2] else {}
            except: pass
            today_strategy = {"buy_signals": int(row[0]) if row[0] else 0, "status": row[1], "time": str(row[3])[:19] if row[3] else None, "total_signals": detail.get("total_signals", 0), "scanned": detail.get("scanned", 0), "elapsed_seconds": detail.get("elapsed_seconds", 0), "preference": detail.get("preference", ""), "strategy_date": str(row[3])[:10] if row[3] else None}

        # ── DAG 运行日志 ──
        # 取最新一次 run_id 的所有节点日志
        result = db.execute(text("""
            SELECT trade_date, node_name, status, rows, detail, run_id,
                   created_at, started_at, finished_at
            FROM dag_run_log WHERE run_id = (SELECT run_id FROM dag_run_log ORDER BY id DESC LIMIT 1)
            OR run_id = '' ORDER BY id ASC LIMIT 30
        """))
        download_log = []
        for r in result.fetchall():
            def ts(v): return str(v)[:19] if v else None
            download_log.append({
                "date": str(r[0]), "node": r[1], "status": r[2], "rows": r[3] or 0,
                "detail": r[4] or '', "run_id": r[5] or '',
                "created_at": ts(r[6]), "started_at": ts(r[7]), "finished_at": ts(r[8]),
            })

        # ── 数据明细：优先从 data_stats_cache 读（DAG stats 节点写入），无缓存时实时回退 ──
        import json as _json2
        cache_row = db.execute(text(
            "SELECT stats_json, computed_at FROM data_stats_cache ORDER BY id DESC LIMIT 1"
        )).fetchone()
        if cache_row:
            data_tables = _json2.loads(cache_row[0])
            stats_computed_at = str(cache_row[1])[:19] if cache_row[1] else None
        else:
            # 回退：实时 COUNT（首次部署，stats 节点未执行过）
            def q(query):
                try: return db.execute(text(query)).scalar()
                except: db.rollback(); return -1
            data_tables = [
                {'label':'上交所A股','rows':q("SELECT COUNT(*) FROM daily_quote WHERE exchange='SSE'"),'items':q("SELECT COUNT(*) FROM stock_master WHERE exchange='SSE' AND status='N' AND stock_type='stock'"),'start':q("SELECT MIN(trade_date)::text FROM daily_quote WHERE exchange='SSE'"),'end':q("SELECT MAX(trade_date)::text FROM daily_quote WHERE exchange='SSE'")},
                {'label':'深交所A股','rows':q("SELECT COUNT(*) FROM daily_quote WHERE exchange='SZSE'"),'items':q("SELECT COUNT(*) FROM stock_master WHERE exchange='SZSE' AND status='N' AND stock_type='stock'"),'start':q("SELECT MIN(trade_date)::text FROM daily_quote WHERE exchange='SZSE'"),'end':q("SELECT MAX(trade_date)::text FROM daily_quote WHERE exchange='SZSE'")},
                {'label':'指数日K线','rows':q("SELECT COALESCE((SELECT reltuples::bigint FROM pg_class WHERE relname='index_daily_quote'),0)"),'items':q("SELECT COUNT(*) FROM stock_master WHERE stock_type='index'")},
                {'label':'ETF日K线','rows':q("SELECT COUNT(*) FROM daily_quote WHERE LEFT(stock_code,2)='15' OR LEFT(stock_code,1)='5'"),'items':q("SELECT COUNT(*) FROM stock_master WHERE stock_type='etf'"),
                 'start':q("SELECT MIN(trade_date)::text FROM daily_quote WHERE LEFT(stock_code,2)='15' OR LEFT(stock_code,1)='5'"),
                 'end':q("SELECT MAX(trade_date)::text FROM daily_quote WHERE LEFT(stock_code,2)='15' OR LEFT(stock_code,1)='5'")},
                {'label':'基本面','rows':q("SELECT COUNT(*) FROM stock_fundamentals"),'items':q("SELECT COUNT(DISTINCT stock_code) FROM stock_fundamentals"),'start':q("SELECT MIN(updated_at)::text FROM stock_fundamentals"),'end':q("SELECT MAX(updated_at)::text FROM stock_fundamentals")},
                {'label':'交易信号','rows':q("SELECT COUNT(*) FROM signal_history"),'items':q("SELECT COUNT(DISTINCT stock_code) FROM signal_history"),'start':q("SELECT MIN(signal_date)::text FROM signal_history"),'end':q("SELECT MAX(signal_date)::text FROM signal_history"),'detail':'买' + str(q("SELECT COUNT(*) FROM signal_history WHERE direction='buy'") or 0) + ' 卖' + str(q("SELECT COUNT(*) FROM signal_history WHERE direction='sell'") or 0)},
                {'label':'交易日历','rows':q("SELECT COUNT(*) FROM trade_calendar"),'start':q("SELECT MIN(cal_date)::text FROM trade_calendar"),'end':q("SELECT MAX(cal_date)::text FROM trade_calendar")},
            ]
            stats_computed_at = None

        return {
            "overview": overview, "calendar": calendar, "missing_dates": missing_dates,
            "download_log": download_log, "today_strategy": today_strategy, "data_tables": data_tables, "stats_computed_at": stats_computed_at,
        }
    finally:
        db.close()


def _has_running_task() -> bool:
    """检查是否有活跃 DAG 任务（心跳 10 分钟内更新过的 running 节点）。"""
    from app.db.connection import get_sync_db
    from sqlalchemy import text
    try:
        db = get_sync_db()
        r = db.execute(text(
            "SELECT 1 FROM dag_run_log WHERE status='running' "
            "AND heartbeat_at > CURRENT_TIMESTAMP - INTERVAL '10 minutes' LIMIT 1"
        )).scalar()
        db.close()
        return bool(r)
    except:
        return False


@router.post("/dag_trigger")
def dag_trigger(body: dict, user: str = Depends(get_current_user)):
    """手工触发 DAG 节点（后台执行）。body: {"node": "kline", "date": "2026-06-05"}"""
    if _has_running_task():
        return {"ok": False, "error": "待上一个任务完成后再进行", "busy": True}
    import uuid
    node = body.get("node", "stats")
    td = body.get("date", str(date.today()))
    include_downstream = body.get("include_downstream", True)
    task_id = str(uuid.uuid4())[:8]
    thread = threading.Thread(target=_run_dag_background, args=(node, td, task_id, False, include_downstream), daemon=True)
    thread.start()
    # 不在此处唤醒 — dag.start() 写 pending 后内部调用 _wake_broadcast()
    return {"ok": True, "task_id": task_id, "node": node, "date": td, "status": "started"}


@router.post("/refresh_stats")
def refresh_stats(user: str = Depends(get_current_user)):
    """手工触发全库数据统计（TaskManager + 后台 generate_stats）。"""
    from app.task import TaskManager
    tm = TaskManager()
    task = tm.create_task(task_type="stats", flow_name="全库统计")
    if task.status == "failed":
        return {"ok": False, "error": task.error}
    tm.start_task(task.task_id)

    def _run():
        try:
            from scripts.pipeline import generate_stats
            # 注意：signal.alarm 只能在主线程使用，后台线程必须用 threading.Timer
            rows = generate_stats()
            tm.update_node(task.task_id, "node-0", status="success", rows=rows, progress_pct=100)
            tm.complete_task(task.task_id)
        except Exception as e:
            tm.update_node(task.task_id, "node-0", status="failed", error=str(e))
            tm.fail_task(task.task_id, str(e))

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    return {"ok": True, "task_id": task.task_id}


@router.get("/dag_status")
def get_dag_status():
    """DAG 状态 + 自动看门狗：检测卡住>10分钟节点并标记超时。"""
    from scripts.pipeline import dag
    from app.db.connection import get_sync_db
    from sqlalchemy import text

    structure = []  # 已废弃，由 dag_flows 动态管理

    # 看门狗：检测心跳超过 10 分钟未更新的 running 节点 → 卡死
    db = get_sync_db()
    stuck = db.execute(text("""
        SELECT id, node_name, run_id FROM dag_run_log
        WHERE status='running' AND heartbeat_at < CURRENT_TIMESTAMP - INTERVAL '10 minutes'
    """)).fetchall()
    for row in stuck:
        db.execute(text("UPDATE dag_run_log SET status='failed', detail=:dt, finished_at=CURRENT_TIMESTAMP WHERE id=:id"),
                   {"id": row[0], "dt": f"心跳超时(>10min无更新), node={row[1]}"})
    if stuck:
        run_ids = list(set(r[2] for r in stuck))
        for rid in run_ids:
            db.execute(text("UPDATE dag_run_log SET status='failed', detail='上游超时跳过', finished_at=CURRENT_TIMESTAMP WHERE run_id=:rid AND status='pending'"),
                       {"rid": rid})
        db.commit()
        logger.warning(f"[看门狗] 标记 {len(stuck)} 个超时, 连带下游 pending")
    # 清理孤立 pending（超过 30 分钟的旧任务）
    orphaned = db.execute(text("""
        UPDATE dag_run_log SET status='failed', detail='上游已失败', finished_at=CURRENT_TIMESTAMP
        WHERE status='pending' AND created_at < CURRENT_TIMESTAMP - INTERVAL '30 minutes'
    """)).rowcount
    if orphaned:
        db.commit()
    db.close()
    db = get_sync_db()
    rows = db.execute(text("""
        SELECT DISTINCT ON (node_name) node_name, status, rows, detail, finished_at, trade_date
        FROM dag_run_log
        WHERE run_id = (SELECT run_id FROM dag_run_log ORDER BY id DESC LIMIT 1)
        ORDER BY node_name, id DESC
    """)).fetchall()
    run_status = {}
    current_run_id = None
    current_run_latest = None
    for r in rows:
        run_status[r[0]] = {
            "status": r[1], "rows": r[2] or 0,
            "detail": r[3] or '', "time": str(r[4])[:19] if r[4] else None,
            "date": str(r[5]) if r[5] else None
        }
    # 获取当前 run_id
    cr = db.execute(text("SELECT run_id, MAX(CASE WHEN status='success' OR status='failed' THEN finished_at ELSE NULL END) FROM dag_run_log GROUP BY run_id ORDER BY MAX(id) DESC LIMIT 1")).fetchone()
    if cr:
        current_run_id = cr[0]
        current_run_latest = str(cr[1])[:19] if cr[1] else None
    db.close()

    return {"structure": structure, "status": dag.status(), "run_status": run_status,
            "current_run_id": current_run_id, "current_run_latest": current_run_latest}


@router.get("/trade_calendar")
def get_trade_calendar(year: int = Query(None)):
    from crawler.trade_calendar import get_year_calendar
    db = get_sync_db()
    try:
        calendar = get_year_calendar(db, year)
        return {"year": year or date.today().year, "total": len(calendar), "calendar": calendar}
    finally:
        db.close()


import threading
import time as _time
from loguru import logger
from pydantic import BaseModel

import threading as _threading
_sync_lock = _threading.Lock()
_sync_tasks: dict = {}

class SyncDateRequest(BaseModel):
    date: str
    node: str = "cron"
    mode: str = "quick"  # force=全量覆盖, quick=跳过已有


def _run_dag_background(node: str, trade_date: str, task_id: str, force: bool = False,
                        include_downstream: bool = True):
    """后台线程执行 DAG 节点（带锁保护 _sync_tasks）。"""
    with _sync_lock:
        _sync_tasks[task_id] = {"status": "running", "node": node, "date": trade_date, "started_at": _time.time()}
    try:
        kwargs = {"trade_date": trade_date, "force": force, "include_downstream": include_downstream}
        if node == "all":
            dag.run_all(**kwargs)
        else:
            # 模块级 dag 惰性加载（dag_config 拓扑过滤已移除节点），
            # 否则 dag_trigger/sync_date 旧接口会因空图报"未知节点"
            from scripts.pipeline import _ensure_module_dag_loaded
            _ensure_module_dag_loaded()
            dag.run(node, **kwargs)
        with _sync_lock:
            if task_id in _sync_tasks:
                _sync_tasks[task_id].update({"status": "completed", "elapsed": round(_time.time() - _sync_tasks[task_id]["started_at"])})
    except Exception as e:
        logger.error(f"后台 DAG {node} {trade_date} 失败: {e}")
        with _sync_lock:
            if task_id in _sync_tasks:
                _sync_tasks[task_id].update({"status": "failed", "error": str(e)})
    _time.sleep(600)
    with _sync_lock:
        _sync_tasks.pop(task_id, None)


@router.post("/data_status/sync_date")
def sync_date(body: SyncDateRequest, user: str = Depends(get_current_user)):
    if _has_running_task():
        return {"ok": False, "error": "待上一个任务完成后再进行", "busy": True}
    import uuid
    task_id = str(uuid.uuid4())[:8]
    node = getattr(body, 'node', 'cron') or 'cron'
    force = (body.mode == 'force')
    logger.info(f"手动触发数据采集: {body.date} mode={body.mode} force={force} (task={task_id})")
    thread = threading.Thread(target=_run_dag_background, args=(node, body.date, task_id, force), daemon=True)
    thread.start()
    return {"ok": True, "task_id": task_id, "date": body.date, "mode": body.mode, "status": "started"}


# ══════════════════════════════════════════
# DAG 配置 API（流程结构唯一来源）
# ══════════════════════════════════════════

@router.post("/dag_terminate")
def dag_terminate(body: dict, user: str = Depends(get_current_user)):
    """终止正在运行的任务。body: {"run_id": "xxx"}"""
    run_id = body.get("run_id", "")
    if not run_id:
        return {"ok": False, "error": "缺少 run_id"}
    from app.signal import request_stop
    request_stop(run_id)
    # 不直接写 DB — 由各节点的 _hb_thread 或 _execute 检测到信号后自行终止
    logger.warning(f"[dag] 用户手动终止任务: {run_id}")
    return {"ok": True, "run_id": run_id, "status": "terminated"}


@router.get("/dag_config")
def get_dag_config():
    """返回当前 DAG 流程结构（从 dag_config 表读取，无任务时也可渲染）。"""
    structure = []  # 已废弃，由 dag_flows 动态管理
    return {"structure": structure}


# ══════════════════════════════════════════
# WebSocket 管理器（DAG 状态实时推送）
# ══════════════════════════════════════════

import asyncio
import json as _json
from collections import OrderedDict

_ws_clients: set = set()
_last_dag_broadcast = {}

@router.websocket("/ws/dag")
async def ws_dag(websocket: WebSocket):
    # WebSocket 认证：dev 模式放行，prod 模式强制校验 token
    token = websocket.query_params.get("token", "")
    if token:
        try:
            from app.auth.auth import verify_token
            verify_token(token)
        except Exception:
            if getattr(settings, 'APP_ENV', 'dev') == 'prod':
                await websocket.close(code=4001, reason="Invalid token")
                return
            # dev 模式：token 无效不阻塞，仅记录日志
            logger.warning(f"[ws] token 校验失败，dev 模式放行")
    elif getattr(settings, 'APP_ENV', 'dev') == 'prod':
        await websocket.close(code=4001, reason="Token required")
        return
    await websocket.accept()
    _ws_clients.add(websocket)
    # 立即发送当前状态（新连接不用等广播周期）
    try:
        await _send_current_state(websocket)
        while True:
            msg = await websocket.receive_text()
            if msg == "ping":
                await websocket.send_text('{"type":"pong"}')
    except WebSocketDisconnect:
        pass
    finally:
        _ws_clients.discard(websocket)


async def _send_current_state(ws):
    """向单个 WS 客户端发送当前 DAG 状态。"""
    try:
        from app.db.connection import get_sync_db
        from sqlalchemy import text
        db = get_sync_db()
        rows = db.execute(text("""
            SELECT id, node_name, status, rows, detail, run_id, trade_date,
                   created_at, started_at, finished_at
            FROM dag_run_log
            WHERE run_id = (SELECT run_id FROM dag_run_log ORDER BY id DESC LIMIT 1)
            ORDER BY id
        """)).fetchall()
        run_status = {}
        log_nodes = []
        for r in rows:
            run_status[r[1]] = {"status": r[2], "rows": r[3] or 0, "detail": r[4] or ''}
            log_nodes.append({
                "date": str(r[6]) if r[6] else None,
                "node": r[1], "status": r[2], "rows": r[3] or 0,
                "detail": r[4] or '', "run_id": r[5],
                "created_at": str(r[7])[:19] if r[7] else None,
                "started_at": str(r[8])[:19] if r[8] else None,
                "finished_at": str(r[9])[:19] if r[9] else None,
            })
        cr = db.execute(text("SELECT run_id, MAX(CASE WHEN status='success' OR status='failed' THEN finished_at ELSE NULL END) FROM dag_run_log GROUP BY run_id ORDER BY MAX(id) DESC LIMIT 1")).fetchone()
        rid = cr[0] if cr else None
        rlatest = str(cr[1])[:19] if cr and cr[1] else None
        has_run = any(s.get('status') == 'running' for s in run_status.values())
        db.close()
        await ws.send_text(_json.dumps({
            "type": "dag_status", "run_status": run_status,
            "current_run_id": rid, "current_run_latest": rlatest, "has_running": has_run
        }))
        if log_nodes:
            await ws.send_text(_json.dumps({"type": "dag_log", "nodes": log_nodes}))
        # 首次连接时也推送当前补数任务状态
        try:
            from crawler.backfill import BackfillManager
            mgr = BackfillManager.get_instance()
            active = mgr.get_active_task()
            if active:
                await ws.send_text(_json.dumps({"type": "backfill_progress", **active}))
        except Exception as e:
            logger.error(f"[ws] _send_current_state 异常: {e}")
    except Exception as e:
        logger.error(f"[ws] _send_current_state 异常: {e}")

async def broadcast_dag_status():
    """后台任务：每 2 秒检查 DAG 状态，有变化时推送给所有 WS 客户端。"""
    global _ws_clients
    from app.db.connection import get_sync_db
    from sqlalchemy import text
    _last_state = None
    _last_log_state = None
    _completed_since = None  # 记录全部完成的时间
    _sys_tracker = {"last": 0, "cpu_prev": None}  # sys metrics throttle + CPU prev

    while True:
        try:
            db = get_sync_db()
            # 获取最新 run 的节点状态
            rows = db.execute(text("""
                SELECT id, node_name, status, rows, detail, run_id, trade_date,
                       created_at, started_at, finished_at
                FROM dag_run_log
                WHERE run_id = (SELECT run_id FROM dag_run_log ORDER BY id DESC LIMIT 1)
                ORDER BY id
            """)).fetchall()

            run_status = {}
            has_running = False
            node_list = []
            for r in rows:
                if r[2] == 'running': has_running = True
                run_status[r[1]] = {"status": r[2], "rows": r[3] or 0, "detail": r[4] or ''}
                node_list.append({
                    "date": str(r[6]) if r[6] else None,
                    "node": r[1], "status": r[2], "rows": r[3] or 0,
                    "detail": r[4] or '', "run_id": r[5],
                    "created_at": str(r[7])[:19] if r[7] else None,
                    "started_at": str(r[8])[:19] if r[8] else None,
                    "finished_at": str(r[9])[:19] if r[9] else None,
                })

            cr = db.execute(text("SELECT run_id, MAX(finished_at) FROM dag_run_log GROUP BY run_id ORDER BY MAX(id) DESC LIMIT 1")).fetchone()
            current_run_id = cr[0] if cr else None
            current_run_latest = str(cr[1])[:19] if cr and cr[1] else None

            # 全部完成后开始计时 5 分钟
            now_ts = _time.time()
            if not has_running and current_run_latest:
                ft = _time.mktime(_time.strptime(current_run_latest, "%Y-%m-%d %H:%M:%S"))
                age = now_ts - ft
                if age > 300:  # >5 分钟
                    current_run_id = None
                    current_run_latest = None

            state_hash = f"{current_run_id}_{current_run_latest}_{has_running}"
            log_hash = _json.dumps(node_list, sort_keys=True)

            # DAG 状态变化 → 推送
            if state_hash != _last_state:
                _last_state = state_hash
                payload = _json.dumps({
                    "type": "dag_status",
                    "run_status": run_status,
                    "current_run_id": current_run_id,
                    "current_run_latest": current_run_latest,
                    "has_running": has_running
                })
                dead = set()
                for ws in _ws_clients:
                    try:
                        await ws.send_text(payload)
                    except:
                        dead.add(ws)
                _ws_clients -= dead

            # 日志状态变化 → 推送
            if log_hash != _last_log_state:
                _last_log_state = log_hash
                log_payload = _json.dumps({"type": "dag_log", "nodes": node_list})
                dead = set()
                for ws in _ws_clients:
                    try:
                        await ws.send_text(log_payload)
                    except:
                        dead.add(ws)
                _ws_clients -= dead

            # ── 系统指标推送（每 30s）──
            try:
                _now = _time.time()
                if _now - _sys_tracker["last"] >= 30:
                    _sys_tracker["last"] = _now
                    import subprocess, re

                    def _smem():
                        try:
                            with open("/proc/meminfo") as f:
                                lines = f.readlines()
                            t = a = 0
                            for l in lines:
                                if l.startswith("MemTotal:"): t = int(l.split()[1]) // 1024
                                elif l.startswith("MemAvailable:"): a = int(l.split()[1]) // 1024
                            return t, a
                        except: return 0, 0

                    def _sdisk():
                        try:
                            r = subprocess.run(["df", "-BM", "/app/data"], capture_output=True, text=True, timeout=5)
                            parts = r.stdout.strip().split("\n")[1].split()
                            return int(parts[1].replace("M",""))//1024, int(parts[2].replace("M",""))//1024, int(parts[3].replace("M",""))//1024
                        except: return 0,0,0

                    def _scpu():
                        try:
                            with open("/proc/stat") as f:
                                cols = [int(x) for x in f.readline().split()[1:8]]
                            idle = cols[3] + cols[4]
                            return sum(cols), idle
                        except: return 0,0

                    mt, ma = _smem()
                    dt, du, da = _sdisk()
                    ct, ci = _scpu()
                    cp = 0
                    prev = _sys_tracker["cpu_prev"]
                    if prev and ct > prev[0]:
                        cp = round((1 - (ci - prev[1]) / (ct - prev[0])) * 100)
                    _sys_tracker["cpu_prev"] = (ct, ci)

                    # DB 总大小（每 5 分钟查一次，避免每次 WS 循环都查）
                    _dbs = 0
                    if _now - _sys_tracker.get("db_last", 0) >= 300:
                        _sys_tracker["db_last"] = _now
                        try:
                            from app.db.connection import get_sync_db as _gsd
                            _tdb = _gsd()
                            _dbs = round((_tdb.execute(text("SELECT pg_database_size(current_database())")).scalar() or 0) / 1024 / 1024)
                            _tdb.close()
                            _sys_tracker["db_size"] = _dbs
                        except: pass
                    else:
                        _dbs = _sys_tracker.get("db_size", 0)

                    sys_payload = _json.dumps({"type": "sys_metrics", "data": {
                        "memory_total_mb": mt, "memory_avail_mb": ma,
                        "memory_used_pct": round((mt-ma)/mt*100,1) if mt else 0,
                        "disk_total_gb": dt, "disk_used_gb": du, "disk_avail_gb": da,
                        "disk_used_pct": round(du/dt*100,1) if dt else 0,
                        "cpu_pct": cp,
                        "db_size_mb": _dbs,
                    }})
                    dead = set()
                    for ws in _ws_clients:
                        try: await ws.send_text(sys_payload)
                        except: dead.add(ws)
                    _ws_clients -= dead
            except Exception:
                pass

            # ── 补数进度推送 ──
            try:
                from crawler.backfill import BackfillManager
                mgr = BackfillManager.get_instance()
                active = mgr.get_active_task()
                if active:
                    payload = _json.dumps({"type": "backfill_progress", **active})
                    dead = set()
                    for ws in _ws_clients:
                        try: await ws.send_text(payload)
                        except: dead.add(ws)
                    _ws_clients -= dead
            except Exception:
                pass

            # ── 特征补数进度推送 ──
            try:
                from app.api.features import get_active_compute_tasks
                tasks = get_active_compute_tasks()
                for t in tasks:
                    payload = _json.dumps({"type": "feature_compute_progress", **t})
                    dead = set()
                    for ws in _ws_clients:
                        try: await ws.send_text(payload)
                        except: dead.add(ws)
                    _ws_clients -= dead
            except Exception:
                pass

            # ── 统一任务进度推送（TaskManager）──
            try:
                from app.task import TaskManager
                tm = TaskManager()
                for t in tm.get_active_tasks():
                    payload = _json.dumps({"type": "task_progress", **t.to_dict()})
                    dead = set()
                    for ws in _ws_clients:
                        try: await ws.send_text(payload)
                        except: dead.add(ws)
                    _ws_clients -= dead
            except Exception:
                pass

            db.close()
        except Exception as e:
            logger.error(f"[ws] broadcast error: {e}", exc_info=True)

        # 空闲时 30 秒，DAG 运行中 2 秒，补数运行中 2 秒
        has_backfill = False
        try:
            from crawler.backfill import BackfillManager
            bf = BackfillManager.get_instance().get_active_task()
            has_backfill = bf is not None
        except Exception:
            pass
        try:
            await asyncio.wait_for(_dag_wake_event.wait(), timeout=2 if (has_running or has_backfill) else 30)
            _dag_wake_event.clear()
        except asyncio.TimeoutError:
            pass


# ══════════════════════════════════════════
# 日志 API（WebSocket 补充，用于弹窗）
# ══════════════════════════════════════════

@router.get("/dag_logs")
def get_dag_logs(log_id: int = Query(None)):
    """获取 DAG 执行日志。传 log_id 查单条，否则取最新运行组。"""
    from app.db.connection import get_sync_db
    from sqlalchemy import text
    try:
        db = get_sync_db()
        if log_id:
            row = db.execute(text(
                "SELECT id, node_name, trade_date, status, detail, rows, started_at, finished_at "
                "FROM dag_run_log WHERE id = :lid"
            ), {"lid": log_id}).fetchone()
            db.close()
            if not row:
                return {"ok": True, "nodes": []}
            return {"ok": True, "nodes": [{
                "id": row[0], "node_name": row[1], "trade_date": str(row[2]) if row[2] else "",
                "status": row[3], "detail": row[4] or "", "rows": row[5] or 0,
                "started_at": str(row[6])[:19] if row[6] else None,
                "finished_at": str(row[7])[:19] if row[7] else None,
            }]}
        run_id = db.execute(text("""
            SELECT run_id FROM dag_run_log
            WHERE status='running' AND heartbeat_at > CURRENT_TIMESTAMP - INTERVAL '5 minutes'
            ORDER BY id DESC LIMIT 1
        """)).scalar()
        if not run_id:
            run_id = db.execute(text("""
                SELECT run_id FROM dag_run_log
                ORDER BY id DESC LIMIT 1
            """)).scalar()
        if not run_id:
            return {"ok": True, "nodes": []}
        rows = db.execute(text("""
            SELECT node_name, status, rows, detail, run_id, created_at, started_at, finished_at
            FROM dag_run_log WHERE run_id=:rid ORDER BY id
        """), {"rid": run_id}).fetchall()
        nodes = []
        for r in rows:
            nodes.append({
                "node": r[0], "status": r[1], "rows": r[2] or 0, "detail": r[3] or '',
                "run_id": r[4],
                "created_at": str(r[5])[:19] if r[5] else None,
                "started_at": str(r[6])[:19] if r[6] else None,
                "finished_at": str(r[7])[:19] if r[7] else None,
            })
        db.close()
        return {"ok": True, "nodes": nodes}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@router.get("/data_status/sync_status")
def sync_status(task_id: str):
    task = _sync_tasks.get(task_id)
    if task is None:
        return {"ok": False, "error": "task_not_found"}
    return {"ok": True, "task": task}


@router.get("/data-sources/health")
def data_sources_health():
    """数据源健康状态。返回所有注册源的可用性和当前活跃源。"""
    try:
        from crawler.adapters import get_data_source_manager
        manager = get_data_source_manager()
        return manager.get_health_summary()
    except Exception as e:
        return {"sources": [], "active_source": None, "error": str(e)}


# ═══════════════════════════════════════════════
#  历史补数
# ═══════════════════════════════════════════════

@router.post("/data_status/backfill")
def backfill_start(payload: dict):
    """启动补数任务。

    Body: {"type":"kline","start_date":"2021-06-16","end_date":"2026-06-17","force":false}
    """
    from crawler.backfill import BackfillManager, BusyError

    task_type = payload.get("type", "")
    if task_type not in ("kline", "index", "etf", "fund", "indicator", "calendar", "stock_master"):
        return {"ok": False, "error": "不支持的补数类型"}

    start_date = payload.get("start_date")
    end_date = payload.get("end_date")
    force = payload.get("force", False)
    batch_size = max(1, min(int(payload.get("batch_size", 20) or 20), 500))

    # 日期校验
    if task_type != "fund":
        if start_date and end_date and start_date > end_date:
            return {"ok": False, "error": "起始日期不能晚于截止日期"}
        today = date.today().isoformat()
        if end_date and end_date > today:
            return {"ok": False, "error": "截止日期不能晚于今天"}

    try:
        mgr = BackfillManager.get_instance()
        task_id = mgr.start(task_type, start_date, end_date, force, batch_size)
        return {"ok": True, "task_id": task_id}
    except BusyError as e:
        return {"ok": False, "error": str(e), "busy": True, "current_task": e.current_task}


@router.post("/data_status/backfill/{task_id}/cancel")
def backfill_cancel(task_id: str):
    """取消运行中的补数任务。"""
    from crawler.backfill import BackfillManager
    try:
        mgr = BackfillManager.get_instance()
        return mgr.cancel(task_id)
    except ValueError as e:
        return {"ok": False, "error": str(e)}


@router.get("/data_status/backfill/history")
def backfill_history(limit: int = 20):
    """获取补数历史记录。"""
    from crawler.backfill import BackfillManager
    mgr = BackfillManager.get_instance()
    return {"tasks": mgr.get_history(limit)}


@router.get("/data_status/backfill/logs")
def backfill_logs(page: int = 1, page_size: int = 20):
    """分页获取补数任务日志（按时间倒序，含进行中+已完成+失败）。"""
    from crawler.backfill import BackfillManager
    mgr = BackfillManager.get_instance()
    return mgr.get_logs(page, page_size)


# ═══════════════════════════════════════════════
#  系统监控
# ═══════════════════════════════════════════════

@router.get("/stock_fund_list")
def stock_fund_list(stock_type: str = "stock", page: int = 1, page_size: int = 50,
                    search: str = "", user: str = Depends(get_current_user)):
    """股票主表 + 最新基本面信息 分页联表查询。"""
    from app.db.connection import get_sync_db
    from sqlalchemy import text
    db = get_sync_db()
    try:
        where = "sm.stock_type = :t"
        params = {"t": stock_type}
        if search:
            where += " AND (sm.stock_code ILIKE :s OR sm.stock_name ILIKE :s)"
            params["s"] = f"%{search}%"
        count_sql = f"SELECT COUNT(*) FROM stock_master sm WHERE {where}"
        total = db.execute(text(count_sql), params).scalar() or 0
        offset = (page - 1) * page_size
        sql = f"""
            SELECT sm.*, sf.pe_ttm, sf.pb_mrq, sf.market_cap, sf.dv_ttm,
                   sf.turnover_rate, sf.volume_ratio, sf.ps_ttm, sf.dv_ratio,
                   sf.circ_mv, sf.total_shares, sf.float_share, sf.roe,
                   sf.trade_date AS fund_date
            FROM stock_master sm
            LEFT JOIN LATERAL (
                SELECT * FROM stock_fundamentals WHERE stock_code = sm.stock_code
                ORDER BY COALESCE(trade_date_v2, updated_at, CURRENT_DATE) DESC LIMIT 1
            ) sf ON true
            WHERE {where}
            ORDER BY sm.stock_code
            LIMIT :lim OFFSET :off
        """
        rows = db.execute(text(sql), {**params, "lim": page_size, "off": offset}).fetchall()
        items = []
        for r in rows:
            d = dict(r._mapping)
            for k, v in d.items():
                if isinstance(v, date):
                    d[k] = str(v)
            items.append(d)
        return {"items": items, "total": total, "page": page, "page_size": page_size}
    finally:
        db.close()


@router.get("/system/metrics")
def system_metrics():
    """服务器基本指标：内存、磁盘、CPU。"""
    import subprocess, re

    def _mem():
        try:
            with open("/proc/meminfo") as f:
                lines = f.readlines()
            total = avail = 0
            for l in lines:
                if l.startswith("MemTotal:"):
                    total = int(l.split()[1]) // 1024  # kB → MB
                elif l.startswith("MemAvailable:"):
                    avail = int(l.split()[1]) // 1024
            return total, avail
        except Exception:
            return 0, 0

    def _disk():
        try:
            # 数据盘用量
            r = subprocess.run(["df", "-BM", "/app/data"], capture_output=True, text=True, timeout=5)
            lines = r.stdout.strip().split("\n")
            if len(lines) >= 2:
                parts = lines[1].split()
                total = int(parts[1].replace("M", "")) // 1024  # MB → GB
                used = int(parts[2].replace("M", "")) // 1024
                avail = int(parts[3].replace("M", "")) // 1024
                return total, used, avail
        except Exception:
            pass
        return 0, 0, 0

    def _cpu():
        try:
            with open("/proc/stat") as f:
                line = f.readline()
            cols = [int(x) for x in line.split()[1:8]]
            idle = cols[3] + cols[4]  # idle + iowait
            total = sum(cols)
            return total, idle
        except Exception:
            return 0, 0

    mem_total, mem_avail = _mem()
    disk_total, disk_used, disk_avail = _disk()
    cpu_total, cpu_idle = _cpu()

    # CPU 需要两次采样求差值，这里只返回瞬时值由前端计算
    def _db_tables():
        try:
            from app.db.connection import get_sync_db
            from sqlalchemy import text
            db = get_sync_db()
            rows = db.execute(text("""
                SELECT relname, pg_size_pretty(pg_total_relation_size(relid)) AS size,
                       n_live_tup
                FROM pg_stat_user_tables
                ORDER BY pg_total_relation_size(relid) DESC
                LIMIT 10
            """)).fetchall()
            db_size = db.execute(text("SELECT pg_database_size(current_database())")).scalar() or 0
            db.close()
            return {
                "db_size_mb": round(db_size / 1024 / 1024),
                "tables": [{"name": r[0], "size": r[1], "rows": r[2]} for r in rows],
            }
        except Exception:
            return {"db_size_mb": 0, "tables": []}

    return {
        "memory_total_mb": mem_total,
        "memory_avail_mb": mem_avail,
        "memory_used_pct": round((mem_total - mem_avail) / mem_total * 100, 1) if mem_total else 0,
        "disk_total_gb": disk_total,
        "disk_used_gb": disk_used,
        "disk_avail_gb": disk_avail,
        "disk_used_pct": round(disk_used / disk_total * 100, 1) if disk_total else 0,
        "cpu_idle": cpu_idle,
        "cpu_total": cpu_total,
        "db": _db_tables(),
    }
