"""本地开发数据初始化：从 baostock 拉取最近 1 个月数据。"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import socket
socket.setdefaulttimeout(30)  # 防止 baostock 连接 hang 住

from datetime import date, timedelta
from sqlalchemy import text
from loguru import logger

from app.db.connection import get_sync_db
from crawler.baostock_crawler import BaostockCrawler


def main():
    today = date.today()
    start = (today - timedelta(days=30)).isoformat()
    end = today.isoformat()
    logger.info(f"=== 初始化本地数据: {start} ~ {end} ===")

    db = get_sync_db()
    c = BaostockCrawler()
    if not c.login():
        logger.error("baostock 登录失败")
        return

    # 1. 更新 stock_master
    logger.info("[1/4] 更新 stock_master...")
    stock_info = c.get_a_stock_codes()
    for code, info in stock_info.items():
        db.execute(text("""
            INSERT INTO stock_master (stock_code, stock_name, exchange, ipo_date, status, stock_type)
            VALUES (:c, :n, :e, :i, 'N', 'stock')
            ON CONFLICT (stock_code) DO UPDATE SET stock_name=EXCLUDED.stock_name
        """), {"c": code, "n": info["name"], "e": "SSE" if code[0]=='6' else "SZSE", "i": info.get("ipo_date") or None})
    db.commit()
    logger.info(f"  stock_master: {len(stock_info)} 只")

    # 2. 日K线（使用 _download_kline_batch 支持日期范围）
    logger.info(f"[2/4] 下载个股日K线 ({start} ~ {end})...")
    codes = list(stock_info.keys())
    stock_names = {code: info["name"] for code, info in stock_info.items()}
    r = c._download_kline_batch(codes, stock_names, start, end, db=db, upsert_mode=True)
    logger.info(f"  个股K线: {r.get('rows', 0)} 行")

    # 3. 指数 + ETF
    logger.info(f"[3/4] 下载指数 + ETF...")
    r_idx = c.download_all_index_daily(trade_date=None, db=db, force=True, start_date=start, end_date=end)
    logger.info(f"  指数: {r_idx.get('rows', 0) if isinstance(r_idx, dict) else r_idx} 行")
    r_etf = c.download_etf_daily(trade_date=None, db=db, force=True, start_date=start, end_date=end)
    logger.info(f"  ETF: {r_etf.get('rows', 0)} 行")

    # 4. 基本面
    logger.info("[4/4] 下载基本面...")
    r_fund = c.download_fundamentals(force=True)
    logger.info(f"  基本面: {r_fund.get('rows', 0) if isinstance(r_fund, dict) else r_fund} 只")

    c.logout()
    db.close()
    logger.info("=== 初始化完成 ===")


if __name__ == "__main__":
    main()
