"""数据采集编排器 — baostock 单一数据源。"""
from datetime import date, timedelta
from sqlalchemy import text
from loguru import logger

from app.db.connection import get_sync_db
from crawler.baostock_crawler import BaostockCrawler


def download_full_history(start: str = "2021-01-01", end: str = None,
                          skip_existing: bool = True,
                          include_fundamentals: bool = True):
    """下载全量历史数据（首次部署或补全时使用）。"""
    if end is None:
        end = date.today().isoformat()
    c = BaostockCrawler()

    # ── 1. 日K线 ──
    logger.info("===== [1/2] 日K线历史数据 =====")
    result = c.download_all_stocks(start, end, skip_existing=skip_existing)
    logger.info(f"日K线完成: {result}")

    # ── 2. stock_master ──
    if result.get("stocks", 0) > 0 or result.get("new_stocks", 0) > 0:
        logger.info("更新 stock_master...")
        try:
            c.login()
            df = c.get_stock_basic_info()
            db = get_sync_db()
            for _, row in df.iterrows():
                db.execute(text("""
                    INSERT INTO stock_master
                    (stock_code, stock_name, exchange, ipo_date, status, stock_type, updated_at)
                    VALUES (:c, :n, :e, :ip, :st, :tp, CURRENT_TIMESTAMP)
                    ON CONFLICT (stock_code) DO UPDATE SET
                    stock_name=EXCLUDED.stock_name, exchange=EXCLUDED.exchange,
                    ipo_date=EXCLUDED.ipo_date, status=EXCLUDED.status,
                    stock_type=EXCLUDED.stock_type, updated_at=CURRENT_TIMESTAMP
                """), {
                    "c": row["stock_code"], "n": row["stock_name"],
                    "e": row["exchange"], "ip": row.get("ipo_date"),
                    "st": row.get("status", "N"),
                    "tp": row.get("stock_type", ""),
                })
            db.commit()
            db.close()
            logger.info(f"  stock_master: {len(df)} 只")
        except Exception as e:
            logger.warning(f"  stock_master 更新失败: {e}")

    # ── 3. 基本面 ──
    if include_fundamentals:
        logger.info("===== [2/2] 基本面数据 =====")
        try:
            c.login()
            fund_count = c.download_fundamentals(skip_existing=skip_existing)
            logger.info(f"基本面完成: {fund_count} 只")
        except Exception as e:
            logger.error(f"基本面下载失败: {e}")
            fund_count = 0
        result["fundamentals"] = fund_count

    c.logout()
    return result


def download_fundamentals_only(skip_existing: bool = True,
                               skip_pe_pb: bool = True) -> int:
    """仅下载基本面数据（ROE/PE/PB/行业/增长率）。"""
    logger.info("===== 仅基本面下载 =====")
    c = BaostockCrawler()
    try:
        count = c.download_fundamentals(skip_existing=skip_existing,
                                        skip_pe_pb=skip_pe_pb)
        return count
    finally:
        c.logout()


def daily_update(trade_date: date = None):
    """每日增量更新（cron 17:35 触发），触发 DAG daily_update 节点。"""
    if trade_date is None:
        trade_date = date.today()
    from scripts.pipeline import dag
    dag.run("daily_update", trade_date=str(trade_date))
    return {"ok": True}


def update_stock_master():
    """更新 stock_master 表（股票基础信息，含 stock_type）。"""
    c = BaostockCrawler()
    df = c.get_stock_basic_info()
    c.logout()

    if df.empty:
        return 0

    db = get_sync_db()
    count = 0
    for _, row in df.iterrows():
        db.execute(text("""
            INSERT INTO stock_master
            (stock_code, stock_name, exchange, ipo_date, status, stock_type, updated_at)
            VALUES (:c, :n, :e, :ip, :st, :tp, CURRENT_TIMESTAMP)
            ON CONFLICT (stock_code) DO UPDATE SET
            stock_name=EXCLUDED.stock_name, exchange=EXCLUDED.exchange,
            ipo_date=EXCLUDED.ipo_date, status=EXCLUDED.status,
            stock_type=EXCLUDED.stock_type, updated_at=CURRENT_TIMESTAMP
        """), {
            "c": row["stock_code"], "n": row["stock_name"],
            "e": row["exchange"], "ip": row.get("ipo_date"),
            "st": row.get("status", "N"),
            "tp": row.get("stock_type", ""),
        })
        count += 1
    db.commit()
    db.close()
    return count
