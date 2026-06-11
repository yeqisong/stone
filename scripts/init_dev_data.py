#!/usr/bin/env python3
"""
本地开发环境数据初始化：快速拉取少量股票近60天日K线 + 基本面数据。
用法: python3 scripts/init_dev_data.py
"""
import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from loguru import logger
from app.db.connection import get_sync_db
from sqlalchemy import text
from app.db.schema import init_db
import baostock as bs

# 开发测试用的股票列表（沪深各选几只典型）
DEV_STOCKS = [
    ("000001", "平安银行", "SZSE"),
    ("000002", "万科A", "SZSE"),
    ("300750", "宁德时代", "SZSE"),
    ("600519", "贵州茅台", "SSE"),
    ("600036", "招商银行", "SSE"),
    ("688256", "寒武纪", "SSE"),
]

# 近60天起始
from datetime import date, timedelta
START_DATE = (date.today() - timedelta(days=60)).isoformat()
END_DATE = date.today().isoformat()


def init_schema():
    logger.info("初始化数据库 schema...")
    db = get_sync_db()
    init_db(db)
    db.close()


def init_master():
    logger.info("写入 stock_master...")
    db = get_sync_db()
    for sc, sn, ex in DEV_STOCKS:
        db.execute(text("""
            INSERT INTO stock_master (stock_code, stock_name, exchange, status, stock_type)
            VALUES (:c, :n, :e, 'N', 'stock')
            ON CONFLICT (stock_code) DO UPDATE SET stock_name=EXCLUDED.stock_name, exchange=EXCLUDED.exchange
        """), {"c": sc, "n": sn, "e": ex})
    db.commit()
    db.close()


def download_kline():
    logger.info(f"下载开发日K线 ({START_DATE} ~ {END_DATE})...")
    lg = bs.login()
    assert lg.error_code == '0', f"baostock 登录失败: {lg.error_msg}"

    db = get_sync_db()
    update_sql = """
        INSERT INTO daily_quote (trade_date, exchange, stock_code, stock_name,
            open, high, low, close, close_hfq, close_qfq, volume, amount, turnover)
        VALUES (:d, :e, :c, :n, :o, :h, :l, :cl, :cl, :cl, :v, :a, :t)
        ON CONFLICT (stock_code, exchange, trade_date)
        DO UPDATE SET close=EXCLUDED.close, volume=EXCLUDED.volume
    """
    for sc, sn, ex in DEV_STOCKS:
        bs_code = f"sh.{sc}" if ex == "SSE" else f"sz.{sc}"
        rs = bs.query_history_k_data_plus(bs_code,
            "date,open,high,low,close,volume,amount,turn",
            START_DATE, END_DATE, "d", "3")
        count = 0
        while rs.next():
            d = rs.get_row_data()
            if not d[0]: continue
            db.execute(text(update_sql), {
                "d": d[0], "e": ex, "c": sc, "n": sn,
                "o": float(d[1]) if d[1] else 0,
                "h": float(d[2]) if d[2] else 0,
                "l": float(d[3]) if d[3] else 0,
                "cl": float(d[4]) if d[4] else 0,
                "v": int(float(d[5])) if d[5] else 0,
                "a": float(d[6]) if d[6] else 0,
                "t": float(d[7]) if d[7] else None,
            })
            count += 1
        db.commit()
        logger.info(f"  {sc} {sn}: {count} 行")
        time.sleep(0.1)
    bs.logout()
    db.close()


def download_fundamentals():
    """下载开发用基本面 + 基本面历史"""
    from crawler.baostock_crawler import BaostockCrawler
    c = BaostockCrawler()
    c.login()

    db = get_sync_db()
    for sc, sn, ex in DEV_STOCKS:
        bs_code = f"sh.{sc}" if ex == "SSE" else f"sz.{sc}"
        # 最新基本面
        fundamentals = c._get_latest_fundamentals(bs_code)
        if fundamentals:
            db.execute(text("""
                INSERT INTO stock_fundamentals
                (stock_code, stock_name, industry, pe_ttm, pb_mrq, roe, revenue_yoy, profit_yoy, updated_at)
                VALUES (:c, :n, :i, :pe, :pb, :roe, :ry, :py, CURRENT_TIMESTAMP)
                ON CONFLICT (stock_code) DO UPDATE SET
                pe_ttm=EXCLUDED.pe_ttm, pb_mrq=EXCLUDED.pb_mrq, roe=EXCLUDED.roe,
                revenue_yoy=EXCLUDED.revenue_yoy, profit_yoy=EXCLUDED.profit_yoy
            """), {"c": sc, "n": sn, "i": fundamentals.get("industry", ""),
                   "pe": fundamentals.get("pe_ttm"), "pb": fundamentals.get("pb_mrq"),
                   "roe": fundamentals.get("roe"), "ry": fundamentals.get("revenue_yoy"),
                   "py": fundamentals.get("profit_yoy")})
            logger.info(f"  {sc} 基本面已下载")

        # 基本面历史 (2024-2026 半年)
        for year in [2024, 2025, 2026]:
            for q in [1, 2, 3, 4]:
                rs = bs.query_profit_data(code=bs_code, year=year, quarter=q)
                while rs.next():
                    d = rs.get_row_data()
                    if not d: continue
                    pe = float(d[4]) if len(d)>4 and d[4] else None
                    pb = float(d[5]) if len(d)>5 and d[5] else None
                    roe = float(d[3]) if len(d)>3 and d[3] else None
                    if pe is None: continue
                    report_date = f"{year}-{q*3:02d}-01"
                    db.execute(text("""
                        INSERT INTO stock_fundamentals_history
                        (stock_code, report_date, pe_ttm, pb_mrq, roe)
                        VALUES (:c, :d, :pe, :pb, :roe)
                        ON CONFLICT (stock_code, report_date) DO UPDATE SET
                        pe_ttm=EXCLUDED.pe_ttm, pb_mrq=EXCLUDED.pb_mrq, roe=EXCLUDED.roe
                    """), {"c": sc, "d": report_date, "pe": pe, "pb": pb, "roe": roe})
        db.commit()
        time.sleep(0.1)
    c.logout()
    db.close()


if __name__ == '__main__':
    logger.info("========== 开发环境数据初始化 ==========")
    logger.info(f"股票: {len(DEV_STOCKS)} 只, 日期: {START_DATE} ~ {END_DATE}")

    init_schema()
    init_master()
    download_kline()
    download_fundamentals()

    logger.info("========== 开发环境初始化完成! ==========")
    logger.info("访问 http://localhost:8000/ 查看效果")
