"""本地已验证的4个适配器接口 + writers 直接写入"""
import sys, os, time
sys.path.insert(0, '.')

from datetime import date
from sqlalchemy import text
from loguru import logger
from app.db.connection import get_sync_db

from crawler.adapters.baostock_adapter import BaostockAdapter
from crawler.writers import batch_upsert_kline, batch_upsert_index_kline, batch_upsert_fundamentals

START = "2021-06-16"
END = "2026-06-17"
BATCH = 200  # 每批股票数，批间刷新 baostock 会话

def main():
    db = get_sync_db()
    adapter = BaostockAdapter()

    # [1] stock_master + 适配器拉取 + writers 写入
    logger.info("[1/4] 个股K线 {}/{}", START, END)
    adapter._ensure_login()
    codes = [s.stock_code for s in adapter.get_stock_list("stock")]
    logger.info("stock_master: {} codes", len(codes))
    
    for s in adapter.get_stock_list("stock"):
        db.execute(text("INSERT INTO stock_master (stock_code,stock_name,exchange,status,stock_type) VALUES (:c,:n,:e,'N','stock') ON CONFLICT (stock_code,stock_type) DO UPDATE SET stock_name=EXCLUDED.stock_name"),
            {"c":s.stock_code,"n":s.stock_name,"e":s.exchange})
    db.commit()

    for i in range(0, len(codes), BATCH):
        batch = codes[i:i+BATCH]
        rows = adapter.fetch_stock_kline(batch, START, END)
        saved = batch_upsert_kline(db, rows)
        logger.info("  [{}/{}] +{} rows", i+len(batch), len(codes), saved)
        if i + BATCH < len(codes):
            adapter._logout(); time.sleep(2)
            if not adapter._login():
                logger.error("relogin failed at {}", i)
                break

    # [2] 指数K线
    logger.info("[2/4] 指数K线")
    adapter._logout(); time.sleep(2); adapter._ensure_login()
    idx_codes = [s.stock_code for s in adapter.get_stock_list("index")]
    logger.info("{} indices", len(idx_codes))
    for s in adapter.get_stock_list("index"):
        db.execute(text("INSERT INTO stock_master (stock_code,stock_name,exchange,status,stock_type) VALUES (:c,:n,:e,'N','index') ON CONFLICT (stock_code,stock_type) DO UPDATE SET stock_name=EXCLUDED.stock_name"),
            {"c":s.stock_code,"n":s.stock_name,"e":s.exchange})
    db.commit()
    rows = adapter.fetch_index_kline(idx_codes, START, END)
    saved = batch_upsert_index_kline(db, rows)
    logger.info("index: {} rows", saved)

    # [3] ETF K线
    logger.info("[3/4] ETF K线")
    adapter._logout(); time.sleep(2); adapter._ensure_login()
    etf_codes = [s.stock_code for s in adapter.get_stock_list("etf")]
    logger.info("{} ETFs", len(etf_codes))
    for s in adapter.get_stock_list("etf"):
        db.execute(text("INSERT INTO stock_master (stock_code,stock_name,exchange,status,stock_type) VALUES (:c,:n,:e,'N','etf') ON CONFLICT (stock_code,stock_type) DO UPDATE SET stock_name=EXCLUDED.stock_name"),
            {"c":s.stock_code,"n":s.stock_name,"e":s.exchange})
    db.commit()
    rows = adapter.fetch_etf_kline(etf_codes, START, END)
    saved = batch_upsert_kline(db, rows)
    logger.info("etf: {} rows", saved)

    # [4] 基本面（依赖前面K线数据写入 daily_quote）
    logger.info("[4/4] 基本面")
    adapter._logout(); time.sleep(2); adapter._ensure_login()
    stock_codes = [s.stock_code for s in adapter.get_stock_list("stock")]
    fund_rows = adapter.fetch_fundamentals(stock_codes)
    saved = batch_upsert_fundamentals(db, fund_rows)
    logger.info("fund: {} stocks", saved)

    adapter._logout()
    db.close()
    logger.info("=== DONE ===")

if __name__ == "__main__":
    main()
