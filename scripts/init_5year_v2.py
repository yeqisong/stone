"""全量5年数据初始化 — 极简版。"""
import sys, os, time
sys.path.insert(0, '.')
import socket; socket.setdefaulttimeout(30)

from datetime import date
from sqlalchemy import text
from loguru import logger
from app.db.connection import get_sync_db
from crawler.baostock_crawler import BaostockCrawler

START = "2021-06-16"
END = "2026-06-17"

def main():
    db = get_sync_db()
    c = BaostockCrawler()
    if not c.login():
        logger.error("login failed"); return

    # [1] stock_master + 个股K线
    logger.info("[1/4] stock_master + 个股K线 {}", START)
    stock_info = c.get_a_stock_codes()
    for code, info in stock_info.items():
        db.execute(text("INSERT INTO stock_master (stock_code,stock_name,exchange,status,stock_type) VALUES (:c,:n,:e,'N','stock') ON CONFLICT (stock_code,stock_type) DO UPDATE SET stock_name=EXCLUDED.stock_name"),
            {"c":code,"n":info["name"],"e":"SSE" if code[0]=='6' else "SZSE"})
    db.commit()
    logger.info("master {} stocks", len(stock_info))
    codes = list(stock_info.keys())
    names = {c: stock_info[c]["name"] for c in codes}
    r = c._download_kline_batch(codes, names, START, END, db=db, upsert_mode=True)
    logger.info("kline: {} rows {} errs {}s", r.get('rows',0), r.get('errors',0), r.get('elapsed_seconds',0))

    # [2] 指数K线
    logger.info("[2/4] 指数K线")
    c.logout(); time.sleep(2); c.login()
    r = c.download_all_index_daily(trade_date=None, db=db, force=True, start_date=START, end_date=END)
    logger.info("index: {} rows", r.get('rows',0) if isinstance(r,dict) else r)

    # [3] ETF K线
    logger.info("[3/4] ETF K线")
    c.logout(); time.sleep(2); c.login()
    r = c.download_etf_daily(trade_date=None, db=db, force=True, start_date=START, end_date=END)
    logger.info("etf: {} rows", r.get('rows',0))

    # [4] 基本面
    logger.info("[4/4] 基本面")
    c.logout(); time.sleep(2); c.login()
    r = c.download_fundamentals(force=True)
    logger.info("fund: {} stocks", r.get('rows',0) if isinstance(r,dict) else r)

    c.logout(); db.close()
    logger.info("DONE")

if __name__ == "__main__":
    main()
