#!/usr/bin/env python3
"""并行刷新：基本面+基本面历史+交易日历+股本数据。可与全量K线同时运行。"""
import sys, time, os
sys.path.insert(0, '/app')
from datetime import date
from loguru import logger
from app.db.connection import get_sync_db
from sqlalchemy import text
import baostock as bs


def step_calendar():
    logger.info("===== 交易日历 =====")
    from crawler.trade_calendar import sync_from_baostock
    db = get_sync_db()
    sync_from_baostock(db, start_year=2020, end_year=2030)
    db.close()


def step_fundamentals_latest():
    logger.info("===== 最新基本面(PE/PB/ROE/行业) =====")
    from crawler.baostock_crawler import BaostockCrawler
    c = BaostockCrawler()
    count = c.download_fundamentals(skip_existing=False, skip_pe_pb=False)
    c.logout()
    logger.info(f"基本面更新: {count} 只")


def step_fundamentals_history():
    logger.info("===== 基本面历史(季度PE/PB/ROE) =====")
    from crawler.baostock_crawler import BaostockCrawler
    c = BaostockCrawler()
    c.login()
    db = get_sync_db()
    codes = c.get_all_stock_codes()
    logger.info(f"  待处理 {len(codes)} 只")
    total = 0
    for idx, code in enumerate(codes):
        if idx % 500 == 0:
            logger.info(f"  进度: {idx}/{len(codes)}, 已写入 {total} 条")
        bs_code = c._bs_code(code)
        for year in range(2020, 2027):
            for q in [1, 2, 3, 4]:
                try:
                    rs = bs.query_profit_data(code=bs_code, year=year, quarter=q)
                    while rs.next():
                        d = rs.get_row_data()
                        if not d: continue
                        pe = float(d[4]) if len(d)>4 and d[4] and d[4]!='' else None
                        pb = float(d[5]) if len(d)>5 and d[5] and d[5]!='' else None
                        roe = float(d[3]) if len(d)>3 and d[3] and d[3]!='' else None
                        report = f"{year}-{q*3:02d}-01"
                        if pe is not None:
                            db.execute(text(
                                "INSERT INTO stock_fundamentals_history "
                                "(stock_code,report_date,pe_ttm,pb_mrq,roe) VALUES (:c,:d,:pe,:pb,:roe) "
                                "ON CONFLICT (stock_code,report_date) DO UPDATE SET "
                                "pe_ttm=EXCLUDED.pe_ttm,pb_mrq=EXCLUDED.pb_mrq,roe=EXCLUDED.roe"
                            ), {"c":code,"d":report,"pe":pe,"pb":pb,"roe":roe})
                            total += 1
                        time.sleep(0.02)
                except: pass
        db.commit()
    db.close()
    c.logout()
    logger.info(f"基本面历史完成: {total} 条")


def step_total_shares():
    """从 baostock 获取总股本更新 stock_fundamentals.total_shares。"""
    logger.info("===== 总股本更新 =====")
    db = get_sync_db()
    lg = bs.login()
    if lg.error_code != '0':
        logger.error("登录失败"); return

    rs = bs.query_stock_basic()
    updated = 0
    while rs.next():
        d = rs.get_row_data()
        raw = d[0]
        for p in ('sh.','sz.','bj.'):
            if raw.startswith(p): code=raw[len(p):]; break
        else: code=raw
        # baostock query_stock_basic 没有总股本，用 query_profit_data 获取
        bs_code = raw
        try:
            rs2 = bs.query_profit_data(code=bs_code, year=2025, quarter=4)
            while rs2.next():
                d2 = rs2.get_row_data()
                if d2 and len(d2) > 8 and d2[8]:
                    shares = int(float(d2[8]))
                    db.execute(text(
                        "UPDATE stock_fundamentals SET total_shares=:s WHERE stock_code=:c"
                    ), {"s": shares, "c": code})
                    updated += 1
                    break
            time.sleep(0.03)
        except: pass
    db.commit()
    bs.logout()
    db.close()
    logger.info(f"总股本更新: {updated} 只")


if __name__ == '__main__':
    t0 = time.time()
    logger.info("====== 基本面+日历并行刷新开始 ======")
    step_calendar()
    step_fundamentals_latest()
    step_total_shares()
    step_fundamentals_history()
    logger.info(f"====== 完成! 耗时 {time.time()-t0:.0f} 秒 ======")
