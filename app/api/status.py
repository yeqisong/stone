"""数据状态 API — 交易日历 + 每日下载进度 + 数据完整性。"""
from fastapi import APIRouter, Query
from sqlalchemy import text
from datetime import date, timedelta

from app.db.connection import get_sync_db

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

        # ── 交易日历 + 每日数据量 ──
        result = db.execute(text("SELECT trade_date, COUNT(*) as cnt FROM daily_quote WHERE trade_date BETWEEN :s AND :e GROUP BY trade_date ORDER BY trade_date"), {"s": cal_start, "e": cal_end})
        daily_data = {str(r[0]): {"rows": r[1]} for r in result.fetchall()}
        # 补充指数/ETF 数据到完整性判断
        idx_data = {}
        try:
            ir = db.execute(text("SELECT trade_date, COUNT(*) FROM index_daily_quote WHERE trade_date BETWEEN :s AND :e GROUP BY trade_date"), {"s": cal_start, "e": cal_end}).fetchall()
            idx_data = {str(r[0]): r[1] for r in ir}
        except: pass
        for d, cnt in idx_data.items():
            if d in daily_data:
                daily_data[d]['idx_rows'] = cnt
            else:
                daily_data[d] = {'rows': 0, 'idx_rows': cnt}
        result = db.execute(text("SELECT cal_date, is_trade_day FROM trade_calendar WHERE cal_date BETWEEN :s AND :e ORDER BY cal_date"), {"s": cal_start, "e": cal_end})
        calendar_dates = {str(r[0]): bool(r[1]) for r in result.fetchall()}
        daily_counts = [v["rows"] for v in daily_data.values()]
        baseline_stocks = sorted(daily_counts)[len(daily_counts) // 2] if daily_counts else (overview["total_stocks"] or 1)

        calendar = []
        missing_dates = []
        current = cal_start
        while current <= cal_end:
            d = str(current)
            is_trade = calendar_dates.get(d, current.weekday() < 5)
            dd = daily_data.get(d)
            if is_trade and dd:
                pct = dd["rows"] / baseline_stocks * 100
                # 指数、ETF 数据标记
                has_idx = dd.get("idx_rows", 0) > 0
                days_ago = (today - current).days
                if pct < 80 and days_ago <= 7: missing_dates.append(d)
                calendar.append({"date": d, "is_trade_day": True, "completeness": {"rows": dd["rows"], "pct": round(pct, 1), "baseline": baseline_stocks, "idx": has_idx}, "weekday": current.weekday()})
            elif is_trade and not dd:
                days_ago = (today - current).days
                if days_ago <= 30 and current <= today: missing_dates.append(d)
                calendar.append({"date": d, "is_trade_day": True, "completeness": {"rows": 0, "pct": 0, "baseline": baseline_stocks}, "weekday": current.weekday()})
            else:
                calendar.append({"date": d, "is_trade_day": False, "completeness": None, "weekday": current.weekday()})
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
        # 取最近3天的所有节点日志，按 trade_date DESC, id ASC 排列
        result = db.execute(text(
            "SELECT trade_date, node_name, status, rows, finished_at, detail FROM dag_run_log "
            "WHERE trade_date >= (SELECT COALESCE(MAX(trade_date), CURRENT_DATE) - 3 FROM dag_run_log) "
            "ORDER BY trade_date DESC, id ASC LIMIT 30"
        ))
        download_log = [{"date": str(r[0]), "node": r[1], "status": r[2], "rows": r[3] or 0, "time": str(r[4])[:19] if r[4] else None, "detail": r[5] or ''} for r in result.fetchall()]

        # ── 数据明细（优先从 data_stats_cache 缓存表读取） ──
        import json as _json
        cached = db.execute(text("SELECT stats_json, computed_at FROM data_stats_cache ORDER BY id DESC LIMIT 1")).fetchone()
        if cached:
            try:
                parsed = _json.loads(cached[0])
                data_tables = parsed.get('tables', [])
                stats_computed_at = str(cached[1])[:19] if cached[1] else None
            except Exception:
                data_tables = []
                stats_computed_at = None
        else:
            data_tables = []
            stats_computed_at = None

        return {
            "overview": overview, "calendar": calendar, "missing_dates": missing_dates,
            "download_log": download_log, "today_strategy": today_strategy, "data_tables": data_tables, "stats_computed_at": stats_computed_at,
        }
    finally:
        db.close()


@router.post("/dag_trigger")
def dag_trigger(body: dict):
    """手工触发 DAG 节点。body: {"node": "kline", "date": "2026-06-05"}"""
    from scripts.pipeline import dag
    from datetime import date
    node = body.get("node", "stats")
    td = body.get("date", str(date.today()))
    try:
        if node == "all":
            dag.run_all(trade_date=td)
        else:
            dag.run(node, trade_date=td)
        return {"ok": True, "status": dag.status()}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@router.post("/refresh_stats")
def refresh_stats():
    """手工触发全库数据统计（DAG stats 节点）。"""
    from scripts.pipeline import dag
    from datetime import date
    dag.run("stats", trade_date=str(date.today()))
    return {"ok": True}


@router.get("/dag_status")
def get_dag_status():
    """查看 DAG 各节点最近完成时间。"""
    from scripts.pipeline import dag
    return {"status": dag.status()}


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

_sync_tasks: dict = {}

class SyncDateRequest(BaseModel):
    date: str


def _run_sync_in_background(sync_date: str, task_id: str):
    _sync_tasks[task_id] = {"status": "running", "date": sync_date, "started_at": _time.time()}
    try:
        from scripts.pipeline import dag
        dag.run("daily_update", trade_date=sync_date)
        _sync_tasks[task_id] = {"status": "completed", "date": sync_date, "elapsed": round(_time.time() - _sync_tasks[task_id]["started_at"])}
    except Exception as e:
        logger.error(f"后台同步 {sync_date} 失败: {e}")
        _sync_tasks[task_id] = {"status": "failed", "date": sync_date, "error": str(e)}
    _time.sleep(600)
    _sync_tasks.pop(task_id, None)


@router.post("/data_status/sync_date")
def sync_date(body: SyncDateRequest):
    import uuid
    task_id = str(uuid.uuid4())[:8]
    logger.info(f"手动触发数据采集: {body.date} (task={task_id})")
    thread = threading.Thread(target=_run_sync_in_background, args=(body.date, task_id), daemon=True)
    thread.start()
    return {"ok": True, "task_id": task_id, "date": body.date, "status": "started"}


@router.get("/data_status/sync_status")
def sync_status(task_id: str):
    task = _sync_tasks.get(task_id)
    if task is None: return {"ok": False, "error": "task_not_found"}
    return {"ok": True, "task": task}
