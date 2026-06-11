"""数据状态 API — 交易日历 + 每日下载进度 + 数据完整性。"""
from fastapi import APIRouter, Query
from sqlalchemy import text
from datetime import date, timedelta

from app.db.connection import get_sync_db

router = APIRouter(tags=["status"])


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

        # 最新交易日（索引 B-tree 末页，~0.2ms）
        result = db.execute(text("SELECT MAX(trade_date) FROM daily_quote"))
        overview["latest_date"] = str(r) if (r := result.scalar()) else None

        # 总行数（pg_class 统计信息，SQLite 回退 COUNT）
        try:
            result = db.execute(text(
                "SELECT reltuples::bigint FROM pg_class WHERE relname='daily_quote'"
            ))
            overview["total_rows"] = result.scalar() or 0
        except Exception:
            result = db.execute(text("SELECT COUNT(*) FROM daily_quote"))
            overview["total_rows"] = result.scalar() or 0

        # 各交易所最新日期（逐个 LIMIT 1 + 新索引 idx_dq_exchange_date，各 ~0.2s）
        # COUNT(DISTINCT *) 或 GROUP BY COUNT(*) 需全索引扫 → 换成 stock_master 的股票数
        exchanges = {}
        for ex in ("SSE", "SZSE", "BSE"):
            r = db.execute(text(
                "SELECT trade_date FROM daily_quote WHERE exchange=:ex ORDER BY trade_date DESC LIMIT 1"
            ), {"ex": ex})
            row = r.fetchone()
            exchanges[ex] = {
                "latest_date": str(row[0]) if row else None,
                "rows": 0,
            }

        # 股票总数和交易所分布（stock_master 仅数千行，秒出）
        result = db.execute(text("""
            SELECT exchange, COUNT(*) FROM stock_master WHERE status='N' GROUP BY exchange
        """))
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
        # 一次查询获取日历范围内所有日期的数据行数
        # 注意：去掉 COUNT(DISTINCT stock_code) — 在 656 万行上太慢
        result = db.execute(text("""
            SELECT trade_date, COUNT(*) as cnt
            FROM daily_quote WHERE trade_date BETWEEN :s AND :e
            GROUP BY trade_date ORDER BY trade_date
        """), {"s": cal_start, "e": cal_end})
        daily_data = {}
        for r in result.fetchall():
            daily_data[str(r[0])] = {"rows": r[1]}

        # 获取交易日历
        result = db.execute(text("""
            SELECT cal_date, is_trade_day FROM trade_calendar
            WHERE cal_date BETWEEN :s AND :e ORDER BY cal_date
        """), {"s": cal_start, "e": cal_end})
        calendar_dates = {}
        for r in result.fetchall():
            calendar_dates[str(r[0])] = bool(r[1])

        # 组装日历
        # 基准股票数 = 该月每天实际有数据的股票数的中位数
        # 而非 stock_master 全量（含退市/未上市），避免虚低
        daily_counts = [v["rows"] for v in daily_data.values()]
        if daily_counts:
            sorted_counts = sorted(daily_counts)
            baseline_stocks = sorted_counts[len(sorted_counts) // 2] or 1
        else:
            baseline_stocks = overview["total_stocks"] or 1

        calendar = []
        current = cal_start
        missing_dates = []
        while current <= cal_end:
            d = str(current)
            is_trade = calendar_dates.get(d, current.weekday() < 5)
            dd = daily_data.get(d)
            completeness = None
            if is_trade and dd:
                # 数据完整度: 当日行数 / 基准股票数 ≈ 覆盖率
                pct = dd["rows"] / baseline_stocks * 100
                completeness = {"rows": dd["rows"],
                                "pct": round(pct, 1), "baseline": baseline_stocks}
                # 仅对最近 7 天标记缺失
                days_ago = (today - current).days
                if pct < 80 and days_ago <= 7:
                    missing_dates.append(d)
            elif is_trade and not dd:
                # 完全无数据：仅标记最近 30 天
                days_ago = (today - current).days
                if days_ago <= 30 and current <= today:
                    missing_dates.append(d)
                    completeness = {"rows": 0, "pct": 0, "baseline": baseline_stocks}

            calendar.append({
                "date": d,
                "is_trade_day": is_trade,
                "completeness": completeness,
                "weekday": current.weekday(),
            })
            current += timedelta(days=1)

        # ── 今日策略运行状态 ──
        result = db.execute(text("""
            SELECT metric_value, status, detail, checked_at FROM system_metrics
            WHERE metric_name='daily_strategy' ORDER BY checked_at DESC LIMIT 1
        """))
        today_strategy = None
        row = result.fetchone()
        if row:
            import json as _json
            detail = {}
            try:
                detail = _json.loads(row[2]) if row[2] else {}
            except (_json.JSONDecodeError, TypeError):
                pass
            today_strategy = {
                "buy_signals": int(row[0]) if row[0] else 0,
                "status": row[1],
                "time": str(row[3])[:19] if row[3] else None,
                "total_signals": detail.get("total_signals", 0),
                "scanned": detail.get("scanned", 0),
                "elapsed_seconds": detail.get("elapsed_seconds", 0),
                "preference": detail.get("preference", ""),
                "strategy_date": str(row[3])[:10] if row[3] else None,
            }

        # ── 最近下载日志 ──
        result = db.execute(text("""
            SELECT metric_value, status, detail, checked_at FROM system_metrics
            WHERE metric_name='daily_download' ORDER BY checked_at DESC LIMIT 10
        """))
        download_log = []
        for r in result.fetchall():
            download_log.append({
                "rows": int(r[0]) if r[0] else 0,
                "status": r[1],
                "detail": r[2],
                "time": str(r[3])[:19] if r[3] else None,
            })

        return {
            "overview": overview,
            "calendar": calendar,
            "missing_dates": missing_dates,
            "download_log": download_log,
            "today_strategy": today_strategy,
        }
    finally:
        db.close()


@router.get("/trade_calendar")
def get_trade_calendar(
    year: int = Query(None, description="年份，默认今年"),
):
    """获取指定年份的完整交易日历（含真实中国法定节假日）。"""
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

# 内存中记录后台同步任务状态
_sync_tasks: dict = {}

class SyncDateRequest(BaseModel):
    date: str  # YYYY-MM-DD


def _run_sync_in_background(sync_date: str, task_id: str):
    """后台线程执行数据采集。"""
    _sync_tasks[task_id] = {"status": "running", "date": sync_date, "started_at": _time.time()}
    try:
        from crawler.baostock_crawler import BaostockCrawler

        db = get_sync_db()
        try:
            # 1. 同步 A 股
            crawler = BaostockCrawler()
            d = date.fromisoformat(sync_date)
            result = crawler.download_daily_update(d, db=db)
            crawler.logout()
            db.commit()

            # 2. 同步全量指数 (596 只)
            idx_crawler = BaostockCrawler()
            idx_rows = idx_crawler.download_all_index_daily(sync_date, db=db)
            idx_crawler.logout()
            db.commit()

            # 3. 同步 ETF (1558 只)
            etf_crawler = BaostockCrawler()
            etf_result = etf_crawler.download_etf_daily(sync_date, db=db)
            etf_crawler.logout()
            db.commit()

            after = db.execute(text(
                "SELECT COUNT(*) FROM daily_quote WHERE trade_date=:d"
            ), {"d": sync_date}).scalar() or 0

            _sync_tasks[task_id] = {
                "status": "completed",
                "date": sync_date,
                "stocks_added": result.get("rows", 0),
                "stocks_errors": result.get("errors", 0),
                "index_rows": idx_rows,
                "etf_rows": etf_result.get("rows", 0),
                "total_after": after,
                "elapsed": round(_time.time() - _sync_tasks[task_id]["started_at"]),
            }
        except Exception as e:
            logger.error(f"后台同步 {sync_date} 失败: {e}")
            _sync_tasks[task_id] = {"status": "failed", "date": sync_date, "error": str(e)}
        finally:
            db.close()
    except Exception as e:
        _sync_tasks[task_id] = {"status": "failed", "date": sync_date, "error": str(e)}

    # 10 分钟后清理
    _time.sleep(600)
    _sync_tasks.pop(task_id, None)


@router.post("/data_status/sync_date")
def sync_date(body: SyncDateRequest):
    """手动触发指定日期的数据采集（后台异步执行）。

    立即返回 task_id，前端轮询 /api/data_status/sync_status?task_id=xxx 获取进度。
    """
    sync_date = body.date
    import uuid
    task_id = str(uuid.uuid4())[:8]

    logger.info(f"手动触发数据采集: {sync_date} (task={task_id})")

    thread = threading.Thread(target=_run_sync_in_background, args=(sync_date, task_id), daemon=True)
    thread.start()

    return {"ok": True, "task_id": task_id, "date": sync_date, "status": "started"}


@router.get("/data_status/sync_status")
def sync_status(task_id: str):
    """查询后台同步任务状态。"""
    task = _sync_tasks.get(task_id)
    if task is None:
        return {"ok": False, "error": "task_not_found"}
    return {"ok": True, "task": task}
